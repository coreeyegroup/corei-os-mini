-- Paper-only boundary (gap D-02). COREI-OS-MINI is not authorised for live trading.
-- Existing LIVE rows are kept for history but disabled; no LIVE row may be created
-- or enabled. Removing this requires a separate, documented live-trading decision.
UPDATE corei_accounts SET enabled = false WHERE mode = 'LIVE' AND enabled;
UPDATE corei_connector_profiles SET enabled = false WHERE environment = 'LIVE' AND enabled;

CREATE OR REPLACE FUNCTION corei_block_live_accounts() RETURNS trigger AS $corei_paper$
BEGIN
    IF NEW.mode = 'LIVE' AND (TG_OP = 'INSERT' OR NEW.enabled) THEN
        RAISE EXCEPTION 'COREI-OS-MINI is paper-only: live accounts cannot be created or enabled'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$corei_paper$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION corei_block_live_profiles() RETURNS trigger AS $corei_paper$
BEGIN
    IF NEW.environment = 'LIVE' AND (TG_OP = 'INSERT' OR NEW.enabled) THEN
        RAISE EXCEPTION 'COREI-OS-MINI is paper-only: live connector profiles cannot be created or enabled'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$corei_paper$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS corei_accounts_paper_only ON corei_accounts;
CREATE TRIGGER corei_accounts_paper_only BEFORE INSERT OR UPDATE ON corei_accounts
    FOR EACH ROW EXECUTE FUNCTION corei_block_live_accounts();

DROP TRIGGER IF EXISTS corei_connector_profiles_paper_only ON corei_connector_profiles;
CREATE TRIGGER corei_connector_profiles_paper_only BEFORE INSERT OR UPDATE ON corei_connector_profiles
    FOR EACH ROW EXECUTE FUNCTION corei_block_live_profiles();
