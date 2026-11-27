-- 052 · Universal integration gateway (release 0.65.0).
-- Why: the only machine access was a read-only research API with one shared token from the environment. CORE-AI, n8n
-- and other applications need their own credentials, explicit permissions, resource scopes, usage limits, durable
-- jobs, candidate-signal submission with provenance, and signed webhooks — without touching COREI's own data model or
-- bypassing its risk, gates or order management.
-- What: one client (connection) per external application; any number of hashed keys per client (shown once, expiring,
-- rotatable, revocable); a usage log; snapshot records for traceability; external candidate signals with provenance
-- (never orders); webhook subscriptions with an at-least-once delivery outbox; job-notice bookkeeping for scan jobs.
-- Additive only. Nothing here can enable LIVE order sending.

CREATE TABLE IF NOT EXISTS int_clients (
    id            UUID PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9][a-z0-9_-]{1,39}$'),
    name          TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    client_type   TEXT NOT NULL CHECK (client_type IN ('CORE_AI', 'N8N', 'AI_AGENT', 'PYTHON', 'JAVASCRIPT', 'RESEARCH', 'DASHBOARD', 'CUSTOM')),
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    environment   TEXT NOT NULL DEFAULT 'PAPER' CHECK (environment IN ('PAPER', 'DEMO')),   -- LIVE is not offered to integrations
    scopes        TEXT[] NOT NULL DEFAULT '{}',
    -- Resource scope per kind: {"all": true} or {"ids": [...]}; an empty list grants nothing.
    access        JSONB NOT NULL DEFAULT '{"accounts": {"ids": []}, "instruments": {"all": true}, "strategies": {"all": true}}',
    requests_per_minute INTEGER NOT NULL DEFAULT 60 CHECK (requests_per_minute BETWEEN 1 AND 6000),
    max_concurrent_jobs INTEGER NOT NULL DEFAULT 2 CHECK (max_concurrent_jobs BETWEEN 1 AND 50),
    allowed_networks TEXT[] NOT NULL DEFAULT '{}',   -- CIDRs; empty = any
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    version       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS int_keys (
    id            UUID PRIMARY KEY,
    client_id     UUID NOT NULL REFERENCES int_clients(id) ON DELETE CASCADE,
    prefix        TEXT NOT NULL UNIQUE,             -- first characters of the key, shown to identify it
    key_hash      BYTEA NOT NULL UNIQUE,            -- SHA-256 of the whole key (keys carry 256 bits of randomness)
    label         TEXT NOT NULL DEFAULT '',
    expires_at    TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    revoked_by    TEXT,
    last_used_at  TIMESTAMPTZ,
    last_used_ip  TEXT,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS int_keys_client ON int_keys(client_id);

-- One row per gateway request, including refused ones (no request bodies, no keys).
CREATE TABLE IF NOT EXISTS int_usage (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    client_id     UUID REFERENCES int_clients(id) ON DELETE CASCADE,
    key_prefix    TEXT,
    method        TEXT NOT NULL,
    path          TEXT NOT NULL,
    status        INTEGER NOT NULL,
    error_code    TEXT,
    duration_ms   INTEGER NOT NULL,
    response_bytes INTEGER,
    request_id    TEXT,
    ip            TEXT
);
CREATE INDEX IF NOT EXISTS int_usage_client_at ON int_usage(client_id, at DESC);
CREATE INDEX IF NOT EXISTS int_usage_at ON int_usage(at DESC);

-- What a market snapshot contained, so a signal can cite the evidence it was made from.
CREATE TABLE IF NOT EXISTS int_snapshots (
    id            UUID PRIMARY KEY,
    client_id     UUID NOT NULL REFERENCES int_clients(id) ON DELETE CASCADE,
    request_id    TEXT,
    request       JSONB NOT NULL,
    summary       JSONB NOT NULL,                   -- per instrument and timeframe: last bar time, quality, counts
    output_chars  INTEGER NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS int_snapshots_client ON int_snapshots(client_id, created_at DESC);

-- Candidate signals from external applications. A signal is never an order and never authorizes one.
CREATE TABLE IF NOT EXISTS int_signals (
    id            UUID PRIMARY KEY,
    client_id     UUID NOT NULL REFERENCES int_clients(id) ON DELETE CASCADE,
    request_id    TEXT NOT NULL,
    instrument_id UUID NOT NULL REFERENCES cfg_instruments(id),
    account_id    UUID REFERENCES cfg_accounts(id),
    strategy_ref  TEXT,
    direction     TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT', 'FLAT')),
    entry         NUMERIC(38, 12),
    entry_condition TEXT,
    stop_loss     NUMERIC(38, 12),
    targets       NUMERIC(38, 12)[] NOT NULL DEFAULT '{}',
    strength      NUMERIC(6, 4) CHECK (strength IS NULL OR (strength >= 0 AND strength <= 1)),
    confidence_kind TEXT NOT NULL DEFAULT 'SUBJECTIVE' CHECK (confidence_kind IN ('SUBJECTIVE', 'CALIBRATED', 'NONE')),
    valid_until   TIMESTAMPTZ NOT NULL,
    invalidation  TEXT,
    evidence      JSONB NOT NULL DEFAULT '[]',
    opposing_evidence JSONB NOT NULL DEFAULT '[]',
    provenance    JSONB NOT NULL,                   -- application, agent, model, model_version, input_snapshot_id, ...
    status        TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'VALIDATED', 'REJECTED', 'EXPIRED', 'WITHDRAWN')),
    risk_decision TEXT CHECK (risk_decision IN ('APPROVED', 'REJECTED', 'REQUIRES_APPROVAL', 'UNAVAILABLE')),
    risk_reasons  JSONB NOT NULL DEFAULT '[]',
    risk_checked_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, request_id)
);
CREATE INDEX IF NOT EXISTS int_signals_created ON int_signals(created_at DESC);

CREATE TABLE IF NOT EXISTS int_webhooks (
    id            UUID PRIMARY KEY,
    client_id     UUID NOT NULL REFERENCES int_clients(id) ON DELETE CASCADE,
    url           TEXT NOT NULL CHECK (url ~ '^https://' OR url ~ '^http://'),
    events        TEXT[] NOT NULL,
    secret        BYTEA NOT NULL,                   -- signing secret, encrypted with the credential key
    status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    timeout_seconds INTEGER NOT NULL DEFAULT 10 CHECK (timeout_seconds BETWEEN 1 AND 30),
    max_attempts  INTEGER NOT NULL DEFAULT 6 CHECK (max_attempts BETWEEN 1 AND 20),
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At-least-once delivery; event_id is stable across retries so receivers can drop duplicates.
CREATE TABLE IF NOT EXISTS int_webhook_deliveries (
    id            BIGSERIAL PRIMARY KEY,
    webhook_id    UUID NOT NULL REFERENCES int_webhooks(id) ON DELETE CASCADE,
    event_id      UUID NOT NULL,
    event         TEXT NOT NULL,
    payload       JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'FAILED')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_status   INTEGER,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at       TIMESTAMPTZ,
    UNIQUE (webhook_id, event_id)
);
CREATE INDEX IF NOT EXISTS int_webhook_pending ON int_webhook_deliveries(next_at) WHERE status IN ('PENDING', 'SENDING');

-- Which scan jobs have had their completion event sent (one notice per job).
CREATE TABLE IF NOT EXISTS int_job_notices (
    run_id        UUID PRIMARY KEY,
    status        TEXT NOT NULL,
    noticed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
