-- 053 · Platform mode (release 0.66.0).
-- Why: one installation is used both to build/test the platform (DEVELOPMENT) and to trade for real (PRODUCTION).
-- What: one policy row `platform_mode` in cfg_policies. It starts in PRODUCTION so that deploying this release changes
-- nothing by itself (no gate loosens, no order-sending switch moves); an administrator switches to Development in
-- Settings › Platform mode. Additive: no table or column changes.
INSERT INTO cfg_policies (key, value, updated_by)
VALUES ('platform_mode', '{"mode": "PRODUCTION", "allow_live_in_development": false}'::jsonb, 'system')
ON CONFLICT (key) DO NOTHING;
