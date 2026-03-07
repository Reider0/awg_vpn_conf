import os
import asyncio
import subprocess
import aiohttp
from datetime import datetime
from pathlib import Path

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    InputMediaDocument
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# Импорты
from ui import main_menu
from monitor import get_dashboard, alert_loop, cleanup_peers
# Импортируем функцию удаления
from wireguard_manager import create_peer, delete_peer
from database import db
from backup_manager import create_backup, restore_backup
from graphs import generate_vpn_graph

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

VERSION_FILE = "/app/VERSION_FILE"
BACKUP_FILE = "/volumes/backups/backup_latest.tar.gz"
# Основной URL репозитория (можно без пароля)
GIT_REPO = os.getenv("GIT_REPO", "") 
# Дополнительные переменные для авторизации
GIT_USERNAME = os.getenv("GIT_USERNAME", "")
GIT_TOKEN = os.getenv("GIT_TOKEN", "")

WG_API_URL = "http://wireguard:8000/api"
CONFIGS_DIR = Path("/volumes/configs")

# --- ГЛОБАЛЬНОЕ СОСТОЯНИЕ ---
dashboard_running = False
dashboard_task = None

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def check_admin(user_id):
    return user_id == ADMIN_ID

def get_current_version():
    try:
        if os.path.exists(VERSION_FILE):
            with open(VERSION_FILE, "r") as f:
                return f.read().strip()
    except Exception:
        pass
    return "Unknown"

def get_git_version():
    """
    Умная проверка версии Git.
    Берет токен из ENV и подставляет его в URL на лету.
    Безопасно для приватных репозиториев.
    """
    try:
        if not GIT_REPO:
            return "NO_REPO"
        
        # Формируем URL с авторизацией
        auth_repo_url = GIT_REPO
        
        # Если задан токен и URL еще не содержит собачку (значит он чистый)
        if GIT_TOKEN and "https://" in GIT_REPO and "@" not in GIT_REPO:
            # Превращаем https://github.com/... -> https://user:token@github.com/...
            prefix = "https://"
            suffix = GIT_REPO[len(prefix):]
            if GIT_USERNAME:
                auth_repo_url = f"{prefix}{GIT_USERNAME}:{GIT_TOKEN}@{suffix}"
            else:
                auth_repo_url = f"{prefix}{GIT_TOKEN}@{suffix}"
        
        # Отключаем интерактивный ввод
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        
        cmd = f"git ls-remote {auth_repo_url} refs/heads/main"
        
        # Таймаут 15 секунд на случай плохой сети
        output = subprocess.check_output(
            cmd, 
            shell=True, 
            stderr=subprocess.STDOUT, 
            env=env,
            timeout=15
        ).decode().strip()
        
        parts = output.split()
        if parts:
            return parts[0][:7]
        return "пусто"
        
    except subprocess.TimeoutExpired:
        return "таймаут"
    except subprocess.CalledProcessError as e:
        # Логируем ошибку в консоль сервера, но не боту (чтобы токен не утек)
        print(f"GIT CHECK ERROR. Check credentials.") 
        return "ошибка доступа"
    except Exception as e:
        print(f"GIT GENERIC ERROR: {e}")
        return "ошибка сети"

async def safe_delete(context, chat_id, message_id):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def stop_bg_tasks():
    """Останавливает дашборд."""
    global dashboard_running, dashboard_task
    dashboard_running = False
    if dashboard_task:
        dashboard_task.cancel()
        dashboard_task = None

# --- НАВИГАЦИЯ ---

async def return_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None, chat_id=None):
    await stop_bg_tasks()

    text = "🛡 **VPN Dashboard**\nВыберите действие:"
    markup = main_menu()
    
    if not chat_id and update.effective_chat:
        chat_id = update.effective_chat.id
    
    if not message_id and update.callback_query:
        message_id = update.callback_query.message.message_id
    
    # Если ID не передан - шлем новое
    if not message_id:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        return

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except BadRequest as e:
        # Если нельзя отредактировать (например, была картинка) - пересоздаем
        if "Message is not modified" in str(e):
            return
        await safe_delete(context, chat_id, message_id)
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


# --- БЭКАПЫ ---

