import os
import time
import psutil
import asyncio
import aiohttp
from datetime import datetime, timedelta
from database import db
from utils import get_moscow_now, dt_to_moscow, broadcast_message

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WG_API_URL = "http://wireguard:8000/api"

notified_cache = set()
last_ip_cache = {}

# Кеш для контролировалки (чтобы не спамить админу каждую секунду)
ghost_cache = {}
paused_cache = {}

def escape_md(text: str) -> str:
    if not text:
        return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

# ------------------------ DASHBOARD ------------------------
async def get_dashboard():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    peers_text = "0"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WG_API_URL}/status", timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    total = data.get("peers_count", 0)
                    active = data.get("active_peers", 0)
                    peers_text = f"{active} [ {total} ]"
                else:
                    peers_text = "⚠️ Ошибка API"
    except Exception:
        peers_text = "⚠️ Сервер недоступен"

    return f"📊 Дашборд сервера\n\nCPU:   {cpu}%\nRAM:   {ram}%\nДиск:  {disk}%\n\nАктивных VPN: {peers_text}"

# ------------------------ MONITOR & ANTI-SHARING ------------------------
async def alert_loop(app):
    wg_is_down = False
    
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{WG_API_URL}/peers", timeout=5) as resp:
                    resp.raise_for_status()
                    peers_data = await resp.json()

            if wg_is_down:
                wg_is_down = False
                if ADMIN_ID:
                    await app.bot.send_message(chat_id=ADMIN_ID, text="✅ VPN-сервер снова в сети.")
                    await db.log_event("System", "VPN Server is back online.")

            now = int(time.time())
            active_uuids = set()
            
            users_list = await db.get_all_users()
            users_dict = {u['uuid']: u for u in users_list}

            for peer in peers_data:
                uuid_val = peer.get("uuid")
                pubkey = peer.get("public_key")
                handshake = peer.get("latest_handshake", 0)
                endpoint = peer.get("endpoint", "")
                
                if not pubkey or pubkey == "(none)":
                    continue
                
                # ---- КОНТРОЛИРОВАЛКА (АУДИТ В РЕАЛЬНОМ ВРЕМЕНИ) ----
                is_ghost = False
                is_paused_violation = False
                
                if uuid_val not in users_dict:
                    is_ghost = True
                elif not users_dict[uuid_val].get('is_active', True):
                    is_paused_violation = True
                    
                if is_ghost or is_paused_violation:
                    print(f"🔪 Warden: Обнаружен нарушитель (PubKey: {pubkey}). Запускаю удаление сессии...")
                    # Убиваем сессию нарушителя прямо в ядре, открывая новую сессию aiohttp
                    try:
                        async with aiohttp.ClientSession() as kill_session:
                            async with kill_session.post(f"{WG_API_URL}/kill_ghost", json={"public_key": pubkey, "purge_config": is_ghost}, timeout=5) as kill_resp:
                                if kill_resp.status == 200:
                                    print(f"✅ Warden: Сессия успешно уничтожена (PubKey: {pubkey})")
                                else:
                                    print(f"❌ Warden: Ошибка при уничтожении (HTTP {kill_resp.status})")
                    except Exception as e:
                        print(f"❌ Warden: Ошибка связи с ядром: {e}")
                    
                    # Оповещаем админа, если была попытка прокинуть трафик (есть IP)
                    if endpoint and endpoint != "(none)":
                        if is_ghost:
                            if pubkey not in ghost_cache or (now - ghost_cache[pubkey] > 3600):
                                ghost_cache[pubkey] = now
                                msg = f"🚨 **Несанкционированный доступ!**\n\nНеизвестный ключ (Призрак) попытался подключиться.\n📱 IP: `{endpoint}`\n🔑 PubKey: `{pubkey}`\n\n🛡 Сессия принудительно разорвана, ключ удален из системы."
                                if ADMIN_ID:
                                    await app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
                                await db.log_event("Security", f"Killed ghost connection from {endpoint}")
                        elif is_paused_violation:
                            if uuid_val not in paused_cache or (now - paused_cache[uuid_val] > 3600):
                                paused_cache[uuid_val] = now
                                u_name = escape_md(users_dict[uuid_val]['name'])
                                msg = f"🛡 **Блокировка доступа!**\n\nОтключенный пользователь **{u_name}** попытался подключиться.\n📱 IP: `{endpoint}`\n\n⛔️ Доступ отклонен, сессия сброшена."
                                if ADMIN_ID:
                                    await app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
                                await db.log_event("Security", f"Blocked access for paused user {users_dict[uuid_val]['name']}")
                    
                    # Переходим к следующему подключению, это заблокировано
                    continue
                # ----------------------------------------------------

                hostname = endpoint.split(":")[0] if endpoint and endpoint != "(none)" else ""

                if handshake > 0 and (now - handshake) < 180 and hostname:
                    active_uuids.add(uuid_val)
                    user = users_dict.get(uuid_val)

                    if uuid_val in last_ip_cache:
                        prev_ip, prev_time = last_ip_cache[uuid_val]
                        if prev_ip != hostname and (now - prev_time) < 60:
                            if user and ADMIN_ID:
                                safe_name = escape_md(user['name'])
                                msg = f"⚠️ **Подозрение на передачу ключа!**\n\n👤 Пользователь: {safe_name}\n🔄 Быстрая смена IP:\nС `{prev_ip}` на `{hostname}` (менее 1 мин)."
                                await app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
                                await db.log_event("Security", f"Anti-sharing warning for {user['name']}. IPs: {prev_ip} -> {hostname}")
                    
                    last_ip_cache[uuid_val] = (hostname, now)

                    if uuid_val not in notified_cache:
                        device_set = await db.device_set(uuid_val)
                        
                        if user:
                            safe_name = escape_md(user['name'])

                            if not device_set:
                                await db.execute("UPDATE users SET device=$1, first_connected_at=NOW() WHERE uuid=$2", hostname, uuid_val)
                                await db.log_event("Connection", f"First connection by {user['name']} from {hostname}")
                                if ADMIN_ID:
                                    await app.bot.send_message(
                                        chat_id=ADMIN_ID, 
                                        text=f"🎉 **Новое подключение!**\n\n👤 Пользователь: {safe_name}\n📱 IP-адрес: `{hostname}`\n🆔 `{uuid_val}`",
                                        parse_mode="Markdown"
                                    )

                                tg_ids = user.get('tg_ids',[])
                                if tg_ids:
                                    msg_tg = f"🟢 **VPN Подключен!**\n\nКлюч: **{safe_name}**.\nЗащищенное соединение установлено."
                                    for tid in tg_ids:
                                        try:
                                            await app.bot.send_message(chat_id=tid, text=msg_tg, parse_mode="Markdown")
                                        except Exception:
                                            pass

                        notified_cache.add(uuid_val) 

            disconnected_uuids = notified_cache - active_uuids
            for uid in disconnected_uuids:
                notified_cache.remove(uid)

        except Exception as e:
            if not wg_is_down and isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError)):
                wg_is_down = True
                await db.log_event("Error", "VPN API is unreachable")
                if ADMIN_ID:
                    await app.bot.send_message(chat_id=ADMIN_ID, text="⚠️ VPN-сервер недоступен!")

        await asyncio.sleep(10)

