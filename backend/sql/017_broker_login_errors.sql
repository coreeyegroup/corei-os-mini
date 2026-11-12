-- 017: the last refused broker login, shown on the account (audit 12 Nov 2026: "The broker refused the
-- login" carried no reason). Idempotent.
ALTER TABLE cfg_accounts
    ADD COLUMN IF NOT EXISTS last_login_error TEXT CHECK (last_login_error IS NULL OR length(last_login_error) <= 400),
    ADD COLUMN IF NOT EXISTS last_login_error_at TIMESTAMPTZ;
