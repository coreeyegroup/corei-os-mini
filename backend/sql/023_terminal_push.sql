-- MT5 terminal push (DEC-68): the COREI EA inside an MT5 terminal (Windows PC, Windows VPS or the Docker container)
-- sends quotes, bars and the symbol list to COREI over HTTPS; the collector reads them from this inbox exactly like
-- any other source (quality, bars, journal, failover). Commands for the EA (subscribe, history) wait in the outbox.
CREATE TABLE IF NOT EXISTS md_push_inbox (
    id          BIGSERIAL PRIMARY KEY,
    account_id  UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    line        JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS md_push_inbox_account ON md_push_inbox(account_id, id);
CREATE TABLE IF NOT EXISTS md_push_outbox (
    id          BIGSERIAL PRIMARY KEY,
    account_id  UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    command     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS md_push_outbox_account ON md_push_outbox(account_id, id);
CREATE TABLE IF NOT EXISTS md_push_terminals (
    account_id  UUID PRIMARY KEY REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    login       TEXT,
    server      TEXT,
    company     TEXT,
    build       INTEGER,
    ea_version  TEXT,
    remote_ip   TEXT,
    last_push_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    pushes      BIGINT NOT NULL DEFAULT 0
);
ALTER TABLE cfg_connections DROP CONSTRAINT IF EXISTS cfg_connections_adapter_check;
ALTER TABLE cfg_connections ADD CONSTRAINT cfg_connections_adapter_check CHECK (adapter IN ('DEFAULT', 'MTAPI_DIRECT', 'TERMINAL_BRIDGE', 'TERMINAL_PUSH', 'NATIVE'));
