import os
import asyncio
import time
import json
from datetime import timedelta
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, InputMediaPhoto
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

from utils import (
    ADMIN_ID, WG_API_URL, escape_md, state_data, stop_bg_tasks, deregister_menu, 
    safe_delete, get_current_version, get_update_info, broadcast_message, get_moscow_now, ts_to_moscow
)
from ui import main_menu
from database import db
from monitor import get_dashboard
from graphs import generate_vpn_graph
from backup_manager import create_backup, restore_backup

async def return_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None, chat_id=None):
    await stop_bg_tasks()
    bot = context.bot if hasattr(context, 'bot') else context.bot

    if not chat_id and update and update.effective_chat:
        chat_id = update.effective_chat.id
    if not message_id and update and update.callback_query:
        message_id = update.callback_query.message.message_id
    
    active_count = 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WG_API_URL}/status", timeout=2) as resp:
                if resp.status == 200: active_count = (await resp.json()).get("active_peers", 0)
    except Exception: pass

    text = "🛡 **VPN Dashboard**\nВыберите действие:"
    markup = main_menu(active_count=active_count)
    sent_msg = None

    if not message_id:
        sent_msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    else:
        try:
            sent_msg = await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        except BadRequest as e:
            if "not modified" in str(e):
                state_data["active_menus"][chat_id] = message_id
                return
            await safe_delete(context, chat_id, message_id)
            sent_msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

    if sent_msg: state_data["active_menus"][chat_id] = sent_msg.message_id

