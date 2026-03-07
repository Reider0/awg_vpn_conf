#!/bin/bash

# 1. Проверка прав root
if [ "$EUID" -ne 0 ]; then
  echo "❌ Запустите с правами root: sudo bash install.sh"
  exit 1
fi

APP_DIR=$(pwd)
echo "🚀 Установка VPN Dashboard в: $APP_DIR"

# 2. Установка Docker
echo "📦 Проверка Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
else
    echo "✅ Docker уже установлен."
fi

# Установка плагина (на всякий случай)
apt-get update && apt-get install -y docker-compose-plugin git

# 3. Настройка прав и папок
echo "🔧 Настройка прав..."
chmod +x "$APP_DIR/install.sh"
chmod +x "$APP_DIR/scripts/"*.sh
mkdir -p "$APP_DIR/volumes/flags"
mkdir -p "$APP_DIR/volumes/backups"
mkdir -p "$APP_DIR/volumes/configs"
mkdir -p "$APP_DIR/volumes/wireguard"
mkdir -p "$APP_DIR/volumes/database"

# --- ФИКС ВЕРСИИ ---
# Сразу записываем текущую версию Git в файл, чтобы бот её видел
if [ -d ".git" ]; then
    echo "📝 Записываем текущую версию проекта..."
    git rev-parse --short HEAD > "$APP_DIR/volumes/VERSION"
else
    echo "⚠️ Git репозиторий не найден, версия будет 'unknown'"
    echo "unknown" > "$APP_DIR/volumes/VERSION"
fi

# 4. Демон автообновлений
echo "⚙️ Установка демона обновлений..."
cat <<EOF > /etc/systemd/system/vpn-updater.service
[Unit]
Description=VPN Dashboard Auto-Updater Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=/bin/bash $APP_DIR/scripts/host_updater.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vpn-updater
systemctl restart vpn-updater

# 5. Генерация .env
if [ ! -f "$APP_DIR/.env" ]; then
    echo "📝 Создаю .env..."
    cat <<EOF > "$APP_DIR/.env"
BOT_TOKEN=
ADMIN_ID=
# Для приватных репозиториев используйте формат:
# https://USER:TOKEN@github.com/USER/REPO.git
GIT_REPO=
EOF
    echo "⚠️ Файл .env создан. Заполните его!"
fi

echo "✅ Установка завершена!"
echo "👉 Заполните .env и запустите: docker compose up -d --build"