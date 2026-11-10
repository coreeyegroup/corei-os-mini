-- Additive storage. The existing provider/connection/account identities remain canonical.
CREATE TABLE corei_mt5_catalogs (
 id UUID PRIMARY KEY,
 sha256 TEXT NOT NULL UNIQUE CHECK (sha256 ~ '^[a-f0-9]{64}$'),
 source_bytes BYTEA NOT NULL CHECK (octet_length(source_bytes) BETWEEN 32 AND 1048576),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE corei_mt5_access_points (
 id UUID PRIMARY KEY,
 catalog_id UUID NOT NULL REFERENCES corei_mt5_catalogs(id),
 server_name TEXT NOT NULL,
 name TEXT NOT NULL,
 host TEXT NOT NULL,
 port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
 UNIQUE(catalog_id, server_name, name, host, port)
);
CREATE TABLE corei_broker_secrets (
 id UUID PRIMARY KEY,
 ciphertext BYTEA NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE corei_broker_account_settings (
 account_id UUID PRIMARY KEY REFERENCES corei_accounts(id),
 secret_id UUID NOT NULL UNIQUE REFERENCES corei_broker_secrets(id),
 server_name TEXT NOT NULL,
 login TEXT NOT NULL CHECK (login ~ '^[1-9][0-9]{0,19}$'),
 platform TEXT NOT NULL DEFAULT 'MT5' CHECK (platform = 'MT5'),
 access_point_id UUID NOT NULL REFERENCES corei_mt5_access_points(id),
 version INTEGER NOT NULL DEFAULT 1,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(server_name, login)
);
