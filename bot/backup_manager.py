import os
import subprocess
from pathlib import Path
import shutil
import aiohttp
from utils import get_moscow_now

# Пути (внутри контейнера)
BACKUP_DIR = Path("/volumes/backups")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Итоговый файл, который мы отдаем боту
BACKUP_FILE = BACKUP_DIR / "backup_latest.tar.gz"

# Параметры подключения (берем из ENV контейнера)
DB_URL = os.getenv("DATABASE_URL", "postgres://vpn:vpnpass@postgres:5432/vpndb")
WG_API_URL = "http://wireguard:8000/api"

def create_backup():
    """Создает полный бэкап системы."""
    timestamp = get_moscow_now().strftime("%Y%m%d%H%M%S")
    temp_backup = BACKUP_DIR / f"backup_{timestamp}.tar.gz"
    db_dump = BACKUP_DIR / "db_dump.sql"

    try:
        if db_dump.exists():
            db_dump.unlink()

        print("⏳ Dumping database...")
        subprocess.run(
            f"pg_dump --clean --if-exists -O -x '{DB_URL}' > {db_dump}", 
            shell=True, 
            check=True
        )

        print("⏳ Archiving files...")
        cmd = f"tar -czf {temp_backup} -C /volumes wireguard configs backups/db_dump.sql"
        subprocess.run(cmd, shell=True, check=True)

        if BACKUP_FILE.exists():
            BACKUP_FILE.unlink()
        
        shutil.copy(temp_backup, BACKUP_FILE)
        
        db_dump.unlink()
        temp_backup.unlink()

        return str(BACKUP_FILE)

    except Exception as e:
        print(f"❌ Backup failed: {e}")
        if db_dump.exists(): db_dump.unlink()
        if temp_backup.exists(): temp_backup.unlink()
        raise e

async def restore_backup(path: str):
    """Восстанавливает систему из архива."""
    print(f"♻️ Restoring from {path}...")
    
    subprocess.run(f"tar -xzf {path} -C /volumes/ --overwrite", shell=True, check=True)

    db_dump = BACKUP_DIR / "db_dump.sql"
    if db_dump.exists():
        print("♻️ Restoring Database...")
        subprocess.run(f"psql '{DB_URL}' < {db_dump}", shell=True, check=True)
        db_dump.unlink()
    else:
        print("⚠️ Warning: db_dump.sql not found in backup archive.")

    print("♻️ Reloading WireGuard Interface...")
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{WG_API_URL}/reload") as resp:
            if resp.status == 200:
                print("✅ WireGuard reloaded successfully.")
            else:
                print(f"❌ WireGuard reload failed: {await resp.text()}")