-- Plans typed with a symbol in the wrong case (e.g. 'EUrUSD') never receive a price: MT5 symbols are case-sensitive.
-- Spell them as the source's symbol list does, when exactly one listed symbol matches and nothing was collected yet.
UPDATE md_collection_plans p SET broker_symbol = k.broker_symbol, version = p.version + 1, updated_at = now(), updated_by = 'migration-025'
FROM md_symbol_catalog k
WHERE k.account_id = p.account_id AND lower(k.broker_symbol) = lower(p.broker_symbol) AND k.broker_symbol <> p.broker_symbol
  AND p.status <> 'ARCHIVED'
  AND (SELECT count(*) FROM md_symbol_catalog k2 WHERE k2.account_id = p.account_id AND lower(k2.broker_symbol) = lower(p.broker_symbol)) = 1
  AND NOT EXISTS (SELECT 1 FROM md_collection_plans o WHERE o.account_id = p.account_id AND o.broker_symbol = k.broker_symbol AND o.id <> p.id)
  AND NOT EXISTS (SELECT 1 FROM md_ticks t WHERE t.account_id = p.account_id AND t.broker_symbol = p.broker_symbol);
