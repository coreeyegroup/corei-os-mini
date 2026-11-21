CREATE TABLE IF NOT EXISTS studio_strategy_drafts (
    id UUID PRIMARY KEY,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS studio_strategy_draft_versions (
    draft_id UUID NOT NULL REFERENCES studio_strategy_drafts(id),
    version INTEGER NOT NULL CHECK (version > 0),
    workflow JSONB NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    promotion_version_id UUID UNIQUE REFERENCES calc_graph_versions(id),
    PRIMARY KEY (draft_id, version)
);