async def update_persistent_backup(context: ContextTypes.DEFAULT_TYPE, force_new: bool = False):
    try:
        archive_path = create_backup()
        if not os.path.exists(archive_path): 
            return False, "Файл архива не создан."

        saved_msg_id = await db.get_setting("backup_message_id")
        caption = f"💾 **Актуальный бэкап системы**\n📅 Дата (МСК): {get_moscow_now().strftime('%d.%m.%Y %H:%M:%S')}\nℹ️ Сообщение обновляется автоматически."

        if force_new:
            with open(archive_path, "rb") as f:
                msg = await context.bot.send_document(chat_id=ADMIN_ID, document=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
                try: await context.bot.pin_chat_message(chat_id=ADMIN_ID, message_id=msg.message_id)
                except: pass
                await db.set_setting("backup_message_id", str(msg.message_id))
            return True, "Успешно"

        sent_new = False
        if saved_msg_id:
            try:
                with open(archive_path, "rb") as f:
                    await context.bot.edit_message_media(chat_id=ADMIN_ID, message_id=int(saved_msg_id), media=InputMediaDocument(media=f, caption=caption, parse_mode=ParseMode.MARKDOWN))
            except Exception: 
                sent_new = True
        else:
            sent_new = True

        if sent_new:
            if saved_msg_id: 
                await safe_delete(context, ADMIN_ID, int(saved_msg_id))
            with open(archive_path, "rb") as f:
                msg = await context.bot.send_document(chat_id=ADMIN_ID, document=f, caption=caption, parse_mode=ParseMode.MARKDOWN)
                try: await context.bot.pin_chat_message(chat_id=ADMIN_ID, message_id=msg.message_id)
                except: pass
                await db.set_setting("backup_message_id", str(msg.message_id))
                
        return True, "Успешно"
    except Exception as e: 
        print(f"Backup update error: {e}")
        return False, str(e)

async def dashboard_loop(context, chat_id, message_id):
    while state_data["dashboard_running"]:
        try:
            text = await get_dashboard()
            keyboard = [[InlineKeyboardButton("🔙 Главное меню (Стоп)", callback_data="back_to_main")]]
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            if "not found" in str(e):
                state_data["dashboard_running"] = False
                break
        await asyncio.sleep(5)

async def start_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await stop_bg_tasks()
    deregister_menu(query.message.chat_id)
    
    state_data["dashboard_running"] = True
    state_data["dashboard_task"] = asyncio.create_task(dashboard_loop(context, query.message.chat_id, query.message.message_id))

async def confirm_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_bg_tasks()
    deregister_menu(update.effective_chat.id)
    keyboard = [[InlineKeyboardButton("🚨 ДА, Перезагрузить", callback_data="do_reboot_server")],[InlineKeyboardButton("🔙 Нет, Отмена", callback_data="back_to_main")]]
    await update.callback_query.edit_message_text("⚠️ **Внимание!**\nВы собираетесь перезагрузить **ФИЗИЧЕСКИЙ СЕРВЕР**.\nВы уверены?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def do_reboot_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await broadcast_message(context.application, "⚠️ **Внимание!**\n\nСервер уходит на перезагрузку. VPN будет недоступен 2-3 минуты.", db)
    await db.log_event("System", "Admin requested physical server reboot.")
    await update.callback_query.edit_message_text("🔄 **Команда на перезагрузку отправлена!**\n\nСервер уходит в ребут.", parse_mode=ParseMode.MARKDOWN)
    
    os.makedirs("/volumes/flags", exist_ok=True)
    with open("/volumes/flags/was_rebooting", "w") as f: f.write("true")
    with open("/volumes/flags/do_reboot", "w") as f: f.write("reboot_requested")

async def graph_loop(context, chat_id, message_id):
    while state_data["graph_running"]:
        await asyncio.sleep(10)
        if not state_data["graph_running"]: break
        try:
            path = await generate_vpn_graph()
            keyboard = [[InlineKeyboardButton("🔙 Назад (Остановить)", callback_data="back_to_main")]]
            with open(path, "rb") as f:
                media = InputMediaPhoto(media=f, caption=f"📡 **Live-мониторинг трафика**\n⏳ Обновлено: `{get_moscow_now().strftime('%H:%M:%S')} МСК`", parse_mode=ParseMode.MARKDOWN)
                await context.bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media, reply_markup=InlineKeyboardMarkup(keyboard))
        except BadRequest as e:
            if "not modified" not in str(e): pass
        except Exception: pass

async def send_vpn_graph(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_bg_tasks()
    deregister_menu(update.effective_chat.id)
    query = update.callback_query
    await query.answer()
    await safe_delete(context, query.message.chat_id, query.message.message_id)

    try:
        path = await generate_vpn_graph()
        keyboard = [[InlineKeyboardButton("🔙 Назад (Остановить)", callback_data="back_to_main")]]
        msg = await context.bot.send_photo(chat_id=query.message.chat_id, photo=open(path, "rb"), caption=f"📡 **Live-мониторинг трафика**\n⏳ Обновлено: `{get_moscow_now().strftime('%H:%M:%S')} МСК`", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        
        state_data["graph_running"] = True
        state_data["graph_task"] = asyncio.create_task(graph_loop(context, query.message.chat_id, msg.message_id))
    except Exception as e:
        await return_to_main_menu(update, context)
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Ошибка графика: {e}")

async def online_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_bg_tasks()
    deregister_menu(update.effective_chat.id)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WG_API_URL}/peers", timeout=3) as resp:
                peers_data = await resp.json()

        db_users = await db.get_all_users()
        uuid_to_name = {u['uuid']: u['name'] for u in db_users}
        active_list =[]
        now = int(time.time())
        for peer in peers_data:
            last_hs = peer.get('latest_handshake', 0)
            diff = now - last_hs
            if last_hs > 0 and diff < 180:
                uuid_val = peer.get('uuid')
                name = uuid_to_name.get(uuid_val, "Unknown")
                date_str = ts_to_moscow(last_hs).strftime('%d.%m.%y %H:%M:%S')
                diff_str = str(timedelta(seconds=diff)).split('.')[0]
                active_list.append(f"👤 **{escape_md(name)}**\n🕒 МСК: {date_str} `[{diff_str} назад]`")

        text = "🟢 **Пользователи онлайн:**\n\n" + "\n\n".join(active_list) if active_list else "💤 **Сейчас никого нет онлайн.**"
    except Exception as e: text = f"⚠️ Ошибка получения данных: {e}"
    await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]), parse_mode=ParseMode.MARKDOWN)

