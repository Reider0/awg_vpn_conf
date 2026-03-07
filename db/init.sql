-- Сначала удаляем старые таблицы, если они есть (чтобы избежать конфликтов при пересоздании)
DROP TABLE IF EXISTS stats;
DROP TABLE IF EXISTS devices;
DROP TABLE IF EXISTS users;

-- 1. Таблица пользователей (ОСНОВНАЯ ДЛЯ БОТА)
-- Бот ищет UUID и Device именно здесь
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    uuid TEXT NOT NULL UNIQUE,     -- Вот эта колонка, которой не хватало!
    device TEXT,                   -- Сюда запишется hostname устройства
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    first_connected_at TIMESTAMP
);

-- 2. Таблица статистики (на будущее)
-- Пока бот берет статистику напрямую через API WireGuard, но пусть таблица будет
CREATE TABLE stats (
    id SERIAL PRIMARY KEY,
    user_uuid TEXT REFERENCES users(uuid) ON DELETE CASCADE,
    bytes_in BIGINT DEFAULT 0,
    bytes_out BIGINT DEFAULT 0,
    last_seen TIMESTAMP
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);