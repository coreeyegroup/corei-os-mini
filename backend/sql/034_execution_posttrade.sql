-- Real execution, post-trade and the advanced risk, strategy and portfolio controls (DEC-78 … DEC-84).
-- Orders reach the broker (MT5 through a bridge, Upstox through its API) from a dispatcher that records SUBMITTED
-- before sending and the broker's answer after; an order with no answer is UNKNOWN until the broker is asked.
-- Post-trade keeps the broker's own record (deals, positions, money) beside COREI's and raises every difference.
-- Money, prices and quantities stay NUMERIC.

-- ---------- Stage 7 · execution ----------
ALTER TABLE oms_orders DROP CONSTRAINT IF EXISTS oms_orders_order_type_check;
ALTER TABLE oms_orders ADD CONSTRAINT oms_orders_order_type_check CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP'));
ALTER TABLE oms_orders DROP CONSTRAINT IF EXISTS oms_orders_source_check;
ALTER TABLE oms_orders ADD CONSTRAINT oms_orders_source_check CHECK (source IN ('RUNTIME', 'MANUAL', 'FLATTEN', 'COPY', 'RECONCILE'));
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS stop_price      NUMERIC(38, 12) CHECK (stop_price IS NULL OR stop_price > 0);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS stop_loss       NUMERIC(38, 12) CHECK (stop_loss IS NULL OR stop_loss > 0);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS take_profit     NUMERIC(38, 12) CHECK (take_profit IS NULL OR take_profit > 0);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS adapter         TEXT;          -- PAPER | MT5_BRIDGE | UPSTOX
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS broker_tag      TEXT;          -- what the broker sees (MT5 comment), to find it again
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS broker_order_id TEXT;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS submitted_at    TIMESTAMPTZ;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS completed_at    TIMESTAMPTZ;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS checks          INTEGER NOT NULL DEFAULT 0;   -- broker look-ups of an UNKNOWN order
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS last_check_at   TIMESTAMPTZ;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS last_check      JSONB;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS hold_for_approval BOOLEAN NOT NULL DEFAULT false;  -- four-eyes: waits for a second person
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS released_by     TEXT;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS released_at     TIMESTAMPTZ;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS routing         JSONB;         -- smart routing: candidates and why this account
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS copy_of         UUID;          -- copy trading: the master's fill
CREATE UNIQUE INDEX IF NOT EXISTS oms_orders_broker_tag ON oms_orders (account_id, broker_tag) WHERE broker_tag IS NOT NULL;
CREATE INDEX IF NOT EXISTS oms_orders_dispatch ON oms_orders (mode, state) WHERE mode = 'BROKER';

ALTER TABLE oms_fills ADD COLUMN IF NOT EXISTS broker_deal_id TEXT;
ALTER TABLE oms_fills ADD COLUMN IF NOT EXISTS commission NUMERIC(38, 12) NOT NULL DEFAULT 0;
ALTER TABLE oms_fills ADD COLUMN IF NOT EXISTS swap       NUMERIC(38, 12) NOT NULL DEFAULT 0;
ALTER TABLE oms_fills ADD COLUMN IF NOT EXISTS fee        NUMERIC(38, 12) NOT NULL DEFAULT 0;
ALTER TABLE oms_fills ADD COLUMN IF NOT EXISTS latency_ms INTEGER;     -- submitted → this fill
ALTER TABLE oms_fills ADD COLUMN IF NOT EXISTS arrival_price NUMERIC(38, 12);  -- the side's price when the order was decided
CREATE UNIQUE INDEX IF NOT EXISTS oms_fills_broker_deal ON oms_fills (account_id, broker_deal_id) WHERE broker_deal_id IS NOT NULL;

ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS broker_magic BIGINT NOT NULL DEFAULT 7770001 CHECK (broker_magic BETWEEN 1 AND 2147483647);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS max_deviation_points INTEGER NOT NULL DEFAULT 20 CHECK (max_deviation_points BETWEEN 0 AND 100000);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS broker_timeout_seconds INTEGER NOT NULL DEFAULT 30 CHECK (broker_timeout_seconds BETWEEN 5 AND 300);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS unknown_check_seconds INTEGER NOT NULL DEFAULT 15 CHECK (unknown_check_seconds BETWEEN 5 AND 3600);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS pre_check_orders BOOLEAN NOT NULL DEFAULT true;     -- MT5 order_check before order_send
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS upstox_product TEXT NOT NULL DEFAULT 'D' CHECK (upstox_product IN ('D', 'I', 'MTF'));
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS live_four_eyes TEXT NOT NULL DEFAULT 'MANUAL' CHECK (live_four_eyes IN ('OFF', 'MANUAL', 'ALL'));
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS recon_interval_minutes INTEGER NOT NULL DEFAULT 5 CHECK (recon_interval_minutes BETWEEN 1 AND 1440);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS recon_qty_tolerance NUMERIC(38, 12) NOT NULL DEFAULT 0 CHECK (recon_qty_tolerance >= 0);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS recon_kill_on_break BOOLEAN NOT NULL DEFAULT false;  -- engage the account switch on a break
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS drift_min_trades INTEGER NOT NULL DEFAULT 20 CHECK (drift_min_trades BETWEEN 5 AND 10000);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS drift_tolerance_pct NUMERIC(10, 2) NOT NULL DEFAULT 50 CHECK (drift_tolerance_pct > 0);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS var_confidence NUMERIC(6, 2) NOT NULL DEFAULT 99 CHECK (var_confidence IN (90, 95, 97.5, 99, 99.5));
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS var_lookback_bars INTEGER NOT NULL DEFAULT 250 CHECK (var_lookback_bars BETWEEN 30 AND 5000);
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS var_timeframe TEXT NOT NULL DEFAULT 'D1' CHECK (var_timeframe IN ('M15', 'M30', 'H1', 'H4', 'D1'));

