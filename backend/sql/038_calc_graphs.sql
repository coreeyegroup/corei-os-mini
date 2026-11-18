-- Calculation Graphs (DEC-93): every calculation as a graph of typed nodes, versioned, approved and audited.
--   calc_graphs           one row per graph (code, name, purpose); built-ins are flagged and read-only
--   calc_graph_versions   the definition (JSONB), its SHA-256 hash and the parity reference (the code it must equal);
--                         DRAFT → PROPOSED → APPROVED → RETIRED; once APPROVED the definition and hash can never change
--                         and the row can never be deleted (enforced below, not only in the application)
--   calc_bindings         where an approved version is used: a signal definition, a deployment, a feature, a risk
--                         check, a sizing rule or a measure; one active binding per target and kind
--   calc_runs             every preview, parity check and engine run: who, which version or hash, which bars, how long,
--                         whether it worked and the errors — insert-only
CREATE TABLE IF NOT EXISTS calc_graphs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code          TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,59}$'),
    name          TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    purpose       TEXT NOT NULL CHECK (purpose IN ('FEATURE', 'SIGNAL', 'STRATEGY_RULE', 'SIZING', 'PROTECTIVE', 'RISK_CHECK', 'ALLOCATION', 'MEASURE')),
    description   TEXT NOT NULL DEFAULT '',
    builtin       BOOLEAN NOT NULL DEFAULT false,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS calc_graph_versions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id      UUID NOT NULL REFERENCES calc_graphs(id) ON DELETE RESTRICT,
    version       INTEGER NOT NULL CHECK (version >= 1),
    status        TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PROPOSED', 'APPROVED', 'RETIRED')),
    definition    JSONB NOT NULL,
    hash          TEXT NOT NULL CHECK (hash ~ '^[0-9a-f]{64}$'),
    parity_ref    TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposed_by   TEXT, proposed_at TIMESTAMPTZ,
    approved_by   TEXT, approved_at TIMESTAMPTZ,
    retired_by    TEXT, retired_at  TIMESTAMPTZ,
    reason        TEXT NOT NULL DEFAULT '',
    UNIQUE (graph_id, version),
    CHECK (status NOT IN ('APPROVED', 'RETIRED') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS calc_graph_versions_hash ON calc_graph_versions (hash);

CREATE OR REPLACE FUNCTION calc_versions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('APPROVED', 'RETIRED') THEN
            RAISE EXCEPTION 'An approved calculation graph version is kept for audit and cannot be deleted' USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status IN ('APPROVED', 'RETIRED') THEN
        IF NEW.definition IS DISTINCT FROM OLD.definition OR NEW.hash IS DISTINCT FROM OLD.hash OR NEW.version <> OLD.version
           OR NEW.graph_id <> OLD.graph_id OR NEW.approved_by IS DISTINCT FROM OLD.approved_by THEN
            RAISE EXCEPTION 'An approved calculation graph version cannot be changed; propose a new version' USING ERRCODE = 'restrict_violation';
        END IF;
        IF OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED' THEN
            RAISE EXCEPTION 'A retired calculation graph version stays retired' USING ERRCODE = 'restrict_violation';
        END IF;
        IF OLD.status = 'APPROVED' AND NEW.status NOT IN ('APPROVED', 'RETIRED') THEN
            RAISE EXCEPTION 'An approved calculation graph version can only be retired' USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS calc_versions_guard ON calc_graph_versions;
CREATE TRIGGER calc_versions_guard BEFORE UPDATE OR DELETE ON calc_graph_versions FOR EACH ROW EXECUTE FUNCTION calc_versions_guard();

CREATE TABLE IF NOT EXISTS calc_bindings (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               TEXT NOT NULL CHECK (kind IN ('SIGNAL_DEFINITION', 'DEPLOYMENT', 'FEATURE', 'RISK_CHECK', 'SIZING', 'MEASURE')),
    target             TEXT NOT NULL CHECK (length(target) BETWEEN 1 AND 120),
    graph_version_id   UUID NOT NULL REFERENCES calc_graph_versions(id) ON DELETE RESTRICT,
    parameters         JSONB NOT NULL DEFAULT '{}',
    active             BOOLEAN NOT NULL DEFAULT true,
    created_by         TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_by           TEXT, ended_at TIMESTAMPTZ,
    reason             TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS calc_bindings_one_active ON calc_bindings (kind, target) WHERE active;

CREATE TABLE IF NOT EXISTS calc_runs (
    id                 BIGSERIAL PRIMARY KEY,
    kind               TEXT NOT NULL CHECK (kind IN ('PREVIEW', 'PARITY', 'ENGINE', 'BACKTEST')),
    graph_code         TEXT,
    graph_version_id   UUID REFERENCES calc_graph_versions(id) ON DELETE SET NULL,
    graph_hash         TEXT NOT NULL,
    purpose            TEXT NOT NULL,
    actor              TEXT NOT NULL,
    account_id         UUID,
    broker_symbol      TEXT,
    timeframe          TEXT,
    bars               INTEGER NOT NULL DEFAULT 0,
    last_bar_time      TIMESTAMPTZ,
    parameters         JSONB NOT NULL DEFAULT '{}',
    ok                 BOOLEAN NOT NULL,
    errors             JSONB NOT NULL DEFAULT '{}',
    ms                 NUMERIC(12, 3) NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calc_runs_recent ON calc_runs (created_at DESC);

CREATE OR REPLACE FUNCTION calc_runs_insert_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Calculation runs are an audit record and cannot be changed or deleted' USING ERRCODE = 'restrict_violation';
END $$;
DROP TRIGGER IF EXISTS calc_runs_insert_only ON calc_runs;
CREATE TRIGGER calc_runs_insert_only BEFORE UPDATE OR DELETE ON calc_runs FOR EACH ROW EXECUTE FUNCTION calc_runs_insert_only();
