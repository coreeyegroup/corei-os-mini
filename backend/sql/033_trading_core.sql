-- Trading core, Stages 3–7 (DEC-76): signals and intents, strategy runtime state and backtests, risk limits, kill
-- switches and decisions, orders with an append-only event history and fills, positions and equity.
-- Money, prices and quantities are NUMERIC (exact). Every order state change is an order event (append-only);
-- settings, limits and kill switches are audited in cfg_audit_log by the API in the same transaction.

-- Deployments: how each one trades. PAPER = simulated by COREI at live prices; BROKER = real orders to the account
-- (demo or live), which also needs the order adapter and the environment's order-sending switch.
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'PAPER' CHECK (mode IN ('PAPER', 'BROKER'));
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS sizing JSONB NOT NULL DEFAULT '{"method": "FIXED_QTY", "quantity": "1"}';
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS signal_expiry_bars INTEGER NOT NULL DEFAULT 2 CHECK (signal_expiry_bars BETWEEN 1 AND 500);

CREATE TABLE IF NOT EXISTS tr_settings (
    id                    BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    runtime_enabled       BOOLEAN NOT NULL DEFAULT true,
    max_quote_age_seconds INTEGER NOT NULL DEFAULT 60 CHECK (max_quote_age_seconds BETWEEN 1 AND 86400),
    max_spread_atr_pct    NUMERIC(10, 2) NOT NULL DEFAULT 30 CHECK (max_spread_atr_pct > 0),
    block_on_event        BOOLEAN NOT NULL DEFAULT true,
    min_strength          INTEGER NOT NULL DEFAULT 0 CHECK (min_strength BETWEEN 0 AND 100),
    decision_mode         TEXT NOT NULL DEFAULT 'LATEST' CHECK (decision_mode IN ('LATEST', 'AGREE')),
    agree_count           INTEGER NOT NULL DEFAULT 2 CHECK (agree_count BETWEEN 2 AND 10),
    paper_slippage_ticks  INTEGER NOT NULL DEFAULT 1 CHECK (paper_slippage_ticks BETWEEN 0 AND 1000),
    default_capital       NUMERIC(38, 2) NOT NULL DEFAULT 100000 CHECK (default_capital > 0),
    version               INTEGER NOT NULL DEFAULT 1,
    updated_by            TEXT NOT NULL DEFAULT 'system',
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO tr_settings(id) VALUES (true) ON CONFLICT DO NOTHING;

-- Stage 4 · strategy runtime: the last closed bar each deployment was evaluated on (a bar is evaluated once).
CREATE TABLE IF NOT EXISTS tr_runtime_state (
    deployment_id  UUID PRIMARY KEY REFERENCES cfg_deployments(id) ON DELETE CASCADE,
    timeframe      TEXT NOT NULL,
    source_account UUID,
    source_symbol  TEXT,
    last_bar_time  TIMESTAMPTZ,
    last_target    TEXT,
    last_run_at    TIMESTAMPTZ,
    last_error     TEXT
);

-- Stage 3 · signals and their history.
CREATE TABLE IF NOT EXISTS tr_signals (
    id              UUID PRIMARY KEY,
    deployment_id   UUID REFERENCES cfg_deployments(id) ON DELETE SET NULL,
    strategy_id     UUID,
    strategy_version_id UUID,
    account_id      UUID NOT NULL,
    instrument_id   UUID NOT NULL,
    timeframe       TEXT NOT NULL,
    bar_time        TIMESTAMPTZ NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'FLAT')),
    strength        INTEGER NOT NULL CHECK (strength BETWEEN 0 AND 100),
    horizon_bars    INTEGER NOT NULL DEFAULT 10,
    expires_at      TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('RUNTIME', 'MANUAL')),
    state           TEXT NOT NULL DEFAULT 'NEW' CHECK (state IN ('NEW', 'VALID', 'REJECTED', 'ACTED', 'EXPIRED', 'CANCELLED')),
    reasons         JSONB NOT NULL DEFAULT '[]',
    explanation     JSONB NOT NULL DEFAULT '[]',
    features        JSONB NOT NULL DEFAULT '{}',
    price           NUMERIC(38, 12),
    forward_return_pct NUMERIC(20, 6),
    evaluated_at    TIMESTAMPTZ,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS tr_signals_once_per_bar ON tr_signals (deployment_id, bar_time) WHERE source = 'RUNTIME';
CREATE INDEX IF NOT EXISTS tr_signals_recent ON tr_signals (created_at DESC);
CREATE INDEX IF NOT EXISTS tr_signals_open ON tr_signals (deployment_id, state);

