-- 050 · Setup gates and overrides (release 0.59.0).
-- Why: the platform had every feature but no systematic order: on 25 Nov 2026 order sending was on with zero risk
-- limits, and nothing said which setup step was missing. Setup is now eight gates per track (Paper, DEMO, LIVE), each
-- computed from real data. Each gate has a level (off, warn, soft, supervised, hard) chosen by the firm, and a scoped,
-- time-boxed override where the level allows one.
-- Additive only.

INSERT INTO cfg_policies (key, value) VALUES
    ('setup_gates', '{"preset": "RECOMMENDED", "levels": {}, "target_track": "DEMO", "default_hours": 24,
                      "max_hours": {"PAPER": 168, "DEMO": 168, "LIVE": 24}}'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- An override lets one gate pass for one track (optionally one account) until it expires or is revoked. Soft: the
-- requester grants it; supervised: a second person approves it; hard gates take none.
CREATE TABLE IF NOT EXISTS setup_overrides (
    id           UUID PRIMARY KEY,
    gate         TEXT NOT NULL CHECK (gate IN ('PEOPLE', 'ACCOUNTS', 'SYMBOLS', 'MARKET_DATA', 'RISK', 'EVIDENCE', 'PAPER_RECORD')),
    track        TEXT NOT NULL CHECK (track IN ('PAPER', 'DEMO', 'LIVE')),
    account_id   UUID REFERENCES cfg_accounts(id) ON DELETE CASCADE,   -- NULL: the whole track
    level        TEXT NOT NULL CHECK (level IN ('SOFT', 'SUPERVISED')),
    reason       TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 5 AND 500),
    hours        INTEGER NOT NULL CHECK (hours BETWEEN 1 AND 8760),
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by  TEXT,                                -- SOFT: the requester; SUPERVISED: a second person
    approved_at  TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,                         -- set when approved
    revoked_by   TEXT,
    revoked_at   TIMESTAMPTZ,
    revoke_reason TEXT CHECK (revoke_reason IS NULL OR length(revoke_reason) <= 500),
    CHECK (level = 'SOFT' OR approved_by IS NULL OR approved_by <> requested_by)
);
CREATE INDEX IF NOT EXISTS setup_overrides_live ON setup_overrides (track, gate) WHERE revoked_at IS NULL;
