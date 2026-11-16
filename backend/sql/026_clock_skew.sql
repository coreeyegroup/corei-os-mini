-- Latency is receipt time minus the broker's tick time. When the machine reading prices (a Windows VPS running the
-- terminal bridge) has its clock off by seconds, every latency figure is off by the same amount: the collector now
-- measures that skew from the bridge's heartbeats, corrects for it and reports it.
ALTER TABLE md_collector_status ADD COLUMN IF NOT EXISTS clock_skew_ms INTEGER;
ALTER TABLE md_events DROP CONSTRAINT IF EXISTS md_events_kind_check;
ALTER TABLE md_events ADD CONSTRAINT md_events_kind_check CHECK (kind IN ('CONNECTED', 'DISCONNECTED', 'GAP', 'STALE', 'FROZEN', 'SPIKE',
    'LATENCY', 'SPREAD', 'DEVIATION', 'FEED_SWITCH', 'FEED_RESTORED', 'BACKFILL', 'ARCHIVE', 'VERIFY', 'BACKUP', 'DISK', 'BACKLOG', 'ERROR',
    'CLOCK_MEASURED', 'CONFIG_CHANGED', 'CLOCK_SKEW'));
