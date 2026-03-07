#!/bin/bash

# Автоматически вычисляем корень проекта и переходим туда
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || exit 1

echo "🔄 [Deploy] Start nuclear update in $APP_DIR..."

# 1. Остановка контейнеров
echo "🛑 [Deploy] Stopping containers..."
docker compose down

# 2. ЯДЕРНАЯ ЗАЧИСТКА (удаляет всё: образы, кэш, сеть)
echo "🧹 [Deploy] Pruning Docker system..."
docker system prune -a -f

# 3. Обновление кода (Специфическая логика)
echo "⬇️ [Deploy] Updating code..."

git fetch --all
git reset --hard origin/main

# 4. Выдача прав
echo "🔧 [Deploy] Restoring permissions..."
chmod +x install.sh
chmod +x scripts/*.sh

# 5. Сборка и запуск
echo "🏗️ [Deploy] Building and Starting..."
docker compose up -d --build

echo "✅ [Deploy] Finished successfully!"