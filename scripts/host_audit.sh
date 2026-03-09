#!/bin/bash

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLAG_DIR="$APP_DIR/volumes/flags"
REPORT_FILE="$FLAG_DIR/audit_report.json"
STATUS_FILE="$FLAG_DIR/audit_status"

mkdir -p "$FLAG_DIR"
rm -f "$REPORT_FILE"

# Утилита для очистки строк от спецсимволов для валидного JSON
clean() {
    echo "$1" | sed 's/"/\\"/g' | tr -d '\n' | tr -d '\r'
}

# Массивы для хранения JSON-объектов по категориям
CAT_NET=""
CAT_HOST=""
CAT_DOCKER=""
CAT_VPN=""
CAT_STORAGE=""
CAT_SEC=""
CAT_LOGS=""

add_check() {
    local cat_var=$1
    local name=$(clean "$2")
    local status=$(clean "$3")
    local msg=$(clean "$4")
    local json_str="{\"name\":\"$name\",\"status\":\"$status\",\"msg\":\"$msg\"},"
    eval "$cat_var=\"\${$cat_var}$json_str\""
}

# ==========================================
# СТАДИЯ 1: СЕТЬ И ИНТЕРНЕТ (7 тестов)
# ==========================================
echo "network" > "$STATUS_FILE"

ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1[ $? -eq 0 ] && add_check CAT_NET "Ping Google DNS (8.8.8.8)" "ok" "Доступно" || add_check CAT_NET "Ping Google DNS (8.8.8.8)" "error" "Таймаут"

ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_NET "Ping Cloudflare (1.1.1.1)" "ok" "Доступно" || add_check CAT_NET "Ping Cloudflare (1.1.1.1)" "warning" "Таймаут"

ping -c 1 -W 2 google.com >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_NET "DNS Разрешение имен" "ok" "Работает" || add_check CAT_NET "DNS Разрешение имен" "error" "Сбой DNS"

curl -s -m 3 https://api.telegram.org >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_NET "Доступность Telegram API" "ok" "Связь есть" || add_check CAT_NET "Доступность Telegram API" "error" "Заблокировано или недоступно"

curl -s -m 3 https://github.com >/dev/null 2>&1[ $? -eq 0 ] && add_check CAT_NET "Доступность GitHub" "ok" "Связь есть" || add_check CAT_NET "Доступность GitHub" "warning" "Недоступен (обновления не сработают)"

FWD=$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo "0")
[ "$FWD" = "1" ] && add_check CAT_NET "IPv4 Forwarding (Маршрутизация)" "ok" "Включено" || add_check CAT_NET "IPv4 Forwarding (Маршрутизация)" "error" "Выключено (VPN без интернета)"

GW=$(ip route show default | grep -oP 'via \K\S+')
[ -n "$GW" ] && add_check CAT_NET "Шлюз по умолчанию" "ok" "$GW" || add_check CAT_NET "Шлюз по умолчанию" "error" "Не найден"

sleep 1

# ==========================================
# СТАДИЯ 2: РЕСУРСЫ СЕРВЕРА (8 тестов)
# ==========================================
echo "host" > "$STATUS_FILE"

CPU_IDLE=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/")
CPU_USE=$(awk "BEGIN {print 100 - ${CPU_IDLE:-50}}")[ $(awk "BEGIN {print ($CPU_USE < 90)}") -eq 1 ] && add_check CAT_HOST "Загрузка CPU" "ok" "${CPU_USE}%" || add_check CAT_HOST "Загрузка CPU" "warning" "Высокая: ${CPU_USE}%"

LOAD=$(cat /proc/loadavg | awk '{print $1}')
add_check CAT_HOST "Load Average (1m)" "ok" "$LOAD"

RAM_USE=$(free -m | awk 'NR==2{printf "%d", $3*100/$2 }')
[ "$RAM_USE" -lt 95 ] && add_check CAT_HOST "Оперативная память (RAM)" "ok" "${RAM_USE}% занято" || add_check CAT_HOST "Оперативная память (RAM)" "error" "Критично: ${RAM_USE}%"

SWAP=$(free -m | awk 'NR==3{if($2>0) printf "%d", $3*100/$2; else print "0"}')
add_check CAT_HOST "Файл подкачки (Swap)" "ok" "${SWAP}% занято"

