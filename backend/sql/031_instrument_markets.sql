-- Indian and other exchange-traded instruments (DEC-74). An instrument now records where it trades and, for
-- derivatives, its contract: exchange, market (whose hours apply: NSE, NSE_FO, NSE_CD, BSE, BSE_FO, MCX, FX, CRYPTO),
-- ISIN, lot size, expiry, strike, option type and underlying, and other names traders use for it (aliases such as
-- BANKNIFTY for NIFTYBANK). Collection schedules and stale-price alarms follow the market's hours.
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS exchange TEXT NOT NULL DEFAULT '' CHECK (exchange ~ '^[A-Z0-9_]{0,20}$');
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT ''
    CHECK (market IN ('', 'NSE', 'NSE_FO', 'NSE_CD', 'BSE', 'BSE_FO', 'MCX', 'FX', 'CRYPTO'));
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS isin TEXT NOT NULL DEFAULT '' CHECK (isin ~ '^([A-Z]{2}[A-Z0-9]{9}[0-9])?$');
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS lot_size NUMERIC(38, 12) CHECK (lot_size IS NULL OR lot_size > 0);
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS expiry DATE;
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS strike NUMERIC(38, 12) CHECK (strike IS NULL OR strike > 0);
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS option_type TEXT NOT NULL DEFAULT '' CHECK (option_type IN ('', 'CE', 'PE'));
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS underlying TEXT NOT NULL DEFAULT '' CHECK (length(underlying) <= 40);
ALTER TABLE cfg_instruments ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT '{}';

-- The two Indian indices already set up: where they trade and what traders call them.
WITH changed AS (
    UPDATE cfg_instruments i SET exchange='NSE', market='NSE',
           aliases = CASE i.symbol WHEN 'NIFTY50' THEN '{NIFTY,"NIFTY 50"}'::text[] ELSE '{BANKNIFTY,"NIFTY BANK"}'::text[] END,
           version = version + 1, updated_at = now()
    WHERE i.symbol IN ('NIFTY50', 'NIFTYBANK') AND i.market = ''
    RETURNING i.*)
INSERT INTO cfg_audit_log(actor, action, entity, entity_id, after_state, reason)
SELECT 'migration-031', 'UPDATE', 'instrument', id::text, to_jsonb(changed), 'Exchange, market hours and aliases for an Indian index'
FROM changed;

ALTER TABLE md_symbol_catalog ADD COLUMN IF NOT EXISTS isin TEXT NOT NULL DEFAULT '';
