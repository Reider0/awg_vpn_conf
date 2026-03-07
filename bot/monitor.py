import os
import psutil
import asyncio
import aiohttp
from database import db

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WG_API_URL = "http://wireguard:8000/api"

# Кэш, чтобы не спамить уведомлениями (хранит UUID)
notified_cache = set()

# ------------------------ DASHBOARD ------------------------
async def get_dashboard():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    peers = 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WG_API_URL}/status", timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    peers = data.get("peers_count", 0)
    except Exception:
        peers = "⚠️ Сервер недоступен"

    return f"📊 Дашборд сервера\n\nCPU:   {cpu}%\nRAM:   {ram}%\nДиск:  {disk}%\n\nПодключено VPN: {peers}"

# ------------------------ MONITOR ------------------------
async def alert_loop(app):
    wg_is_down = False
    
    while True:
        try:
            # 1. Пинг API
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{WG_API_URL}/peers", timeout=5) as resp:
                    resp.raise_for_status()
                    peers_data = await resp.json()

            # Если сервер поднялся
            if wg_is_down:
                wg_is_down = False
                if ADMIN_ID:
                    await app.bot.send_message(chat_id=ADMIN_ID, text="✅ VPN-сервер снова в сети.")

            # 2. Проверка новых подключений (АНТИ-СПАМ ЛОГИКА)
            for peer in peers_data:
                uuid_val = peer.get("uuid")
                endpoint = peer.get("endpoint", "")
                
                # Пропускаем, если UUID выглядит как публичный ключ (ошибка парсинга API)
                if len(uuid_val) > 40 and "=" in uuid_val:
                    continue

                # Пропускаем, если уже уведомляли в этом сеансе
                if uuid_val in notified_cache:
                    continue
                
                # Пропускаем, если endpoint пустой (нет подключения)
                if not endpoint or endpoint == "(none)":
                    continue

                hostname = endpoint.split(":")[0]
                
                # Проверяем в БД
                device_set = await db.device_set(uuid_val)
                
                if not device_set:
                    # Если в БД устройства нет - обновляем и уведомляем
                    user = await db.get_user_by_uuid(uuid_val)
                    if user:
                        await db.execute("UPDATE users SET device=$1, first_connected_at=NOW() WHERE uuid=$2", hostname, uuid_val)
                        if ADMIN_ID:
                            await app.bot.send_message(
                                chat_id=ADMIN_ID, 
                                text=f"🎉 **Новое подключение!**\n\n👤 Пользователь: {user['name']}\n📱 IP-адрес: `{hostname}`\n🆔 `{uuid_val}`",
                                parse_mode="Markdown"
                            )
                    notified_cache.add(uuid_val) # Запоминаем, чтобы не спамить
                else:
                    # Если в БД уже есть - тоже добавляем в кэш, чтобы не дергать БД зря
                    notified_cache.add(uuid_val)

        except Exception as e:
            if not wg_is_down and isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError)):
                wg_is_down = True
                if ADMIN_ID:
                    await app.bot.send_message(chat_id=ADMIN_ID, text="⚠️ VPN-сервер недоступен!")

        await asyncio.sleep(10)

async def cleanup_peers():
    # Очищаем кэш уведомлений раз в час, чтобы ловить смены IP
    while True:
        await asyncio.sleep(3600)
        notified_cache.clear()