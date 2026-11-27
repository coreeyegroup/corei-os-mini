"""Integration gateway (release 0.65.0, migration 052): external Bearer access for CORE-AI, n8n and other applications.

Evidence for: a real Bearer key is needed and every refusal names its reason (missing, invalid, revoked, expired,
disabled, network, rate limit); keys are stored only as hashes and shown once; each endpoint checks its own permission
and every resource its scope (an empty list grants nothing); UNAVAILABLE capabilities are reported, never granted;
the analysis snapshot returns provenance and quality and lists missing data instead of inventing it, and shortens
candles (reporting it) or refuses rather than dropping a dataset; scan jobs are durable, de-duplicated, limited and
cancellable; candidate signals keep their provenance and a risk assessment never creates an order; paper orders are
idempotent on Idempotency-Key; LIVE is refused; webhooks refuse private destinations, are signed and keep one event id
across retries; workstation alerts fan out to subscribed webhooks; passwords need only 2 characters (Development mode).
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.api import app  # noqa: E402
from corei_os_mini.integration import webhooks  # noqa: E402


class Gateway(te.Execution):
    def setUp(self):
        super().setUp()
        self.ext = TestClient(app, base_url='https://testserver')   # an outside application: no session, no workstation header

    def tearDown(self):
        self.ext.close()
        super().tearDown()

    def connection(self, code='coreai', **extra):
        body = {'code': code, 'name': code.upper(), 'client_type': 'CORE_AI', 'reason': 'test connection', **extra}
        c = self.ok(self.alice.post('/api/config/integrations', json=body), 201)
        key = self.ok(self.alice.post(f"/api/config/integrations/{c['id']}/keys", json={'label': 'test'}), 201)
        return c, key['key']

    def get(self, key, path, **kw):
        return self.ext.get('/api/v1/automation' + path, headers={'Authorization': f'Bearer {key}'}, **kw)

    def post(self, key, path, body=None, headers=None):
        return self.ext.post('/api/v1/automation' + path, json=body or {}, headers={'Authorization': f'Bearer {key}', **(headers or {})})

    def err(self, result, status, code):
        self.assertEqual(result.status_code, status, result.text)
        self.assertEqual(result.json()['error']['code'], code, result.text)
        self.assertTrue(result.json()['error']['request_id'])

    def feed(self):
        connection, a, b, ins = self.real_account('DEMO')
        with self.connect() as db:
            db.execute("""INSERT INTO md_collection_plans(id, code, account_id, broker_symbol, instrument_id, status, created_by, updated_by)
                          VALUES (gen_random_uuid(), 'FEED_EURUSD', %s, 'EURUSD', %s, 'ACTIVE', 'alice', 'alice')""", (a['id'], ins['id']))
        return connection, a, b, ins

    # ---------- passwords (owner decision 27 Nov 2026) ----------

    def test_password_minimum_is_two_characters_without_complexity(self):
        # 0.66.0: 2 characters in Development mode (6 in Production: test_platform_mode).
        v = self.ok(self.alice.get('/api/config/platform-mode'))['version']
        self.ok(self.alice.put('/api/config/platform-mode', json={'mode': 'DEVELOPMENT', 'reason': 'test passwords', 'version': v}))
        self.ok(self.alice.post('/api/admin/users', json={'username': 'tester', 'display_name': 'Tester', 'roles': ['OBSERVER'], 'temporary_password': 'ab'}), 201)
        self.ok(self.alice.post('/api/admin/users', json={'username': 'tester2', 'display_name': 'T2', 'roles': ['OBSERVER'], 'temporary_password': 'a'}), 422)

    # ---------- authentication ----------

    def test_bearer_keys_every_refusal_has_a_reason_and_keys_are_never_stored(self):
        c, key = self.connection()
        self.err(self.ext.get('/api/v1/automation/auth/test'), 401, 'MISSING_CREDENTIAL')
        self.err(self.get('corei_nothing', '/auth/test'), 401, 'INVALID_CREDENTIAL')
        self.err(self.get(key[:-2] + 'xx', '/auth/test'), 401, 'INVALID_CREDENTIAL')
        self.err(self.ext.get('/api/v1/automation/auth/test', params={'key': key}), 401, 'MISSING_CREDENTIAL')   # never in a query string
        me = self.ok(self.get(key, '/auth/test'))
        self.assertEqual((me['connection'], me['authenticated']), ('coreai', True))
        self.assertIn('signals.propose', me['scopes'])
        self.assertFalse(any(s.startswith('orders.') and s != 'orders.read' for s in me['scopes']), 'profiles never grant order authority')
        listed = self.ok(self.alice.get('/api/config/integrations'))['items'][0]
        self.assertNotIn(key, json.dumps(listed))
        with self.connect() as db:
            stored = db.execute('SELECT key_hash FROM int_keys').fetchone()['key_hash']
            self.assertEqual(bytes(stored), hashlib.sha256(key.encode()).digest())
            self.assertTrue(db.execute("SELECT 1 FROM cfg_audit_log WHERE action='ISSUE_INTEGRATION_KEY'").fetchone())
        # Rotation keeps the old key for the grace period; revocation stops it at once.
        old = listed['keys'][0]
        new = self.ok(self.alice.post(f"/api/config/integrations/{c['id']}/keys/rotate", json={'old_key_id': old['id'], 'grace_hours': 1}), 201)
        self.ok(self.get(key, '/auth/test'))
        self.ok(self.get(new['key'], '/auth/test'))
        self.ok(self.alice.post(f"/api/config/integrations/keys/{old['id']}/revoke"))
        self.err(self.get(key, '/auth/test'), 401, 'REVOKED_CREDENTIAL')
        with self.connect() as db:
            db.execute("UPDATE int_keys SET expires_at=now() - interval '1 minute' WHERE prefix=%s", (new['prefix'],))
        self.err(self.get(new['key'], '/auth/test'), 401, 'EXPIRED_CREDENTIAL')
        _c2, k2 = self.connection('n8n', client_type='N8N')
        with self.connect() as db:
            db.execute("UPDATE int_clients SET status='DISABLED' WHERE code='n8n'")
        self.err(self.get(k2, '/auth/test'), 403, 'CONNECTION_DISABLED')
        with self.connect() as db:
            db.execute("UPDATE int_clients SET status='ACTIVE', allowed_networks='{198.51.100.0/24}' WHERE code='n8n'")
        self.err(self.get(k2, '/auth/test'), 403, 'NETWORK_NOT_ALLOWED')
        with self.connect() as db:
            db.execute("UPDATE int_clients SET allowed_networks='{}', requests_per_minute=3 WHERE code='n8n'")
            db.execute('DELETE FROM int_usage')   # refused requests count too; start the minute clean
        for _ in range(3):
            self.ok(self.get(k2, '/auth/test'))
        self.err(self.get(k2, '/auth/test'), 429, 'RATE_LIMITED')
        with self.connect() as db:
            logged = db.execute("SELECT status, error_code, key_prefix FROM int_usage WHERE error_code='RATE_LIMITED'").fetchone()
        self.assertEqual(logged['status'], 429)
        self.assertNotIn(k2, json.dumps(self.ok(self.alice.get('/api/config/integrations'))))

    # ---------- authorization ----------

    def test_each_endpoint_checks_its_permission_and_each_resource_its_scope(self):
        _c, ro = self.connection('custom', client_type='CUSTOM')
        self.ok(self.get(ro, '/system'))
        self.err(self.get(ro, '/instruments'), 403, 'PERMISSION_DENIED')
        caps = self.ok(self.get(ro, '/capabilities'))
        status = {s['scope']: s for s in caps['scopes']}
        self.assertEqual(status['orders.live_request']['status'], 'UNAVAILABLE')
        self.assertEqual(status['market_data.stream']['status'], 'UNAVAILABLE')
        self.assertEqual(caps['market_data']['datasets']['open_interest'], 'UNAVAILABLE')
        self.assertEqual(caps['orders']['live'], 'UNAVAILABLE')
        self.ok(self.alice.post('/api/config/integrations', json={'code': 'bad', 'name': 'Bad', 'scopes': ['orders.live_request'], 'reason': 'try live'}), 422)
        self.ok(self.alice.post('/api/config/integrations', json={'code': 'bad', 'name': 'Bad', 'scopes': ['*'], 'reason': 'try wildcard'}), 422)
        self.ok(self.bob.post('/api/config/integrations', json={'code': 'bobs', 'name': 'X', 'reason': 'bob is an administrator too'}), 201)
        _connection, a, _b, ins = self.feed()
        c, key = self.connection('scoped', scopes=['accounts.read', 'instruments.read', 'risk.assess', 'market_data.read'],
                                 access={'accounts': {'ids': []}, 'instruments': {'ids': []}, 'strategies': {'all': True}})
        self.assertEqual(self.ok(self.get(key, '/accounts'))['items'], [], 'an empty account list grants no account')
        self.assertEqual(self.ok(self.get(key, '/instruments'))['total'], 0)
        self.err(self.get(key, '/market-data/quotes', params={'instruments': 'EURUSD'}), 403, 'OUT_OF_SCOPE')
        body = {'account_id': a['id'], 'instrument': 'EURUSD', 'side': 'BUY', 'quantity': '0.10'}
        self.err(self.post(key, '/risk/assess', body), 403, 'OUT_OF_SCOPE')
        c = self.ok(self.alice.get('/api/config/integrations'))['items']
        row = next(x for x in c if x['code'] == 'scoped')
        self.ok(self.alice.put(f"/api/config/integrations/{row['id']}", json={
            'name': 'Scoped', 'client_type': 'CUSTOM', 'scopes': row['scopes'], 'version': row['version'], 'reason': 'grant one account',
            'access': {'accounts': {'ids': [a['id']]}, 'instruments': {'ids': [ins['id']]}, 'strategies': {'all': True}}}))
        self.assertEqual([x['code'] for x in self.ok(self.get(key, '/accounts'))['items']], ['VTM_A'])
        q = self.ok(self.get(key, '/market-data/quotes', params={'instruments': 'EURUSD'}))
        self.assertEqual(q['items'][0]['quote']['bid'], '1.08')
        self.assertEqual(q['items'][0]['quote']['kind'], 'POLLED_OBSERVATION')

    # ---------- market data snapshot ----------

    def test_snapshot_has_provenance_quality_and_lists_what_is_missing(self):
        _connection, a, _b, ins = self.feed()
        start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=5 * 60)
        start -= timedelta(minutes=start.minute % 5)
        self.bars(a['id'], [1.08 + i * 0.0001 for i in range(60)], start)
        with self.connect() as db:
            db.execute("""INSERT INTO intel_snapshot(account_id, broker_symbol, timeframe, bar_time, version, bars, bars_source, features, regime, volatility, confidence)
                          VALUES (%s,'EURUSD','M5',%s,1,60,'BROKER',%s,'TREND_UP','NORMAL',0.8)""",
                       (a['id'], start + timedelta(minutes=295), json.dumps({'close': 1.0859, 'ema20': 1.085, 'rsi14': 66.0, 'donchian_high20': 1.086, 'donchian_low20': 1.084})))
        _c, key = self.connection()
        body = {'request_id': 'snap-1', 'instruments': ['EURUSD', 'NOPE'], 'timeframes': ['M5', 'H4'],
                'include': ['instrument_metadata', 'latest_quote', 'candles', 'indicators', 'market_regime', 'support_resistance', 'data_quality',
                            'open_interest', 'greeks'], 'format': 'AI_COMPACT', 'candles_per_timeframe': 50}
        s = self.ok(self.post(key, '/market-data/snapshot', body))
        self.assertEqual((s['schema_version'], s['request_id']), ('corei.snapshot.v1', 'snap-1'))
        item = s['items'][0]
        self.assertEqual(item['source']['plan'], 'FEED_EURUSD')
        self.assertEqual(item['timeframes']['M5']['candles']['columns'], ['t', 'o', 'h', 'l', 'c', 'v'])
        self.assertEqual(item['timeframes']['M5']['candles']['count'], 50)
        self.assertEqual(item['timeframes']['M5']['market_regime']['regime'], 'TREND_UP')
        self.assertIn('M5 · TREND_UP', item['summary'][0])
        self.assertIn({'kind': 'RESISTANCE', 'level': 1.086, 'method': 'donchian_high20'}, item['timeframes']['M5']['support_resistance']['levels'])
        self.assertIn(item['data_quality']['state'], ('PASS', 'WARNING', 'FAIL', 'UNAVAILABLE'))
        missing = {(m.get('timeframe'), m['dataset'], m['code']) for m in s['missing_datasets']}
        self.assertIn((None, 'open_interest', 'CAPABILITY_UNAVAILABLE'), missing)
        self.assertIn((None, 'greeks', 'CAPABILITY_UNAVAILABLE'), missing)
        self.assertIn(('H4', 'indicators', 'NOT_COMPUTED'), missing)
        self.assertEqual(s['missing_instruments'][0]['code'], 'INVALID_INSTRUMENT')
        self.assertNotIn('open_interest', item, 'missing data is listed, never invented')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT request_id FROM int_snapshots').fetchone()['request_id'], 'snap-1')
        # A tight budget shortens candles (reported), never drops a dataset; an impossible one is refused.
        small = self.ok(self.post(key, '/market-data/snapshot', {**body, 'max_output_chars': 6000}))
        self.assertTrue(small['coverage']['reduced'])
        self.assertIn('indicators', small['items'][0]['timeframes']['M5'])
        self.err(self.post(key, '/market-data/snapshot', {**body, 'format': 'NORMALIZED', 'max_output_chars': 2000}), 413, 'REQUEST_SIZE_EXCEEDED')
        self.err(self.post(key, '/market-data/snapshot', {**body, 'include': ['tea_leaves']}), 422, 'INVALID_DATASET')
        candles = self.ok(self.get(key, '/market-data/candles', params={'instrument': 'EURUSD', 'timeframe': 'M5', 'limit': 10}))
        self.assertEqual((candles['count'], candles['closed_only'], candles['source']['plan']), (10, True, 'FEED_EURUSD'))

    # ---------- jobs ----------

    def test_scan_jobs_are_durable_deduplicated_limited_and_cancellable(self):
        self.feed()
        s = self.ok(self.alice.post('/api/config/strategies', json={'code': 'GW_EMA', 'name': 'GW'}), 201)
        v = self.ok(self.alice.post(f"/api/config/strategies/{s['id']}/versions",
                                    json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 5}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{v['id']}/approve", json={'version': v['version']}))
        c, key = self.connection(max_concurrent_jobs=1)
        strategies = self.ok(self.get(key, '/strategies'))['items']
        self.assertEqual(strategies[0]['approved_version']['algorithm'], 'EMA_CROSSOVER')
        j = self.ok(self.post(key, '/strategies/scan', {'request_id': 'scan-1', 'mode': 'strategy', 'strategies': ['GW_EMA']}), 202)
        self.assertEqual(j['status'], 'QUEUED')
        again = self.ok(self.post(key, '/strategies/scan', {'request_id': 'scan-1', 'mode': 'strategy', 'strategies': ['GW_EMA']}), 202)
        self.assertEqual((again['job_id'], again['duplicate']), (j['job_id'], True))
        self.err(self.post(key, '/strategies/scan', {'request_id': 'scan-2', 'mode': 'instrument'}), 429, 'TOO_MANY_JOBS')
        self.assertEqual(self.ok(self.get(key, f"/jobs/{j['job_id']}"))['job_id'], j['job_id'])
        _c2, other = self.connection('other')
        self.err(self.get(other, f"/jobs/{j['job_id']}"), 404, 'JOB_NOT_FOUND')
        self.assertEqual(self.ok(self.post(key, f"/jobs/{j['job_id']}/cancel"))['status'], 'CANCELLED')
        with self.connect() as db:
            db.execute("INSERT INTO int_webhooks(id, client_id, url, events, secret, created_by) VALUES (gen_random_uuid(), %s, 'https://hooks.example.com/x', "
                       "ARRAY['job.failed'], '\\x00', 'alice')", (c['id'],))
            db.execute("UPDATE int_clients SET scopes = scopes || ARRAY['events.subscribe'] WHERE id=%s", (c['id'],))
        self.assertEqual(webhooks.job_notices(), 1)
        self.assertEqual(webhooks.job_notices(), 0, 'one notice per job')
        with self.connect() as db:
            d = db.execute('SELECT event, payload FROM int_webhook_deliveries').fetchone()
        self.assertEqual((d['event'], d['payload']['data']['status']), ('job.failed', 'CANCELLED'))

    # ---------- signals, risk and orders ----------

    def test_signal_provenance_and_risk_assessment_create_no_order(self):
        _connection, a, _b, ins = self.feed()
        c, key = self.connection(access={'accounts': {'all': True}, 'instruments': {'all': True}, 'strategies': {'all': True}})
        self.err(self.post(key, '/signals', {'request_id': 's1', 'instrument': 'EURUSD', 'direction': 'LONG', 'provenance': {}}), 422, 'MISSING_PROVENANCE')
        self.err(self.post(key, '/signals', {'request_id': 's1', 'instrument': 'EURUSD', 'direction': 'LONG',
                                             'provenance': {'model': 'm', 'input_snapshot_id': '00000000-0000-0000-0000-000000000001'}}), 422, 'INVALID_SNAPSHOT')
        body = {'request_id': 's1', 'instrument': 'EURUSD', 'account_id': a['id'], 'direction': 'LONG', 'entry': '1.0801', 'stop_loss': '1.0780',
                'targets': ['1.0840'], 'strength': '0.7', 'evidence': ['EMA20 above EMA50 on H1'], 'provenance': {'application': 'CORE-AI', 'agent': 'Forex Agent', 'model': 'gpt-x'}}
        s = self.ok(self.post(key, '/signals', body), 201)
        self.assertEqual((s['status'], s['provenance']['agent'], s['provenance']['connection']), ('PROPOSED', 'Forex Agent', 'coreai'))
        self.assertTrue(self.ok(self.post(key, '/signals', body), 201)['duplicate'])
        with self.connect() as db:
            before = db.execute('SELECT count(*) AS n FROM oms_orders').fetchone()['n']
        r = self.ok(self.post(key, '/risk/assess', {'signal_id': s['id'], 'quantity': '0.10'}))
        self.assertIn(r['decision'], ('APPROVED', 'REJECTED', 'UNAVAILABLE'))
        self.assertIn('not an order authorization', r['note'])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM oms_orders').fetchone()['n'], before, 'an assessment never creates an order')
            sig = db.execute('SELECT status, risk_decision FROM int_signals').fetchone()
        self.assertEqual(sig['risk_decision'], r['decision'])
        self.assertEqual(self.ok(self.get(key, '/signals'))['items'][0]['request_id'], 's1')

    def test_paper_orders_are_idempotent_and_live_is_refused(self):
        _connection, a, _b, ins = self.feed()
        paper = self.ok(self.alice.post('/api/trading/paper-accounts', json={'code': 'PAPER_GW', 'name': 'GW', 'currency': 'USD', 'amount': '50000',
                                                                              'reason': 'gateway test'}), 201)
        c, key = self.connection('trader', client_type='N8N', scopes=['orders.read', 'orders.preview', 'orders.paper', 'orders.cancel'],
                                 access={'accounts': {'ids': [paper['id'], a['id']]}, 'instruments': {'all': True}, 'strategies': {'all': True}})
        order = {'account_id': paper['id'], 'instrument': 'EURUSD', 'side': 'BUY', 'quantity': '0.10', 'mode': 'PAPER'}
        self.err(self.post(key, '/orders', order), 422, 'INVALID_REQUEST')        # no Idempotency-Key
        p = self.ok(self.post(key, '/orders/preview', order))
        self.assertTrue(p['dry_run'])
        first = self.ok(self.post(key, '/orders', order, {'Idempotency-Key': 'n8n-run-0001'}), 201)
        second = self.ok(self.post(key, '/orders', order, {'Idempotency-Key': 'n8n-run-0001'}), 201)
        self.assertEqual(first['orders'][0]['id'], second['orders'][0]['id'])
        self.assertTrue(second['duplicate'])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM oms_orders WHERE created_by='api:trader'").fetchone()['n'], 1)
        self.err(self.post(key, '/orders', {**order, 'mode': 'LIVE'}, {'Idempotency-Key': 'n8n-run-0002'}), 403, 'LIVE_NOT_ENABLED')
        self.err(self.post(key, '/orders', {**order, 'account_id': a['id'], 'mode': 'DEMO'}, {'Idempotency-Key': 'n8n-run-0003'}), 403, 'PERMISSION_DENIED')
        with self.connect() as db:
            db.execute("UPDATE int_clients SET scopes = scopes || ARRAY['orders.demo'] WHERE code='trader'")
        self.err(self.post(key, '/orders', {**order, 'account_id': a['id'], 'mode': 'DEMO'}, {'Idempotency-Key': 'n8n-run-0003'}), 403, 'ENVIRONMENT_NOT_ALLOWED')
        self.err(self.post(key, '/orders', {**order, 'account_id': _b['id']}, {'Idempotency-Key': 'n8n-run-0004'}), 403, 'OUT_OF_SCOPE')
        self.assertEqual(len(self.ok(self.get(key, '/orders'))['items']), 1)

    # ---------- webhooks ----------

    def test_webhooks_refuse_private_destinations_are_signed_and_keep_one_event_id(self):
        c, key = self.connection('hooks', client_type='N8N')
        self.err(self.post(key, '/webhooks', {'url': 'https://127.0.0.1/hook', 'events': ['signal.created']}), 422, 'UNSAFE_DESTINATION')
        self.err(self.post(key, '/webhooks', {'url': 'http://hooks.example.com/x', 'events': ['signal.created']}), 422, 'UNSAFE_DESTINATION')
        self.err(self.post(key, '/webhooks', {'url': 'https://hooks.example.com/x', 'events': ['nope']}), 422, 'INVALID_EVENT')
        public = {'93.184.216.34'}
        with patch.object(webhooks, '_resolve', return_value=public):
            hook = self.ok(self.post(key, '/webhooks', {'url': 'https://hooks.example.com/x', 'events': ['order.event', 'webhook.test']}), 201)
        secret = hook['signing_secret']
        self.assertNotIn(secret, json.dumps(self.ok(self.get(key, '/webhooks'))))
        # A workstation alert reaches the subscribed webhook.
        from corei_os_mini import alerts
        with store.transaction('write') as db:
            alerts.enqueue(db, 'TRADING', 'WARNING', 'Order refused: EURUSD', 'MAX_DAILY_LOSS')
        sent = []

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fail(req, timeout):
            raise OSError('connection refused')

        def ok(req, timeout):
            sent.append(req)
            return Resp()
        with patch.object(webhooks, '_resolve', return_value=public), patch.object(webhooks._opener, 'open', side_effect=fail):
            self.assertEqual(webhooks.deliver_pending(), 0)
        with self.connect() as db:
            d = db.execute('SELECT * FROM int_webhook_deliveries').fetchone()
            self.assertEqual((d['status'], d['attempts']), ('PENDING', 1))
            db.execute('UPDATE int_webhook_deliveries SET next_at=now()')
        with patch.object(webhooks, '_resolve', return_value=public), patch.object(webhooks._opener, 'open', side_effect=ok):
            self.assertEqual(webhooks.deliver_pending(), 1)
        req = sent[0]
        body, ts = req.data, req.get_header('X-corei-timestamp')
        self.assertEqual(req.get_header('X-corei-signature'), 'sha256=' + hmac.new(secret.encode(), ts.encode() + b'.' + body, hashlib.sha256).hexdigest())
        self.assertEqual(req.get_header('X-corei-event-id'), str(d['event_id']), 'the same event id on every attempt')
        self.assertEqual(json.loads(body)['event'], 'order.event')

    def test_openapi_documents_only_the_gateway(self):
        doc = self.ext.get('/api/v1/automation/openapi.json').json()
        self.assertTrue(doc['paths'])
        self.assertTrue(all(p.startswith('/api/v1/automation') for p in doc['paths']))
        self.assertIn('/api/v1/automation/market-data/snapshot', doc['paths'])
        from corei_os_mini.integration.gateway import ENDPOINT_SCOPES
        documented = {(m, p) for p, ms in doc['paths'].items() for m in ms}
        self.assertEqual(documented, set(ENDPOINT_SCOPES), 'every endpoint documents its required permission')
        self.assertEqual(doc['paths']['/api/v1/automation/orders']['post']['x-required-scopes'], ['orders.paper (mode PAPER)', 'orders.demo (mode DEMO)'])


for _name in [n for n in dir(te.Execution) if n.startswith('test_')]:
    if _name not in Gateway.__dict__:
        setattr(Gateway, _name, None)


def test_n8n_workflows_are_importable_and_hold_no_secrets():
    """integrations/n8n: valid JSON, every HTTP node uses the named Bearer credential, no key or secret inside, paper only."""
    import json
    from pathlib import Path
    folder = Path(__file__).resolve().parents[2] / 'integrations' / 'n8n'
    files = sorted(folder.glob('*.json'))
    assert [f.name[0] for f in files] == list('ABCDEF')
    for f in files:
        text = f.read_text()
        assert not re.search(r'corei_[A-Za-z0-9]{10}_', text) and 'whsec_' not in text, f.name
        wf = json.loads(text)
        names = {n['name'] for n in wf['nodes']}
        assert set(wf['connections']) <= names
        for n in wf['nodes']:
            if n['type'] == 'n8n-nodes-base.httpRequest':
                assert n['parameters']['genericAuthType'] == 'httpBearerAuth'
                assert n['credentials'] == {'httpBearerAuth': {'name': 'COREI Gateway'}}
                assert '/api/v1/automation/' in n['parameters']['url']
        assert '"LIVE"' not in text, f.name
