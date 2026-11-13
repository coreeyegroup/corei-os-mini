-- 019: FIX connections (FIX 4.4 / 4.2 sessions) and their session login. Idempotent.
ALTER TABLE cfg_connections DROP CONSTRAINT IF EXISTS cfg_connections_platform_check;
ALTER TABLE cfg_connections ADD CONSTRAINT cfg_connections_platform_check CHECK (platform IN ('MT5', 'REST_API', 'FIX'));
ALTER TABLE cfg_credentials DROP CONSTRAINT IF EXISTS cfg_credentials_kind_check;
ALTER TABLE cfg_credentials ADD CONSTRAINT cfg_credentials_kind_check
    CHECK (kind IN ('API_KEY', 'OAUTH_CLIENT', 'MT5_LOGIN', 'BRIDGE_TOKEN', 'FIX_LOGIN'));
