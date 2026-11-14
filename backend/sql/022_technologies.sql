-- Provider › account › server, with the technology chosen per account (DEC-64).
-- MetaTrader 4 and cTrader accounts can be recorded (provider, account, server, login) before their connectors are built;
-- collection and orders start when the connector exists. A provider lists the technologies it offers.
ALTER TABLE cfg_connections DROP CONSTRAINT IF EXISTS cfg_connections_platform_check;
ALTER TABLE cfg_connections ADD CONSTRAINT cfg_connections_platform_check CHECK (platform IN ('MT5', 'MT4', 'CTRADER', 'REST_API', 'FIX'));
ALTER TABLE cfg_providers ADD COLUMN IF NOT EXISTS technologies TEXT[] NOT NULL DEFAULT '{}';
