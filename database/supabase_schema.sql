-- Supabase PostgreSQL Schema for Local AI Trading Agent

-- 1. Account Table
CREATE TABLE IF NOT EXISTS public.account (
    id BIGINT PRIMARY KEY,
    initial_balance DOUBLE PRECISION NOT NULL DEFAULT 100000.0,
    cash_balance DOUBLE PRECISION NOT NULL DEFAULT 100000.0,
    realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_fees_paid DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    peak_equity DOUBLE PRECISION NOT NULL DEFAULT 100000.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Open Positions Table
CREATE TABLE IF NOT EXISTS public.positions (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT UNIQUE NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    avg_entry_price DOUBLE PRECISION NOT NULL,
    current_price DOUBLE PRECISION NOT NULL,
    market_value DOUBLE PRECISION NOT NULL,
    unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unrealized_pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Trade Execution History Table
CREATE TABLE IF NOT EXISTS public.trades (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    slippage DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_cost DOUBLE PRECISION NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT
);

-- Disable Row Level Security (RLS) for paper trading research demo, or enable with public read/write policy:
ALTER TABLE public.account ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trades ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all actions for anon users on account" ON public.account FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all actions for anon users on positions" ON public.positions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all actions for anon users on trades" ON public.trades FOR ALL USING (true) WITH CHECK (true);

-- Insert initial account if not exists
INSERT INTO public.account (id, initial_balance, cash_balance, realized_pnl, total_fees_paid, peak_equity, updated_at)
VALUES (1, 100000.0, 100000.0, 0.0, 0.0, 100000.0, NOW())
ON CONFLICT (id) DO NOTHING;
