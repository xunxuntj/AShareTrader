-- AShareTrader Multi-User Migration Script (Supabase PostgreSQL)
-- Safe for existing instances: PRESERVES market_bars, data_health_logs, daily_signals, backtest_runs.
-- Run this once in Supabase Dashboard -> SQL Editor.

-- 1. Lightweight user registry (PBKDF2-SHA256 salted password hashes)
CREATE TABLE IF NOT EXISTS public.app_users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user')) DEFAULT 'user',
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Rebuild user-isolated ledger tables (no prior user trades to migrate)
DROP TABLE IF EXISTS public.ledger_trades;
DROP TABLE IF EXISTS public.ledger_positions;
DROP TABLE IF EXISTS public.ledger_account;

CREATE TABLE public.ledger_account (
    user_id         TEXT PRIMARY KEY,
    initial_capital DOUBLE PRECISION NOT NULL DEFAULT 100000,
    cash            DOUBLE PRECISION NOT NULL DEFAULT 100000,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE public.ledger_positions (
    user_id         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    shares          INTEGER NOT NULL DEFAULT 0,
    avg_cost        DOUBLE PRECISION NOT NULL DEFAULT 0,
    highest_price   DOUBLE PRECISION NOT NULL DEFAULT 0,
    stop_loss_price DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, symbol)
);

CREATE TABLE public.ledger_trades (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,
    trade_date      DATE NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW')),
    shares          INTEGER NOT NULL DEFAULT 0,
    price           DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee             DOUBLE PRECISION NOT NULL DEFAULT 0,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_trades_user_date
    ON public.ledger_trades (user_id, trade_date DESC);

-- 3. Enable RLS on all new/rebuilt tables (service_role bypasses RLS)
ALTER TABLE public.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_trades ENABLE ROW LEVEL SECURITY;
