-- Bridge nodes (DEC-75): a Windows machine (VPS, PC) or server running the COREI MT5 bridge, registered once and used
-- by any number of MT5 accounts. The machine enrols itself with a one-time code (Setup › Bridge nodes): it sends its
-- certificate fingerprint and token over HTTPS, so nobody copies them by hand, and accounts pick the node instead of
-- repeating host, port, fingerprint and token on every route. The token is stored encrypted like every credential.
CREATE TABLE IF NOT EXISTS cfg_bridge_nodes (
    id                 UUID PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
    location           TEXT NOT NULL DEFAULT 'WINDOWS_VPS' CHECK (location IN ('WINDOWS_VPS', 'WINDOWS_PC', 'WINDOWS_SERVER', 'OTHER')),
    mode               TEXT NOT NULL DEFAULT 'DIRECT' CHECK (mode IN ('DIRECT', 'TUNNEL')),
    host               TEXT CHECK (host IS NULL OR length(host) <= 253),
    port               INTEGER CHECK (port IS NULL OR port BETWEEN 1 AND 65535),
    tunnel_port        INTEGER CHECK (tunnel_port IS NULL OR tunnel_port BETWEEN 1024 AND 65535),
    fingerprint        TEXT CHECK (fingerprint IS NULL OR fingerprint ~ '^([0-9A-F]{2}:){31}[0-9A-F]{2}$'),
    token              BYTEA,
    token_key_id       TEXT,
    token_set_at       TIMESTAMPTZ,
    ssh_public_key     TEXT CHECK (ssh_public_key IS NULL OR length(ssh_public_key) <= 800),
    max_terminals      INTEGER NOT NULL DEFAULT 20 CHECK (max_terminals BETWEEN 1 AND 500),
    default_for_new    BOOLEAN NOT NULL DEFAULT false,
    bridge_version     TEXT NOT NULL DEFAULT '',
    machine            TEXT NOT NULL DEFAULT '',
    remote_ip          TEXT,
    status             TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ACTIVE', 'UNREACHABLE', 'DISABLED')),
    enroll_hash        TEXT,
    enroll_expires_at  TIMESTAMPTZ,
    enrolled_at        TIMESTAMPTZ,
    last_check_at      TIMESTAMPTZ,
    last_check_ok      BOOLEAN,
    last_check         JSONB,
    notes              TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 500),
    version            INTEGER NOT NULL DEFAULT 1,
    created_by         TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         TEXT NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS cfg_bridge_nodes_one_default ON cfg_bridge_nodes (default_for_new) WHERE default_for_new;
CREATE UNIQUE INDEX IF NOT EXISTS cfg_bridge_nodes_tunnel_port ON cfg_bridge_nodes (tunnel_port) WHERE tunnel_port IS NOT NULL;

ALTER TABLE cfg_account_routes ADD COLUMN IF NOT EXISTS node_id UUID REFERENCES cfg_bridge_nodes(id) ON DELETE RESTRICT;
ALTER TABLE cfg_account_routes DROP CONSTRAINT IF EXISTS cfg_account_routes_check;
ALTER TABLE cfg_account_routes DROP CONSTRAINT IF EXISTS cfg_account_routes_bridge_target;
ALTER TABLE cfg_account_routes ADD CONSTRAINT cfg_account_routes_bridge_target CHECK (
    method <> 'TERMINAL_BRIDGE' OR node_id IS NOT NULL OR (host IS NOT NULL AND port IS NOT NULL AND fingerprint IS NOT NULL));
CREATE INDEX IF NOT EXISTS cfg_account_routes_node ON cfg_account_routes(node_id) WHERE node_id IS NOT NULL;

ALTER TABLE cfg_credential_use DROP CONSTRAINT IF EXISTS cfg_credential_use_owner_check;
ALTER TABLE cfg_credential_use ADD CONSTRAINT cfg_credential_use_owner_check CHECK (owner IN ('account', 'connection', 'bridge_node'));
