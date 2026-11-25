-- 045 · Stage 1 Market data integrity (release 0.54.0).
-- Why: the 23 Nov 2026 Market Data audit found plans that collect nothing still shown as "collecting" (a symbol the
-- broker does not know), bars bucketed by receipt time and aligned to UTC instead of the exchange session, Upstox's
-- day-volume summed per quote, and feed quality never reaching trading decisions.
-- Additive only: existing bars keep their values and are labelled as made before this release (NULL basis columns
-- mean "built before 0.54.0: receipt time, UTC-aligned, bid, summed volume"); nothing is rewritten.

-- One health state per plan, written by the collector (and the supervisor for plans it does not run).
CREATE TABLE IF NOT EXISTS md_plan_health (
    plan_id UUID PRIMARY KEY REFERENCES md_collection_plans(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('LIVE', 'CLOSED', 'WAITING', 'STALE', 'DEGRADED', 'ERROR', 'INVALID_SYMBOL', 'OFFLINE', 'PAUSED')),
    detail TEXT NOT NULL DEFAULT '',
    suggestions JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_price_at TIMESTAMPTZ,
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    since TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS md_plan_health_state ON md_plan_health (state);

-- How each bar was made (NULL = built before 0.54.0).
ALTER TABLE md_bars ADD COLUMN IF NOT EXISTS price_basis TEXT CHECK (price_basis IN ('BID', 'LAST', 'MID'));
ALTER TABLE md_bars ADD COLUMN IF NOT EXISTS volume_kind TEXT CHECK (volume_kind IN ('EXCHANGE', 'PER_UPDATE', 'BROKER'));
ALTER TABLE md_bars ADD COLUMN IF NOT EXISTS anchor TEXT CHECK (anchor IN ('UTC', 'SESSION', 'BROKER'));
ALTER TABLE md_bars ADD COLUMN IF NOT EXISTS time_basis TEXT CHECK (time_basis IN ('EVENT', 'RECEIPT', 'BROKER'));
ALTER TABLE md_bars ADD COLUMN IF NOT EXISTS derivation TEXT CHECK (derivation IN ('TICKS', 'BACKFILLED', 'REPLAYED'));

-- Feed quality gate for trading (risk check and signal validation): a risk-increasing order or signal is refused
-- while its price feed is in one of these states, or its last scored minute is below the score (0 = off).
INSERT INTO cfg_policies (key, value) VALUES
    ('md_block_states', '["INVALID_SYMBOL", "ERROR", "OFFLINE", "STALE"]'::jsonb),
    ('md_min_quality_score', '40'::jsonb),
    ('md_backfill_max_failures', '3'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- New event kinds: a plan symbol the source does not list; automatic backfill held after repeated failures.
ALTER TABLE md_events DROP CONSTRAINT IF EXISTS md_events_kind_check;
ALTER TABLE md_events ADD CONSTRAINT md_events_kind_check CHECK (kind IN ('CONNECTED', 'DISCONNECTED', 'GAP', 'STALE', 'FROZEN', 'SPIKE',
    'LATENCY', 'SPREAD', 'DEVIATION', 'FEED_SWITCH', 'FEED_RESTORED', 'BACKFILL', 'ARCHIVE', 'VERIFY', 'BACKUP', 'DISK', 'BACKLOG', 'ERROR',
    'CLOCK_MEASURED', 'CONFIG_CHANGED', 'CLOCK_SKEW', 'SYMBOL_INVALID', 'BACKFILL_HELD'));
