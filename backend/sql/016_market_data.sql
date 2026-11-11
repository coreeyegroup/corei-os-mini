-- 0.13.0: market data collection and intelligence. Idempotent.
-- Recent data lives here; history is moved nightly to fingerprinted Parquet files.

-- What to collect, from where, and how to keep it.
CREATE TABLE IF NOT EXISTS md_collection_plans (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9][A-Z0-9_.-]{1,63}$'),
    account_id UUID NOT NULL REFERENCES cfg_accounts(id),
    broker_symbol TEXT NOT NULL CHECK (length(btrim(broker_symbol)) BETWEEN 1 AND 64),
    instrument_id UUID REFERENCES cfg_instruments(id),
    purpose TEXT NOT NULL DEFAULT 'TRADING_FEED' CHECK (purpose IN ('TRADING_FEED', 'REFERENCE', 'RESEARCH', 'MONITORING')),
    feed_role TEXT NOT NULL DEFAULT 'PRIMARY' CHECK (feed_role IN ('PRIMARY', 'BACKUP', 'REFERENCE')),
    data_types TEXT[] NOT NULL DEFAULT ARRAY['QUOTES', 'BARS'] CHECK (data_types <@ ARRAY['QUOTES', 'DEPTH', 'BARS'] AND cardinality(data_types) > 0),
    bar_timeframes TEXT[] NOT NULL DEFAULT ARRAY['M1'] CHECK (bar_timeframes <@ ARRAY['S1', 'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']),
    depth_levels INTEGER NOT NULL DEFAULT 0 CHECK (depth_levels BETWEEN 0 AND 50),
    schedule TEXT NOT NULL DEFAULT 'ALWAYS' CHECK (schedule IN ('ALWAYS', 'SESSIONS', 'WHILE_DEPLOYED', 'WINDOW')),
    window_start TIME,
    window_end TIME,
    window_days SMALLINT[] NOT NULL DEFAULT ARRAY[1, 2, 3, 4, 5]::SMALLINT[],
    conflate_ms INTEGER NOT NULL DEFAULT 0 CHECK (conflate_ms BETWEEN 0 AND 60000),
    hot_days INTEGER NOT NULL DEFAULT 14 CHECK (hot_days BETWEEN 1 AND 365),
    bars_hot_days INTEGER NOT NULL DEFAULT 365 CHECK (bars_hot_days BETWEEN 1 AND 3650),
    archive BOOLEAN NOT NULL DEFAULT true,
    backfill_from DATE,
    backfill_per_minute INTEGER NOT NULL DEFAULT 30 CHECK (backfill_per_minute BETWEEN 1 AND 600),
    max_latency_ms INTEGER NOT NULL DEFAULT 1500 CHECK (max_latency_ms BETWEEN 1 AND 600000),
    max_gap_seconds INTEGER NOT NULL DEFAULT 60 CHECK (max_gap_seconds BETWEEN 1 AND 86400),
    max_spread_points NUMERIC CHECK (max_spread_points IS NULL OR max_spread_points > 0),
    stale_seconds INTEGER NOT NULL DEFAULT 10 CHECK (stale_seconds BETWEEN 1 AND 86400),
    on_breach TEXT NOT NULL DEFAULT 'ALERT' CHECK (on_breach IN ('ALERT', 'SWITCH')),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED')),
    status_reason TEXT NOT NULL DEFAULT '' CHECK (length(status_reason) <= 500),
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (schedule <> 'WINDOW' OR (window_start IS NOT NULL AND window_end IS NOT NULL)),
    CHECK (feed_role = 'REFERENCE' OR purpose <> 'TRADING_FEED' OR instrument_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS md_plans_account_symbol ON md_collection_plans (account_id, broker_symbol) WHERE status <> 'ARCHIVED';

-- Collection switches: 'ALL' or 'account:<uuid>'. Separate from order sending.
CREATE TABLE IF NOT EXISTS md_controls (
    scope TEXT PRIMARY KEY CHECK (scope = 'ALL' OR scope ~ '^account:[0-9a-f-]{36}$'),
    paused BOOLEAN NOT NULL DEFAULT false,
    reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 500),
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO md_controls(scope, paused, reason) VALUES ('ALL', false, 'Initial state') ON CONFLICT DO NOTHING;

-- Every price update, as received. Split by day of receipt; raw rows are never edited.
CREATE TABLE IF NOT EXISTS md_ticks (
    account_id UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    plan_id UUID,
    source_time TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    seq BIGINT NOT NULL,
    bid DOUBLE PRECISION,
    ask DOUBLE PRECISION,
    last DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    backfilled BOOLEAN NOT NULL DEFAULT false
) PARTITION BY RANGE (received_at);
CREATE INDEX IF NOT EXISTS md_ticks_symbol_time ON md_ticks (account_id, broker_symbol, received_at);

CREATE OR REPLACE FUNCTION md_ensure_partition(day DATE) RETURNS TEXT AS $md_part$
DECLARE
    name TEXT := 'md_ticks_' || to_char(day, 'YYYYMMDD');
BEGIN
    IF to_regclass(quote_ident(current_schema()) || '.' || name) IS NULL THEN
        EXECUTE format('CREATE TABLE %I PARTITION OF md_ticks FOR VALUES FROM (%L) TO (%L)',
                       name, day::timestamp AT TIME ZONE 'UTC', (day + 1)::timestamp AT TIME ZONE 'UTC');
    END IF;
    RETURN name;
END;
$md_part$ LANGUAGE plpgsql;

-- Bars: built by the collector from ticks (BUILT) or taken from broker history (BROKER).
CREATE TABLE IF NOT EXISTS md_bars (
    account_id UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL CHECK (timeframe IN ('S1', 'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1')),
    bar_time TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    ticks INTEGER NOT NULL DEFAULT 0,
    spread_avg DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'BUILT' CHECK (source IN ('BUILT', 'BROKER')),
    complete BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (account_id, broker_symbol, timeframe, bar_time)
);

-- Latest price per account × symbol, for screens and the pre-send price check.
CREATE TABLE IF NOT EXISTS md_latest (
    account_id UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    bid DOUBLE PRECISION,
    ask DOUBLE PRECISION,
    last DOUBLE PRECISION,
    source_time TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    seq BIGINT NOT NULL,
    PRIMARY KEY (account_id, broker_symbol)
);

-- Quality per account × symbol × minute.
CREATE TABLE IF NOT EXISTS md_quality (
    account_id UUID NOT NULL,
    broker_symbol TEXT NOT NULL,
    minute TIMESTAMPTZ NOT NULL,
    ticks INTEGER NOT NULL,
    latency_p50_ms DOUBLE PRECISION,
    latency_p95_ms DOUBLE PRECISION,
    max_gap_ms DOUBLE PRECISION,
    spread_avg DOUBLE PRECISION,
    spread_max DOUBLE PRECISION,
    frozen_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    spikes INTEGER NOT NULL DEFAULT 0,
    deviation_bp DOUBLE PRECISION,
    score DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 100),
    PRIMARY KEY (account_id, broker_symbol, minute)
);

