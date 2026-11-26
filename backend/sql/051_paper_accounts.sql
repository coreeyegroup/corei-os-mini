-- 051 · Paper accounts (release 0.60.0).
-- Why: paper trading was a "book" inside a broker account. You needed a broker login to paper-trade, every paper book
-- started from the same firm-wide capital in the broker account's currency, and paper fills charged no cost. The owner
-- asked for many paper accounts, each with any instrument and any strategy, international forex and Indian markets, and
-- a chosen currency and amount.
-- This makes a paper account a first-class account (environment PAPER) on COREI's built-in simulator connection, with
-- its own currency, starting amount, market scope, costs and price source. FX conversion can go through USD/EUR or use
-- rates a person enters, so a USD or INR account can value positions in any currency.
-- Additive only: existing DEMO/LIVE connections, accounts and paper books inside broker accounts are unchanged.

ALTER TABLE cfg_connections DROP CONSTRAINT IF EXISTS cfg_connections_environment_check;
ALTER TABLE cfg_connections ADD CONSTRAINT cfg_connections_environment_check CHECK (environment IN ('DEMO', 'LIVE', 'PAPER'));
ALTER TABLE cfg_connections DROP CONSTRAINT IF EXISTS cfg_connections_platform_check;
ALTER TABLE cfg_connections ADD CONSTRAINT cfg_connections_platform_check
    CHECK (platform IN ('MT5', 'MT4', 'CTRADER', 'REST_API', 'FIX', 'SIMULATOR'));
ALTER TABLE cfg_providers DROP CONSTRAINT IF EXISTS cfg_providers_kind_check;
ALTER TABLE cfg_providers ADD CONSTRAINT cfg_providers_kind_check CHECK (kind IN (
    'BROKER', 'EXCHANGE', 'ECN', 'PRIME_BROKER', 'LIQUIDITY_PROVIDER', 'FAST_FEED', 'DATA_VENDOR', 'DATA_CENTRE', 'SIMULATOR'));

-- What a paper account is allowed to trade, what it costs, and where its prices come from. Its starting amount and
-- currency are kept in pf_accounts (portfolio capital), like every account.
CREATE TABLE IF NOT EXISTS pf_paper_accounts (
    account_id        UUID PRIMARY KEY REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    scope             TEXT NOT NULL DEFAULT 'ANY' CHECK (scope IN ('ANY', 'FOREX', 'INDIA', 'CUSTOM')),
    asset_classes     TEXT[] NOT NULL DEFAULT '{}',     -- CUSTOM: allowed asset classes (empty = any)
    markets           TEXT[] NOT NULL DEFAULT '{}',     -- CUSTOM: allowed exchanges / markets, e.g. NSE, BSE, MCX (empty = any)
    instruments       UUID[] NOT NULL DEFAULT '{}',     -- CUSTOM: allowed instruments (empty = any)
    commission_per_lot NUMERIC(38, 10) NOT NULL DEFAULT 0 CHECK (commission_per_lot >= 0),   -- account currency, per lot per fill
    commission_pct    NUMERIC(12, 6) NOT NULL DEFAULT 0 CHECK (commission_pct >= 0 AND commission_pct <= 10),   -- % of traded value
    min_commission    NUMERIC(38, 10) NOT NULL DEFAULT 0 CHECK (min_commission >= 0),   -- account currency, per fill
    slippage_ticks    INTEGER CHECK (slippage_ticks IS NULL OR slippage_ticks BETWEEN 0 AND 1000),   -- NULL: the firm setting
    price_source_id   UUID REFERENCES cfg_connections(id) ON DELETE SET NULL,          -- NULL: the freshest feed of the instrument
    cloned_from       UUID REFERENCES cfg_accounts(id) ON DELETE SET NULL,
    notes             TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 1000),
    version           INTEGER NOT NULL DEFAULT 1,
    updated_by        TEXT NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Exchange rates a person enters, used only when no collected FX pair (direct, inverse or through USD/EUR) gives one.
CREATE TABLE IF NOT EXISTS cfg_fx_rates (
    base       TEXT NOT NULL CHECK (base ~ '^[A-Z0-9]{3,10}$'),
    quote      TEXT NOT NULL CHECK (quote ~ '^[A-Z0-9]{3,10}$' AND quote <> base),
    rate       NUMERIC(38, 12) NOT NULL CHECK (rate > 0),   -- 1 base = rate quote
    note       TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
    set_by     TEXT NOT NULL,
    set_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (base, quote)
);

-- Paper costs are written to oms_fills.commission (added in 034, NOT NULL DEFAULT 0, in the instrument's quote currency:
-- the same unit as realised P&L).
