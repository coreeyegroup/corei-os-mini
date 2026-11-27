-- 054 · Flexible configuration (release 0.68.0). Additive.
-- 1. Collection plans get a display name ("EURUSD · MetaQuotes demo · main feed"); the code stays short for logs.
ALTER TABLE md_collection_plans ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '' CHECK (length(name) <= 120);

-- 2. A symbol mapping can apply to every account on a server, to one account, or (new) to the accounts of one group
--    (e.g. all VIP or raw accounts). Precedence when resolving a symbol: account, then group, then server.
ALTER TABLE cfg_symbol_mappings ADD COLUMN IF NOT EXISTS account_group_id UUID REFERENCES cfg_account_groups(id) ON DELETE CASCADE;
DO $$ BEGIN
    ALTER TABLE cfg_symbol_mappings ADD CONSTRAINT cfg_symbol_mappings_one_scope CHECK (account_id IS NULL OR account_group_id IS NULL);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP INDEX IF EXISTS cfg_symbol_mappings_symbol_scope;
DROP INDEX IF EXISTS cfg_symbol_mappings_instrument_scope;
CREATE UNIQUE INDEX IF NOT EXISTS cfg_symbol_mappings_symbol_scope2 ON cfg_symbol_mappings
    (connection_id, COALESCE(account_id, account_group_id, '00000000-0000-0000-0000-000000000000'::uuid), broker_symbol);
CREATE UNIQUE INDEX IF NOT EXISTS cfg_symbol_mappings_instrument_scope2 ON cfg_symbol_mappings
    (connection_id, COALESCE(account_id, account_group_id, '00000000-0000-0000-0000-000000000000'::uuid), instrument_id);