# ------------------------ SELF-HEALING ------------------------
async def self_healing_loop(app):
    fail_count = 0
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{WG_API_URL}/health", timeout=5) as resp:
                    if resp.status == 200:
                        fail_count = 0
                    else:
                        fail_count += 1
        except Exception:
            fail_count += 1

        if fail_count >= 3:
            fail_count = 0
            await db.log_event("Self-Healing", "Interface hang detected. Triggering auto-reboot of wg0.")
            if ADMIN_ID:
                await app.bot.send_message(chat_id=ADMIN_ID, text="⚙️ **Self-Healing:** Обнаружено зависание интерфейса VPN. Произвожу авто-перезагрузку сервиса.")
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(f"{WG_API_URL}/reload", timeout=10)
            except Exception:
                pass
            
        await asyncio.sleep(180)

# ------------------------ EXPIRATION LOGIC ------------------------
async def expiration_loop(app):
    while True:
        try:
            users = await db.get_all_users()
            now = datetime.utcnow()
            
            for u in users:
                if u['is_active'] and u['expires_at'] and u['expires_at'] < now:
                    uuid_val = u['uuid']
                    safe_name = escape_md(u['name'])
                    
                    try:
                        async with aiohttp.ClientSession() as session:
                            await session.post(f"{WG_API_URL}/peers/{uuid_val}/pause")
                    except Exception: pass
                    
                    await db.execute("UPDATE users SET is_active=FALSE WHERE uuid=$1", uuid_val)
                    await db.log_event("Expiration", f"Key {u['name']} expired and was paused.")
                    
                    if ADMIN_ID:
                        await app.bot.send_message(
                            chat_id=ADMIN_ID, 
                            text=f"⏳ **Ключ просрочен!**\n\nПользователь: **{safe_name}**\nСрок вышел: {dt_to_moscow(u['expires_at']).strftime('%d.%m.%Y %H:%M')}\nКлюч заморожен.", 
                            parse_mode="Markdown"
                        )
                        
                    tg_ids = u.get('tg_ids',[])
                    for tid in tg_ids:
                        try:
                            await app.bot.send_message(chat_id=tid, text=f"⏳ Ваш VPN-ключ **{safe_name}** просрочен и был отключен. Обратитесь к администратору.", parse_mode="Markdown")
                        except Exception: pass
        except Exception as e:
            print(f"Expiration loop error: {e}")
            
        await asyncio.sleep(3600)

