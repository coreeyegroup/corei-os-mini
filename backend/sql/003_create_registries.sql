CREATE TABLE corei_providers (
 id UUID PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('BROKER','EXCHANGE','DATA_VENDOR')),
 enabled BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE corei_connections (
 id UUID PRIMARY KEY, provider_id UUID NOT NULL REFERENCES corei_providers(id), name TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('DATA','EXECUTION','BOTH')), enabled BOOLEAN NOT NULL DEFAULT false,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(provider_id,name)
);
CREATE TABLE corei_accounts (
 id UUID PRIMARY KEY, connection_id UUID NOT NULL REFERENCES corei_connections(id), name TEXT NOT NULL,
 external_reference TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('PAPER','SANDBOX','LIVE')),
 enabled BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(connection_id,external_reference)
);
CREATE TABLE corei_instruments (
 id UUID PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, asset_class TEXT NOT NULL CHECK(asset_class IN ('FX','METAL','CRYPTO','EQUITY','FUTURE','OPTION','OTHER')),
 description TEXT NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE corei_symbol_mappings (
 id UUID PRIMARY KEY, connection_id UUID NOT NULL REFERENCES corei_connections(id),
 instrument_id UUID NOT NULL REFERENCES corei_instruments(id), native_symbol TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(connection_id,native_symbol)
);
CREATE INDEX corei_connections_provider_idx ON corei_connections(provider_id);
CREATE INDEX corei_accounts_connection_idx ON corei_accounts(connection_id);
CREATE INDEX corei_mappings_instrument_idx ON corei_symbol_mappings(instrument_id);
