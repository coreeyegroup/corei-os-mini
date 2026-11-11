-- Institutional controls from the 11 Nov 2026 provider/connection/account audit (DEC-29..36).
-- Everything is additive and optional so no option is closed: facts typed by people carry
-- their source, facts read from the broker carry the time they were read.

-- ---------- Archive instead of delete ----------
ALTER TABLE cfg_providers DROP CONSTRAINT IF EXISTS cfg_providers_status_check;
ALTER TABLE cfg_providers ADD CONSTRAINT cfg_providers_status_check CHECK (status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED'));
ALTER TABLE cfg_connections DROP CONSTRAINT IF EXISTS cfg_connections_status_check;
ALTER TABLE cfg_connections ADD CONSTRAINT cfg_connections_status_check CHECK (status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED'));
ALTER TABLE cfg_accounts DROP CONSTRAINT IF EXISTS cfg_accounts_status_check;
ALTER TABLE cfg_accounts ADD CONSTRAINT cfg_accounts_status_check CHECK (status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED'));

-- ---------- Provider: the firm behind the brand ----------
ALTER TABLE cfg_providers
    ADD COLUMN IF NOT EXISTS legal_entity TEXT NOT NULL DEFAULT '' CHECK (length(legal_entity) <= 200),
    ADD COLUMN IF NOT EXISTS regulator TEXT NOT NULL DEFAULT '' CHECK (length(regulator) <= 120),
    ADD COLUMN IF NOT EXISTS licence_number TEXT NOT NULL DEFAULT '' CHECK (length(licence_number) <= 80),
    ADD COLUMN IF NOT EXISTS jurisdiction TEXT NOT NULL DEFAULT '' CHECK (length(jurisdiction) <= 80),
    ADD COLUMN IF NOT EXISTS execution_model TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (execution_model IN ('UNKNOWN', 'MARKET_MAKER', 'STP', 'ECN', 'EXCHANGE', 'MIXED')),
    ADD COLUMN IF NOT EXISTS website TEXT NOT NULL DEFAULT '' CHECK (length(website) <= 200),
    ADD COLUMN IF NOT EXISTS support_contact TEXT NOT NULL DEFAULT '' CHECK (length(support_contact) <= 300),
    ADD COLUMN IF NOT EXISTS dealing_contact TEXT NOT NULL DEFAULT '' CHECK (length(dealing_contact) <= 300),
    ADD COLUMN IF NOT EXISTS exposure_limit NUMERIC(20, 2) CHECK (exposure_limit IS NULL OR exposure_limit > 0),
    ADD COLUMN IF NOT EXISTS exposure_currency TEXT NOT NULL DEFAULT '' CHECK (exposure_currency = '' OR exposure_currency ~ '^[A-Z]{3}$'),
    ADD COLUMN IF NOT EXISTS holidays JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(holidays) = 'array');

-- ---------- Connection: method, clock, limits, network ----------
ALTER TABLE cfg_connections
    -- MT5: MTAPI_DIRECT (licensed SDK, no terminal) or TERMINAL_BRIDGE; APIs: NATIVE.
    ADD COLUMN IF NOT EXISTS adapter TEXT NOT NULL DEFAULT 'DEFAULT'
        CHECK (adapter IN ('DEFAULT', 'MTAPI_DIRECT', 'TERMINAL_BRIDGE', 'NATIVE')),
    ADD COLUMN IF NOT EXISTS server_utc_offset_minutes INTEGER CHECK (server_utc_offset_minutes BETWEEN -720 AND 840),
    ADD COLUMN IF NOT EXISTS dst_rule TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (dst_rule IN ('UNKNOWN', 'NONE', 'US', 'EU')),
    ADD COLUMN IF NOT EXISTS clock_source TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (clock_source IN ('UNKNOWN', 'MANUAL', 'SERVER_FILE', 'BROKER_LOGIN')),
    ADD COLUMN IF NOT EXISTS max_orders_per_second NUMERIC(8, 2) CHECK (max_orders_per_second IS NULL OR max_orders_per_second > 0),
    ADD COLUMN IF NOT EXISTS max_order_burst INTEGER CHECK (max_order_burst IS NULL OR max_order_burst BETWEEN 1 AND 1000),
    ADD COLUMN IF NOT EXISTS registered_egress_ip INET,
    ADD COLUMN IF NOT EXISTS maintenance JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(maintenance) = 'array');

-- Live state written by the adapter; one row per connection.
CREATE TABLE IF NOT EXISTS cfg_connection_status (
    connection_id UUID PRIMARY KEY REFERENCES cfg_connections(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('DISCONNECTED', 'CONNECTING', 'CONNECTED', 'DEGRADED', 'FAILED')),
    current_host TEXT,
    current_port INTEGER,
    last_login_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    failovers_today INTEGER NOT NULL DEFAULT 0,
    server_time_offset_ms INTEGER,
    last_error TEXT CHECK (last_error IS NULL OR length(last_error) <= 300),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Account: facts the broker decides, ownership, limits ----------
ALTER TABLE cfg_accounts
    ADD COLUMN IF NOT EXISTS margin_mode TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (margin_mode IN ('UNKNOWN', 'HEDGING', 'NETTING', 'EXCHANGE')),
    ADD COLUMN IF NOT EXISTS margin_mode_source TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (margin_mode_source IN ('UNKNOWN', 'MANUAL', 'BROKER')),
    ADD COLUMN IF NOT EXISTS broker_group TEXT NOT NULL DEFAULT '' CHECK (length(broker_group) <= 120),
    ADD COLUMN IF NOT EXISTS leverage INTEGER CHECK (leverage IS NULL OR leverage BETWEEN 1 AND 10000),
    ADD COLUMN IF NOT EXISTS stop_out_level NUMERIC(8, 2) CHECK (stop_out_level IS NULL OR stop_out_level >= 0),
    ADD COLUMN IF NOT EXISTS max_open_orders INTEGER CHECK (max_open_orders IS NULL OR max_open_orders > 0),
    ADD COLUMN IF NOT EXISTS max_open_positions INTEGER CHECK (max_open_positions IS NULL OR max_open_positions > 0),
    ADD COLUMN IF NOT EXISTS broker_facts_read_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expires_on DATE,
    ADD COLUMN IF NOT EXISTS password_changed_on DATE,
    ADD COLUMN IF NOT EXISTS relationship TEXT NOT NULL DEFAULT 'OWN' CHECK (relationship IN ('OWN', 'FIRM', 'CLIENT')),
    ADD COLUMN IF NOT EXISTS mandate_reference TEXT NOT NULL DEFAULT '' CHECK (length(mandate_reference) <= 200),
    ADD COLUMN IF NOT EXISTS mandate_expires_on DATE,
    ADD COLUMN IF NOT EXISTS other_users TEXT NOT NULL DEFAULT '' CHECK (length(other_users) <= 300);

CREATE TABLE IF NOT EXISTS cfg_account_groups (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,31}$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 500),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cfg_account_group_members (
    group_id UUID NOT NULL REFERENCES cfg_account_groups(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, account_id)
);

-- ---------- Symbols: per-account override and broker specification snapshots ----------
ALTER TABLE cfg_symbol_mappings ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES cfg_accounts(id) ON DELETE CASCADE;
ALTER TABLE cfg_symbol_mappings DROP CONSTRAINT IF EXISTS cfg_symbol_mappings_connection_id_broker_symbol_key;
ALTER TABLE cfg_symbol_mappings DROP CONSTRAINT IF EXISTS cfg_symbol_mappings_connection_id_instrument_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS cfg_symbol_mappings_symbol_scope ON cfg_symbol_mappings
    (connection_id, COALESCE(account_id, '00000000-0000-0000-0000-000000000000'::uuid), broker_symbol);
CREATE UNIQUE INDEX IF NOT EXISTS cfg_symbol_mappings_instrument_scope ON cfg_symbol_mappings
    (connection_id, COALESCE(account_id, '00000000-0000-0000-0000-000000000000'::uuid), instrument_id);

CREATE TABLE IF NOT EXISTS cfg_symbol_specs (
    id BIGSERIAL PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    broker_symbol TEXT NOT NULL CHECK (length(broker_symbol) BETWEEN 1 AND 64),
    spec JSONB NOT NULL CHECK (jsonb_typeof(spec) = 'object'),
    source TEXT NOT NULL DEFAULT 'BROKER' CHECK (source IN ('BROKER', 'MANUAL')),
    read_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cfg_symbol_specs_latest ON cfg_symbol_specs (account_id, broker_symbol, read_at DESC);

-- ---------- Credentials: master and investor per account, key id, use log ----------
ALTER TABLE cfg_credentials DROP CONSTRAINT IF EXISTS cfg_credentials_account_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS cfg_credentials_account_level ON cfg_credentials (account_id, access_level) WHERE account_id IS NOT NULL;
ALTER TABLE cfg_credentials ADD COLUMN IF NOT EXISTS key_id TEXT NOT NULL DEFAULT '' CHECK (length(key_id) <= 64);

CREATE TABLE IF NOT EXISTS cfg_credential_use (
    id BIGSERIAL PRIMARY KEY,
    credential_id UUID NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('account', 'connection')),
    owner_id UUID NOT NULL,
    used_by TEXT NOT NULL CHECK (length(used_by) BETWEEN 1 AND 120),
    purpose TEXT NOT NULL CHECK (length(purpose) BETWEEN 1 AND 200),
    used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cfg_credential_use_owner ON cfg_credential_use (owner_id, used_at DESC);
CREATE OR REPLACE FUNCTION cfg_append_only() RETURNS trigger AS $cfg_ao$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$cfg_ao$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS cfg_credential_use_no_change ON cfg_credential_use;
CREATE TRIGGER cfg_credential_use_no_change BEFORE UPDATE OR DELETE ON cfg_credential_use
    FOR EACH ROW EXECUTE FUNCTION cfg_append_only();

-- Broker session tokens (e.g. Upstox OAuth access token, expires 03:30 next day).
CREATE TABLE IF NOT EXISTS cfg_session_tokens (
    account_id UUID PRIMARY KEY REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL,
    key_id TEXT NOT NULL DEFAULT '',
    obtained_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    obtained_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cfg_oauth_states (
    state_hash BYTEA PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Four-eyes change requests ----------
CREATE TABLE IF NOT EXISTS cfg_change_requests (
    id UUID PRIMARY KEY,
    entity TEXT NOT NULL CHECK (entity IN ('connections', 'accounts', 'credential')),
    entity_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (length(action) BETWEEN 1 AND 60),
    summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 500),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    secret BYTEA,
    base_version INTEGER,
    reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 500),
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPLIED', 'REJECTED', 'WITHDRAWN', 'FAILED')),
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    decision_note TEXT NOT NULL DEFAULT '' CHECK (length(decision_note) <= 500)
);
CREATE INDEX IF NOT EXISTS cfg_change_requests_open ON cfg_change_requests (status, requested_at DESC);

-- ---------- Policies (switchable, audited) ----------
CREATE TABLE IF NOT EXISTS cfg_policies (
    key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,63}$'),
    value JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO cfg_policies (key, value) VALUES
    ('four_eyes_live_changes', 'true'::jsonb),
    ('reauth_minutes', '5'::jsonb),
    ('expiry_warning_days', '7'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- ---------- Step-up (re-authentication) on sessions ----------
ALTER TABLE cfg_sessions ADD COLUMN IF NOT EXISTS reauth_until TIMESTAMPTZ;

-- ---------- Alerts ----------
CREATE TABLE IF NOT EXISTS cfg_alert_channels (
    id UUID PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('EMAIL', 'TELEGRAM', 'WEBHOOK')),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    target TEXT NOT NULL CHECK (length(target) BETWEEN 1 AND 500),
    secret BYTEA,
    events TEXT[] NOT NULL DEFAULT ARRAY['LIVE_CHANGE', 'APPROVAL_NEEDED', 'ROUTE_FALLBACK', 'LOGIN_FAILED', 'EXPIRY', 'ORDER_SENDING']::TEXT[],
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cfg_alert_outbox (
    id BIGSERIAL PRIMARY KEY,
    event TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    title TEXT NOT NULL CHECK (length(title) <= 200),
    body TEXT NOT NULL CHECK (length(body) <= 2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel_id UUID REFERENCES cfg_alert_channels(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS cfg_alert_outbox_pending ON cfg_alert_outbox (status, id);

-- ---------- Per-user screen preferences (window positions, filters) ----------
CREATE TABLE IF NOT EXISTS cfg_user_prefs (
    username TEXT NOT NULL,
    key TEXT NOT NULL CHECK (key ~ '^[A-Za-z0-9._-]{1,120}$'),
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (username, key)
);
