#!/bin/bash

# Автоматически вычисляем корень проекта и переходим туда
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || exit 1

echo "🔄 [Deploy] Start nuclear update in $APP_DIR..."

# 1. Остановка контейнеров
echo "🛑 [Deploy] Stopping containers..."
docker compose down

# 2. ЯДЕРНАЯ ЗАЧИСТКА (удаляет всё: образы, кэш, сеть)
echo "🧹[Deploy] Pruning Docker system..."
docker system prune -a -f

# 3. Обновление кода (Специфическая логика)
echo "⬇️ [Deploy] Updating code..."

# Чтение .env файла для использования GIT_TOKEN (для приватных репозиториев)
if [ -f "$APP_DIR/.env" ]; then
    set -a
    source "$APP_DIR/.env"
    set +a
fi

if [ -n "$GIT_TOKEN" ] && [ -n "$GIT_REPO" ]; then
    # Формируем URL с токеном
    PREFIX="https://"
    REPO_URL=${GIT_REPO#$PREFIX}
    if [ -n "$GIT_USERNAME" ]; then
        AUTH_URL="${PREFIX}${GIT_USERNAME}:${GIT_TOKEN}@${REPO_URL}"
    else
        AUTH_URL="${PREFIX}${GIT_TOKEN}@${REPO_URL}"
    fi
    git remote set-url origin "$AUTH_URL"
fi

git fetch --all

git reset --hard 

# ---> ФИКС ВЕРСИИ: Восстанавливаем оригинальный VERSION, чтобы бот не писал хэши <---
git checkout origin/main -- VERSION 2>/dev/null || git checkout origin/master -- VERSION 2>/dev/null || true

# Записываем новый хэш ТОЛЬКО в системный файл для сверки 
if [ -d ".git" ]; then
    git rev-parse HEAD | cut -c1-7 > "$APP_DIR/volumes/VERSION"
else
    echo "unknown" > "$APP_DIR/volumes/VERSION"
fi

# 4. Выдача прав
echo "🔧 [Deploy] Restoring permissions..."
chmod +x install.sh
chmod +x scripts/*.sh

# 5. Сборка и запуск
echo "🏗️ [Deploy] Building and Starting..."
docker compose up -d --build

echo "✅ [Deploy] Finished successfully!"

# 6. Перезапуск системного демона
# Запускаем отложенный рестарт в фоновом режиме.
# Это нужно, чтобы скрипт deploy.sh успел корректно завершиться 
# до того, как systemd "убьет" его родительский процесс.
echo "🔄 [Deploy] Restarting host daemon (vpn-updater) to apply new bash scripts..."
(sleep 2 && systemctl restart vpn-updater) &