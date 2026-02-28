-- ============================================================
-- INSECT BOT — Supabase initialization script
-- Run this in Supabase SQL Editor
-- WARNING: drops all existing tables and recreates them
-- ============================================================

-- Drop tables in reverse dependency order
DROP TABLE IF EXISTS favorites CASCADE;
DROP TABLE IF EXISTS requests CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS settings CASCADE;
DROP TABLE IF EXISTS api_keys CASCADE;

-- ============================================================
-- TABLE: users
-- ============================================================
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    is_banned BOOLEAN DEFAULT FALSE,
    daily_limit INTEGER DEFAULT 20,
    requests_today INTEGER DEFAULT 0,
    limit_reset_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_telegram_id ON users(telegram_id);
CREATE INDEX idx_users_last_active ON users(last_active_at);

-- ============================================================
-- TABLE: requests
-- ============================================================
CREATE TABLE requests (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    taxon_name TEXT,
    taxon_common_name TEXT,
    taxon_id INTEGER,
    score FLOAT,
    groq_response TEXT,
    image_size_before INTEGER,   -- bytes
    image_size_after INTEGER,    -- bytes
    response_time_ms INTEGER,
    success BOOLEAN DEFAULT TRUE,
    error_text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_requests_telegram_id ON requests(telegram_id);
CREATE INDEX idx_requests_created_at ON requests(created_at);
CREATE INDEX idx_requests_taxon_name ON requests(taxon_name);

-- ============================================================
-- TABLE: favorites
-- ============================================================
CREATE TABLE favorites (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    taxon_id INTEGER,
    taxon_name TEXT NOT NULL,
    taxon_common_name TEXT,
    taxon_rank TEXT,
    wikipedia_url TEXT,
    photo_url TEXT,
    note TEXT,
    added_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_favorites_telegram_id ON favorites(telegram_id);
CREATE UNIQUE INDEX idx_favorites_unique ON favorites(telegram_id, taxon_name);

-- ============================================================
-- TABLE: settings
-- ============================================================
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO settings (key, value, description) VALUES
    ('score_threshold', '0.40', 'Minimum iNaturalist score to show a confident answer'),
    ('daily_limit_default', '20', 'Default daily request limit per user'),
    ('resize_max_kb', '500', 'Max image size in KB before sending to APIs'),
    ('bot_active', 'true', 'Global bot on/off switch'),
    ('groq_model', 'meta-llama/llama-4-scout-17b-16e-instruct', 'Groq model to use for vision');

-- ============================================================
-- TABLE: api_keys
-- ============================================================
CREATE TABLE api_keys (
    id BIGSERIAL PRIMARY KEY,
    service TEXT NOT NULL,          -- 'inaturalist' | 'groq'
    key_value TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    requests_today INTEGER DEFAULT 0,
    daily_limit INTEGER DEFAULT 10000,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_api_keys_service ON api_keys(service, is_active);

-- ============================================================
-- FUNCTION: reset daily counters (call via pg_cron or manually)
-- ============================================================
CREATE OR REPLACE FUNCTION reset_daily_counters()
RETURNS void AS $$
BEGIN
    UPDATE users SET requests_today = 0, limit_reset_at = NOW();
    UPDATE api_keys SET requests_today = 0;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- FUNCTION: get or create user
-- ============================================================
CREATE OR REPLACE FUNCTION upsert_user(
    p_telegram_id BIGINT,
    p_username TEXT,
    p_first_name TEXT,
    p_last_name TEXT
) RETURNS users AS $$
DECLARE
    result users;
BEGIN
    INSERT INTO users (telegram_id, username, first_name, last_name)
    VALUES (p_telegram_id, p_username, p_first_name, p_last_name)
    ON CONFLICT (telegram_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            last_active_at = NOW()
    RETURNING * INTO result;
    RETURN result;
END;
$$ LANGUAGE plpgsql;
