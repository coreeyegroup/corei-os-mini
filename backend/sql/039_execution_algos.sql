-- Execution algorithms (DEC-94): a parent order worked over time as child orders through the normal order engine.
--   algo        TWAP (even slices over a duration) · VWAP (slices weighted by the instrument's usual volume at that time
--               of day) · POV (a share of the market volume traded since the start) · ICEBERG (one visible clip at a time)
--   state       WORKING → COMPLETED | CANCELLED | EXPIRED; PAUSED by a person, or by COREI when a child is refused or
--               its outcome is unknown (a parent never sends past a refusal or an unknown child — a person resumes it)
--   price_cap   no child buys above (sells below) this price; a slice waits while the market is beyond it
-- Children are ordinary oms_orders (parent_id), each with its own risk check, idempotent client order ID
-- ("algo:<parent>:<n>") and append-only events. Every parent change is audited.
CREATE TABLE IF NOT EXISTS ex_parent_orders (
    id              UUID PRIMARY KEY,
    client_key      TEXT NOT NULL UNIQUE,
    algo            TEXT NOT NULL CHECK (algo IN ('TWAP', 'VWAP', 'POV', 'ICEBERG')),
    account_id      UUID NOT NULL,
    instrument_id   UUID NOT NULL,
    deployment_id   UUID,
    intent_id       UUID UNIQUE,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity        NUMERIC(38, 12) NOT NULL CHECK (quantity > 0),
    filled_qty      NUMERIC(38, 12) NOT NULL DEFAULT 0,
    avg_price       NUMERIC(38, 12),
    arrival_bid     NUMERIC(38, 12),
    arrival_ask     NUMERIC(38, 12),
    price_cap       NUMERIC(38, 12),
    mode            TEXT NOT NULL DEFAULT 'PAPER' CHECK (mode IN ('PAPER', 'BROKER')),
    params          JSONB NOT NULL DEFAULT '{}',
    state           TEXT NOT NULL DEFAULT 'WORKING' CHECK (state IN ('WORKING', 'PAUSED', 'COMPLETED', 'CANCELLED', 'EXPIRED')),
    children        INTEGER NOT NULL DEFAULT 0,
    start_at        TIMESTAMPTZ NOT NULL,
    end_at          TIMESTAMPTZ NOT NULL,
    last_tick_at    TIMESTAMPTZ,
    last_note       TEXT,
    stop_loss       NUMERIC(38, 12),
    take_profit     NUMERIC(38, 12),
    created_by      TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ex_parent_working ON ex_parent_orders (state) WHERE state IN ('WORKING', 'PAUSED');
CREATE TABLE IF NOT EXISTS ex_parent_events (
    id          BIGSERIAL PRIMARY KEY,
    parent_id   UUID NOT NULL REFERENCES ex_parent_orders(id) ON DELETE CASCADE,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}',
    actor       TEXT NOT NULL
);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES ex_parent_orders(id);
CREATE INDEX IF NOT EXISTS oms_orders_parent ON oms_orders (parent_id) WHERE parent_id IS NOT NULL;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES ex_parent_orders(id);
