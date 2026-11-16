-- Stage 2 · Market intelligence (DEC-70): the feature store.
-- intel_snapshot: the latest closed bar's features and regime per source × broker symbol × timeframe.
-- intel_features: the per-bar history (M5 and up), so a signal or a strategy decision can be replayed against the
-- exact features it saw. Features are descriptive analytics (double precision), never order values.
CREATE TABLE IF NOT EXISTS intel_snapshot (
    account_id    UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    broker_symbol TEXT NOT NULL,
    timeframe     TEXT NOT NULL CHECK (timeframe IN ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1')),
    bar_time      TIMESTAMPTZ NOT NULL,
    version       INTEGER NOT NULL,
    bars          INTEGER NOT NULL,
    bars_source   TEXT NOT NULL CHECK (bars_source IN ('BROKER', 'M1')),
    features      JSONB NOT NULL,
    regime        TEXT NOT NULL CHECK (regime IN ('TREND_UP', 'TREND_DOWN', 'RANGE', 'TRANSITION', 'WARMING_UP')),
    volatility    TEXT CHECK (volatility IN ('HIGH', 'NORMAL', 'LOW')),
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 0,
    reasons       JSONB NOT NULL DEFAULT '[]',
    session       TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, broker_symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS intel_features (
    account_id    UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    broker_symbol TEXT NOT NULL,
    timeframe     TEXT NOT NULL CHECK (timeframe IN ('M5', 'M15', 'M30', 'H1', 'H4', 'D1')),
    bar_time      TIMESTAMPTZ NOT NULL,
    version       INTEGER NOT NULL,
    features      JSONB NOT NULL,
    regime        TEXT NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, broker_symbol, timeframe, bar_time, version)
);
