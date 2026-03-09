#!/bin/bash

# Определяем папку, где лежит скрипт, и поднимаемся на уровень выше (в корень проекта)
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Пути к файлам-флагам
UPDATE_FLAG="$APP_DIR/volumes/flags/do_update"
REBOOT_FLAG="$APP_DIR/volumes/flags/do_reboot"
AUDIT_FLAG="$APP_DIR/volumes/flags/do_audit"

echo "Daemon started. Watching: $APP_DIR/volumes/flags"

while true; do
    # 1. ОБНОВЛЕНИЕ (UPDATE)
    if [ -f "$UPDATE_FLAG" ]; then
        echo "[Updater] Update flag detected!"
        rm -f "$UPDATE_FLAG"
        bash "$APP_DIR/scripts/deploy.sh"
        echo "[Updater] Update sequence finished."
    fi

    # 2. ПЕРЕЗАГРУЗКА СЕРВЕРА (REBOOT)
    if [ -f "$REBOOT_FLAG" ]; then
        echo "[Updater] Reboot flag detected! Rebooting system..."
        rm -f "$REBOOT_FLAG"
        /usr/sbin/reboot
    fi

    # 3. ГЛОБАЛЬНЫЙ АУДИТ ХОСТА (AUDIT)
    if [ -f "$AUDIT_FLAG" ]; then
        echo "[Updater] Audit flag detected! Running global host audit..."
        rm -f "$AUDIT_FLAG"
        bash "$APP_DIR/scripts/host_audit.sh"
        echo "[Updater] Audit sequence finished."
    fi

    sleep 3
done