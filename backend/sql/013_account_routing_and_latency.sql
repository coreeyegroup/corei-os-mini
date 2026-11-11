-- How each MT5 account chooses among its server's addresses (access points).
--   SERVER  : the connection's ticked addresses, in the connection's order (default)
--   FASTEST : the connection's ticked addresses, fastest measured first
--   ACCOUNT : the account's own ordered list, chosen from the server's addresses
-- The account's own list is kept even when the mode is switched back, so switching is
-- never lossy. Addresses later removed from the server are flagged, not silently used.
ALTER TABLE cfg_accounts ADD COLUMN IF NOT EXISTS access_mode TEXT NOT NULL DEFAULT 'SERVER'
    CHECK (access_mode IN ('SERVER', 'FASTEST', 'ACCOUNT'));
ALTER TABLE cfg_accounts ADD COLUMN IF NOT EXISTS access_points JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(access_points) = 'array' AND jsonb_array_length(access_points) <= 256);

-- Latency measurements (TCP connect time, no login) from the COREI host to a server's
-- addresses. Append-only history so routing can later use trends, not one sample.
CREATE TABLE IF NOT EXISTS cfg_latency_checks (
    id BIGSERIAL PRIMARY KEY,
    connection_id UUID NOT NULL REFERENCES cfg_connections(id) ON DELETE CASCADE,
    host TEXT NOT NULL CHECK (length(host) BETWEEN 1 AND 253),
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
    reachable BOOLEAN NOT NULL,
    latency_ms NUMERIC(10, 2) CHECK (latency_ms IS NULL OR latency_ms >= 0),
    error TEXT CHECK (error IS NULL OR length(error) <= 200),
    measured_from TEXT NOT NULL CHECK (length(measured_from) <= 253),
    measured_by TEXT NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cfg_latency_checks_latest ON cfg_latency_checks (connection_id, host, port, measured_at DESC);