CREATE TABLE IF NOT EXISTS tr_signal_events (
    id         BIGSERIAL PRIMARY KEY,
    signal_id  UUID NOT NULL REFERENCES tr_signals(id) ON DELETE CASCADE,
    state      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tr_signal_events_signal ON tr_signal_events (signal_id, id);

-- Stage 3 · decision: one intent per acted signal (target position of a deployment).
CREATE TABLE IF NOT EXISTS tr_intents (
    id             UUID PRIMARY KEY,
    signal_id      UUID REFERENCES tr_signals(id) ON DELETE SET NULL,
    deployment_id  UUID REFERENCES cfg_deployments(id) ON DELETE SET NULL,
    account_id     UUID NOT NULL,
    instrument_id  UUID NOT NULL,
    target         TEXT NOT NULL CHECK (target IN ('LONG', 'SHORT', 'FLAT')),
    current_qty    NUMERIC(38, 12) NOT NULL,
    target_qty     NUMERIC(38, 12),
    outcome        TEXT NOT NULL CHECK (outcome IN ('ORDERED', 'REFUSED', 'NO_CHANGE')),
    reasons        JSONB NOT NULL DEFAULT '[]',
    order_id       UUID,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stage 4 · backtests (a run record: inputs, metrics, trades, equity curve).
CREATE TABLE IF NOT EXISTS tr_backtests (
    id            UUID PRIMARY KEY,
    strategy_version_id UUID,
    algorithm     TEXT NOT NULL,
    parameters    JSONB NOT NULL,
    account_id    UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    instrument_id UUID,
    timeframe     TEXT NOT NULL,
    range_from    TIMESTAMPTZ NOT NULL,
    range_to      TIMESTAMPTZ NOT NULL,
    settings      JSONB NOT NULL,
    metrics       JSONB NOT NULL,
    trades        JSONB NOT NULL,
    equity        JSONB NOT NULL,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stage 5 · risk.
CREATE TABLE IF NOT EXISTS risk_limits (
    id         UUID PRIMARY KEY,
    scope      TEXT NOT NULL CHECK (scope IN ('FIRM', 'ACCOUNT', 'STRATEGY', 'INSTRUMENT', 'DEPLOYMENT')),
    target_id  UUID,
    kind       TEXT NOT NULL CHECK (kind IN ('MAX_ORDER_QTY', 'MAX_POSITION_QTY', 'MAX_OPEN_ORDERS', 'MAX_ORDERS_PER_MINUTE', 'MAX_DAILY_LOSS',
                                             'MAX_DRAWDOWN_PCT', 'MAX_GROSS_EXPOSURE', 'MAX_INSTRUMENT_EXPOSURE')),
    value      NUMERIC(38, 12) NOT NULL CHECK (value >= 0),
    enabled    BOOLEAN NOT NULL DEFAULT true,
    note       TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    version    INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((scope = 'FIRM') = (target_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS risk_limits_one ON risk_limits (scope, COALESCE(target_id, '00000000-0000-0000-0000-000000000000'::uuid), kind);

CREATE TABLE IF NOT EXISTS risk_kill_switches (
    id          UUID PRIMARY KEY,
    scope       TEXT NOT NULL CHECK (scope IN ('FIRM', 'ACCOUNT', 'STRATEGY', 'INSTRUMENT', 'DEPLOYMENT')),
    target_id   UUID,
    engaged     BOOLEAN NOT NULL DEFAULT true,
    automatic   BOOLEAN NOT NULL DEFAULT false,
    reason      TEXT NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
    engaged_by  TEXT NOT NULL,
    engaged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_by TEXT,
    released_at TIMESTAMPTZ,
    release_reason TEXT,
    version     INTEGER NOT NULL DEFAULT 1,
    CHECK ((scope = 'FIRM') = (target_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS risk_kill_one_engaged ON risk_kill_switches (scope, COALESCE(target_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE engaged;

CREATE TABLE IF NOT EXISTS risk_decisions (
    id             UUID PRIMARY KEY,
    order_id       UUID,
    intent_id      UUID,
    deployment_id  UUID,
    account_id     UUID NOT NULL,
    instrument_id  UUID NOT NULL,
    side           TEXT NOT NULL,
    requested_qty  NUMERIC(38, 12),
    approved_qty   NUMERIC(38, 12),
    approved       BOOLEAN NOT NULL,
    reasons        JSONB NOT NULL DEFAULT '[]',
    inputs         JSONB NOT NULL DEFAULT '{}',
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS risk_decisions_recent ON risk_decisions (at DESC);

-- Stage 7 · orders, their events (append-only) and fills.
CREATE TABLE IF NOT EXISTS oms_orders (
    id              UUID PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE CHECK (length(client_order_id) BETWEEN 8 AND 64),
    intent_id       UUID,
    deployment_id   UUID REFERENCES cfg_deployments(id) ON DELETE SET NULL,
    account_id      UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE RESTRICT,
    instrument_id   UUID NOT NULL REFERENCES cfg_instruments(id) ON DELETE RESTRICT,
    broker_symbol   TEXT,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type      TEXT NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT')),
    time_in_force   TEXT NOT NULL DEFAULT 'GTC' CHECK (time_in_force IN ('GTC', 'DAY', 'IOC')),
    quantity        NUMERIC(38, 12) NOT NULL CHECK (quantity > 0),
    limit_price     NUMERIC(38, 12) CHECK (limit_price IS NULL OR limit_price > 0),
    mode            TEXT NOT NULL CHECK (mode IN ('PAPER', 'BROKER')),
    reduce_only     BOOLEAN NOT NULL DEFAULT false,
    state           TEXT NOT NULL CHECK (state IN ('NEW', 'APPROVED', 'REJECTED', 'SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED',
                                                   'CANCEL_PENDING', 'CANCELLED', 'EXPIRED', 'UNKNOWN')),
    filled_qty      NUMERIC(38, 12) NOT NULL DEFAULT 0,
    avg_fill_price  NUMERIC(38, 12),
    reasons         JSONB NOT NULL DEFAULT '[]',
    decision_bid    NUMERIC(38, 12),
    decision_ask    NUMERIC(38, 12),
    expires_at      TIMESTAMPTZ,
    source          TEXT NOT NULL CHECK (source IN ('RUNTIME', 'MANUAL', 'FLATTEN')),
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS oms_orders_open ON oms_orders (account_id, state);
CREATE INDEX IF NOT EXISTS oms_orders_recent ON oms_orders (created_at DESC);

CREATE TABLE IF NOT EXISTS oms_order_events (
    id         BIGSERIAL PRIMARY KEY,
    order_id   UUID NOT NULL REFERENCES oms_orders(id) ON DELETE RESTRICT,
    state_from TEXT,
    state_to   TEXT NOT NULL,
    detail     JSONB NOT NULL DEFAULT '{}',
    actor      TEXT NOT NULL,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oms_order_events_order ON oms_order_events (order_id, id);
CREATE OR REPLACE FUNCTION oms_order_events_append_only() RETURNS trigger AS $oms$
BEGIN
    RAISE EXCEPTION 'oms_order_events is append-only';
END;
$oms$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS oms_order_events_no_change ON oms_order_events;
CREATE TRIGGER oms_order_events_no_change BEFORE UPDATE OR DELETE ON oms_order_events FOR EACH ROW EXECUTE FUNCTION oms_order_events_append_only();

CREATE TABLE IF NOT EXISTS oms_fills (
    id             UUID PRIMARY KEY,
    order_id       UUID NOT NULL REFERENCES oms_orders(id) ON DELETE RESTRICT,
    deployment_id  UUID,
    account_id     UUID NOT NULL,
    instrument_id  UUID NOT NULL,
    side           TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity       NUMERIC(38, 12) NOT NULL CHECK (quantity > 0),
    price          NUMERIC(38, 12) NOT NULL CHECK (price > 0),
    quote_bid      NUMERIC(38, 12),
    quote_ask      NUMERIC(38, 12),
    slippage       NUMERIC(38, 12),
    realised_pnl   NUMERIC(38, 12) NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL,
    mode           TEXT NOT NULL,
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oms_fills_account ON oms_fills (account_id, at);

-- Stage 6 · positions (kept by the portfolio in the fill's transaction), capital and equity.
CREATE TABLE IF NOT EXISTS pf_positions (
    id             UUID PRIMARY KEY,
    account_id     UUID NOT NULL,
    instrument_id  UUID NOT NULL,
    deployment_id  UUID,
    mode           TEXT NOT NULL,
    quantity       NUMERIC(38, 12) NOT NULL DEFAULT 0,
    avg_price      NUMERIC(38, 12),
    realised_pnl   NUMERIC(38, 12) NOT NULL DEFAULT 0,
    currency       TEXT NOT NULL,
    opened_at      TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS pf_positions_key ON pf_positions (account_id, instrument_id, COALESCE(deployment_id, '00000000-0000-0000-0000-000000000000'::uuid), mode);

CREATE TABLE IF NOT EXISTS pf_accounts (
    account_id       UUID PRIMARY KEY REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    starting_capital NUMERIC(38, 2) NOT NULL CHECK (starting_capital > 0),
    currency         TEXT NOT NULL,
    peak_equity      NUMERIC(38, 2),
    version          INTEGER NOT NULL DEFAULT 1,
    updated_by       TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pf_equity (
    account_id  UUID NOT NULL,
    at          TIMESTAMPTZ NOT NULL,
    equity      NUMERIC(38, 2) NOT NULL,
    realised    NUMERIC(38, 2) NOT NULL,
    unrealised  NUMERIC(38, 2) NOT NULL,
    currency    TEXT NOT NULL,
    PRIMARY KEY (account_id, at)
);
