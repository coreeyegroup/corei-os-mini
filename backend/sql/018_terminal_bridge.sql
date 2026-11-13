-- 018: the MT5 terminal bridge (DEC-54). The bridge's token is a write-only credential on the connection.
-- Idempotent.
ALTER TABLE cfg_credentials DROP CONSTRAINT IF EXISTS cfg_credentials_kind_check;
ALTER TABLE cfg_credentials ADD CONSTRAINT cfg_credentials_kind_check
    CHECK (kind IN ('API_KEY', 'OAUTH_CLIENT', 'MT5_LOGIN', 'BRIDGE_TOKEN'));

-- A source that cannot state its clock has the broker offset measured from its first live tick.
ALTER TABLE md_events DROP CONSTRAINT IF EXISTS md_events_kind_check;
ALTER TABLE md_events ADD CONSTRAINT md_events_kind_check
    CHECK (kind IN ('CONNECTED', 'DISCONNECTED', 'GAP', 'STALE', 'FROZEN', 'SPIKE', 'LATENCY', 'SPREAD', 'DEVIATION',
                    'FEED_SWITCH', 'FEED_RESTORED', 'BACKFILL', 'ARCHIVE', 'VERIFY', 'BACKUP', 'DISK', 'BACKLOG', 'ERROR', 'CLOCK_MEASURED'));