ZOMBIES=$(top -bn1 | grep "zombie" | awk '{print $10}')[ "${ZOMBIES:-0}" -eq 0 ] && add_check CAT_HOST "Зомби-процессы" "ok" "0" || add_check CAT_HOST "Зомби-процессы" "warning" "Найдено: $ZOMBIES"

UPTIME=$(awk '{print int($1/86400)"d "int(($1%86400)/3600)"h"}' /proc/uptime)
add_check CAT_HOST "Аптайм сервера" "ok" "$UPTIME"

TIMEDATE=$(timedatectl show 2>/dev/null | grep NTPSynchronized | cut -d= -f2)
[ "$TIMEDATE" = "yes" ] && add_check CAT_HOST "Синхронизация времени (NTP)" "ok" "Включена" || add_check CAT_HOST "Синхронизация времени (NTP)" "warning" "Не синхронизировано"

KERNEL=$(uname -r)
add_check CAT_HOST "Версия Ядра Linux" "ok" "$KERNEL"

sleep 1

# ==========================================
# СТАДИЯ 3: DOCKER И КОНТЕЙНЕРЫ (8 тестов)
# ==========================================
echo "docker" > "$STATUS_FILE"

systemctl is-active --quiet docker[ $? -eq 0 ] && add_check CAT_DOCKER "Служба Docker Daemon" "ok" "Active" || add_check CAT_DOCKER "Служба Docker Daemon" "error" "Остановлен"

docker compose version >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_DOCKER "Плагин Docker Compose" "ok" "Установлен" || add_check CAT_DOCKER "Плагин Docker Compose" "error" "Не найден"

check_cont() {
    local stat=$(docker inspect -f '{{.State.Status}}' $1 2>/dev/null || echo "missing")
    [ "$stat" = "running" ] && add_check CAT_DOCKER "Контейнер $1" "ok" "Running" || add_check CAT_DOCKER "Контейнер $1" "error" "$stat"
}
check_cont "vpn_bot"
check_cont "vpn_wireguard"
check_cont "vpn_db"

NET_EXISTS=$(docker network ls | grep vpn)
[ -n "$NET_EXISTS" ] && add_check CAT_DOCKER "Изолированная сеть Docker" "ok" "Существует" || add_check CAT_DOCKER "Изолированная сеть Docker" "error" "Не найдена"

D_SPACE=$(docker system df --format '{{.Size}}' | head -n 1)
add_check CAT_DOCKER "Объем данных Docker" "ok" "$D_SPACE"

API_PORT=$(ss -tuln 2>/dev/null | grep -q ":8000 "; echo $?)
[ $API_PORT -ne 0 ] && add_check CAT_DOCKER "Порты внутри моста" "ok" "Закрыты снаружи" || add_check CAT_DOCKER "Порты внутри моста" "warning" "API торчит наружу!"

sleep 1

# ==========================================
# СТАДИЯ 4: VPN И WIREGUARD (8 тестов)
# ==========================================
echo "vpn" > "$STATUS_FILE"

WG_PORT=$(ss -uln 2>/dev/null | grep ":51820")[ -n "$WG_PORT" ] && add_check CAT_VPN "Прослушивание UDP 51820" "ok" "Открыт" || add_check CAT_VPN "Прослушивание UDP 51820" "error" "Порт закрыт/Не слушается"

docker exec vpn_wireguard ip link show wg0 >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_VPN "Сетевой интерфейс wg0" "ok" "Поднят" || add_check CAT_VPN "Сетевой интерфейс wg0" "error" "Не найден в контейнере"

CONF_FILE="/volumes/wireguard/wg0.conf"[ -f "$APP_DIR$CONF_FILE" ] && add_check CAT_VPN "Конфиг wg0.conf" "ok" "Существует" || add_check CAT_VPN "Конфиг wg0.conf" "error" "Отсутствует"

[ -f "$APP_DIR/volumes/wireguard/public.key" ] && add_check CAT_VPN "Ключи шифрования (Server)" "ok" "Существуют" || add_check CAT_VPN "Ключи шифрования (Server)" "error" "Отсутствуют"

OBFUSCATION=$(grep -E "Jc|Jmin|Jmax" "$APP_DIR$CONF_FILE" 2>/dev/null)
[ -n "$OBFUSCATION" ] && add_check CAT_VPN "Обфускация AmneziaWG" "ok" "Активна (Анти-DPI)" || add_check CAT_VPN "Обфускация AmneziaWG" "warning" "Параметры не найдены"

