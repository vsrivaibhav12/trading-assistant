-- Run this ONCE in Supabase: SQL Editor -> New query -> paste -> Run.
-- Row Level Security is switched on with no policies, so the public/anon key can read nothing.
-- Only your secret "service_role" key (kept in GitHub Secrets / Streamlit secrets) can access these tables.

create table if not exists kv (
  key text primary key,
  value jsonb,
  updated_at timestamptz default now()
);

create table if not exists trades (
  id bigint generated always as identity primary key,
  source text not null default 'system',          -- system (shadow/alerted signal) | user (you)
  strategy text, symbol text not null, name text, mode text, timeframe text,
  side text not null,
  opened_at timestamptz default now(),
  entry double precision, initial_sl double precision, sl double precision,
  t1 double precision, t2 double precision, qty double precision,
  status text not null default 'OPEN',            -- OPEN | CLOSED | CANCELLED
  t1_hit boolean default false, best double precision, bars_held int default 0,
  taken boolean default true,                     -- true = you were alerted; false = filtered out (still tracked)
  tracked boolean default true,                   -- false = journal only (e.g. option premium trade)
  last_bar text,
  closed_at timestamptz, exit_price double precision, exit_reason text,
  r_multiple double precision, pnl_pct double precision,
  meta jsonb default '{}'::jsonb
);
create index if not exists trades_status_idx on trades(status);
create index if not exists trades_closed_idx on trades(closed_at);

create table if not exists signals (
  id bigint generated always as identity primary key,
  created_at timestamptz default now(),
  strategy text, symbol text, name text, mode text, side text,
  entry double precision, sl double precision, score double precision,
  reason text, decision text,                      -- decision: OK or the reason it was filtered
  bar_time timestamptz, trade_id bigint, meta jsonb default '{}'::jsonb
);
create index if not exists signals_created_idx on signals(created_at);

create table if not exists news (
  id bigint generated always as identity primary key,
  source text, title text, link text unique, published timestamptz,
  tags jsonb, sentiment text, symbols jsonb
);
create index if not exists news_pub_idx on news(published);

create table if not exists runs (
  id bigint generated always as identity primary key,
  started_at timestamptz, job text, ok boolean, detail text,
  data_lag_min double precision, n_signals int, n_alerts int, seconds double precision
);

create table if not exists backtests (
  id bigint generated always as identity primary key,
  run_at timestamptz, strategy text, mode text, timeframe text, universe_n int, trades int,
  win_rate double precision, avg_r double precision, total_r double precision,
  profit_factor double precision, max_dd_r double precision, t_stat double precision,
  verdict text, by_year jsonb, note text,
  avg_win_r double precision, avg_loss_r double precision, payoff double precision
);
alter table backtests add column if not exists avg_win_r double precision;
alter table backtests add column if not exists avg_loss_r double precision;
alter table backtests add column if not exists payoff double precision;

alter table kv enable row level security;
alter table trades enable row level security;
alter table signals enable row level security;
alter table news enable row level security;
alter table runs enable row level security;
alter table backtests enable row level security;