-- Outages, gaps, frozen prices, spikes, feed switches, backfills, archive and disk events.
CREATE TABLE IF NOT EXISTS md_events (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    account_id UUID,
    broker_symbol TEXT,
    plan_id UUID,
    kind TEXT NOT NULL CHECK (kind IN ('CONNECTED', 'DISCONNECTED', 'GAP', 'STALE', 'FROZEN', 'SPIKE', 'LATENCY', 'SPREAD', 'DEVIATION',
                                       'FEED_SWITCH', 'FEED_RESTORED', 'BACKFILL', 'ARCHIVE', 'VERIFY', 'BACKUP', 'DISK', 'BACKLOG', 'ERROR')),
    severity TEXT NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS md_events_recent ON md_events (at DESC);
CREATE INDEX IF NOT EXISTS md_events_symbol ON md_events (account_id, broker_symbol, at DESC);

-- Which plan currently feeds each instrument (primary, or backup after a switch).
CREATE TABLE IF NOT EXISTS md_feed_state (
    instrument_id UUID PRIMARY KEY REFERENCES cfg_instruments(id) ON DELETE CASCADE,
    active_plan_id UUID REFERENCES md_collection_plans(id) ON DELETE SET NULL,
    switched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason TEXT NOT NULL DEFAULT ''
);

-- One row per collector session (per account).
CREATE TABLE IF NOT EXISTS md_collector_status (
    account_id UUID PRIMARY KEY REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('STARTING', 'CONNECTED', 'RECONNECTING', 'STOPPED', 'PAUSED', 'FAILED', 'UNSUPPORTED')),
    host TEXT,
    port INTEGER,
    pid INTEGER,
    subscribed INTEGER NOT NULL DEFAULT 0,
    ticks_total BIGINT NOT NULL DEFAULT 0,
    lag_ms DOUBLE PRECISION,
    backlog INTEGER NOT NULL DEFAULT 0,
    server_offset_minutes INTEGER,
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    last_error TEXT
);

-- History backfill jobs.
CREATE TABLE IF NOT EXISTS md_jobs (
    id UUID PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'BACKFILL' CHECK (kind IN ('BACKFILL')),
    plan_id UUID NOT NULL REFERENCES md_collection_plans(id) ON DELETE CASCADE,
    timeframe TEXT NOT NULL DEFAULT 'M1' CHECK (timeframe IN ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1')),
    range_from TIMESTAMPTZ NOT NULL,
    range_to TIMESTAMPTZ NOT NULL CHECK (range_to > range_from),
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED')),
    done_until TIMESTAMPTZ,
    bars_saved INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    error TEXT,
    requested_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every journal and Parquet file, with its fingerprint. Checked nightly.
CREATE TABLE IF NOT EXISTS md_files (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('JOURNAL', 'TICKS', 'BARS')),
    day DATE NOT NULL,
    account_id UUID,
    broker_symbol TEXT,
    path TEXT NOT NULL UNIQUE,
    rows BIGINT,
    bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at TIMESTAMPTZ,
    verify_status TEXT NOT NULL DEFAULT 'OK' CHECK (verify_status IN ('OK', 'MISMATCH', 'MISSING', 'RETIRED'))
);
CREATE INDEX IF NOT EXISTS md_files_day ON md_files (day DESC, kind);

-- Nightly backups of the database and of the files.
CREATE TABLE IF NOT EXISTS md_backups (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('DATABASE', 'FILES')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    ok BOOLEAN NOT NULL DEFAULT false,
    path TEXT,
    bytes BIGINT,
    files INTEGER,
    error TEXT
);

INSERT INTO cfg_policies (key, value) VALUES
    ('md_disk_warn_pct', '85'::jsonb),
    ('md_disk_pause_pct', '92'::jsonb),
    ('md_journal_days', '30'::jsonb),
    ('md_backup_keep', '14'::jsonb),
    ('md_deviation_bp', '10'::jsonb),
    ('md_max_symbols_per_account', '300'::jsonb),
    ('md_four_eyes_trading_feeds', 'true'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- Activating a LIVE trading feed is a change request like LIVE connection changes.
ALTER TABLE cfg_change_requests DROP CONSTRAINT IF EXISTS cfg_change_requests_entity_check;
ALTER TABLE cfg_change_requests ADD CONSTRAINT cfg_change_requests_entity_check CHECK (entity IN ('connections', 'accounts', 'credential', 'md_plans'));