MASQ=$(docker exec vpn_wireguard iptables -t nat -S | grep MASQUERADE)
[ -n "$MASQ" ] && add_check CAT_VPN "NAT Masquerade (Трафик)" "ok" "Правило настроено" || add_check CAT_VPN "NAT Masquerade (Трафик)" "error" "Правило отсутствует"

WG_DUMP=$(docker exec vpn_wireguard wg show wg0 dump 2>/dev/null | wc -l)
[ "$WG_DUMP" -ge 1 ] && add_check CAT_VPN "Ответ ядра WireGuard" "ok" "Успешно" || add_check CAT_VPN "Ответ ядра WireGuard" "error" "Ядро не отвечает"

TUN=$(ls /dev/net/tun 2>/dev/null)
[ -n "$TUN" ] && add_check CAT_VPN "Модуль TUN/TAP" "ok" "Доступен" || add_check CAT_VPN "Модуль TUN/TAP" "error" "Не найден"

sleep 1

# ==========================================
# СТАДИЯ 5: ХРАНИЛИЩЕ И БД (7 тестов)
# ==========================================
echo "storage" > "$STATUS_FILE"

ROOT_DISK=$(df -h / | awk 'NR==2 {print $5}' | tr -d '%')[ "$ROOT_DISK" -lt 95 ] && add_check CAT_STORAGE "Свободное место на диске (/)" "ok" "${ROOT_DISK}% занято" || add_check CAT_STORAGE "Свободное место на диске (/)" "error" "Критично: ${ROOT_DISK}%"

INODES=$(df -i / | awk 'NR==2 {print $5}' | tr -d '%')[ "$INODES" -lt 95 ] && add_check CAT_STORAGE "Индексные дескрипторы (Inodes)" "ok" "${INODES}% занято" || add_check CAT_STORAGE "Индексные дескрипторы (Inodes)" "error" "Заканчиваются: ${INODES}%"

[ -d "$APP_DIR/volumes/database" ] && add_check CAT_STORAGE "Директория БД (/database)" "ok" "Смонтирована" || add_check CAT_STORAGE "Директория БД (/database)" "error" "Отсутствует"

docker exec vpn_db pg_isready -U vpn >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_STORAGE "Соединение с PostgreSQL" "ok" "Принимает запросы" || add_check CAT_STORAGE "Соединение с PostgreSQL" "error" "Отказ в обслуживании"

[ -d "$APP_DIR/volumes/backups" ] && add_check CAT_STORAGE "Директория резервных копий" "ok" "Существует" || add_check CAT_STORAGE "Директория резервных копий" "warning" "Отсутствует"

BACKUP_FILE="$APP_DIR/volumes/backups/backup_latest.tar.gz"
if[ -f "$BACKUP_FILE" ]; then
    AGE=$(find "$BACKUP_FILE" -mtime -2)
    [ -n "$AGE" ] && add_check CAT_STORAGE "Актуальность Бэкапа" "ok" "Свежий (< 48ч)" || add_check CAT_STORAGE "Актуальность Бэкапа" "warning" "Устарел (> 48ч)"
else
    add_check CAT_STORAGE "Актуальность Бэкапа" "warning" "Бэкап не найден"
fi

[ -d "$APP_DIR/volumes/configs" ] && add_check CAT_STORAGE "Хранилище конфигов клиентов" "ok" "Доступно" || add_check CAT_STORAGE "Хранилище конфигов клиентов" "error" "Удалено"

sleep 1

# ==========================================
# СТАДИЯ 6: БЕЗОПАСНОСТЬ (5 тестов)
# ==========================================
echo "security" > "$STATUS_FILE"

ROOT_SSH=$(grep "^PermitRootLogin yes" /etc/ssh/sshd_config)[ -n "$ROOT_SSH" ] && add_check CAT_SEC "SSH Root Login" "warning" "Разрешен (Рекомендуется отключить)" || add_check CAT_SEC "SSH Root Login" "ok" "Защищен"

UFW_STAT=$(ufw status 2>/dev/null | grep -i "active")
IPT_STAT=$(iptables -L -n | grep "Chain INPUT" | wc -l)
if[ -n "$UFW_STAT" ]; then
    add_check CAT_SEC "Межсетевой экран (Firewall)" "ok" "UFW Активен"