-- ---------- Stage 5 · risk: margin and VaR limits, stress scenarios ----------
ALTER TABLE risk_limits DROP CONSTRAINT IF EXISTS risk_limits_kind_check;
ALTER TABLE risk_limits ADD CONSTRAINT risk_limits_kind_check CHECK (kind IN ('MAX_ORDER_QTY', 'MAX_POSITION_QTY', 'MAX_OPEN_ORDERS',
    'MAX_ORDERS_PER_MINUTE', 'MAX_DAILY_LOSS', 'MAX_DRAWDOWN_PCT', 'MAX_GROSS_EXPOSURE', 'MAX_INSTRUMENT_EXPOSURE', 'MAX_MARGIN_PCT',
    'MAX_VAR_PCT', 'MAX_ORDER_NOTIONAL', 'MAX_PRICE_DEVIATION_PCT', 'MAX_LEVERAGE'));

CREATE TABLE IF NOT EXISTS risk_stress_scenarios (
    id          UUID PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 2 AND 80),
    description TEXT NOT NULL DEFAULT '',
    shocks      JSONB NOT NULL,        -- [{"match": "ALL|ASSET_CLASS|SYMBOL|CURRENCY", "value": "FX", "pct": "-3"}]
    builtin     BOOLEAN NOT NULL DEFAULT false,
    version     INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO risk_stress_scenarios(id, name, description, shocks, builtin, created_by, updated_by) VALUES
 ('5e000000-0000-0000-0000-000000000001', 'Everything −5%', 'Every price falls 5%', '[{"match": "ALL", "value": "", "pct": "-5"}]', true, 'system', 'system'),
 ('5e000000-0000-0000-0000-000000000002', 'Everything +5%', 'Every price rises 5%', '[{"match": "ALL", "value": "", "pct": "5"}]', true, 'system', 'system'),
 ('5e000000-0000-0000-0000-000000000003', 'USD shock +3%', 'Pairs quoted in USD fall 3% (USD stronger)', '[{"match": "CURRENCY", "value": "USD", "pct": "-3"}]', true, 'system', 'system'),
 ('5e000000-0000-0000-0000-000000000004', 'Gold crash −10%', 'Metals fall 10%', '[{"match": "ASSET_CLASS", "value": "METAL", "pct": "-10"}]', true, 'system', 'system'),
 ('5e000000-0000-0000-0000-000000000005', 'India gap −8%', 'Indian equities and indices open 8% lower', '[{"match": "CURRENCY", "value": "INR", "pct": "-8"}]', true, 'system', 'system')
ON CONFLICT DO NOTHING;

-- ---------- Stage 4 · optimisation and walk-forward ----------
CREATE TABLE IF NOT EXISTS tr_optimisations (
    id            UUID PRIMARY KEY,
    algorithm     TEXT NOT NULL,
    base_parameters JSONB NOT NULL,
    grid          JSONB NOT NULL,          -- {"fast": ["5","10"], "slow": ["20","50"]}
    objective     TEXT NOT NULL,
    account_id    UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    range_from    TIMESTAMPTZ NOT NULL,
    range_to      TIMESTAMPTZ NOT NULL,
    walk_forward  JSONB,                   -- {"windows": 4, "in_sample_pct": 70}
    results       JSONB NOT NULL,          -- every combination's metrics
    folds         JSONB NOT NULL DEFAULT '[]',
    best          JSONB,
    robustness    JSONB,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE tr_runtime_state ADD COLUMN IF NOT EXISTS drift JSONB;

-- ---------- Stage 6 · capital partitions, weighting, copy trading ----------
CREATE TABLE IF NOT EXISTS pf_partitions (
    id               UUID PRIMARY KEY,
    account_id       UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    name             TEXT NOT NULL CHECK (length(name) BETWEEN 2 AND 80),
    capital          NUMERIC(38, 2) NOT NULL CHECK (capital >= 0),
    weight           NUMERIC(20, 6) NOT NULL DEFAULT 1 CHECK (weight >= 0),
    max_drawdown_pct NUMERIC(10, 2) CHECK (max_drawdown_pct IS NULL OR max_drawdown_pct BETWEEN 0 AND 100),
    status           TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED')),
    note             TEXT NOT NULL DEFAULT '',
    version          INTEGER NOT NULL DEFAULT 1,
    created_by       TEXT NOT NULL,
    updated_by       TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, name)
);
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS partition_id UUID REFERENCES pf_partitions(id) ON DELETE SET NULL;
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS weight NUMERIC(20, 6) NOT NULL DEFAULT 1 CHECK (weight >= 0 AND weight <= 100);

