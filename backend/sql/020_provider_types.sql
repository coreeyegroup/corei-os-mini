-- 020: who a provider is. Brokers, exchanges and data vendors, plus ECNs, prime brokers, liquidity
-- providers, fast feeds and data centres (co-location / cross-connect). Idempotent.
ALTER TABLE cfg_providers DROP CONSTRAINT IF EXISTS cfg_providers_kind_check;
ALTER TABLE cfg_providers ADD CONSTRAINT cfg_providers_kind_check CHECK (kind IN (
    'BROKER', 'EXCHANGE', 'ECN', 'PRIME_BROKER', 'LIQUIDITY_PROVIDER', 'FAST_FEED', 'DATA_VENDOR', 'DATA_CENTRE'));
