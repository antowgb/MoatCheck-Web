-- Supabase schema for the quant stock-picking tool (V1).
-- Full database: tables, indexes, and RLS policies. Run in the Supabase SQL Editor.

create table if not exists stocks (
    ticker      text primary key,
    name        text,
    sector      text,
    industry    text,
    currency    text,
    is_benchmark boolean not null default false,  -- true = reference index (e.g. SPY), excluded from the investable universe
    asset_type  text not null default 'equity' check (asset_type in ('equity', 'etf')),
    -- equities only: the sector ETF used as a comparison benchmark, e.g. AMD -> 'SOXX'
    sector_benchmark_ticker text references stocks (ticker),
    -- 'pending_refresh': added via POST /api/stocks, no data yet
    -- (prices/fundamentals); moves to 'active' after the first successful refresh.
    status      text not null default 'active' check (status in ('active', 'pending_refresh')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- Refresh queue (e.g. tickers manually added via POST /api/stocks).
-- No worker consumes this table for now (V1): a future job could
-- process `status = 'pending'` entries in `priority` order, or in the
-- meantime, a manual /api/refresh on the ticker clears the `stocks` row
-- out of its 'pending_refresh' state (see app/data/fetch.py::refresh_ticker).
create table if not exists refresh_queue (
    id          bigint generated always as identity primary key,
    ticker      text not null references stocks (ticker) on delete cascade,
    priority    text not null default 'normal' check (priority in ('high', 'normal')),
    status      text not null default 'pending' check (status in ('pending', 'done', 'failed')),
    created_at  timestamptz not null default now(),
    processed_at timestamptz
);

create index if not exists idx_refresh_queue_status
    on refresh_queue (status, priority, created_at);

-- At most one pending entry per ticker: POST /api/stocks can otherwise
-- double-insert if a retry follows a request whose insert succeeded but
-- whose response timed out.
create unique index if not exists idx_refresh_queue_ticker_pending
    on refresh_queue (ticker)
    where status = 'pending';

create table if not exists price_history (
    id      bigint generated always as identity primary key,
    ticker  text not null references stocks (ticker) on delete cascade,
    date    date not null,
    close   double precision not null,
    volume  bigint,
    unique (ticker, date)
);

create index if not exists idx_price_history_ticker_date
    on price_history (ticker, date);

-- One snapshot per available quarter and per ticker (not a single current
-- snapshot): enables real point-in-time scoring in the backtest, without look-ahead.
create table if not exists fundamentals (
    id                  bigint generated always as identity primary key,
    ticker              text not null references stocks (ticker) on delete cascade,
    report_date         date,           -- quarter's fiscalDateEnding
    know_date           date,           -- report_date + REPORTING_LAG_DAYS: date at which
                                        -- the info becomes publicly known (anti-look-ahead)
    revenue             double precision,
    revenue_growth_yoy  double precision,
    operating_margin    double precision,
    net_margin          double precision,
    roe                 double precision,
    roic                double precision,  -- nullable: not properly computable via Alpha Vantage
    debt_to_ebitda      double precision,
    free_cash_flow      double precision,
    pe_trailing         double precision,
    pe_forward          double precision,
    market_cap          double precision,
    fetched_at          timestamptz not null default now(),
    data_source         jsonb,          -- per-field source ("alpha_vantage"/"finnhub") +
                                        -- fiscal_date_match gating, see finnhub_client.py
    unique (ticker, report_date)
);

create index if not exists idx_fundamentals_ticker
    on fundamentals (ticker, report_date desc);

create index if not exists idx_fundamentals_know_date
    on fundamentals (ticker, know_date);

create table if not exists scores (
    id                     bigint generated always as identity primary key,
    ticker                 text not null references stocks (ticker) on delete cascade,
    computed_at            timestamptz not null default now(),
    volatility_annualized  double precision,
    sharpe_ratio           double precision,
    max_drawdown           double precision,
    fundamental_score      double precision,  -- 0-100
    risk_score             double precision,  -- 0-100
    composite_score        double precision,  -- 0-100
    score_breakdown        jsonb
);

create index if not exists idx_scores_ticker
    on scores (ticker, computed_at desc);

-- Reserved for V2 (qualitative layer: moats, contracts, LLM-based 10-K reading).
-- Deliberately empty in V1.
create table if not exists qualitative_notes (
    id          bigint generated always as identity primary key,
    ticker      text not null references stocks (ticker) on delete cascade,
    note        text,
    source_url  text,
    created_at  timestamptz not null default now()
);


-- RLS: "write locally (service_role), read on Render (anon)".
--   - service_role bypasses RLS, so the /refresh pipeline run locally can write.
--   - anon (the public key used by Render) only gets the read policies below.
--     No INSERT/UPDATE/DELETE policy for anon, so writing with the anon key is impossible.

alter table stocks             enable row level security;
alter table price_history      enable row level security;
alter table fundamentals       enable row level security;
alter table scores             enable row level security;
alter table qualitative_notes  enable row level security;
-- refresh_queue is an internal work list: no anon policy at all, so it stays
-- fully locked to the anon key (only service_role, which bypasses RLS, touches it).
alter table refresh_queue      enable row level security;

drop policy if exists "anon read stocks"            on stocks;
drop policy if exists "anon read price_history"     on price_history;
drop policy if exists "anon read fundamentals"      on fundamentals;
drop policy if exists "anon read scores"            on scores;
drop policy if exists "anon read qualitative_notes" on qualitative_notes;

create policy "anon read stocks"            on stocks            for select to anon, authenticated using (true);
create policy "anon read price_history"     on price_history     for select to anon, authenticated using (true);
create policy "anon read fundamentals"      on fundamentals      for select to anon, authenticated using (true);
create policy "anon read scores"            on scores            for select to anon, authenticated using (true);
create policy "anon read qualitative_notes" on qualitative_notes for select to anon, authenticated using (true);
