FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        git \
        iputils-ping \
        postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Настройка Git:
# 1. safe.directory чтобы не ругался на права
# 2. credential.helper пустой, чтобы не искал кэш
RUN git config --global --add safe.directory '*' && \
    git config --global credential.helper ""

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]