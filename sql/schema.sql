-- AShareTrader Supabase PostgreSQL Schema
-- Run this script once in Supabase Dashboard -> SQL Editor.

-- 1. Daily adjusted market bars (incremental upsert with intraday/EOD flag)
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

-- 2. Data source health monitoring logs
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

-- 3. Daily strategy target weights & trading signals
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

-- 4. Account cash & initial capital state
CREATE TABLE IF NOT EXISTS public.ledger_account (
    account_id      TEXT PRIMARY KEY DEFAULT 'default',
    initial_capital DOUBLE PRECISION NOT NULL DEFAULT 100000,
    cash            DOUBLE PRECISION NOT NULL DEFAULT 100000,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO public.ledger_account (account_id, initial_capital, cash)
VALUES ('default', 100000, 100000)
ON CONFLICT (account_id) DO NOTHING;

-- 5. Current actual holdings and dynamic trailing stop tracker
CREATE TABLE IF NOT EXISTS public.ledger_positions (
    symbol          TEXT PRIMARY KEY,
    shares          INTEGER NOT NULL DEFAULT 0,
    avg_cost        DOUBLE PRECISION NOT NULL DEFAULT 0,
    highest_price   DOUBLE PRECISION NOT NULL DEFAULT 0,
    stop_loss_price DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. User confirmed trade & cashflow history
CREATE TABLE IF NOT EXISTS public.ledger_trades (
    id              BIGSERIAL PRIMARY KEY,
    trade_date      DATE NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW')),
    shares          INTEGER NOT NULL DEFAULT 0,
    price           DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee             DOUBLE PRECISION NOT NULL DEFAULT 0,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_trades_date ON public.ledger_trades (trade_date DESC);

-- 7. Latest multi-strategy backtest snapshots
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
-- Server-side jobs (GitHub Actions / Streamlit Cloud) use SUPABASE_SERVICE_ROLE_KEY which bypasses RLS.
ALTER TABLE public.market_bars ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.data_health_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ledger_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.backtest_runs ENABLE ROW LEVEL SECURITY;
