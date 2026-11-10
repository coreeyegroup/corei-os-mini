-- Configuration foundation (Blueprint v1.2 §8, §11, §13.3, §17.2).
-- Owner decision 10 Nov 2026: the earlier registries are removed and replaced; their
-- records (including saved broker passwords) are deleted, not migrated.
-- Paper runs and market-data assessments (001, 002) are untouched.

DROP TABLE IF EXISTS corei_symbol_mappings, corei_broker_account_settings, corei_broker_secrets,
    corei_mt5_access_points, corei_mt5_catalogs, corei_connector_profiles, corei_accounts,
    corei_connections, corei_instruments, corei_providers CASCADE;
DROP FUNCTION IF EXISTS corei_block_live_accounts() CASCADE;
DROP FUNCTION IF EXISTS corei_block_live_profiles() CASCADE;

-- Broker, exchange or data vendor.
CREATE TABLE cfg_providers (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,31}$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    kind TEXT NOT NULL CHECK (kind IN ('BROKER', 'EXCHANGE', 'DATA_VENDOR')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One session endpoint at a provider. Environment lives here so every account
-- under it inherits the same DEMO or LIVE classification.
CREATE TABLE cfg_connections (
    id UUID PRIMARY KEY,
    provider_id UUID NOT NULL REFERENCES cfg_providers(id) ON DELETE RESTRICT,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,31}$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    platform TEXT NOT NULL CHECK (platform IN ('MT5', 'REST_API')),
    environment TEXT NOT NULL CHECK (environment IN ('DEMO', 'LIVE')),
    purpose TEXT NOT NULL CHECK (purpose IN ('MARKET_DATA', 'TRADING', 'BOTH')),
    settings JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Broker-issued account reachable through one connection.
CREATE TABLE cfg_accounts (
    id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES cfg_connections(id) ON DELETE RESTRICT,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,31}$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    external_account_id TEXT NOT NULL CHECK (length(btrim(external_account_id)) BETWEEN 1 AND 64),
    base_currency TEXT NOT NULL CHECK (base_currency ~ '^[A-Z0-9]{3,10}$'),
    trading_permission TEXT NOT NULL DEFAULT 'DISABLED'
        CHECK (trading_permission IN ('DISABLED', 'READ_ONLY', 'TRADING')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (connection_id, external_account_id)
);

-- Canonical COREI instrument. Decimal values are exact NUMERIC, never floating point.
CREATE TABLE cfg_instruments (
    id UUID PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE CHECK (symbol ~ '^[A-Z0-9][A-Z0-9._/-]{0,31}$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    asset_class TEXT NOT NULL CHECK (asset_class IN
        ('FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION')),
    base_currency TEXT NOT NULL CHECK (base_currency ~ '^[A-Z0-9]{2,10}$'),
    quote_currency TEXT NOT NULL CHECK (quote_currency ~ '^[A-Z0-9]{2,10}$'),
    price_precision SMALLINT NOT NULL CHECK (price_precision BETWEEN 0 AND 12),
    quantity_precision SMALLINT NOT NULL CHECK (quantity_precision BETWEEN 0 AND 12),
    tick_size NUMERIC(38, 12) NOT NULL CHECK (tick_size > 0),
    min_quantity NUMERIC(38, 12) NOT NULL CHECK (min_quantity > 0),
    quantity_step NUMERIC(38, 12) NOT NULL CHECK (quantity_step > 0),
    contract_size NUMERIC(38, 12) NOT NULL CHECK (contract_size > 0),
    contract_unit TEXT NOT NULL CHECK (length(btrim(contract_unit)) BETWEEN 1 AND 32),
    trading_session TEXT NOT NULL DEFAULT '' CHECK (length(trading_session) <= 200),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Broker symbol for a canonical instrument on one connection.
CREATE TABLE cfg_symbol_mappings (
    id UUID PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES cfg_connections(id) ON DELETE RESTRICT,
    instrument_id UUID NOT NULL REFERENCES cfg_instruments(id) ON DELETE RESTRICT,
    broker_symbol TEXT NOT NULL CHECK (length(btrim(broker_symbol)) BETWEEN 1 AND 64),
    usage TEXT NOT NULL CHECK (usage IN ('MARKET_DATA', 'TRADING', 'BOTH')),
    broker_contract_size NUMERIC(38, 12) CHECK (broker_contract_size > 0),
    broker_price_digits SMALLINT CHECK (broker_price_digits BETWEEN 0 AND 12),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (connection_id, broker_symbol),
    UNIQUE (connection_id, instrument_id)
);

-- Encrypted secrets. Exactly one owner: a connection (API keys) or an account (MT5 login).
CREATE TABLE cfg_credentials (
    id UUID PRIMARY KEY,
    connection_id UUID UNIQUE REFERENCES cfg_connections(id) ON DELETE CASCADE,
    account_id UUID UNIQUE REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('API_KEY', 'OAUTH_CLIENT', 'MT5_LOGIN')),
    access_level TEXT NOT NULL DEFAULT 'FULL' CHECK (access_level IN ('FULL', 'READ_ONLY')),
    ciphertext BYTEA NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((connection_id IS NULL) <> (account_id IS NULL))
);

-- Firm-wide order-sending switch per environment (Blueprint §13.3). Off by default.
CREATE TABLE cfg_execution_controls (
    environment TEXT PRIMARY KEY CHECK (environment IN ('DEMO', 'LIVE')),
    trading_enabled BOOLEAN NOT NULL DEFAULT false,
    reason TEXT NOT NULL DEFAULT 'Initial state: off',
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO cfg_execution_controls (environment) VALUES ('DEMO'), ('LIVE');

-- Append-only record of configuration and control changes. Secrets are never stored here.
CREATE TABLE cfg_audit_log (
    id BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_state JSONB,
    after_state JSONB,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX cfg_audit_log_entity ON cfg_audit_log (entity, entity_id, id DESC);

CREATE FUNCTION cfg_audit_log_append_only() RETURNS trigger AS $cfg_audit$
BEGIN
    RAISE EXCEPTION 'cfg_audit_log is append-only';
END;
$cfg_audit$ LANGUAGE plpgsql;
CREATE TRIGGER cfg_audit_log_no_change BEFORE UPDATE OR DELETE ON cfg_audit_log
    FOR EACH ROW EXECUTE FUNCTION cfg_audit_log_append_only();