elif[ "$IPT_STAT" -gt 0 ]; then
    add_check CAT_SEC "Межсетевой экран (Firewall)" "ok" "Iptables настроен"
else
    add_check CAT_SEC "Межсетевой экран (Firewall)" "warning" "Не обнаружен"
fi

EMPTY_PW=$(awk -F: '($2 == "") {print $1}' /etc/shadow 2>/dev/null)[ -z "$EMPTY_PW" ] && add_check CAT_SEC "Пустые пароли пользователей" "ok" "Не обнаружены" || add_check CAT_SEC "Пустые пароли пользователей" "error" "ОПАСНОСТЬ: Есть аккаунты без пароля"

visudo -c >/dev/null 2>&1
[ $? -eq 0 ] && add_check CAT_SEC "Синтаксис Sudoers" "ok" "Корректен" || add_check CAT_SEC "Синтаксис Sudoers" "error" "Сломан файл sudoers!"

FAILED_LOGINS=$(grep "Failed password" /var/log/auth.log 2>/dev/null | wc -l)
[ "${FAILED_LOGINS:-0}" -gt 50 ] && add_check CAT_SEC "Брутфорс атаки (SSH)" "warning" "$FAILED_LOGINS попыток" || add_check CAT_SEC "Брутфорс атаки (SSH)" "ok" "В норме"

sleep 1

# ==========================================
# СТАДИЯ 7: СИСТЕМНЫЕ СЛУЖБЫ И ЛОГИ (7 тестов)
# ==========================================
echo "services" > "$STATUS_FILE"

systemctl is-active --quiet vpn-updater[ $? -eq 0 ] && add_check CAT_LOGS "Демон vpn-updater" "ok" "Active" || add_check CAT_LOGS "Демон vpn-updater" "error" "Остановлен"

FAILED_UNITS=$(systemctl list-units --state=failed --no-legend | wc -l)
[ "$FAILED_UNITS" -eq 0 ] && add_check CAT_LOGS "Упавшие службы Linux" "ok" "0" || add_check CAT_LOGS "Упавшие службы Linux" "warning" "Найдено: $FAILED_UNITS"

OOM=$(dmesg 2>/dev/null | grep -i "killed process" | wc -l)[ "$OOM" -eq 0 ] && add_check CAT_LOGS "Убийства процессов по памяти (OOM)" "ok" "Не зафиксировано" || add_check CAT_LOGS "Убийства процессов по памяти (OOM)" "warning" "Были утечки памяти"

# Чтение логов контейнеров
check_logs() {
    local errs=$(docker logs --tail 150 $1 2>&1 | grep -iE "error|fatal|exception|traceback" | grep -vi "Task was destroyed" | tail -n 1)
    if [ -n "$errs" ]; then
        local cln=$(clean "$errs")
        add_check CAT_LOGS "Логи контейнера $1" "warning" "${cln:0:50}..."
    else
        add_check CAT_LOGS "Логи контейнера $1" "ok" "Чисто"
    fi
}
check_logs "vpn_bot"
check_logs "vpn_wireguard"
check_logs "vpn_db"

UPDATES=$(apt-get -s upgrade 2>/dev/null | grep -Po "^Inst \K[^ ]+" | wc -l)
[ "${UPDATES:-0}" -eq 0 ] && add_check CAT_LOGS "Системные обновления ОС" "ok" "Все установлено" || add_check CAT_LOGS "Системные обновления ОС" "warning" "Доступно $UPDATES пакетов"

sleep 1

# ==========================================
# СБОРКА ИТОГОВОГО JSON
# ==========================================
echo "done" > "$STATUS_FILE"

# Удаляем последнюю запятую в массивах
CAT_NET="[${CAT_NET%,}]"
CAT_HOST="[${CAT_HOST%,}]"
CAT_DOCKER="[${CAT_DOCKER%,}]"
CAT_VPN="[${CAT_VPN%,}]"
CAT_STORAGE="[${CAT_STORAGE%,}]"
CAT_SEC="[${CAT_SEC%,}]"
CAT_LOGS="[${CAT_LOGS%,}]"

cat <<EOF > "$REPORT_FILE"
{
  "network": $CAT_NET,
  "host": $CAT_HOST,
  "docker": $CAT_DOCKER,
  "vpn": $CAT_VPN,
  "storage": $CAT_STORAGE,
  "security": $CAT_SEC,
  "services": $CAT_LOGS
}
EOF