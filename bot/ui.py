from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu():
    """
    Формирует главное меню бота с кнопками на русском языке.
    """
    keyboard = [
        [
            InlineKeyboardButton("📊 Дашборд", callback_data="start_dashboard"),
            InlineKeyboardButton("⏹️ Остановить дашборд", callback_data="stop_dashboard")
        ],
        [
            InlineKeyboardButton("🔑 Создать ключ", callback_data="gen_key"),
            InlineKeyboardButton("📈 График VPN", callback_data="vpn_graph")
        ],
        [
            InlineKeyboardButton("💾 Создать бэкап", callback_data="backup"),
            InlineKeyboardButton("📂 Скачать бэкап", callback_data="download_backup"),
        ],
        [
            InlineKeyboardButton("♻️ Восстановить бэкап", callback_data="restore")
        ],
        [
            InlineKeyboardButton("🆕 Проверить обновление", callback_data="check_update")
        ],
        [
            InlineKeyboardButton("👥 Пользователи", callback_data="show_users"),
            InlineKeyboardButton("📊 Экспорт Excel", callback_data="export_excel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)