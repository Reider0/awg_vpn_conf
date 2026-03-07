#!/bin/bash

# Включаем форвардинг
sysctl -w net.ipv4.ip_forward=1

echo "🚀 Starting AmneziaWG (Obfuscated VPN)..."

# Запускаем API
# Флаг -u для логов без буферизации
exec /opt/venv/bin/python3 -u -m uvicorn api:app --host 0.0.0.0 --port 8000 --app-dir /app