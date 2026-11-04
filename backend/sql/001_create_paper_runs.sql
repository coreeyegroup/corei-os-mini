CREATE TABLE IF NOT EXISTS corei_paper_runs (
    run_id UUID PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    execution_mode TEXT NOT NULL CHECK (execution_mode = 'PAPER'),
    request_json JSONB NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corei_paper_runs_created_at_idx
    ON corei_paper_runs (created_at DESC);

CREATE INDEX IF NOT EXISTS corei_paper_runs_instrument_created_at_idx
    ON corei_paper_runs (instrument_id, created_at DESC);
