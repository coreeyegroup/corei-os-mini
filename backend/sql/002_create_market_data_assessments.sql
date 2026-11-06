CREATE TABLE IF NOT EXISTS corei_market_data_assessments (
    assessment_id UUID PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    request_json JSONB NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS corei_market_data_assessments_instrument_time_idx
    ON corei_market_data_assessments (instrument_id, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS corei_market_data_assessments_created_at_idx
    ON corei_market_data_assessments (created_at DESC);
