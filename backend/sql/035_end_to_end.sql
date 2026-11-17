-- End-to-end gaps (DEC-85): protective stops per deployment and on positions, realign of unexecuted decisions,
-- broker-side exits (stop-loss, take-profit, stop-out) booked as fills, the deal's reason kept.
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS protect JSONB NOT NULL DEFAULT '{}';      -- {"stop_atr": "2", "take_atr": "3"}
ALTER TABLE cfg_deployments ADD COLUMN IF NOT EXISTS realign BOOLEAN NOT NULL DEFAULT true;     -- retry a decision that was not executed
ALTER TABLE pf_positions ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(38, 12);
ALTER TABLE pf_positions ADD COLUMN IF NOT EXISTS take_profit NUMERIC(38, 12);
ALTER TABLE oms_orders DROP CONSTRAINT IF EXISTS oms_orders_source_check;
ALTER TABLE oms_orders ADD CONSTRAINT oms_orders_source_check CHECK (source IN ('RUNTIME', 'MANUAL', 'FLATTEN', 'COPY', 'RECONCILE', 'PROTECT', 'REALIGN'));
ALTER TABLE tr_settings ADD COLUMN IF NOT EXISTS realign_max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (realign_max_attempts BETWEEN 0 AND 50);
ALTER TABLE pt_broker_deals ADD COLUMN IF NOT EXISTS reason TEXT;