# ------------------------ INACTIVITY LOGIC ------------------------
async def inactivity_loop(app):
    """Отключает пользователей, которые не подключались более 30 дней"""
    while True:
        try:
            users = await db.get_all_users()
            now = datetime.utcnow()
            
            for u in users:
                if u.get('is_active', False):
                    # Если активности не было вообще, считаем от даты создания
                    last_active = u.get('last_active_at') or u.get('created_at')
                    
                    if last_active and (now - last_active).days >= 30:
                        uuid_val = u['uuid']
                        safe_name = escape_md(u['name'])
                        
                        try:
                            async with aiohttp.ClientSession() as session:
                                await session.post(f"{WG_API_URL}/peers/{uuid_val}/pause")
                        except Exception: pass
                        
                        await db.execute("UPDATE users SET is_active=FALSE WHERE uuid=$1", uuid_val)
                        await db.log_event("Inactivity", f"Key {u['name']} was paused due to 30 days of inactivity.")
                        
                        if ADMIN_ID:
                            await app.bot.send_message(
                                chat_id=ADMIN_ID, 
                                text=f"💤 **Пользователь отключен за бездействие!**\n\nКлюч: **{safe_name}**\nНе был в сети более 30 дней.\nКлюч автоматически заморожен.", 
                                parse_mode="Markdown"
                            )
        except Exception as e:
            print(f"Inactivity loop error: {e}")
            
        await asyncio.sleep(86400) # Проверяем раз в сутки

