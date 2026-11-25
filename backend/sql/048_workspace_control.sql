-- 048 · Workspace control desks (release 0.57.0).
-- Why: My Workspace saved only a per-user layout and a read-only research scan. A desk could not run anything, and
-- nothing stopped two desks from driving the same strategy on the same account. This adds a shared, owner-held
-- workspace that carries Input → Processing → Output settings, a lifecycle state and the runs (deployments) it controls.
-- Additive only: existing deployments, per-user layouts (cfg_user_prefs 'trading-workspaces.v1') and scans are unchanged;
-- a layout can be imported into a control desk on request.

CREATE TABLE IF NOT EXISTS ws_workspaces (
    id            UUID PRIMARY KEY,
    name          TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    owner         TEXT NOT NULL,                       -- who created it; shared desks are visible to every operator
    shared        BOOLEAN NOT NULL DEFAULT true,
    template      TEXT NOT NULL DEFAULT 'CUSTOM' CHECK (length(template) <= 40),
    config        JSONB NOT NULL,                      -- {input, processing, output} as validated by trading.workspace_control
    config_by     TEXT NOT NULL,                       -- last person who changed config (four-eyes on LIVE starts)
    staged        JSONB,                               -- changes waiting for review while the desk runs
    staged_by     TEXT,
    state         TEXT NOT NULL DEFAULT 'STOPPED' CHECK (state IN ('STOPPED', 'RUNNING', 'PAUSED')),
    state_reason  TEXT NOT NULL DEFAULT '' CHECK (length(state_reason) <= 500),
    state_by      TEXT,
    state_at      TIMESTAMPTZ,
    last_check    JSONB,                               -- the last validation result
    test_run_id   UUID,                                -- the last test run (a read-only research scan)
    legacy_user   TEXT,                                -- imported from a saved layout: its user and number
    legacy_number INTEGER,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (legacy_user, legacy_number)
);

-- The runs a workspace controls (OWNED: created by it; ADOPTED: existing deployment it took over with confirmation)
-- or only watches (WATCH: shown in its results, never started, paused or stopped by it).
CREATE TABLE IF NOT EXISTS ws_runs (
    workspace_id  UUID NOT NULL REFERENCES ws_workspaces(id) ON DELETE CASCADE,
    deployment_id UUID NOT NULL REFERENCES cfg_deployments(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('OWNED', 'ADOPTED', 'WATCH')),
    prior_status  TEXT,                                -- ADOPTED: the deployment's status before adoption
    added_by      TEXT NOT NULL,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, deployment_id)
);
-- One controller per deployment: two workspaces can never both start, pause or stop the same run.
CREATE UNIQUE INDEX IF NOT EXISTS ws_runs_one_controller ON ws_runs (deployment_id) WHERE role IN ('OWNED', 'ADOPTED');
CREATE INDEX IF NOT EXISTS ws_runs_deployment ON ws_runs (deployment_id);
