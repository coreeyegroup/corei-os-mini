-- 047 · Stage 3 Signal intelligence integrity (release 0.56.0).
-- Why: the 24 Nov 2026 Signal Intelligence audit found ~900 side flips a day on M5 for four instruments (scores wobbling
-- around the threshold) and flips that could not be traced to the definition version that produced them.
-- Additive: no exit threshold = the behaviour before (one threshold for entering and leaving a side).

-- Hysteresis: a side, once taken, holds until |score| falls below the exit threshold.
ALTER TABLE sig_definitions ADD COLUMN IF NOT EXISTS exit_threshold NUMERIC(6, 2)
    CHECK (exit_threshold IS NULL OR (exit_threshold >= 0 AND exit_threshold < threshold));

-- Which version of the definition produced a flip (a definition change starts a new version).
ALTER TABLE sig_events ADD COLUMN IF NOT EXISTS definition_version INTEGER;
