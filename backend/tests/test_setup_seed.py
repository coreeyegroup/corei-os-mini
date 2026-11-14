"""The test-setup script: dry run changes nothing; --apply writes the values; a second run finds nothing to do."""
import contextlib
import importlib
import importlib.util
import io

test_market_data = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') \
    else importlib.import_module('tests.test_market_data')


class SetupSeed(test_market_data.MarketData):
    test_plans_estimate_controls_and_jobs = test_collector_end_to_end = None

    def test_dry_run_apply_and_idempotent(self):
        from corei_os_mini import setup_seed
        # The owner's records as they are on the live system.
        vt = self.ok(self.alice.post('/api/config/providers', json={'code': 'VTMARKET', 'name': 'VT Market', 'kind': 'BROKER'}), 201)
        mq = self.ok(self.alice.post('/api/config/providers', json={'code': 'METAQUOTES', 'name': 'MetaQuotes Ltd.', 'kind': 'BROKER'}), 201)
        conn = self.ok(self.alice.post('/api/config/connections', json={'provider_id': mq['id'], 'code': 'METAQUOTES_MT5_DEMO', 'name': 'MetaQuotes demo',
            'platform': 'MT5', 'environment': 'DEMO', 'purpose': 'BOTH', 'settings': {'server_name': 'MetaQuotes-Demo',
            'access_points': [{'name': 'MetaQuotes-Demo', 'host': '194.164.179.214', 'port': 443}]}}), 201)
        self.ok(self.alice.post('/api/config/accounts', json={'connection_id': conn['id'], 'code': 'METAQUOTES_DEMO_5056977914', 'name': 'MetaQuotes demo',
                                                             'external_account_id': '5056977914', 'base_currency': 'USD', 'trading_permission': 'TRADING'}), 201)
        self.ok(self.alice.post('/api/config/providers', json={'code': 'META_TRADER', 'name': 'Meta Trader', 'kind': 'BROKER'}), 201)
        self.ok(self.alice.post('/api/config/providers', json={'code': 'EXNESS', 'name': 'EXNESS', 'kind': 'BROKER'}), 201)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_seed.run('alice', apply=False)
        text = out.getvalue()
        self.assertIn("would update provider VTMARKET: name 'VT Market' → 'VT Markets'", text)
        self.assertIn('would create instrument XAUUSD', text)
        self.assertIn('provider UPSTOK not found: skipped', text)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM cfg_instruments').fetchone()['n'], 0, 'dry run changes nothing')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_seed.run('alice', apply=True)
        with self.connect() as db:
            p = db.execute("SELECT * FROM cfg_providers WHERE code='VTMARKET'").fetchone()
            self.assertEqual((p['name'], p['legal_entity'], p['licence_number']), ('VT Markets', 'VT Markets (Pty) Ltd', 'FSP 50865'))
            self.assertEqual(db.execute("SELECT status FROM cfg_providers WHERE code='META_TRADER'").fetchone()['status'], 'ARCHIVED')
            self.assertEqual(db.execute("SELECT name FROM cfg_providers WHERE code='EXNESS'").fetchone()['name'], 'Exness')
            self.assertEqual(db.execute("SELECT name FROM cfg_connections WHERE code='METAQUOTES_MT5_DEMO'").fetchone()['name'], 'MetaQuotes · MT5 demo · MetaQuotes-Demo')
            self.assertEqual(db.execute('SELECT count(*) AS n FROM cfg_instruments').fetchone()['n'], 6)
            plans = db.execute("SELECT broker_symbol, status, purpose FROM md_collection_plans ORDER BY 1").fetchall()
            self.assertEqual([(x['broker_symbol'], x['status']) for x in plans], [('EURUSD', 'ACTIVE'), ('GBPUSD', 'ACTIVE'), ('USDJPY', 'ACTIVE'), ('XAUUSD', 'ACTIVE')])
            groups = db.execute('SELECT g.code, count(m.account_id) AS n FROM cfg_account_groups g LEFT JOIN cfg_account_group_members m ON m.group_id=g.id GROUP BY 1 ORDER BY 1').fetchall()
            self.assertEqual([(g['code'], g['n']) for g in groups], [('SRC_FX_METALS', 1), ('SRC_INDIA', 0), ('TEST_DEMO', 1)])
            self.assertEqual(db.execute('SELECT count(*) AS n FROM cfg_symbol_mappings').fetchone()['n'], 4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_seed.run('alice', apply=False)
        self.assertIn('\n0 change(s) to make.', out.getvalue(), out.getvalue())

    def test_bridge_switch_over(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from corei_os_mini import setup_seed
        mq = self.ok(self.alice.post('/api/config/providers', json={'code': 'METAQUOTES', 'name': 'MetaQuotes', 'kind': 'BROKER'}), 201)
        conn = self.ok(self.alice.post('/api/config/connections', json={'provider_id': mq['id'], 'code': 'METAQUOTES_MT5_DEMO', 'name': 'MetaQuotes demo',
            'platform': 'MT5', 'environment': 'DEMO', 'purpose': 'BOTH', 'settings': {'server_name': 'MetaQuotes-Demo',
            'access_points': [{'name': 'MetaQuotes-Demo', 'host': '194.164.179.214', 'port': 443}]}}), 201)
        self.ok(self.alice.post('/api/config/accounts', json={'connection_id': conn['id'], 'code': 'METAQUOTES_DEMO_5056977914', 'name': 'MetaQuotes demo',
                                                             'external_account_id': '5056977914', 'base_currency': 'USD', 'trading_permission': 'TRADING'}), 201)
        with tempfile.TemporaryDirectory() as state:
            self.assertEqual(setup_seed.use_bridge('alice', 'METAQUOTES_DEMO_5056977914', 9443, state, apply=True), 1, 'no certificate yet')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=t',
                            '-keyout', f'{state}/corei-bridge.key', '-out', f'{state}/corei-bridge.crt'], check=True, capture_output=True)
            Path(state, 'corei-bridge.token').write_text('t' * 40)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(setup_seed.use_bridge('alice', 'METAQUOTES_DEMO_5056977914', 9443, state, apply=True), 0)
            self.assertNotIn('t' * 40, out.getvalue(), 'the token is never printed')
        with self.connect() as db:
            c = db.execute("SELECT adapter, settings FROM cfg_connections WHERE code='METAQUOTES_MT5_DEMO'").fetchone()
            self.assertEqual(c['adapter'], 'TERMINAL_BRIDGE')
            self.assertEqual((c['settings']['bridge']['host'], c['settings']['bridge']['port']), ('127.0.0.1', 9443))
            self.assertRegex(c['settings']['bridge']['fingerprint'], r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')
            self.assertEqual(db.execute("SELECT kind FROM cfg_credentials WHERE connection_id=%s", (conn['id'],)).fetchone()['kind'], 'BRIDGE_TOKEN')
