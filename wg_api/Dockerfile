# Используем официальный образ AmneziaWG
FROM amneziavpn/amnezia-wg:latest

# Устанавливаем Python и зависимости (так как база Alpine)
RUN apk add --no-cache \
    python3 \
    py3-pip \
    iproute2 \
    iptables \
    curl \
    bash \
    procps \
    iputils

# Создаем виртуальное окружение
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir fastapi uvicorn pydantic

# Копируем скрипты
COPY run_api.sh /run_api.sh
RUN chmod +x /run_api.sh

COPY api.py /app/api.py
WORKDIR /app

# Переопределяем Entrypoint, чтобы запускать наш скрипт
ENTRYPOINT ["/run_api.sh"]