async def update_persistent_backup(context: ContextTypes.DEFAULT_TYPE):
    try:
        archive_path = create_backup()
        if not os.path.exists(archive_path): return

        saved_msg_id = await db.get_setting("backup_message_id")
        
        caption = (
            f"💾 **Актуальный бэкап системы**\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"ℹ️ Сообщение обновляется автоматически."
        )

        sent_new = False
        if saved_msg_id:
            try:
                with open(archive_path, "rb") as f:
                    await context.bot.edit_message_media(
                        chat_id=ADMIN_ID,
                        message_id=int(saved_msg_id),
                        media=InputMediaDocument(media=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
                    )
            except Exception:
                sent_new = True
        else:
            sent_new = True

        if sent_new:
            if saved_msg_id:
                await safe_delete(context, ADMIN_ID, int(saved_msg_id))
            with open(archive_path, "rb") as f:
                msg = await context.bot.send_document(
                    chat_id=ADMIN_ID, document=f, caption=caption, parse_mode=ParseMode.MARKDOWN
                )
                try: await context.bot.pin_chat_message(chat_id=ADMIN_ID, message_id=msg.message_id)
                except: pass
                await db.set_setting("backup_message_id", str(msg.message_id))
    except Exception as e:
        print(f"Backup update error: {e}")

# --- DASHBOARD ---

async def dashboard_loop(context, chat_id, message_id):
    global dashboard_running
    while dashboard_running:
        try:
            text = await get_dashboard()
            keyboard = [[InlineKeyboardButton("🔙 Главное меню (Стоп)", callback_data="back_to_main")]]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            if "Message to edit not found" in str(e):
                dashboard_running = False
                break
        await asyncio.sleep(5)

async def start_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global dashboard_running, dashboard_task
    if not check_admin(update.effective_user.id): return
    
    query = update.callback_query
    await query.answer()
    await stop_bg_tasks()
    
    dashboard_running = True
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    dashboard_task = asyncio.create_task(dashboard_loop(context, chat_id, message_id))

# --- GRAPH ---

async def send_vpn_graph(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await stop_bg_tasks()
    
    query = update.callback_query
    await query.answer()
    await safe_delete(context, query.message.chat_id, query.message.message_id)

    try:
        peers_count = 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{WG_API_URL}/status", timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        peers_count = data.get("peers_count", 0)
        except Exception: pass

        path = generate_vpn_graph(peers_count)
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]]
        
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=open(path, "rb"),
            caption=f"📈 **Активность VPN**\nАктивных пиров: {peers_count}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await return_to_main_menu(update, context)
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Ошибка графика: {e}")

# --- USERS MENU ---

async def users_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    await stop_bg_tasks()
    users = await db.get_all_users()
    
    items_per_page = 5
    start = page * items_per_page
    end = start + items_per_page
    current_users = users[start:end]

    keyboard = []
    for u in current_users:
        keyboard.append([InlineKeyboardButton(f"👤 {u['name']}", callback_data=f"user_detail_{u['uuid']}")])

    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{page-1}"))
    if end < len(users): nav_row.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{page+1}"))
    if nav_row: keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")])
    text = "👥 **Управление пользователями**\nВыберите пользователя:"
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            await return_to_main_menu(update, context) 
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def user_detail_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    await stop_bg_tasks()
    user = await db.get_user_by_uuid(uuid)
    if not user:
        await update.callback_query.answer("Пользователь не найден", show_alert=True)
        return

    text = (
        f"👤 **{user['name']}**\n"
        f"🆔 `{user['uuid']}`\n"
        f"💻 {user['device'] or 'Неизвестно'}\n"
        f"📅 {user['created_at'].strftime('%d.%m.%Y')}"
    )

    keyboard = [
        [InlineKeyboardButton("📨 Отправить конфиг", callback_data=f"act_resend_{uuid}")],
        # Кнопка удаления (ведет на меню подтверждения)
        [InlineKeyboardButton("❌ Удалить пользователя", callback_data=f"confirm_delete_{uuid}")],
        [InlineKeyboardButton("🔙 Назад к списку", callback_data="users_page_0")]
    ]
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- УДАЛЕНИЕ ---

