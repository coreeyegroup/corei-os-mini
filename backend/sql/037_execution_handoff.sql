-- Execution handoff (DEC-89): how a strategy's decision reaches Stage 7, per deployment.
--   handoff     AUTO (straight through, the default and the behaviour so far) · APPROVE (the decision waits in the
--               execution queue for a person to execute, change or decline, until approval_minutes) · SIGNAL_ONLY
--               (the decision is recorded with its size and never ordered)
--   order_type  MARKET · LIMIT (limit_mode MARKETABLE = ask/bid + offset ticks, a price cap; PASSIVE = bid/ask − offset)
--   time_in_force GTC · DAY · IOC
-- Signal flips (sig_events): every change of a library signal's side, so the desk sees what changed and when.
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS execution JSONB NOT NULL DEFAULT '{}';

ALTER TABLE tr_intents DROP CONSTRAINT IF EXISTS tr_intents_outcome_check;
ALTER TABLE tr_intents ADD CONSTRAINT tr_intents_outcome_check
    CHECK (outcome IN ('ORDERED', 'REFUSED', 'NO_CHANGE', 'PENDING', 'DECLINED', 'EXPIRED', 'SUPERSEDED', 'RECORDED'));
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS handoff TEXT NOT NULL DEFAULT 'AUTO' CHECK (handoff IN ('AUTO', 'APPROVE', 'SIGNAL_ONLY'));
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS side TEXT CHECK (side IN ('BUY', 'SELL'));
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS quantity NUMERIC(38, 12);
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS order_type TEXT;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS limit_price NUMERIC(38, 12);
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS time_in_force TEXT;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(38, 12);
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS take_profit NUMERIC(38, 12);
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS decision_bid NUMERIC(38, 12);
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS decision_ask NUMERIC(38, 12);
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS sizing_note TEXT;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS decided_by TEXT;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
ALTER TABLE tr_intents ADD COLUMN IF NOT EXISTS decision_note TEXT;
CREATE INDEX IF NOT EXISTS tr_intents_pending ON tr_intents (expires_at) WHERE outcome = 'PENDING';
CREATE INDEX IF NOT EXISTS tr_intents_created ON tr_intents (created_at DESC);

CREATE TABLE IF NOT EXISTS sig_events (
    id              BIGSERIAL PRIMARY KEY,
    definition_id   UUID NOT NULL REFERENCES sig_definitions(id) ON DELETE CASCADE,
    account_id      UUID NOT NULL,
    broker_symbol   TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    bar_time        TIMESTAMPTZ NOT NULL,
    from_direction  TEXT,
    to_direction    TEXT NOT NULL,
    score           NUMERIC(8, 2),
    previous_score  NUMERIC(8, 2),
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (definition_id, account_id, broker_symbol, timeframe, bar_time)
);
CREATE INDEX IF NOT EXISTS sig_events_at ON sig_events (at DESC);
