import os
import qrcode
import aiohttp
from pathlib import Path

# Папка для кэширования конфигов
CONFIGS_DIR = Path("/volumes/configs")
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)

WG_API_URL = "http://wireguard:8000/api"

async def create_peer(name: str):
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{WG_API_URL}/peers", json={"name": name}) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"Ошибка VPN-сервера: {error_text}")
            data = await resp.json()

    uid = data["uid"]
    config_content = data["config"]
    
    conf_path = CONFIGS_DIR / f"{name}.conf"
    qr_path = CONFIGS_DIR / f"{name}.png"
    
    with open(conf_path, "w") as f:
        f.write(config_content)
        
    qr_img = qrcode.make(config_content)
    qr_img.save(qr_path)
    
    return uid, str(conf_path), str(qr_path)

async def delete_peer(uuid: str, name: str):
    # 1. Удаляем из WireGuard
    async with aiohttp.ClientSession() as session:
        async with session.delete(f"{WG_API_URL}/peers/{uuid}") as resp:
            if resp.status not in [200, 404]: # 404 тоже ок, если уже удален
                error_text = await resp.text()
                raise Exception(f"Ошибка удаления API: {error_text}")

    # 2. Удаляем файлы
    conf_path = CONFIGS_DIR / f"{name}.conf"
    qr_path = CONFIGS_DIR / f"{name}.png"
    
    try:
        if conf_path.exists(): conf_path.unlink()
        if qr_path.exists(): qr_path.unlink()
    except Exception as e:
        print(f"Ошибка удаления файлов: {e}")