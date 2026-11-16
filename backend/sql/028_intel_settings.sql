-- Stage 2 controls (DEC-70): the feature engine can be paused, its cycle length and timeframes changed, and single
-- instruments left out of the screen. One row; every change is audited with a reason.
CREATE TABLE IF NOT EXISTS intel_settings (
    id               SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled          BOOLEAN NOT NULL DEFAULT true,
    interval_seconds INTEGER NOT NULL DEFAULT 60 CHECK (interval_seconds BETWEEN 15 AND 3600),
    timeframes       TEXT[] NOT NULL DEFAULT ARRAY['M1', 'M5', 'M15', 'H1', 'H4', 'D1'],
    excluded         JSONB NOT NULL DEFAULT '[]',
    version          INTEGER NOT NULL DEFAULT 1,
    updated_by       TEXT NOT NULL DEFAULT 'system',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (timeframes <@ ARRAY['M1', 'M5', 'M15', 'H1', 'H4', 'D1'] AND cardinality(timeframes) >= 1)
);
INSERT INTO intel_settings(id) VALUES (1) ON CONFLICT DO NOTHING;