CREATE TABLE IF NOT EXISTS tr_copy_links (
    id                  UUID PRIMARY KEY,
    master_account_id   UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    follower_account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    method              TEXT NOT NULL DEFAULT 'FIXED_RATIO' CHECK (method IN ('FIXED_RATIO', 'EQUITY_RATIO', 'FIXED_QTY')),
    ratio               NUMERIC(20, 6) NOT NULL DEFAULT 1 CHECK (ratio > 0),
    fixed_qty           NUMERIC(38, 12),
    mode                TEXT NOT NULL DEFAULT 'PAPER' CHECK (mode IN ('PAPER', 'BROKER')),
    reverse             BOOLEAN NOT NULL DEFAULT false,
    max_qty             NUMERIC(38, 12),
    symbols             JSONB NOT NULL DEFAULT '[]',    -- empty: every instrument
    enabled             BOOLEAN NOT NULL DEFAULT true,
    version             INTEGER NOT NULL DEFAULT 1,
    created_by          TEXT NOT NULL,
    updated_by          TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (master_account_id <> follower_account_id),
    UNIQUE (master_account_id, follower_account_id)
);

-- ---------- Stage 8 · post-trade ----------
CREATE TABLE IF NOT EXISTS pt_broker_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    account_id  UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ok          BOOLEAN NOT NULL,
    error       TEXT,
    money       JSONB NOT NULL DEFAULT '{}',
    positions   JSONB NOT NULL DEFAULT '[]',
    orders      JSONB NOT NULL DEFAULT '[]',
    source      TEXT NOT NULL DEFAULT 'MT5_BRIDGE'
);
CREATE INDEX IF NOT EXISTS pt_broker_snapshots_recent ON pt_broker_snapshots (account_id, at DESC);

CREATE TABLE IF NOT EXISTS pt_broker_deals (
    account_id    UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    ticket        TEXT NOT NULL,
    order_ticket  TEXT,
    position_id   TEXT,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    entry         TEXT,
    volume        NUMERIC(38, 12) NOT NULL,
    price         NUMERIC(38, 12) NOT NULL,
    commission    NUMERIC(38, 12) NOT NULL DEFAULT 0,
    swap          NUMERIC(38, 12) NOT NULL DEFAULT 0,
    fee           NUMERIC(38, 12) NOT NULL DEFAULT 0,
    profit        NUMERIC(38, 12) NOT NULL DEFAULT 0,
    at            TIMESTAMPTZ NOT NULL,
    comment       TEXT,
    magic         BIGINT,
    order_id      UUID,          -- the COREI order it belongs to (matched by tag); NULL: not placed by COREI
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, ticket)
);
CREATE INDEX IF NOT EXISTS pt_broker_deals_at ON pt_broker_deals (account_id, at DESC);

CREATE TABLE IF NOT EXISTS pt_recon_runs (
    id          UUID PRIMARY KEY,
    account_id  UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL CHECK (kind IN ('BROKER', 'INTERNAL')),
    status      TEXT NOT NULL CHECK (status IN ('MATCHED', 'BREAKS', 'FAILED')),
    checked     INTEGER NOT NULL DEFAULT 0,
    breaks      INTEGER NOT NULL DEFAULT 0,
    detail      JSONB NOT NULL DEFAULT '{}',
    actor       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pt_recon_runs_recent ON pt_recon_runs (account_id, at DESC);

CREATE TABLE IF NOT EXISTS pt_breaks (
    id            UUID PRIMARY KEY,
    account_id    UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('POSITION_MISMATCH', 'MISSING_AT_BROKER', 'NOT_IN_COREI', 'EXTERNAL_TRADE', 'FILL_MISSING',
                                                'ORDER_STATE', 'CASH', 'INTERNAL_POSITION')),
    key           TEXT NOT NULL,                -- what it is about (symbol, deal ticket, order) — one open break per key and kind
    instrument_id UUID,
    broker_symbol TEXT,
    corei_value   TEXT,
    broker_value  TEXT,
    difference    TEXT,
    detail        JSONB NOT NULL DEFAULT '{}',
    state         TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'RESOLVED', 'ACCEPTED')),
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_by   TEXT,
    resolved_at   TIMESTAMPTZ,
    resolution    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pt_breaks_open ON pt_breaks (account_id, kind, key) WHERE state = 'OPEN';
