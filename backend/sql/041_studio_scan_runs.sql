CREATE TABLE IF NOT EXISTS studio_scan_runs (
    id UUID PRIMARY KEY,
    operator TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('instrument', 'strategy')),
    request JSONB NOT NULL,
    instruments JSONB NOT NULL,
    strategies JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'CANCELLED', 'FAILED')),
    next_instrument INTEGER NOT NULL DEFAULT 0,
    retry_errors BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    failure TEXT
);
CREATE INDEX IF NOT EXISTS studio_scan_runs_owner_idx ON studio_scan_runs (operator, created_at DESC, id);
CREATE INDEX IF NOT EXISTS studio_scan_runs_queue_idx ON studio_scan_runs (updated_at, id) WHERE status IN ('QUEUED', 'RUNNING');

CREATE TABLE IF NOT EXISTS studio_scan_results (
    run_id UUID NOT NULL REFERENCES studio_scan_runs(id) ON DELETE CASCADE,
    instrument TEXT NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SIGNAL', 'WAIT', 'ERROR')),
    strength NUMERIC,
    detail JSONB NOT NULL,
    PRIMARY KEY (run_id, instrument, strategy)
);
CREATE INDEX IF NOT EXISTS studio_scan_results_strategy_idx ON studio_scan_results (run_id, strategy);