async def confirm_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    """Меню подтверждения удаления."""
    user = await db.get_user_by_uuid(uuid)
    if not user: return

    text = f"⚠️ **Вы уверены, что хотите удалить {user['name']}?**\n\nКлюч перестанет работать, файлы будут удалены навсегда."
    keyboard = [
        [InlineKeyboardButton("✅ ДА, Удалить", callback_data=f"do_delete_{uuid}")],
        [InlineKeyboardButton("🔙 Нет, Отмена", callback_data=f"user_detail_{uuid}")]
    ]
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def action_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    """Фактическое удаление пользователя."""
    user = await db.get_user_by_uuid(uuid)
    if not user: 
        await update.callback_query.answer("Пользователь уже удален")
        await users_list_menu(update, context, 0)
        return

    await update.callback_query.answer("Удаление...")
    
    try:
        # 1. Удаляем через менеджер (API + файлы)
        await delete_peer(uuid, user['name'])
        # 2. Удаляем из БД
        await db.execute("DELETE FROM users WHERE uuid=$1", uuid)
        
        await update.callback_query.answer("Успешно удален!", show_alert=True)
        # Возвращаемся в список
        await users_list_menu(update, context, 0)
        
    except Exception as e:
        await update.callback_query.message.reply_text(f"❌ Ошибка удаления: {e}")

async def action_resend_config(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    user = await db.get_user_by_uuid(uuid)
    if not user: return
    name = user['name']
    conf_path = CONFIGS_DIR / f"{name}.conf"
    qr_path = CONFIGS_DIR / f"{name}.png"
    
    try:
        await update.callback_query.answer("Отправка...")
        if conf_path.exists():
            await context.bot.send_document(chat_id=ADMIN_ID, document=open(conf_path, "rb"), caption=f"📄 {name}")
        if qr_path.exists():
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=open(qr_path, "rb"), caption=f"📱 {name}")
    except Exception as e:
        await update.callback_query.answer(f"Ошибка: {e}", show_alert=True)

# --- START & GEN ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    context.user_data["state"] = None
    await stop_bg_tasks()
    await safe_delete(context, update.effective_chat.id, update.message.message_id)
    await return_to_main_menu(update, context, message_id=None)
    asyncio.create_task(update_persistent_backup(context))

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await safe_delete(context, update.effective_chat.id, update.message.message_id)

async def generate_key_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await stop_bg_tasks()
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_main")]]
    await update.callback_query.edit_message_text("✏️ Введите имя пользователя:", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data["state"] = "awaiting_name"
    context.user_data["menu_msg_id"] = update.callback_query.message.message_id

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    state = context.user_data.get("state")
    user_msg_id = update.message.message_id
    chat_id = update.message.chat_id
    await safe_delete(context, chat_id, user_msg_id)
    
    if state == "awaiting_name":
        name = update.message.text.strip().replace(" ", "_")
        context.user_data["name"] = name
        menu_id = context.user_data.get("menu_msg_id")
        
        if menu_id:
             await context.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text="⏳ Генерирую...")

        try:
            uid, conf, qr = await create_peer(name)
            await db.execute("INSERT INTO users (name, uuid, created_at) VALUES ($1, $2, NOW()) ON CONFLICT (uuid) DO NOTHING", name, uid)
            
            if os.path.exists(conf): await context.bot.send_document(chat_id=chat_id, document=open(conf, "rb"), caption=f"📄 {name}")
            if os.path.exists(qr): await context.bot.send_photo(chat_id=chat_id, photo=open(qr, "rb"), caption=f"📱 {name}")
            
            await return_to_main_menu(update, context, message_id=menu_id, chat_id=chat_id)
            context.user_data["state"] = None
        except Exception as e:
            keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data="back_to_main")]]
            if menu_id:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text=f"❌ Ошибка: {e}", reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data["state"] = None

# --- UPDATE ---

