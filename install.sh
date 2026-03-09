#!/bin/bash

# 1. Проверка прав root
if [ "$EUID" -ne 0 ]; then
  echo "❌ Запустите с правами root: sudo bash install.sh"
  exit 1
fi

APP_DIR=$(pwd)
echo "🚀 Установка VPN Dashboard в: $APP_DIR"

# 2. Установка Docker
echo "📦 Проверка и настройка Docker..."

# Если докера нет - ставим
if ! command -v docker &> /dev/null; then
    echo "⬇️ Скачивание Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

# Установка плагина Compose
apt-get update && apt-get install -y docker-compose-plugin git

# ВАЖНО: Включаем автозапуск Docker при старте системы
echo "🔌 Включение автозагрузки Docker..."
systemctl enable docker
systemctl start docker

# 3. Настройка прав и папок
echo "🔧 Настройка прав..."
chmod +x "$APP_DIR/install.sh"
chmod +x "$APP_DIR/scripts/"*.sh
mkdir -p "$APP_DIR/volumes/flags"
mkdir -p "$APP_DIR/volumes/backups"
mkdir -p "$APP_DIR/volumes/configs"
mkdir -p "$APP_DIR/volumes/wireguard"
mkdir -p "$APP_DIR/volumes/database"
# shared_bin больше не создаем

# Фикс версии (Строго 7 символов)
if [ -d ".git" ]; then
    git rev-parse HEAD | cut -c1-7 > "$APP_DIR/volumes/VERSION"
else
    echo "unknown" > "$APP_DIR/volumes/VERSION"
fi

# 4. Настройка системного демона (Auto-Updater)
echo "⚙️ Настройка демона автообновлений..."
cat <<EOF > /etc/systemd/system/vpn-updater.service
[Unit]
Description=VPN Dashboard Auto-Updater Daemon
# Ждем, пока Docker полностью загрузится
After=network-online.target docker.service
Wants=network-online.target docker.service

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

# 5. Генерация .env (если нет)
if [ ! -f "$APP_DIR/.env" ]; then
    echo "📝 Создаю пустой .env..."
    touch "$APP_DIR/.env"
fi

# 6. Финальный запуск
echo "🚀 Запуск контейнеров..."
docker compose up -d --build

echo "✅ УСТАНОВКА ЗАВЕРШЕНА!"
echo "Docker настроен на автостарт. Бот запущен."