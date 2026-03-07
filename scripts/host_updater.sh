#!/bin/bash

# Автоматически вычисляем корень проекта
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLAG_FILE="$APP_DIR/volumes/flags/do_update"

echo "Host Updater Daemon started. Watching for $FLAG_FILE..."

while true; do
    if [ -f "$FLAG_FILE" ]; then
        echo "Update flag detected! Running deploy..."
        rm -f "$FLAG_FILE"
        bash "$APP_DIR/scripts/deploy.sh"
        echo "Update finished. Continuing to watch..."
    fi
    sleep 3
done