async def check_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await stop_bg_tasks()
    
    current_version = get_current_version()
    # Вызываем новую умную функцию
    git_version = get_git_version()
    
    is_update_available = (git_version != "неизвестно" and git_version != current_version)
    
    text = f"📦 **Обновления**\n\nТекущая: `{current_version}`\nGit: `{git_version}`"
    
    if is_update_available:
        text += "\n\n⚠️ **Есть обновление!**"
        btn = InlineKeyboardButton("✅ Обновить сейчас", callback_data="do_update")
    else:
        text += "\n\n✅ У вас последняя версия."
        btn = InlineKeyboardButton("🔄 Переустановить", callback_data="do_update")

    keyboard = [[btn], [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")]]
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def do_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    
    await update.callback_query.message.edit_reply_markup(reply_markup=None)
    status_msg = await update.callback_query.message.reply_text("⚙️ Шаг 1/3: Бэкап...")
    
    try:
        await update_persistent_backup(context)
        await status_msg.edit_text("⚙️ Шаг 2/3: Бэкап OK.\n⚙️ Шаг 3/3: Сигнал обновления...")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        return

    os.makedirs("/volumes/flags", exist_ok=True)
    with open("/volumes/flags/do_update", "w") as f:
        f.write("update_requested")
    
    await status_msg.edit_text("🚀 **Обновление запущено.**\nКонтейнеры перезапускаются. Бот вернется через минуту.")

# --- БЭКАПЫ И ЭКСПОРТ ---

async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await update.callback_query.answer("🔄 Обновление бэкапа...", show_alert=True)
    await update_persistent_backup(context)

async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await stop_bg_tasks()
    
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_main")]]
    await update.callback_query.edit_message_text(
        "📤 Отправьте файл резервной копии (.tar.gz)", 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["state"] = "awaiting_restore_file"
    context.user_data["menu_msg_id"] = update.callback_query.message.message_id

async def restore_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    if context.user_data.get("state") != "awaiting_restore_file": return
    
    user_msg_id = update.message.message_id
    chat_id = update.message.chat_id
    menu_id = context.user_data.get("menu_msg_id")
    
    await safe_delete(context, chat_id, user_msg_id)

    try:
        document = update.message.document
        if not document.file_name.endswith(".tar.gz"):
            await context.bot.send_message(chat_id=chat_id, text="❌ Нужен файл .tar.gz")
            return

        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive("/tmp/restore.tar.gz")
        
        if menu_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text="⏳ Восстанавливаю...")
        
        await restore_backup("/tmp/restore.tar.gz")
        
        context.user_data["state"] = None
        await update_persistent_backup(context)
        await return_to_main_menu(update, context, message_id=menu_id, chat_id=chat_id)

    except Exception as e:
        if menu_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text=f"❌ Ошибка: {e}")

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await update.callback_query.answer("Генерация Excel...", show_alert=True)
    try:
        path = await db.export_to_excel("/volumes/backups/users_export.xlsx")
        await context.bot.send_document(chat_id=ADMIN_ID, document=open(path, "rb"), caption="📊 Полный экспорт")
    except Exception as e:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Ошибка: {e}")

# --- МАРШРУТИЗАТОР ---

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query.data != "start_dashboard":
        await stop_bg_tasks()

    query = update.callback_query
    data = query.data
    
    if data == "back_to_main":
        await return_to_main_menu(update, context)
        return
    
    if data.startswith("users_page_"):
        await users_list_menu(update, context, int(data.split("_")[2]))
        return
    elif data.startswith("user_detail_"):
        await user_detail_menu(update, context, data.split("user_detail_")[1])
        return
    elif data.startswith("confirm_delete_"):
        await confirm_delete_menu(update, context, data.split("confirm_delete_")[1])
        return
    elif data.startswith("do_delete_"):
        await action_delete_user(update, context, data.split("do_delete_")[1])
        return
    elif data.startswith("act_resend_"):
        await action_resend_config(update, context, data.split("act_resend_")[1])
        return
    elif data == "close_graph":
        await return_to_main_menu(update, context)
        return

    actions = {
        "start_dashboard": start_dashboard, 
        "stop_dashboard": return_to_main_menu,
        "gen_key": generate_key_request, 
        "vpn_graph": send_vpn_graph,
        "backup": backup_now, 
        "restore": restore_cmd, 
        "check_update": check_update,
        "do_update": do_update, 
        "show_users": lambda u, c: users_list_menu(u, c, 0),
        "export_excel": export_excel
    }
    
    if data in actions: 
        await actions[data](update, context)
    else: 
        await query.answer("...")

# --- ЗАПУСК ---

async def post_init(application):
    asyncio.create_task(alert_loop(application))
    asyncio.create_task(cleanup_peers())

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db.connect())
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, restore_file_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(CallbackQueryHandler(button_router))
    print("Бот успешно запущен (UI v6.0 - Full Git Env & Deletion)...")
    app.run_polling()