-- 049 · Strategy evidence and risk minimums (release 0.58.0).
-- Why: on 25 Nov 2026 the server had 9 strategies, 8 of them draft and none ever backtested, any version could be
-- approved with no evidence, and DEMO and LIVE order sending were on with no risk limit at all. This records the
-- evidence a version was approved on, and holds the two governance policies (profile + optional overrides).
-- Additive only: existing approved versions keep their state (their evidence is NULL = approved before this release).

ALTER TABLE cfg_strategy_versions ADD COLUMN IF NOT EXISTS evidence JSONB;            -- the checks at approval, or the exception
ALTER TABLE cfg_strategy_versions ADD COLUMN IF NOT EXISTS approval_exception TEXT
    CHECK (approval_exception IS NULL OR length(approval_exception) BETWEEN 5 AND 500);

-- Profiles: RETAIL, PROFESSIONAL, INSTITUTIONAL; "overrides" changes single values of the chosen profile.
INSERT INTO cfg_policies (key, value) VALUES
    ('strategy_evidence', '{"profile": "PROFESSIONAL", "overrides": {}}'::jsonb),
    ('risk_minimum', '{"profile": "PROFESSIONAL", "overrides": {}}'::jsonb)
ON CONFLICT (key) DO NOTHING;
