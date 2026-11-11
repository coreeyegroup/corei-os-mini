-- 0.12.0: what COREI read from the broker at each login, and the infrastructure
-- console's data-edit log. Idempotent.

-- Every read-only login: where it connected, what the broker said, what was stored.
CREATE TABLE IF NOT EXISTS cfg_broker_reads (
    id BIGSERIAL PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES cfg_connections(id) ON DELETE CASCADE,
    requested_by TEXT NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 120),
    credential_level TEXT NOT NULL CHECK (credential_level IN ('FULL', 'READ_ONLY')),
    connected BOOLEAN NOT NULL,
    host TEXT,
    port INTEGER CHECK (port IS NULL OR port BETWEEN 1 AND 65535),
    connect_ms INTEGER,
    attempts JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(attempts) = 'array'),
    server_time_offset_minutes INTEGER,
    account_facts JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(account_facts) = 'object'),
    stored JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(stored) = 'object'),
    symbols_available INTEGER NOT NULL DEFAULT 0,
    symbols_stored INTEGER NOT NULL DEFAULT 0,
    symbols_unmapped JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(symbols_unmapped) = 'array'),
    sdk_version TEXT,
    error TEXT CHECK (error IS NULL OR length(error) <= 500),
    read_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cfg_broker_reads_account ON cfg_broker_reads (account_id, read_at DESC);

-- Raw symbol facts as the SDK returned them, next to the normalised specification.
ALTER TABLE cfg_symbol_specs ADD COLUMN IF NOT EXISTS raw JSONB;
ALTER TABLE cfg_symbol_specs ADD COLUMN IF NOT EXISTS read_id BIGINT REFERENCES cfg_broker_reads(id) ON DELETE SET NULL;

-- Infrastructure console: every row changed through the table editor, before and after.
CREATE TABLE IF NOT EXISTS infra_data_edits (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
    table_name TEXT NOT NULL CHECK (length(table_name) BETWEEN 1 AND 128),
    action TEXT NOT NULL CHECK (action IN ('UPDATE', 'INSERT', 'DELETE')),
    row_key JSONB NOT NULL,
    before JSONB,
    after JSONB,
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 500),
    edited_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS infra_data_edits_table ON infra_data_edits (table_name, edited_at DESC);
DROP TRIGGER IF EXISTS infra_data_edits_append_only ON infra_data_edits;
CREATE TRIGGER infra_data_edits_append_only BEFORE UPDATE OR DELETE ON infra_data_edits
    FOR EACH ROW EXECUTE FUNCTION cfg_append_only();

INSERT INTO cfg_policies (key, value) VALUES ('infra_edit_controlled_tables', 'false'::jsonb) ON CONFLICT (key) DO NOTHING;
