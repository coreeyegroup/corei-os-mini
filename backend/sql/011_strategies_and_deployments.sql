-- Strategy registry and deployment matrix (Blueprint v1.2 §8.4–8.5, §10 step 4;
-- Process Flow v1.4 §2, §7). A deployment is one explicit row:
-- strategy version × instrument × account, with its market-data source.
-- Nothing trades unless such a row exists and is ACTIVE.

CREATE TABLE cfg_strategies (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z][A-Z0-9_]{1,31}$'),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Immutable once approved: a change of rules or parameters is a new version.
CREATE TABLE cfg_strategy_versions (
    id UUID PRIMARY KEY,
    strategy_id UUID NOT NULL REFERENCES cfg_strategies(id) ON DELETE RESTRICT,
    number INTEGER NOT NULL CHECK (number >= 1),
    algorithm TEXT NOT NULL CHECK (algorithm ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    parameters JSONB NOT NULL,
    notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 2000),
    state TEXT NOT NULL DEFAULT 'DRAFT' CHECK (state IN ('DRAFT', 'APPROVED', 'RETIRED')),
    created_by TEXT NOT NULL,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (strategy_id, number),
    UNIQUE (id, strategy_id)
);

CREATE FUNCTION cfg_strategy_version_immutable() RETURNS trigger AS $cfg_sv$
BEGIN
    IF OLD.state <> 'DRAFT' AND (NEW.algorithm IS DISTINCT FROM OLD.algorithm
        OR NEW.parameters IS DISTINCT FROM OLD.parameters OR NEW.strategy_id IS DISTINCT FROM OLD.strategy_id
        OR NEW.number IS DISTINCT FROM OLD.number) THEN
        RAISE EXCEPTION 'Approved or retired strategy versions cannot be changed; create a new version'
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.state = 'RETIRED' AND NEW.state <> 'RETIRED' THEN
        RAISE EXCEPTION 'A retired strategy version cannot be reinstated' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$cfg_sv$ LANGUAGE plpgsql;
CREATE TRIGGER cfg_strategy_versions_immutable BEFORE UPDATE ON cfg_strategy_versions
    FOR EACH ROW EXECUTE FUNCTION cfg_strategy_version_immutable();

CREATE TABLE cfg_deployments (
    id UUID PRIMARY KEY,
    strategy_id UUID NOT NULL REFERENCES cfg_strategies(id) ON DELETE RESTRICT,
    strategy_version_id UUID NOT NULL,
    instrument_id UUID NOT NULL REFERENCES cfg_instruments(id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES cfg_accounts(id) ON DELETE RESTRICT,
    -- Market-data source for this strategy and instrument; may be another connection
    -- than the account's (data selection is separate from order routing).
    data_mapping_id UUID NOT NULL REFERENCES cfg_symbol_mappings(id) ON DELETE RESTRICT,
    allocation_weight_bps INTEGER NOT NULL DEFAULT 10000 CHECK (allocation_weight_bps BETWEEN 1 AND 10000),
    status TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'ACTIVE', 'PAUSED', 'ENDED')),
    note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 1000),
    proposed_by TEXT NOT NULL,
    activated_by TEXT,
    activated_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (strategy_id, instrument_id, account_id),
    FOREIGN KEY (strategy_version_id, strategy_id) REFERENCES cfg_strategy_versions(id, strategy_id) ON DELETE RESTRICT
);
CREATE INDEX cfg_deployments_account ON cfg_deployments (account_id);
CREATE INDEX cfg_deployments_instrument ON cfg_deployments (instrument_id);
