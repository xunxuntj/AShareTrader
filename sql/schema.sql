-- AShareTrader Supabase PostgreSQL Full Schema (Multi-User Ready)
-- Run this script once on a fresh Supabase project in SQL Editor.

-- 1. Daily adjusted market bars (shared across all users)
CREATE TABLE IF NOT EXISTS public.market_bars (
    symbol          TEXT NOT NULL,
    trade_date      DATE NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    volume          DOUBLE PRECISION NOT NULL DEFAULT 0,
    amount          DOUBLE PRECISION NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'akshare',
    is_final        BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_market_bars_date ON public.market_bars (trade_date DESC);

-- 2. Data source health monitoring logs (shared)
CREATE TABLE IF NOT EXISTS public.data_health_logs (
    id              BIGSERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    last_trade_date DATE,
    source_used     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'degraded', 'failed')),
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    error_msg       TEXT
);

CREATE INDEX IF NOT EXISTS idx_data_health_run_at ON public.data_health_logs (run_at DESC);

-- 3. Daily strategy target weights & trading signals (shared model baseline)
CREATE TABLE IF NOT EXISTS public.daily_signals (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    run_mode        TEXT NOT NULL CHECK (run_mode IN ('intraday', 'eod')),
    strategy_name   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('BUY', 'SELL', 'HOLD')),
    target_weight   DOUBLE PRECISION NOT NULL DEFAULT 0,
    ref_price       DOUBLE PRECISION,
    stop_loss_price DOUBLE PRECISION,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_daily_signals_date ON public.daily_signals (signal_date DESC, strategy_name);

-- 4. Application users (~10-member lightweight team management)
CREATE TABLE IF NOT EXISTS public.app_users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user')) DEFAULT 'user',
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Per-user account cash & initial capital state
CREATE TABLE IF NOT EXISTS public.ledger_account (
    user_id         TEXT PRIMARY KEY,
    initial_capital DOUBLE PRECISION NOT NULL DEFAULT 100000,
    cash            DOUBLE PRECISION NOT NULL DEFAULT 100000,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Per-user current actual holdings and dynamic trailing stop tracker
CREATE TABLE IF NOT EXISTS public.ledger_positions (
    user_id         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    shares          INTEGER NOT NULL DEFAULT 0,
    avg_cost        DOUBLE PRECISION NOT NULL DEFAULT 0,
    highest_price   DOUBLE PRECISION NOT NULL DEFAULT 0,
    stop_loss_price DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, symbol)
);

-- 7. Per-user confirmed trade & cashflow history
CREATE TABLE IF NOT EXISTS public.ledger_trades (
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

CREATE INDEX IF NOT EXISTS idx_ledger_trades_user_date ON public.ledger_trades (user_id, trade_date DESC);

-- 8. Latest multi-strategy backtest snapshots (shared)
CREATE TABLE IF NOT EXISTS public.backtest_runs (
    id                BIGSERIAL PRIMARY KEY,
    run_date          DATE NOT NULL,
    strategy_name     TEXT NOT NULL,
    params_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    equity_curve_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_date ON public.backtest_runs (run_date DESC, strategy_name);

-- Security: Enable Row Level Security (RLS) on all tables.
ALTER TABLE public.market_bars ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.data_health_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.backtest_runs ENABLE ROW LEVEL SECURITY;