# ------------------------ WEEKLY REPORTS ------------------------
async def weekly_report_loop(app):
    while True:
        now_msk = get_moscow_now()
        if now_msk.weekday() == 6 and now_msk.hour == 20:
            try:
                users = await db.get_all_users()
                stats_24 = await db.get_stats_24h()
                
                live_data = {}
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"{WG_API_URL}/peers", timeout=5) as resp:
                            if resp.status == 200:
                                peers = await resp.json()
                                for p in peers:
                                    live_data[p.get('uuid')] = p.get('rx', 0) + p.get('tx', 0)
                except Exception: pass

                for u in users:
                    tg_ids = u.get('tg_ids',[])
                    if not tg_ids: continue
                    
                    uuid_val = u['uuid']
                    
                    # Считаем дельту по истории в базе данных
                    user_stats =[s for s in stats_24 if s['user_uuid'] == uuid_val]
                    total_bytes = 0
                    prev_val = 0
                    
                    for s in user_stats:
                        val = s['bytes_in'] + s['bytes_out']
                        delta = val - prev_val
                        if delta < 0: delta = val
                        if prev_val == 0: delta = 0
                        total_bytes += delta
                        prev_val = val
                        
                    # Прибавляем живую дельту текущей активной сессии
                    if uuid_val in live_data:
                        live_val = live_data[uuid_val]
                        if user_stats:
                            delta = live_val - prev_val
                            if delta < 0: delta = live_val
                            total_bytes += delta
                        else:
                            total_bytes += live_val
                            
                    mb_used = round(total_bytes / (1024 * 1024), 2)
                    safe_name = escape_md(u['name'])
                    
                    msg = f"📊 **Еженедельный отчет VPN**\n\nКлюч: **{safe_name}**\nИспользовано трафика: `{mb_used} MB`\nВаш VPN работает стабильно! 🚀"
                    for tid in tg_ids:
                        try:
                            await app.bot.send_message(chat_id=tid, text=msg, parse_mode="Markdown")
                        except Exception: pass
                        
                await db.log_event("System", "Weekly reports dispatched.")
            except Exception as e:
                print(f"Weekly report error: {e}")
            
            await asyncio.sleep(86400)
        else:
            await asyncio.sleep(3600)

async def cleanup_peers():
    while True:
        await asyncio.sleep(3600)
        notified_cache.clear()

async def stats_collector_loop():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{WG_API_URL}/peers", timeout=10) as resp:
                    if resp.status == 200:
                        peers_data = await resp.json()
                        now = int(time.time())
                        for peer in peers_data:
                            uuid_val = peer.get("uuid")
                            rx = peer.get("rx", 0)
                            tx = peer.get("tx", 0)
                            hs = peer.get("latest_handshake", 0)
                            
                            if uuid_val and len(uuid_val) < 40:
                                await db.save_stats(uuid_val, rx, tx)
                                
                                # Записываем дату последней активности, если сессия жива (менее 3 минут)
                                if hs > 0 and (now - hs) < 180:
                                    await db.execute("UPDATE users SET last_active_at=NOW() WHERE uuid=$1", uuid_val)
        except Exception:
            pass
        await asyncio.sleep(300)

async def log_cleanup_loop(app):
    while True:
        try:
            await db.cleanup_old_logs(days=7)
            print("🧹 Old logs (7+ days) cleaned up successfully.")
        except Exception as e:
            print(f"🧹 Cleanup error: {e}")
        await asyncio.sleep(86400)

# ------------------------ AUTO-REBOOT ------------------------
async def auto_reboot_loop(app):
    """Плановая перезагрузка сервера (Воскресенье, 04:00 МСК)"""
    while True:
        now_msk = get_moscow_now()
        
        if now_msk.weekday() == 6 and now_msk.hour == 4:
            try:
                text = "🔄 **Плановое обслуживание!**\n\nСервер автоматически уходит на профилактическую перезагрузку. VPN будет недоступен 1-2 минуты.\nДо встречи по ту сторону! 🫡"
                
                await broadcast_message(app, text, db)
                await db.log_event("System", "Scheduled weekly auto-reboot triggered.")
                
                os.makedirs("/volumes/flags", exist_ok=True)
                with open("/volumes/flags/was_rebooting", "w") as f: f.write("true")
                with open("/volumes/flags/do_reboot", "w") as f: f.write("reboot_requested")
                
                await asyncio.sleep(86400)
            except Exception as e:
                print(f"Auto-reboot error: {e}")
        
        await asyncio.sleep(1800)