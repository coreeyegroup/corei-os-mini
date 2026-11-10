-- Direct MT5 configuration is distinct from the optional terminal EA bridge.
-- Existing profiles and connection/account identities remain unchanged.
ALTER TABLE corei_connector_profiles
 DROP CONSTRAINT corei_connector_profiles_connector_type_check;
ALTER TABLE corei_connector_profiles
 ADD CONSTRAINT corei_connector_profiles_connector_type_check
 CHECK (connector_type IN ('MT4_EA','MT5_EA','MT5_DIRECT','CTRADER_OPEN_API',
                           'BROKER_API','EXCHANGE_API','FIX','DATA_FEED','CUSTOM'));
ALTER TABLE corei_connector_profiles
 ADD CONSTRAINT corei_connector_profiles_direct_mt5_check
 CHECK (connector_type <> 'MT5_DIRECT' OR
        (runtime = 'SERVER_API' AND length(btrim(server_name)) > 0
         AND terminal_host = '' AND terminal_instance = ''));
