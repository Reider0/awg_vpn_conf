from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu(active_count=0):
    """
    Главное меню Админа.
    :param active_count: Число активных пользователей для отображения на кнопке.
    """
    keyboard = [
        [
            InlineKeyboardButton("📊 Дашборд", callback_data="start_dashboard"),
            InlineKeyboardButton("🔄 Reboot", callback_data="confirm_reboot")
        ],
        [
            InlineKeyboardButton("🔑 Создать ключ", callback_data="gen_key"),
            InlineKeyboardButton("📈 График VPN", callback_data="vpn_graph")
        ],
        [
            InlineKeyboardButton(f"🟢 Онлайн [{active_count}]", callback_data="show_online"),
        ],
        [
            InlineKeyboardButton("💾 Бэкап", callback_data="backup"),
            InlineKeyboardButton("📝 Логи (Excel)", callback_data="download_logs"),
        ],
        [
            InlineKeyboardButton("🆕 Проверить обновления", callback_data="check_update")
        ],
        [
            InlineKeyboardButton("♻️ Восстановить", callback_data="restore")
        ],
        [
            InlineKeyboardButton("🛠 Аудит Сервера", callback_data="run_audit"),
        ],
        [
            InlineKeyboardButton("👥 Пользователи", callback_data="users_page_0"),
            InlineKeyboardButton("📊 Экспорт БД", callback_data="export_excel")
        ],
        [
            InlineKeyboardButton("👤 Войти в Режим Клиента", callback_data="client_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)