async def check_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_bg_tasks()
    deregister_menu(update.effective_chat.id)
    
    await update.callback_query.edit_message_text("⏳ Проверка обновлений (сверка хэшей)...")
    
    local_hash, local_version, remote_hash, remote_version = await asyncio.to_thread(get_update_info)
    
    is_update_available = False
    if remote_hash != "unknown" and local_hash != "unknown" and remote_hash != local_hash:
        is_update_available = True
        
    text = f"📦 **Обновления**\n\n"
    text += f"Текущая версия: `{local_version}` (Хэш: `{local_hash}`)\n"
    text += f"Доступна версия: `{remote_version}` (Хэш: `{remote_hash}`)\n\n"
    
    if is_update_available:
        text += "⚠️ **Доступно новое обновление!**"
        btn = InlineKeyboardButton("✅ Обновить сейчас", callback_data="do_update")
    else:
        text += "✅ У вас установлена последняя версия."
        btn = InlineKeyboardButton("🔄 Переустановить", callback_data="do_update")
        
    await update.callback_query.edit_message_text(
        text=text, 
        reply_markup=InlineKeyboardMarkup([[btn],[InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")]]), 
        parse_mode=ParseMode.MARKDOWN
    )

async def do_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Процесс обновления (гарантированное создание флагов)"""
    await update.callback_query.message.edit_reply_markup(reply_markup=None)
    await broadcast_message(context.application, "⚠️ **Технические работы**\n\nСервер уходит на обновление. Связь может прерваться на 1-2 минуты.", db)
    await db.log_event("System", "Admin triggered system update via Git.")
    
    status_msg = await update.callback_query.message.reply_text("⚙️ Шаг 1/3: Бэкап...")
    try:
        res = await update_persistent_backup(context)
        if isinstance(res, tuple) and not res[0]:
            await status_msg.edit_text(f"⚠️ Ошибка бэкапа: {res[1]}\nПродолжаю обновление...")
        else:
            await status_msg.edit_text("⚙️ Шаг 2/3: Бэкап OK.\n⚙️ Шаг 3/3: Сигнал обновления...")
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Ошибка бэкапа: {e}\nПродолжаю обновление...")

    await status_msg.edit_text("🚀 **Обновление запущено.**\nКонтейнеры перезапускаются. Бот вернется через минуту.")

    # ЖЕСТКАЯ ЗАПИСЬ ФЛАГОВ ОБНОВЛЕНИЯ (Только после успешной отправки сообщений)
    os.makedirs("/volumes/flags", exist_ok=True)
    with open("/volumes/flags/was_updating", "w") as f: f.write("true")
    with open("/volumes/flags/do_update", "w") as f: f.write("update_requested")

async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    
    msg = await context.bot.send_message(
        chat_id=ADMIN_ID, 
        text="⏳ **Создание бэкапа...**\nСохраняю базу данных и ключи, пожалуйста, подождите.", 
        parse_mode=ParseMode.MARKDOWN
    )
    
    success, err = await update_persistent_backup(context, force_new=True)
    
    if success:
        await context.bot.edit_message_text(
            chat_id=ADMIN_ID, 
            message_id=msg.message_id, 
            text="✅ **Новый бэкап успешно создан и закреплен в шапке чата!**", 
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(4)
        await safe_delete(context, ADMIN_ID, msg.message_id)
    else:
        await context.bot.edit_message_text(
            chat_id=ADMIN_ID, 
            message_id=msg.message_id, 
            text=f"❌ **Ошибка создания бэкапа:**\n`{err}`", 
            parse_mode=ParseMode.MARKDOWN
        )

async def download_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Генерация логов...", show_alert=True)
    try:
        path = await db.export_logs_to_excel("/volumes/backups/vpn_logs.xlsx")
        await context.bot.send_document(chat_id=ADMIN_ID, document=open(path, "rb"), caption="📑 Логи трафика (Excel)")
    except Exception as e: await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Ошибка генерации: {e}")

async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_bg_tasks()
    deregister_menu(update.effective_chat.id)
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_main")]]
    await update.callback_query.edit_message_text("📤 Отправьте файл резервной копии (.tar.gz)", reply_markup=InlineKeyboardMarkup(keyboard))
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
        if not update.message.document.file_name.endswith(".tar.gz"):
            await context.bot.send_message(chat_id=chat_id, text="❌ Нужен файл .tar.gz"); return

        file = await context.bot.get_file(update.message.document.file_id)
        await file.download_to_drive("/tmp/restore.tar.gz")
        
        if menu_id: await context.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text="⏳ Восстанавливаю...")
        await restore_backup("/tmp/restore.tar.gz")
        
        context.user_data["state"] = None
        await db.log_event("System", "System restored from backup archive.")
        await update_persistent_backup(context)
        await return_to_main_menu(update, context, message_id=menu_id, chat_id=chat_id)
    except Exception as e:
        if menu_id: await context.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text=f"❌ Ошибка: {e}")

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Генерация БД...", show_alert=True)
    try:
        path = await db.export_to_excel("/volumes/backups/users_db.xlsx")
        await context.bot.send_document(chat_id=ADMIN_ID, document=open(path, "rb"), caption="📊 База данных и Логи")
    except Exception as e: await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Ошибка: {e}")

async def run_audit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stop_bg_tasks()
    deregister_menu(update.effective_chat.id)
    query = update.callback_query
    chat_id = query.message.chat_id
    msg_id = query.message.message_id

    await query.answer("Связь с демоном хоста...")

    flags_dir = "/volumes/flags"
    os.makedirs(flags_dir, exist_ok=True)
    
    status_file = os.path.join(flags_dir, "audit_status")
    report_file = os.path.join(flags_dir, "audit_report.json")
    if os.path.exists(status_file): os.remove(status_file)
    if os.path.exists(report_file): os.remove(report_file)

    with open(os.path.join(flags_dir, "do_audit"), "w") as f:
        f.write("run")

    stages = {
        "network": "🌐 Проверка сети и маршрутизации",
        "host": "💻 Анализ аппаратных ресурсов",
        "docker": "🐳 Проверка среды Docker",
        "vpn": "🛡 Тестирование ядра VPN",
        "storage": "🗄 Диагностика хранилища и БД",
        "security": "🔐 Аудит безопасности",
        "services": "⚙️ Анализ системных логов",
        "done": "✅ Сборка 50 параметров отчета"
    }

    dots_arr =[".  ", ".. ", "..."]
    current_stage = "init"
    
    for i in range(50):
        if os.path.exists(status_file):
            with open(status_file, "r") as f:
                current_stage = f.read().strip()
        
        if current_stage == "done" and os.path.exists(report_file):
            break

        dots = dots_arr[i % 3]
        text = "🛠 **Глобальный аудит Сервера**\n\nВыполняется 50 проверок на уровне ОС:\n\n"
        
        stage_keys = list(stages.keys())
        try:
            cur_idx = stage_keys.index(current_stage) if current_stage in stage_keys else -1
        except ValueError:
            cur_idx = -1
            
        for idx, (k, v) in enumerate(stages.items()):
            if k == "done": continue
            if idx < cur_idx:
                text += f"✅ {v}\n"
            elif idx == cur_idx:
                text += f"🔄 {v}{dots}\n"
            else:
                text += f"⏳ {v}\n"
        
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            pass
        
        await asyncio.sleep(0.6)

    if not os.path.exists(report_file):
        text = "❌ **Ошибка аудита:** Демон хоста не ответил.\nПроверьте: `sudo systemctl status vpn-updater`"
        kb = [[InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")]]
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    try:
        with open(report_file, "r") as f:
            report = json.load(f)
    except Exception as e:
        text = f"❌ **Ошибка парсинга отчета:**\n`{e}`"
        kb = [[InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")]]
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    full_report_path = "/tmp/audit_detailed_report.txt"
    with open(full_report_path, "w", encoding="utf-8") as rf:
        rf.write(f"=== ГЛОБАЛЬНЫЙ АУДИТ СЕРВЕРА ({get_moscow_now().strftime('%d.%m.%Y %H:%M:%S')}) ===\n\n")
        for cat_key, tests in report.items():
            cat_name = stages.get(cat_key, cat_key).upper()
            rf.write(f"--- {cat_name} ---\n")
            for t in tests:
                icon = "[ OK ]" if t["status"] == "ok" else ("[WARN]" if t["status"] == "warning" else "[FAIL]")
                rf.write(f"{icon} {t['name']}: {t['msg']}\n")
            rf.write("\n")

    summary_text = f"📊 **Итоги Глобального Аудита:**\n⏳ Завершено проверок: 50 параметров\n\n"
    
    total_fails = 0
    total_warns = 0
    failed_list =[]

    for cat_key, tests in report.items():
        cat_fails = sum(1 for t in tests if t["status"] == "error")
        cat_warns = sum(1 for t in tests if t["status"] == "warning")
        total_fails += cat_fails
        total_warns += cat_warns
        
        cat_icon = "✅"
        if cat_fails > 0: cat_icon = "❌"
        elif cat_warns > 0: cat_icon = "⚠️"
        
        summary_text += f"{cat_icon} **{stages.get(cat_key, cat_key)}** ({len(tests)} тестов)\n"
        
        for t in tests:
            if t["status"] == "error":
                summary_text += f"   └ ❌ `{t['name']}`: {t['msg']}\n"
                failed_list.append(f"{t['name']}")
            elif t["status"] == "warning":
                summary_text += f"   └ ⚠️ `{t['name']}`: {t['msg']}\n"

    summary_text += "\n"
    if total_fails == 0 and total_warns == 0:
        summary_text += "🚀 **Вердикт:** Сервер в идеальном состоянии! Все 50 проверок пройдены успешно."
    else:
        summary_text += f"⚠️ **Вердикт:** Найдено проблем: {total_fails}, Предупреждений: {total_warns}.\nПолный лог со всеми 50 тестами прикреплен в файле ниже 👇"

    await safe_delete(context, chat_id, msg_id)
    
    kb = [[InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")]]
    with open(full_report_path, "rb") as rf:
        sent_msg = await context.bot.send_document(
            chat_id=chat_id, 
            document=rf, 
            caption=summary_text, 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode=ParseMode.MARKDOWN
        )
    state_data["active_menus"][chat_id] = sent_msg.message_id