-- Signal library (DEC-88): the formula belongs to the signal. Every active definition is scored on every collected
-- instrument and timeframe by the feature engine (sig_live: the latest score with every input used); strategies combine
-- signals (SIGNAL_BLEND). Scores are dimensionless analytics from −100 to +100, not money.
CREATE TABLE IF NOT EXISTS sig_definitions (
    id          UUID PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9_]{2,40}$'),
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    parameters  JSONB NOT NULL DEFAULT '{}',
    threshold   NUMERIC(6, 2) NOT NULL DEFAULT 20 CHECK (threshold > 0 AND threshold < 100),
    timeframes  JSONB NOT NULL DEFAULT '["M5", "M15", "H1", "H4", "D1"]',
    status      TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED')),
    builtin     BOOLEAN NOT NULL DEFAULT false,
    notes       TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sig_live (
    definition_id  UUID NOT NULL REFERENCES sig_definitions(id) ON DELETE CASCADE,
    account_id     UUID NOT NULL,
    broker_symbol  TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    bar_time       TIMESTAMPTZ NOT NULL,
    score          NUMERIC(7, 2),
    previous_score NUMERIC(7, 2),
    direction      TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT', 'FLAT')),
    inputs         JSONB NOT NULL DEFAULT '{}',
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (definition_id, account_id, broker_symbol, timeframe)
);
INSERT INTO sig_definitions(id, code, name, kind, parameters, builtin, created_by, updated_by) VALUES
 ('5d000000-0000-0000-0000-000000000001', 'EMA_TREND', 'EMA trend (fast 9, slow 21, scale 1.0)', 'EMA_TREND', '{"fast": 9, "slow": 21, "scale": 1.0}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000002', 'SMA_TREND', 'SMA trend (fast 20, slow 50, scale 1.0)', 'SMA_TREND', '{"fast": 20, "slow": 50, "scale": 1.0}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000003', 'MACD_MOMENTUM', 'MACD momentum (fast 12, slow 26, signal 9, scale 0.5)', 'MACD_MOMENTUM', '{"fast": 12, "slow": 26, "signal": 9, "scale": 0.5}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000004', 'RSI_REVERSION', 'RSI reversion (period 14)', 'RSI_REVERSION', '{"period": 14}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000005', 'RSI_MOMENTUM', 'RSI momentum (period 14)', 'RSI_MOMENTUM', '{"period": 14}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000006', 'BOLLINGER_REVERSION', 'Bollinger reversion (period 20, deviations 2.0)', 'BOLLINGER_REVERSION', '{"period": 20, "deviations": 2.0}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000007', 'ZSCORE_REVERSION', 'Z-score reversion (period 20, zmax 2.0)', 'ZSCORE_REVERSION', '{"period": 20, "zmax": 2.0}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000008', 'DONCHIAN_BREAKOUT', 'Donchian breakout (period 20)', 'DONCHIAN_BREAKOUT', '{"period": 20}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000009', 'MOMENTUM_TSTAT', 'Momentum (t-stat) (period 20, scale 2.0)', 'MOMENTUM_TSTAT', '{"period": 20, "scale": 2.0}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000010', 'STOCHASTIC_REVERSION', 'Stochastic reversion (period 14, smooth 3)', 'STOCHASTIC_REVERSION', '{"period": 14, "smooth": 3}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000011', 'ADX_DIRECTION', 'ADX direction (period 14, full_at 30.0)', 'ADX_DIRECTION', '{"period": 14, "full_at": 30.0}', true, 'system', 'system'),
 ('5d000000-0000-0000-0000-000000000012', 'VWAP_REVERSION', 'VWAP reversion (period 20, scale 1.0)', 'VWAP_REVERSION', '{"period": 20, "scale": 1.0}', true, 'system', 'system')
ON CONFLICT DO NOTHING;
