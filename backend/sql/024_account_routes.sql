-- Routes (DEC-69): the ways COREI can reach one MT5 account, in order, with automatic failover.
-- An account with no routes uses its server's method (unchanged behaviour). An account with routes uses them in
-- priority order: when the active route fails, the collector moves to the next one, raises ROUTE_FALLBACK, and goes
-- back to the first route at the next reconnect after FAILBACK_SECONDS.
--   DIRECT           the direct SDK, to the server's addresses
--   TERMINAL_BRIDGE  the official terminal + COREI bridge: Windows VPS, Windows PC (direct or SSH tunnel), Docker
--   TERMINAL_PUSH    the COREI EA inside a terminal sends to COREI (optional method)
-- Each bridge/push route has its own token (write-only, encrypted like every credential).
CREATE TABLE IF NOT EXISTS cfg_account_routes (
    id            UUID PRIMARY KEY,
    account_id    UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    label         TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
    method        TEXT NOT NULL CHECK (method IN ('DIRECT', 'TERMINAL_BRIDGE', 'TERMINAL_PUSH')),
    location      TEXT NOT NULL DEFAULT 'OTHER'
                  CHECK (location IN ('WINDOWS_VPS', 'WINDOWS_PC', 'DOCKER', 'COREI_SERVER', 'OTHER')),
    host          TEXT CHECK (host IS NULL OR length(host) BETWEEN 1 AND 253),
    port          INTEGER CHECK (port IS NULL OR port BETWEEN 1 AND 65535),
    fingerprint   TEXT CHECK (fingerprint IS NULL OR fingerprint ~ '^([0-9A-F]{2}:){31}[0-9A-F]{2}$'),
    priority      INTEGER NOT NULL DEFAULT 10 CHECK (priority BETWEEN 0 AND 1000),
    enabled       BOOLEAN NOT NULL DEFAULT true,
    token         BYTEA,
    token_key_id  TEXT,
    token_set_at  TIMESTAMPTZ,
    last_ok_at    TIMESTAMPTZ,
    last_error    TEXT,
    last_error_at TIMESTAMPTZ,
    active_since  TIMESTAMPTZ,
    notes         TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 500),
    version       INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (method <> 'TERMINAL_BRIDGE' OR (host IS NOT NULL AND port IS NOT NULL AND fingerprint IS NOT NULL)),
    CONSTRAINT cfg_account_routes_label UNIQUE (account_id, label)
);
CREATE INDEX IF NOT EXISTS cfg_account_routes_account ON cfg_account_routes(account_id, priority);

-- The collector's state shows the waiting push session and a route switch; the route in use is shown with it.
ALTER TABLE md_collector_status DROP CONSTRAINT IF EXISTS md_collector_status_state_check;
ALTER TABLE md_collector_status ADD CONSTRAINT md_collector_status_state_check CHECK (state IN
    ('STARTING', 'CONNECTED', 'RECONNECTING', 'STOPPED', 'PAUSED', 'FAILED', 'UNSUPPORTED', 'WAITING_FOR_TERMINAL', 'SWITCHING_ROUTE'));
ALTER TABLE md_collector_status ADD COLUMN IF NOT EXISTS route TEXT;
