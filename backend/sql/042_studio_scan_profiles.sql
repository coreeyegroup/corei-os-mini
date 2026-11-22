ALTER TABLE studio_scan_runs ADD COLUMN IF NOT EXISTS name TEXT;
CREATE TABLE IF NOT EXISTS studio_scan_profiles (
    operator TEXT NOT NULL,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    mode TEXT NOT NULL CHECK (mode IN ('instrument', 'strategy')),
    request JSONB NOT NULL,
    all_instruments BOOLEAN NOT NULL DEFAULT false,
    all_strategies BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (operator, name)
);
