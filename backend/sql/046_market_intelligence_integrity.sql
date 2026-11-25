-- 046 · Stage 2 Market intelligence integrity (release 0.55.0).
-- Why: the 23 Nov 2026 Market Intelligence audit found week-old broker D1 bars shown as current, freshness measured by
-- recompute time, UTC-cut Indian hours, and no exchange holidays in the calendar.
-- Additive: feature version 4 starts its own history (older versions are kept and still readable).

-- Snapshot: when its bar ended, whether that is the latest the market can have closed, its market and alignment.
ALTER TABLE intel_snapshot ADD COLUMN IF NOT EXISTS freshness TEXT CHECK (freshness IN ('FRESH', 'LAGGING', 'STALE', 'CLOSED'));
ALTER TABLE intel_snapshot ADD COLUMN IF NOT EXISTS bar_end TIMESTAMPTZ;
ALTER TABLE intel_snapshot ADD COLUMN IF NOT EXISTS market TEXT;
ALTER TABLE intel_snapshot ADD COLUMN IF NOT EXISTS anchor TEXT CHECK (anchor IN ('UTC', 'SESSION'));
-- MIXED: the broker's own history continued with M1-built bars after it stops.
ALTER TABLE intel_snapshot DROP CONSTRAINT IF EXISTS intel_snapshot_bars_source_check;
ALTER TABLE intel_snapshot ADD CONSTRAINT intel_snapshot_bars_source_check CHECK (bars_source IN ('BROKER', 'M1', 'MIXED'));

-- Calendar: exchange holidays imported from Upstox (code NSE, BSE or MCX).
ALTER TABLE intel_events DROP CONSTRAINT IF EXISTS intel_events_source_check;
ALTER TABLE intel_events ADD CONSTRAINT intel_events_source_check CHECK (source IN ('MANUAL', 'CSV', 'UPSTOX'));
