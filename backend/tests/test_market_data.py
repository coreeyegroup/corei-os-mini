"""Market data (release 0.13.0): bars, quality, journal, schedules, collection plans and
switches, the collector end to end (journal → database, reconnect, backfill, feed switch),
the nightly archive to fingerprinted Parquet, verification, backups, the history console
and the pre-send price check. PostgreSQL tests need COREI_TEST_DATABASE_URL."""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from corei_os_mini import operator_auth
from corei_os_mini.api import app
from corei_os_mini.md import jobs, journal as md_journal
from corei_os_mini.md.bars import BarBuilder
from corei_os_mini.md.collector import Collector, scheduled
from corei_os_mini.md.quality import Limits, SymbolQuality, consensus_deviation
from corei_os_mini.operator_auth import hash_password

HEADERS = {'x-corei-request': 'workstation'}
PASSWORD = 'Strong-pass-5521'
FAKE = Path(__file__).parent / 'fixtures' / 'fake_stream_reader.py'
T0 = datetime(2026, 11, 11, 10, 0, 0, tzinfo=timezone.utc)


class Pure(unittest.TestCase):
    def test_bars_from_ticks(self):
        b = BarBuilder(['M1', 'M5'])
        self.assertEqual(b.add(T0 + timedelta(seconds=5), 1.1000, 1.1002), [])
        b.add(T0 + timedelta(seconds=20), 1.1010, 1.1013)
        b.add(T0 + timedelta(seconds=40), 1.0990, 1.0991)
        closed = b.add(T0 + timedelta(seconds=65), 1.1005, 1.1006)
        self.assertEqual(len(closed), 1)
        m1 = closed[0]
        self.assertEqual((m1['timeframe'], m1['open'], m1['high'], m1['low'], m1['close'], m1['ticks'], m1['complete']),
                         ('M1', 1.1, 1.101, 1.099, 1.099, 3, True))
        self.assertAlmostEqual(m1['spread_avg'], 0.0002, places=6)
        self.assertEqual(b.add(T0 + timedelta(seconds=1), 9.9, 9.91), [])   # late tick ignored for bars
        due = b.due(T0 + timedelta(minutes=5, seconds=1))
        self.assertEqual(sorted(x['timeframe'] for x in due), ['M1', 'M5'])
        self.assertEqual(b.partial(), [])

    def test_quality_measures_and_flags_once(self):
        q = SymbolQuality(Limits(max_latency_ms=100, max_gap_seconds=2, max_spread_points=30, stale_seconds=5), point=0.00001)
        events = []
        for i in range(60):
            rows, ev = q.on_tick(T0 + timedelta(milliseconds=200 * i), T0 + timedelta(milliseconds=200 * i - 50), 1.1 + (i % 3) * 1e-5, 1.10002 + (i % 3) * 1e-5)
            events += ev
        self.assertEqual(events, [])
        _, ev = q.on_tick(T0 + timedelta(seconds=15), T0 + timedelta(seconds=14.5), 1.1, 1.1005)  # gap, latency, spread
        kinds = sorted(e['kind'] for e in ev)
        self.assertEqual(kinds, ['GAP', 'LATENCY', 'SPREAD'])
        _, ev = q.on_tick(T0 + timedelta(seconds=15.2), T0 + timedelta(seconds=14.6), 1.1, 1.1005)
        self.assertEqual(ev, [])  # already flagged: not repeated
        self.assertEqual([e['kind'] for e in q.stale(T0 + timedelta(seconds=25))], ['STALE'])
        self.assertEqual(q.stale(T0 + timedelta(seconds=26)), [])
        rows = q.roll(T0 + timedelta(minutes=1, seconds=1))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['ticks'], 62)
        self.assertGreater(row['max_gap_ms'], 3000)
        self.assertAlmostEqual(row['score'], 60.0, delta=0.5)  # gap −25, spread −15, a little frozen time
        self.assertEqual(q.roll(T0 + timedelta(minutes=2, seconds=1))[0]['score'], 0.0)  # an empty minute scores zero

    def test_spike_that_reverses(self):
        q = SymbolQuality(Limits(), point=0.00001)
        price = 1.1
        for i in range(80):
            price += (1 if i % 2 else -1) * 0.00001
            q.on_tick(T0 + timedelta(seconds=i), None, price, price + 0.0001)
        q.on_tick(T0 + timedelta(seconds=81), None, price + 0.01, price + 0.0101)
        _, ev = q.on_tick(T0 + timedelta(seconds=82), None, price, price + 0.0001)
        self.assertEqual([e['kind'] for e in ev], ['SPIKE'])

    def test_consensus_and_schedules(self):
        self.assertAlmostEqual(consensus_deviation(1.1011, [1.1000, 1.1000, 1.2]), 10.0, places=3)
        self.assertIsNone(consensus_deviation(1.1, []))
        plan = {'schedule': 'WINDOW', 'window_start': dtime(22, 0), 'window_end': dtime(2, 0), 'window_days': [3]}
        self.assertTrue(scheduled(plan, datetime(2026, 11, 11, 23, 0, tzinfo=timezone.utc)))   # Wednesday
        self.assertFalse(scheduled(plan, datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)))
        sessions = {'schedule': 'SESSIONS', 'holidays': [{'date': '2026-11-11', 'name': 'X'}]}
        self.assertFalse(scheduled(sessions, datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)))
        self.assertTrue(scheduled(sessions, datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)))
        self.assertFalse(scheduled({'schedule': 'WHILE_DEPLOYED', 'deployed': False}, T0))

    def test_journal_appends_and_reads_back(self):
        with tempfile.TemporaryDirectory() as root:
            j = md_journal.Journal(Path(root), 'ACC')
            j.write([b'{"a":1}', b'{"a":2}\n'], T0)
            j.write([b'{"a":3}'], T0)
            j.close()
            path = Path(root) / 'journal' / '2026-11-11' / 'ACC.jsonl.gz'
            self.assertEqual(md_journal.read(path), [b'{"a":1}', b'{"a":2}', b'{"a":3}'])


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class MarketData(unittest.TestCase):
    def setUp(self):
        self.url = os.environ['COREI_TEST_DATABASE_URL']
        self.schema = 'test_md_' + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            for path in sorted((Path(__file__).parents[1] / 'sql').glob('[0-9][0-9][0-9]_*.sql')):
                db.execute(path.read_text())
            db.execute("UPDATE cfg_policies SET value='false'::jsonb WHERE key='four_eyes_live_changes'")
            for name in ('alice', 'bob'):
                db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                           "VALUES (%s,%s,%s,%s,ARRAY['ADMIN','TRADING_OPS','RISK_APPROVER'],false)", (uuid4(), name, name.title(), hash_password(PASSWORD)))
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name) / 'md'
        key = Path(self.folder.name) / 'key'
        key.write_bytes(os.urandom(32)); key.chmod(0o600)
        reader = Path(self.folder.name) / 'reader'
        reader.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n'); reader.chmod(0o700)
        self.patches = [patch('corei_os_mini.config_store._connect', self.connect),
                        patch.dict(os.environ, {'COREI_BROKER_KEY_FILE': str(key), 'COREI_MT5_CATALOG_PARSER': str(reader),
                                                'COREI_MD_ROOT': str(self.root), 'FAKE_SILENT_LOGINS': '1002'})]
        for p in self.patches:
            p.start()
        operator_auth.attempts.clear()
        self.alice, self.bob = self.sign_in('alice'), self.sign_in('bob')
        self.collector = None

    def tearDown(self):
        if self.collector:
            self.collector.shutdown()
        for c in (self.alice, self.bob):
            c.close()
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()
        with psycopg.connect(self.url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def connect(self, _=None):
        return psycopg.connect(self.url, options='-c search_path=' + self.schema, row_factory=dict_row)

    def sign_in(self, username):
        client = TestClient(app, base_url='https://testserver', headers=HEADERS)
        self.assertEqual(client.post('/api/operator/login', json={'username': username, 'password': PASSWORD}).status_code, 200)
        return client

    def ok(self, result, status=200):
        self.assertEqual(result.status_code, status, result.text)
        return result.json() if result.content else None

    def setup_accounts(self, environment='DEMO'):
        provider = self.ok(self.alice.post('/api/config/providers', json={'code': 'VTM', 'name': 'VT Markets', 'kind': 'BROKER'}), 201)
        connection = self.ok(self.alice.post('/api/config/connections', json={
            'provider_id': provider['id'], 'code': 'VTM_L3', 'name': 'Live 3', 'platform': 'MT5', 'environment': environment, 'purpose': 'BOTH',
            'settings': {'server_name': 'VTMarkets-Live 3', 'access_points': [{'name': 'Live 3', 'host': '198.51.100.10', 'port': 443}]}}), 201)
        accounts = []
        for code, login in (('VTM_A', '1001'), ('VTM_B', '1002'), ('VTM_C', '1003')):
            a = self.ok(self.alice.post('/api/config/accounts', json={'connection_id': connection['id'], 'code': code, 'name': code,
                                                                     'external_account_id': login, 'base_currency': 'USD'}), 201)
            self.ok(self.alice.put(f"/api/config/accounts/{a['id']}/credential",
                                   json={'credential': {'kind': 'MT5_LOGIN', 'password': 'investor', 'access_level': 'READ_ONLY'}}))
            accounts.append(a)
        instrument = self.ok(self.alice.post('/api/config/instruments', json={
            'symbol': 'EURUSD', 'name': 'Euro / US dollar', 'asset_class': 'FX', 'base_currency': 'EUR', 'quote_currency': 'USD',
            'price_precision': 5, 'quantity_precision': 2, 'tick_size': '0.00001', 'min_quantity': '0.01', 'quantity_step': '0.01',
            'contract_size': '100000', 'contract_unit': 'EUR'}), 201)
        return connection, accounts, instrument

    def plan(self, account, instrument, code, role='PRIMARY', purpose='TRADING_FEED', **extra):
        body = {'code': code, 'account_id': account['id'], 'broker_symbol': 'EURUSD', 'instrument_id': instrument['id'] if instrument else None,
                'purpose': purpose, 'feed_role': role, 'data_types': ['QUOTES', 'BARS'], 'bar_timeframes': ['S1', 'M1'],
                'stale_seconds': 1, 'max_spread_points': 50, **extra}
        return self.ok(self.alice.post('/api/md/plans', json=body), 201)

    def start(self, plan):
        return self.ok(self.alice.post(f"/api/md/plans/{plan['id']}/status", json={'status': 'ACTIVE', 'version': plan['version'], 'reason': 'Start collecting'}))

    def run_collector(self, seconds, collector=None):
        c = collector or self.collector
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            c.sync()
            c.flush()
            time.sleep(0.25)

    def test_plans_estimate_controls_and_jobs(self):
        _, (a, b, _c), instrument = self.setup_accounts('LIVE')
        bad = self.ok(self.alice.post('/api/md/plans', json={'code': 'X1', 'account_id': a['id'], 'broker_symbol': 'EURUSD'}), 422)
        self.assertIn('names the instrument', json.dumps(bad))
        p = self.plan(a, instrument, 'EU_A')
        self.assertEqual((p['status'], p['instrument_symbol'], p['environment']), ('DRAFT', 'EURUSD', 'LIVE'))
        dup = self.ok(self.alice.post('/api/md/plans', json={'code': 'EU_A2', 'account_id': b['id'], 'broker_symbol': 'EURUSD',
                                                             'instrument_id': instrument['id']}), 409)
        self.assertIn('already has a primary', dup['detail'])
        est = self.ok(self.alice.post('/api/md/estimate', json={'code': 'EST', 'account_id': a['id'], 'broker_symbol': 'EURUSD',
                                                               'instrument_id': instrument['id'], 'bar_timeframes': ['M1'], 'conflate_ms': 1000}))
        self.assertEqual((est['ticks_per_day'], est['bars_per_day'], est['rate_source']), (86400, 1441, 'TYPICAL'))
        # Four-eyes for starting a LIVE trading feed (both policies on).
        with self.connect() as db:
            db.execute("UPDATE cfg_policies SET value='true'::jsonb WHERE key='four_eyes_live_changes'")
        held = self.ok(self.alice.post(f"/api/md/plans/{p['id']}/status", json={'status': 'ACTIVE', 'version': p['version'], 'reason': 'Go live'}), 202)
        self.ok(self.alice.post(f"/api/config/approvals/{held['change_request']['id']}/decision", json={'approve': True}), 403)
        self.ok(self.bob.post(f"/api/config/approvals/{held['change_request']['id']}/decision", json={'approve': True}))
        active = next(x for x in self.ok(self.alice.get('/api/md/plans'))['items'] if x['id'] == p['id'])
        self.assertEqual(active['status'], 'ACTIVE')
        # Switches: version-checked, audited.
        switch = next(c for c in self.ok(self.alice.get('/api/md/controls'))['items'] if c['scope'] == 'ALL')
        self.ok(self.alice.put('/api/md/controls/ALL', json={'paused': True, 'version': switch['version'], 'reason': 'Maintenance'}))
        self.ok(self.alice.put('/api/md/controls/ALL', json={'paused': False, 'version': switch['version'], 'reason': 'Again'}), 409)
        self.ok(self.alice.put(f"/api/md/controls/account:{b['id']}", json={'paused': True, 'version': 0, 'reason': 'Broker issue'}))
        # Backfill jobs.
        job = self.ok(self.alice.post('/api/md/jobs', json={'plan_id': p['id'], 'timeframe': 'M5', 'range_from': '2026-11-01T00:00:00Z',
                                                            'range_to': '2026-11-02T00:00:00Z', 'reason': 'History for research'}), 201)
        self.ok(self.alice.post('/api/md/jobs', json={'plan_id': p['id'], 'timeframe': 'M5', 'range_from': '2026-11-01T00:00:00Z',
                                                      'range_to': '2026-11-02T00:00:00Z', 'reason': 'Again'}), 409)
        self.assertEqual(self.ok(self.alice.post(f"/api/md/jobs/{job['id']}/cancel"))['status'], 'CANCELLED')
        audit = {x['action'] for x in self.ok(self.alice.get('/api/config/audit'))['items']}
        self.assertTrue({'MD_PAUSE', 'MD_BACKFILL', 'MD_BACKFILL_CANCEL', 'APPLY_CHANGE'} <= audit)

    def test_collector_end_to_end(self):
        _, (a, b, c), instrument = self.setup_accounts()
        primary = self.start(self.plan(a, instrument, 'EU_A', on_breach='SWITCH'))
        backup = self.start(self.plan(b, instrument, 'EU_B', role='BACKUP'))
        reference = self.start(self.plan(c, instrument, 'EU_C', role='REFERENCE', purpose='REFERENCE'))
        self.collector = Collector(self.root)
        self.run_collector(3.0)
        with self.connect() as db:
            n = {r['account_id']: r['n'] for r in db.execute('SELECT account_id, count(*) AS n FROM md_ticks GROUP BY 1').fetchall()}
            self.assertGreater(n[UUID(a['id'])], 30)
            latest = db.execute('SELECT * FROM md_latest').fetchall()
            self.assertEqual(len(latest), 3)
            tick = db.execute('SELECT * FROM md_ticks ORDER BY received_at LIMIT 1').fetchone()
            # Broker time (UTC+2 on the server) is stored as UTC: about 40 ms before receipt.
            self.assertLess(abs((tick['received_at'] - tick['source_time']).total_seconds() - 0.04), 0.02)
            bars = db.execute("SELECT * FROM md_bars WHERE timeframe='S1' AND complete").fetchall()
            self.assertTrue(bars)
            status = {r['state'] for r in db.execute('SELECT state FROM md_collector_status').fetchall()}
            self.assertEqual(status, {'CONNECTED'})
            kinds = [r['kind'] for r in db.execute('SELECT kind FROM md_events ORDER BY id').fetchall()]
        self.assertEqual(kinds.count('CONNECTED'), 3)
        self.assertIn('STALE', kinds)           # the backup went silent
        self.assertIn('DEVIATION', kinds)       # the reference account quotes 200 points away
        # The journal holds every price the database holds (journal first).
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        self.collector.flush()
        lines = md_journal.read(self.root / 'journal' / day / 'VTM_A.jsonl.gz')
        quotes = sum(1 for line in lines if b'"t": "q"' in line or b'"t":"q"' in line)
        with self.connect() as db:
            stored = db.execute('SELECT count(*) AS n FROM md_ticks WHERE account_id=%s', (a['id'],)).fetchone()['n']
        self.assertGreaterEqual(quotes, stored)
        # Screens: plans with live price and score, latest, quality comparison, collectors.
        plans = {p['code']: p for p in self.ok(self.alice.get('/api/md/plans'))['items']}
        self.assertIsNotNone(plans['EU_A']['bid'])
        self.assertTrue(plans['EU_A']['active_feed'])
        self.assertEqual(len(self.ok(self.alice.get('/api/md/latest'))['items']), 3)
        self.assertTrue(self.ok(self.alice.get('/api/md/collectors'))['items'])

        # Price check before an order: fresh price passes; a silent feed blocks.
        good = self.ok(self.alice.post(f"/api/config/accounts/{a['id']}/order-check", json={'broker_symbol': 'EURUSD', 'side': 'BUY', 'volume': '0.10'}))
        self.assertIsNotNone(good['live_price'])
        self.assertFalse([p for p in good['problems'] if 'live price' in p])
        silent = self.ok(self.alice.post(f"/api/config/accounts/{b['id']}/order-check", json={'broker_symbol': 'EURUSD', 'side': 'BUY', 'volume': '0.10'}))
        self.assertTrue([p for p in silent['problems'] if 'old (limit 1 s)' in p])

        # Feed switch: the primary goes silent → backup takes over (backup must be healthy, so swap roles of silence).
        self.collector.shutdown()
        with self.connect() as db:
            db.execute('UPDATE md_collector_status SET state=%s', ('STOPPED',))
        os.environ['FAKE_SILENT_LOGINS'] = '1001'
        self.collector = Collector(self.root)
        self.collector.SWITCH_AFTER = 0.5
        self.run_collector(3.5)
        with self.connect() as db:
            state = db.execute('SELECT * FROM md_feed_state').fetchone()
            switched = db.execute("SELECT detail FROM md_events WHERE kind='FEED_SWITCH'").fetchone()
        self.assertEqual(str(state['active_plan_id']), backup['id'])
        self.assertEqual((switched['detail']['from'], switched['detail']['to']), ('EU_A', 'EU_B'))
        self.collector.shutdown()
        self.collector = None

        # Reconnect after an outage queues and runs a backfill from broker history.
        with self.connect() as db:
            db.execute("UPDATE md_collection_plans SET status='PAUSED' WHERE code IN ('EU_B','EU_C')")
        os.environ['FAKE_SILENT_LOGINS'] = ''
        self.collector = Collector(self.root)
        self.run_collector(1.0)
        worker = self.collector.workers[a['id']]
        worker.outage_since = datetime.now(timezone.utc) - timedelta(minutes=3)
        worker._connected({'host': '198.51.100.10', 'port': 443, 'server_time': None, 'utc_now': None})
        self.run_collector(2.0)
        with self.connect() as db:
            job = db.execute("SELECT * FROM md_jobs WHERE requested_by='collector'").fetchone()
            broker_bars = db.execute("SELECT count(*) AS n FROM md_bars WHERE source='BROKER'").fetchone()['n']
        self.assertEqual(job['status'], 'DONE')
        self.assertGreater(broker_bars, 0)

        # Nightly: archive (as if 20 days later), verify, tamper detection, history query, backups.
        self.collector.shutdown()
        self.collector = None
        future = (datetime.now(timezone.utc) + timedelta(days=20)).date()
        summary = jobs.archive(self.root, now=future)
        self.assertEqual(summary['errors'], [])
        self.assertEqual(summary['tick_files'], 3)
        self.assertGreaterEqual(summary['partitions_dropped'], 1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM md_ticks').fetchone()['n'], 0)
            files = db.execute("SELECT * FROM md_files WHERE kind='TICKS'").fetchall()
        self.assertTrue(all(Path(f['path']).is_file() and len(f['sha256']) == 64 for f in files))
        history = self.ok(self.alice.post('/api/infra/history/query', json={
            'sql': "SELECT account, count(*) AS n, min(bid) AS low FROM ticks GROUP BY account ORDER BY account"}))
        self.assertEqual([r['account'] for r in history['rows']], ['VTM_A', 'VTM_B', 'VTM_C'])
        self.assertEqual(sum(r['n'] for r in history['rows']), sum(f['rows'] for f in files))
        for statement in ("SELECT * FROM read_csv('/etc/passwd')", "COPY ticks TO '/tmp/x.csv'", 'SELECT 1; SELECT 2'):
            self.ok(self.alice.post('/api/infra/history/query', json={'sql': statement}), 422)
        self.assertEqual(jobs.journals(self.root, now=future)['fingerprinted'], 3)
        self.assertEqual(jobs.verify()['ok'], 6)
        target = Path(files[0]['path'])
        target.write_bytes(target.read_bytes() + b'x')
        result = jobs.verify()
        self.assertEqual(result['mismatch'], [str(target)])
        with self.connect() as db:
            critical = db.execute("SELECT * FROM md_events WHERE kind='VERIFY' AND severity='CRITICAL'").fetchone()
            alert_kinds = [r['event'] for r in db.execute('SELECT event FROM cfg_alert_outbox').fetchall()]
        self.assertIsNotNone(critical)
        self.assertEqual(jobs.backup(self.root)['database'], None)  # not configured: recorded, not silent
        backup_dir = Path(self.folder.name) / 'backups'
        dump = '/usr/lib/postgresql/16/bin/pg_dump'
        if Path(dump).is_file():
            with patch.dict(os.environ, {'COREI_BACKUP_DIR': str(backup_dir), 'COREI_PG_DUMP': dump, 'COREI_DATABASE_URL': self.url}):
                done = jobs.backup(self.root)
            self.assertTrue(Path(done['database']['path']).is_file())
            self.assertEqual(done['files']['copied'], done['files']['files'] - 1)
            self.assertEqual(len(done['files']['changed_not_copied']), 1)   # the tampered file is not backed up over a good copy
            self.assertTrue(done['files']['same_disk'])
        # Health shows all of it.
        report = self.ok(self.alice.get('/api/infra/health'))
        names = {c['name']: c for c in report['checks']}
        self.assertEqual(names['File fingerprints']['status'], 'FAIL')
        self.assertIn('Nightly archive', names)
        self.assertIn('Database backup', names)


class Explain(unittest.TestCase):
    def test_broker_errors_in_plain_words(self):
        from corei_os_mini.md.explain import explain
        old = explain('ServerException: OLD_VERSION')
        self.assertEqual((old['title'], old['permanent']), ('Broker refused the connector version', True))
        self.assertTrue(explain('Invalid account')['permanent'])
        self.assertFalse(explain('TimeoutException: Connect did not finish in 20 s')['permanent'])
        self.assertEqual(explain('something new')['title'], 'Connection problem')
        self.assertIsNone(explain(None))
        # An unnamed refusal code (seen live: 1035) is permanent: back off, do not hammer the broker.
        code = explain('ServerException: 1035 [Code=1035 (1035)] (with client build 6230; the SDK default build was refused as OLD_VERSION)')
        self.assertEqual((code['title'], code['permanent']), ('Broker refused the login (unlisted code)', True))
        self.assertTrue(explain('ServerException: 1035')['permanent'])


@unittest.skipUnless(os.getenv('COREI_TEST_DATABASE_URL'), 'Set COREI_TEST_DATABASE_URL for PostgreSQL integration tests')
class ShellOverview(MarketData):
    # Reuses MarketData's set-up only; its own tests run once, in MarketData.
    test_plans_estimate_controls_and_jobs = None
    test_collector_end_to_end = None

    def test_refused_login_reported_once_and_on_overview(self):
        _, (a, b, c), instrument = self.setup_accounts()
        self.start(self.plan(a, instrument, 'EU_A'))
        os.environ['FAKE_WRONG_PASSWORD_LOGINS'] = '1001'
        try:
            self.collector = Collector(self.root)
            self.run_collector(2.0)
        finally:
            os.environ.pop('FAKE_WRONG_PASSWORD_LOGINS', None)
        with self.connect() as db:
            failures = db.execute("SELECT detail FROM md_events WHERE kind='DISCONNECTED'").fetchall()
            alerts = db.execute("SELECT count(*) AS n FROM cfg_alert_outbox WHERE event='MARKET_DATA'").fetchone()['n']
        self.assertEqual(len(failures), 1)                        # one event per distinct problem, not per retry
        self.assertEqual(failures[0]['detail']['message'], 'Login or password rejected')
        self.assertLessEqual(alerts, 1)
        worker = self.collector.workers[a['id']]
        self.assertEqual(worker.state, 'FAILED')                   # waits 5 minutes: retrying cannot fix a wrong password
        collectors = self.ok(self.alice.get('/api/md/collectors'))['items']
        self.assertEqual(collectors[0]['problem']['title'], 'Login or password rejected')
        overview = self.ok(self.alice.get('/api/config/overview'))
        self.assertEqual((overview['feeds']['active_plans'], overview['feeds']['problems']), (1, 1))
        first = overview['attention'][0]
        self.assertEqual((first['severity'], first['goto']), ('CRITICAL', {'view': 'Market data', 'section': 'live'}))
        self.assertIn('Login or password rejected', first['title'])
        self.assertTrue(any(x['area'] == 'Backups' for x in overview['attention']))
        self.assertEqual([o['enabled'] for o in overview['order_sending']], [False, False])
        self.ok(TestClient(app, base_url='https://testserver', headers=HEADERS).get('/api/config/overview'), 401)


class PgDumpChoice(unittest.TestCase):
    def test_newest_installed_pg_dump_is_used(self):
        import tempfile
        from pathlib import Path
        from corei_os_mini.md.jobs import _pg_dump
        with tempfile.TemporaryDirectory() as root:
            for v in ('14', '18', '9'):
                b = Path(root, v, 'bin'); b.mkdir(parents=True); (b / 'pg_dump').write_text('')
            old = os.environ.pop('COREI_PG_DUMP', None)
            try:
                self.assertEqual(_pg_dump(root), str(Path(root, '18', 'bin', 'pg_dump')))
            finally:
                if old is not None:
                    os.environ['COREI_PG_DUMP'] = old
