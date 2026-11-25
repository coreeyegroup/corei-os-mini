-- 044 · Wave 0 financial integrity (release 0.53.0).
-- Why: reconciliation needs a verified interval per account (a deal older than a fixed window must still be checked,
-- and a break may close only on positive evidence); each run records which datasets it actually compared; risk
-- decisions keep the book, valuation and capital basis they used; LIVE policy exceptions are explicit and expire;
-- Upstox algo orders carry the configured Algo Name.
-- Additive only: existing rows keep their meaning; nothing is backfilled as a guess.

CREATE TABLE IF NOT EXISTS pt_verified_intervals (
    account_id UUID PRIMARY KEY REFERENCES cfg_accounts(id) ON DELETE CASCADE,
    deals_verified_to TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE pt_recon_runs ADD COLUMN IF NOT EXISTS coverage JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE pt_recon_runs ADD COLUMN IF NOT EXISTS interval_from TIMESTAMPTZ;
ALTER TABLE pt_recon_runs ADD COLUMN IF NOT EXISTS interval_to TIMESTAMPTZ;

ALTER TABLE risk_decisions ADD COLUMN IF NOT EXISTS book_mode TEXT;
ALTER TABLE risk_decisions ADD COLUMN IF NOT EXISTS valuation JSONB;
ALTER TABLE risk_decisions ADD COLUMN IF NOT EXISTS capital_basis JSONB;

CREATE TABLE IF NOT EXISTS cfg_policy_exceptions (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL CHECK (code IN ('LIVE_NO_SECOND_APPROVER', 'LIVE_FOUR_EYES_NOT_ALL', 'LIVE_NO_ALERT_CHANNEL')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 5 AND 500),
    approved_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS cfg_policy_exceptions_live ON cfg_policy_exceptions(code, expires_at) WHERE revoked_at IS NULL;

ALTER TABLE cfg_connections ADD COLUMN IF NOT EXISTS algo_name TEXT;

-- Equity snapshots per book. Existing rows are the account-wide (mixed) series and are labelled 'ALL'; they are never
-- re-labelled as PAPER or BROKER because that cannot be reconstructed honestly.
ALTER TABLE pf_equity ADD COLUMN IF NOT EXISTS book TEXT NOT NULL DEFAULT 'ALL' CHECK (book IN ('ALL', 'PAPER', 'BROKER'));
ALTER TABLE pf_equity DROP CONSTRAINT IF EXISTS pf_equity_pkey;
ALTER TABLE pf_equity ADD CONSTRAINT pf_equity_pkey PRIMARY KEY (account_id, book, at);
