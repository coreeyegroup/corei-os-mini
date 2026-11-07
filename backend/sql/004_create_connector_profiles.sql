-- Additive configuration only; existing registry records remain unchanged.
CREATE TABLE corei_connector_profiles (
 id UUID PRIMARY KEY,
 connection_id UUID NOT NULL UNIQUE REFERENCES corei_connections(id),
 name TEXT NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 120),
 connector_type TEXT NOT NULL CHECK(connector_type IN ('MT4_EA','MT5_EA','CTRADER_OPEN_API','BROKER_API','EXCHANGE_API','FIX','DATA_FEED','CUSTOM')),
 adapter_key TEXT NOT NULL CHECK(adapter_key ~ '^[a-z][a-z0-9_.-]{0,63}$'),
 environment TEXT NOT NULL CHECK(environment IN ('PAPER','SANDBOX','LIVE')),
 runtime TEXT NOT NULL CHECK(runtime IN ('WINDOWS_TERMINAL','UBUNTU_WINE_TERMINAL','SERVER_API')),
 server_name TEXT NOT NULL DEFAULT '' CHECK(length(server_name) <= 120),
 enabled BOOLEAN NOT NULL DEFAULT false,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((connector_type IN ('MT4_EA','MT5_EA') AND runtime IN ('WINDOWS_TERMINAL','UBUNTU_WINE_TERMINAL') AND length(btrim(server_name)) > 0)
     OR (connector_type NOT IN ('MT4_EA','MT5_EA') AND runtime = 'SERVER_API'))
);
