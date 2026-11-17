-- Stage 2 · Market intelligence: custom indicators (DEC-72). The standard baseline (EMA 20/50/200, RSI 14, ATR 14,
-- Bollinger 20/2, MACD 12/26/9, …) stays fixed so every screen and regime reads the same thing; each strategy adds the
-- lengths it needs here (EMA 9, EMA 21, Bollinger 20/2.5…), computed by the engine for the chosen timeframes and usable
-- in rules. Every change is audited by the API. Also: rule thresholds may name another feature, so the value check
-- allows feature keys.
CREATE TABLE IF NOT EXISTS intel_indicators (
    id          UUID PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE CHECK (key ~ '^[a-z0-9_]{3,60}$'),
    kind        TEXT NOT NULL CHECK (kind IN ('EMA', 'SMA', 'RSI', 'ATR', 'ROC', 'ZSCORE', 'DONCHIAN', 'VWAP', 'BOLLINGER', 'MACD', 'STOCH')),
    params      JSONB NOT NULL DEFAULT '{}',
    timeframes  JSONB NOT NULL DEFAULT '["M1","M5","M15","H1","H4","D1"]',
    label       TEXT NOT NULL DEFAULT '' CHECK (length(label) <= 80),
    enabled     BOOLEAN NOT NULL DEFAULT true,
    version     INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE intel_rules DROP CONSTRAINT IF EXISTS intel_rules_field_check;
ALTER TABLE intel_rules ADD CONSTRAINT intel_rules_field_check CHECK (field ~ '^[a-z0-9_]{1,60}$');
