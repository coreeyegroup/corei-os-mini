-- 021: every symbol each source offers (its catalogue), loaded from the provider:
--   MT5 at every login (symbol list and rules), Upstox from its daily instrument file, FIX from a
--   SecurityListRequest. Picking from it creates collection plans; suggested_instrument links the same
--   instrument across sources. Idempotent.
CREATE TABLE IF NOT EXISTS md_symbol_catalog (
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    broker_symbol TEXT NOT NULL CHECK (length(broker_symbol) BETWEEN 1 AND 64),
    display_symbol TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    asset_class TEXT NOT NULL DEFAULT 'OTHER',
    exchange TEXT NOT NULL DEFAULT '',
    segment TEXT NOT NULL DEFAULT '',
    instrument_type TEXT NOT NULL DEFAULT '',
    base_currency TEXT NOT NULL DEFAULT '',
    quote_currency TEXT NOT NULL DEFAULT '',
    digits INT,
    tick_size NUMERIC,
    lot_size NUMERIC,
    volume_min NUMERIC,
    volume_step NUMERIC,
    contract_size NUMERIC,
    expiry DATE,
    strike NUMERIC,
    underlying TEXT NOT NULL DEFAULT '',
    suggested_instrument TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('MT5_LOGIN', 'UPSTOX_FILE', 'FIX_SECURITY_LIST', 'MANUAL')),
    refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, broker_symbol)
);
CREATE INDEX IF NOT EXISTS md_symbol_catalog_class ON md_symbol_catalog (account_id, asset_class, exchange);
CREATE INDEX IF NOT EXISTS md_symbol_catalog_search ON md_symbol_catalog (account_id, lower(display_symbol) text_pattern_ops);

CREATE TABLE IF NOT EXISTS md_catalog_runs (
    id BIGSERIAL PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    ok BOOLEAN,
    symbols INT NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS md_catalog_runs_account ON md_catalog_runs (account_id, started_at DESC);
