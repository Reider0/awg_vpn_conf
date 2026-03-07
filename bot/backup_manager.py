import os
import subprocess
from pathlib import Path
import datetime
import shutil
import aiohttp

BACKUP_DIR = Path("/volumes/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

BACKUP_FILE = BACKUP_DIR / "backup_latest.tar.gz"
DB_URL = os.getenv("DATABASE_URL", "postgres://vpn:vpnpass@postgres:5432/vpndb")
WG_API_URL = "http://wireguard:8000/api"

def create_backup():
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    temp_backup = BACKUP_DIR / f"backup_{timestamp}.tar.gz"
    db_dump = BACKUP_DIR / "db_dump.sql"

    # 1. Безопасный дамп базы (флаг --clean позволит при ресторе пересоздать таблицы)
    subprocess.run(f"pg_dump --clean --if-exists -O -x '{DB_URL}' > {db_dump}", shell=True, check=True)

    # 2. Архивируем папки целиком (с сохранением структуры)
    cmd = f"tar -czf {temp_backup} -C /volumes wireguard configs backups/db_dump.sql"
    subprocess.run(cmd, shell=True, check=True)

    if BACKUP_FILE.exists():
        BACKUP_FILE.unlink()
    
    shutil.copy(temp_backup, BACKUP_FILE)
    db_dump.unlink() # убираем временный sql

    return str(BACKUP_FILE)

async def restore_backup(path: str):
    # 1. Распаковываем конфиги и ключи на свои места
    cmd = f"tar -xzf {path} -C /volumes/"
    subprocess.run(cmd, shell=True, check=True)

    # 2. Заливаем SQL-дамп обратно в базу
    db_dump = BACKUP_DIR / "db_dump.sql"
    if db_dump.exists():
        subprocess.run(f"psql '{DB_URL}' < {db_dump}", shell=True, check=True)
        db_dump.unlink()

    # 3. Дергаем API WireGuard, чтобы он "на лету" подхватил восстановленные ключи!
    async with aiohttp.ClientSession() as session:
        await session.post(f"{WG_API_URL}/reload")