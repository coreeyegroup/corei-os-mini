-- Stage 2 · Market intelligence, the rest of the module (DEC-71): watchlists, condition rules and what they fired,
-- the economic calendar, and how long the feature history is kept. Every change is audited by the API.
CREATE TABLE IF NOT EXISTS intel_watchlists (
    id          UUID PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    items       JSONB NOT NULL DEFAULT '[]',   -- [{account_id, broker_symbol}] (a source's symbol, so brokers stay distinct)
    notes       TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 500),
    version     INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intel_rules (
    id               UUID PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    enabled          BOOLEAN NOT NULL DEFAULT true,
    timeframe        TEXT NOT NULL CHECK (timeframe IN ('M1', 'M5', 'M15', 'H1', 'H4', 'D1')),
    scope            JSONB NOT NULL DEFAULT '{}',  -- {} = every instrument; {watchlist_id} or {account_id, broker_symbol}
    field            TEXT NOT NULL CHECK (field ~ '^[a-z0-9_]{1,40}$'),
    op               TEXT NOT NULL CHECK (op IN ('>', '<', '>=', '<=', 'CROSSES_ABOVE', 'CROSSES_BELOW', 'BECOMES')),
    value            TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 40),
    severity         TEXT NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    cooldown_minutes INTEGER NOT NULL DEFAULT 60 CHECK (cooldown_minutes BETWEEN 0 AND 10080),
    notes            TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 500),
    version          INTEGER NOT NULL DEFAULT 1,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intel_rule_fires (
    id            BIGSERIAL PRIMARY KEY,
    rule_id       UUID NOT NULL REFERENCES intel_rules(id) ON DELETE CASCADE,
    account_id    UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    broker_symbol TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    bar_time      TIMESTAMPTZ NOT NULL,
    value         TEXT,
    message       TEXT NOT NULL,
    fired_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rule_id, account_id, broker_symbol, bar_time)
);
CREATE INDEX IF NOT EXISTS intel_rule_fires_recent ON intel_rule_fires(fired_at DESC);

CREATE TABLE IF NOT EXISTS intel_events (
    id         UUID PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL,
    currency   TEXT NOT NULL CHECK (currency ~ '^[A-Z]{3}$|^ALL$'),
    impact     TEXT NOT NULL CHECK (impact IN ('LOW', 'MEDIUM', 'HIGH', 'HOLIDAY')),
    title      TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    forecast   TEXT NOT NULL DEFAULT '' CHECK (length(forecast) <= 40),
    previous   TEXT NOT NULL DEFAULT '' CHECK (length(previous) <= 40),
    actual     TEXT NOT NULL DEFAULT '' CHECK (length(actual) <= 40),
    source     TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'CSV')),
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (at, currency, title)
);
CREATE INDEX IF NOT EXISTS intel_events_at ON intel_events(at);

ALTER TABLE intel_settings ADD COLUMN IF NOT EXISTS retention_days INTEGER NOT NULL DEFAULT 365 CHECK (retention_days BETWEEN 7 AND 3650);
ALTER TABLE intel_settings ADD COLUMN IF NOT EXISTS event_window_minutes INTEGER NOT NULL DEFAULT 30 CHECK (event_window_minutes BETWEEN 0 AND 720);
