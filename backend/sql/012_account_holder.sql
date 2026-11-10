-- Account holder: the person or legal entity that owns a broker account. One holder can
-- have several accounts at one broker and accounts at several brokers.
ALTER TABLE cfg_accounts ADD COLUMN IF NOT EXISTS holder_name TEXT NOT NULL DEFAULT ''
    CHECK (length(holder_name) <= 120);
CREATE INDEX IF NOT EXISTS cfg_accounts_holder ON cfg_accounts (lower(holder_name));
