"""Wave 0 financial-integrity regressions (release 0.53.0), on PostgreSQL against the scripted MT5 broker.

Why it exists: each test pins one defect found by the 23 Nov 2026 audits so it cannot come back — reconciliation that
forgot deals older than three days and cleared breaks that merely aged out, fills with no position row, paper results
leaking into broker risk, unvalued holdings, silent capital fallbacks, manual fills above the order, concurrent orders
that together exceed a limit, ledger edits through the database editor, order bursts above the broker's rate, and LIVE
order sending without a written policy.
Must never: depend on the machine's date (times are relative to now, or the suite's fixed market time).
"""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.trading import execution, posttrade  # noqa: E402

UTC = timezone.utc


class Wave0(te.Execution):
    # Run only this file's tests (the inherited ones run in their own files).
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None
    test_old_bridge_never_gets_an_order = None
    test_classification = None
    test_one_step_paper_strategy = None
    test_stale_price_and_paper_stop = None
    test_working_order_blocks_and_broker_stop_is_booked = None
    test_realign_and_working_order_rule = None

    def setUp(self):
        super().setUp()
        self.book_days: list[int] = []
        inner = self.broker.call

        def call(account_id, actor, mode, payload, purpose, timeout=30):
            if mode == 'book' and not payload.get('tag'):
                self.book_days.append(payload.get('days'))
            return inner(account_id, actor, mode, payload, purpose, timeout)
        self.broker.call = call
        self.patches[-1].stop()
        from unittest.mock import patch
        self.patches[-1] = patch('corei_os_mini.trading.brokers.mt5_call', call)
        self.patches[-1].start()

    def breaks(self, state='OPEN'):
        with self.connect() as db:
            return db.execute('SELECT kind, key, state, resolution, detail FROM pt_breaks WHERE state=%s ORDER BY kind, key', (state,)).fetchall()

    # ---------- W0-5 · reconciliation interval and closure ----------

    def test_old_deal_is_still_checked_and_aged_break_stays_open(self):
        _c, a, _b, ins = self.real_account()
        self.sending()
        work = self.place(a, ins, 'w0-lim', order_type='LIMIT', limit_price=Decimal('1.07500'))
        execution.dispatch()
        deal = self.broker._deal({'symbol': 'EURUSD', 'side': 'BUY', 'tag': work['broker_tag']}, '0.1', '1.07500')
        deal['time'] = (datetime.now(UTC) - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%S.000')
        run = posttrade.reconcile(a['id'], broker=True)
        self.assertIn('FILL_MISSING', {b['kind'] for b in self.breaks()}, run)
        self.assertEqual(self.book_days[-1], posttrade.BOOK_DAYS_FIRST)
        self.assertEqual(run['coverage']['working_orders'], 'NOT_COMPARED')

        # An external deal raised today, then aged out of what a run reads: the break stays open.
        self.broker.deals.append({'ticket': 99001, 'order': 1, 'symbol': 'GBPUSD', 'side': 'SELL', 'entry': 'IN', 'volume': '0.2', 'price': '1.27',
                                  'commission': '0', 'swap': '0', 'fee': '0', 'profit': '0', 'time': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.000'),
                                  'comment': 'by hand', 'magic': 0, 'position_id': 1, 'reason': 'CLIENT'})
        posttrade.reconcile(a['id'], broker=True)
        self.assertIn(('EXTERNAL_TRADE', 'deal:99001'), {(b['kind'], b['key']) for b in self.breaks()})
        with self.connect() as db:
            db.execute("UPDATE pt_broker_deals SET at = now() - interval '60 days' WHERE ticket='99001'")
        self.broker.deals = [d for d in self.broker.deals if d['ticket'] != 99001]
        posttrade.reconcile(a['id'], broker=True)
        still = {(b['kind'], b['key']) for b in self.breaks()}
        self.assertIn(('EXTERNAL_TRADE', 'deal:99001'), still, 'aging out of the window is not evidence of a match')
        self.assertIn(('FILL_MISSING', f"deal:{deal['ticket']}"), still)

    def test_verified_interval_drives_history_read(self):
        _c, a, _b, _ins = self.real_account()
        posttrade.reconcile(a['id'], broker=True)
        with self.connect() as db:
            first = db.execute('SELECT deals_verified_to FROM pt_verified_intervals WHERE account_id=%s', (UUID(a['id']),)).fetchone()
            self.assertIsNotNone(first, 'a successful broker read starts the verified point')
            db.execute("UPDATE pt_verified_intervals SET deals_verified_to = now() - interval '20 days' WHERE account_id=%s", (UUID(a['id']),))
        posttrade.reconcile(a['id'], broker=True)
        self.assertEqual(self.book_days[-1], 22, 'reads back to the verified point with a day of overlap')
        with self.connect() as db:
            db.execute("UPDATE pt_verified_intervals SET deals_verified_to = now() - interval '200 days' WHERE account_id=%s", (UUID(a['id']),))
        run = posttrade.reconcile(a['id'], broker=True)
        self.assertEqual(self.book_days[-1], posttrade.BOOK_DAYS_MAX)
        self.assertIn('deals_gap', run['coverage'], 'history the broker cannot serve stays a visible gap')
        with self.connect() as db:
            kept = db.execute('SELECT deals_verified_to FROM pt_verified_intervals WHERE account_id=%s', (UUID(a['id']),)).fetchone()['deals_verified_to']
        self.assertLess(kept, datetime.now(UTC) - timedelta(days=150), 'the point does not jump over the gap')

    def test_broker_read_failure_clears_nothing(self):
        _c, a, _b, ins = self.real_account()
        self.sending()
        self.broker.external.append({'ticket': 7, 'symbol': 'GBPUSD', 'side': 'SELL', 'volume': '0.3', 'price_open': '1.27', 'magic': 0})
        posttrade.reconcile(a['id'], broker=True)
        self.assertTrue(self.breaks())
        self.broker.external.clear()
        self.broker.behaviour = 'notsent'

        def down(*_a, **_k):
            raise te.brokers.NotSent('BRIDGE_UNREACHABLE')
        from unittest.mock import patch
        with patch('corei_os_mini.trading.brokers.mt5_call', down):
            run = posttrade.reconcile(a['id'], broker=True)
        self.assertEqual(run['status'], 'FAILED')
        self.assertEqual(run['coverage']['positions'], 'UNAVAILABLE')
        self.assertTrue(self.breaks(), 'an unreadable broker is not a match')

    # ---------- W0-6 · internal reconciliation both ways ----------

    def test_fill_without_position_row_is_a_break(self):
        _c, a, _b, ins = self.real_account()
        self.sending()
        o = self.place(a, ins, 'w0-orphan')
        execution.dispatch()
        self.assertEqual(self.order(o['id'])['state'], 'FILLED')
        posttrade.reconcile(a['id'], broker=False)
        self.assertEqual(self.breaks(), [], 'normal booking: no break')
        with self.connect() as db:
            db.execute('DELETE FROM pf_positions WHERE account_id=%s', (UUID(a['id']),))
        posttrade.reconcile(a['id'], broker=False)
        got = [b for b in self.breaks() if b['kind'] == 'INTERNAL_POSITION']
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['detail']['note'], 'fills with no position row')

    # ---------- W0-7 · manual resolution cannot overfill; a broker overfill is kept as evidence ----------

    def test_manual_resolution_capped_and_overfill_recorded(self):
        _c, a, _b, ins = self.real_account()
        self.sending()
        self.broker.behaviour = 'lost'
        o = self.place(a, ins, 'w0-unknown', qty='1.0')
        execution.dispatch()
        self.assertEqual(self.order(o['id'])['state'], 'UNKNOWN')
        with self.connect() as db:
            db.execute("UPDATE oms_orders SET filled_qty=0.4, avg_fill_price=1.08 WHERE id=%s", (UUID(o['id']),))
        too_much = self.alice.post(f"/api/trading/orders/{o['id']}/resolve",
                                   json={'outcome': 'FILLED', 'price': '1.08', 'quantity': '0.7', 'reason': 'broker shows a fill'})
        self.assertEqual(too_much.status_code, 422, too_much.text)
        done = self.ok(self.alice.post(f"/api/trading/orders/{o['id']}/resolve",
                                       json={'outcome': 'FILLED', 'price': '1.08', 'quantity': '0.6', 'reason': 'broker shows a fill'}))
        self.assertEqual(done['state'], 'FILLED')

        # A broker deal larger than the order: booked (the exposure is real) and kept as an open break.
        from corei_os_mini.trading import oms
        self.broker.behaviour = 'ok'
        p = self.place(a, ins, 'w0-over', qty='0.1')
        execution.dispatch()
        with store.transaction('write') as db:
            row = dict(db.execute('SELECT * FROM oms_orders WHERE id=%s', (UUID(p['id']),)).fetchone())
            row['state'] = 'PARTIALLY_FILLED'
            oms._fill(db, row, Decimal('0.2'), Decimal('1.08'), None, 'broker', datetime.now(UTC), deal_id='over-1')
        over = [b for b in self.breaks() if b['kind'] == 'ORDER_STATE']
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]['detail']['note'], 'fills above the order quantity')

    # ---------- W0-2 · book isolation ----------

    def second_instrument(self, symbol='GBPJPY', quote_ccy='JPY'):
        return self.ok(self.alice.post('/api/config/instruments', json={
            'symbol': symbol, 'name': symbol, 'asset_class': 'FX', 'base_currency': symbol[:3], 'quote_currency': quote_ccy,
            'price_precision': 3, 'quantity_precision': 2, 'tick_size': '0.001', 'min_quantity': '0.01', 'quantity_step': '0.01',
            'contract_size': '100000', 'contract_unit': symbol[:3]}), 201)

    def test_paper_results_never_reach_the_broker_book(self):
        from corei_os_mini.trading import portfolio
        _c, a, _b, ins = self.real_account()
        with self.connect() as db:
            for mode, pnl in (('PAPER', 80), ('BROKER', -20)):
                db.execute("INSERT INTO pf_positions(id, account_id, instrument_id, mode, quantity, avg_price, realised_pnl, currency) "
                           "VALUES (gen_random_uuid(), %s, %s, %s, 0, NULL, %s, 'USD')", (UUID(a['id']), UUID(ins['id']), mode, pnl))
        with store.transaction('read') as db:
            start = portfolio.capital(db, a['id'])['starting_capital']
            broker, paper, both = (portfolio.equity(db, a['id'], 'BROKER'), portfolio.equity(db, a['id'], 'PAPER'), portfolio.equity(db, a['id']))
        self.assertEqual(broker['equity'] - start, Decimal(-20))
        self.assertEqual(paper['equity'] - start, Decimal(80))
        self.assertEqual((both['equity'] - start, both['book']), (Decimal(60), 'ALL'))
        # A paper loss later leaves the broker book exactly where it was.
        with self.connect() as db:
            db.execute("UPDATE pf_positions SET realised_pnl = realised_pnl - 500 WHERE account_id=%s AND mode='PAPER'", (UUID(a['id']),))
        with store.transaction('read') as db:
            self.assertEqual(portfolio.equity(db, a['id'], 'BROKER')['equity'], broker['equity'])
        # Snapshots keep one series per book beside the account-wide one.
        with store.transaction('write') as db:
            portfolio.snapshot(db)
        with self.connect() as db:
            books = {r['book'] for r in db.execute('SELECT book FROM pf_equity WHERE account_id=%s', (UUID(a['id']),)).fetchall()}
        self.assertEqual(books, {'ALL', 'PAPER', 'BROKER'})

    # ---------- W0-3 · an unvalued holding blocks new risk under money limits ----------

    def test_unvalued_holding_refuses_risk_increase_but_allows_reduction(self):
        _c, a, _b, ins = self.real_account()
        self.sending()
        other = self.second_instrument()
        with self.connect() as db:   # a broker-book holding with no live price and no JPY→USD conversion
            db.execute("INSERT INTO pf_positions(id, account_id, instrument_id, mode, quantity, avg_price, realised_pnl, currency) "
                       "VALUES (gen_random_uuid(), %s, %s, 'BROKER', 0.5, 190.000, 0, 'JPY')", (UUID(a['id']), UUID(other['id'])))
        self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_GROSS_EXPOSURE',
                                                                 'value': '100000000', 'reason': 'gross ceiling'}), 201)
        refused = self.place(a, ins, 'w0-val-1')
        self.assertEqual(refused['state'], 'REJECTED')
        self.assertTrue(any(r.startswith('VALUATION_INCOMPLETE') and 'GBPJPY' in r for r in self.order(refused['id'])['reasons']))
        # A reducing order on a valued holding still passes.
        o = self.place(a, ins, 'w0-val-open', mode='BROKER')
        self.assertEqual(o['state'], 'REJECTED', 'still unvalued: no new risk')
        with self.connect() as db:
            db.execute("INSERT INTO pf_positions(id, account_id, instrument_id, mode, quantity, avg_price, realised_pnl, currency) "
                       "VALUES (gen_random_uuid(), %s, %s, 'BROKER', 0.2, 1.08, 0, 'USD')", (UUID(a['id']), UUID(ins['id'])))
        reduce = self.place(a, ins, 'w0-val-reduce', side='SELL', qty='0.10')
        self.assertNotEqual(reduce['state'], 'REJECTED', self.order(reduce['id'])['reasons'])

    # ---------- W0-4 · capital basis ----------

    def test_broker_sizing_never_falls_back_to_paper_equity(self):
        from corei_os_mini.trading import portfolio, risk
        _c, a, _b, ins = self.real_account()
        dep = {'account_id': a['id'], 'instrument_id': ins['id'], 'mode': 'BROKER', 'partition_id': None,
               'sizing': {'method': 'RISK_PCT', 'risk_pct': '1', 'atr_multiple': '2'}}

        def snap(money, age_minutes=0):
            with self.connect() as db:
                db.execute('DELETE FROM pt_broker_snapshots WHERE account_id=%s', (UUID(a['id']),))
                db.execute("INSERT INTO pt_broker_snapshots(account_id, ok, money, positions, orders, source, at) VALUES (%s,true,%s,'[]','[]','MT5_BRIDGE', now() - make_interval(mins => %s))",
                           (UUID(a['id']), __import__('json').dumps(money), age_minutes))
            with store.transaction('read') as db:
                return portfolio.sizing_equity(db, dep), risk.size(db, dep, Decimal('1.08'), 0.0010)

        basis, (qty, why) = snap({})
        self.assertEqual((basis['state'], qty), ('MISSING', None))
        self.assertTrue(why.startswith('CAPITAL_BASIS_UNAVAILABLE'))
        basis, (qty, _) = snap({'equity': '0', 'balance': '0'})
        self.assertEqual((basis['state'], qty), ('ZERO', None))
        basis, (qty, _) = snap({'equity': '5000'}, age_minutes=60)
        self.assertEqual((basis['state'], qty), ('STALE', None))
        basis, (qty, _) = snap({'balance': '9000', 'margin': '100', 'margin_free': '8900'})
        self.assertEqual((basis['state'], qty), ('MARGIN_ONLY', None), 'an Upstox-style margin-only reply is not equity')
        basis, (qty, why) = snap({'equity': '10000'})
        self.assertEqual(basis['state'], 'PRESENT')
        self.assertIsNotNone(qty, why)
        # Fixed quantity needs no capital figure at all.
        with store.transaction('read') as db:
            self.assertEqual(risk.size(db, {**dep, 'sizing': {'method': 'FIXED_QTY', 'quantity': '0.1'}}, Decimal('1.08'), None)[0], Decimal('0.1'))

    # ---------- W0-8 · working orders are reserved ----------

    def test_working_orders_count_against_position_limit(self):
        _c, a, _b, ins = self.real_account()
        self.sending()
        self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_POSITION_QTY',
                                                                 'value': '1.0', 'reason': 'one lot at most'}), 201)
        first = self.place(a, ins, 'w0-res-1', qty='0.6', order_type='LIMIT', limit_price=Decimal('1.07000'))
        execution.dispatch()
        self.assertEqual(self.order(first['id'])['state'], 'ACKNOWLEDGED')
        second = self.place(a, ins, 'w0-res-2', qty='0.6', order_type='LIMIT', limit_price=Decimal('1.07000'))
        self.assertEqual(second['state'], 'REJECTED')
        self.assertTrue(self.order(second['id'])['reasons'][0].startswith('MAX_POSITION_QTY: position 1.2'))
        # Cancelling the working order frees the reservation.
        self.ok(self.alice.post(f"/api/trading/orders/{first['id']}/cancel", json={'reason': 'not needed now'}))
        third = self.place(a, ins, 'w0-res-3', qty='0.6', order_type='LIMIT', limit_price=Decimal('1.07000'))
        self.assertNotEqual(third['state'], 'REJECTED', self.order(third['id'])['reasons'])

    def test_concurrent_checks_cannot_both_pass(self):
        import threading
        _c, a, _b, ins = self.real_account()
        self.sending()
        self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_POSITION_QTY',
                                                                 'value': '1.0', 'reason': 'one lot at most'}), 201)
        results: list = []
        gate = threading.Barrier(2)

        def go(seed):
            gate.wait()
            results.append(self.place(a, ins, seed, qty='0.6', order_type='LIMIT', limit_price=Decimal('1.07000'))['state'])
        threads = [threading.Thread(target=go, args=(f'w0-par-{i}',)) for i in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ['APPROVED', 'REJECTED'], results)

    # ---------- W0-10 · order rate per connection; Upstox algo name ----------

    def test_dispatch_respects_the_connection_order_rate(self):
        import time as _t
        from corei_os_mini import rate_limit
        connection, a, _b, ins = self.real_account()
        self.sending()
        rate_limit._windows.clear()
        with self.connect() as db:
            db.execute('UPDATE cfg_connections SET max_orders_per_second=3, max_order_burst=10 WHERE id=%s', (UUID(connection['id']),))
        orders = [self.place(a, ins, f'w0-rate-{i}', qty='0.01') for i in range(7)]
        out = execution.dispatch()
        self.assertEqual(out['sent'], 3, out)
        states = [self.order(o['id'])['state'] for o in orders]
        self.assertEqual(states.count('APPROVED'), 4, 'over the rate: held, not rejected, nothing sent')
        _t.sleep(1.05)
        execution.dispatch()
        _t.sleep(1.05)
        execution.dispatch()
        self.assertEqual(self.broker.sends, 7)
        # A strict window: never more than 3 inside one second (a token bucket would let 3 + refill through).
        w = rate_limit.SlidingWindow(3, clock=lambda: 100.0)
        self.assertEqual([w.try_acquire() == 0 for _ in range(4)], [True, True, True, False])

    def test_upstox_algo_name_header_on_sending_calls(self):
        import json as _json
        from unittest.mock import patch
        from corei_os_mini.trading import brokers
        connection, a, _b, _ins = self.real_account()
        with self.connect() as db:
            # The Wave 0 column; the connection setting upstox_algo_name (V3 routing) takes precedence when set.
            db.execute("""UPDATE cfg_connections SET algo_name='corei-ema-v1',
                          settings = settings || '{"api_base_url": "https://api.upstox.com"}'::jsonb WHERE id=%s""", (UUID(connection['id']),))
        seen: list = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return _json.dumps(self.body).encode()

        def fake_open(req, timeout=20):
            seen.append({k.lower(): v for k, v in req.header_items()})
            data = {'order_ids': ['250101000000001']} if req.get_method() == 'POST' else {'equity': {}}
            return Resp({'status': 'success', 'data': data})
        with patch('corei_os_mini.broker_login.session_token', lambda *a, **k: 'tok'), \
                patch.object(brokers.urllib.request, 'build_opener') as make_opener:
            make_opener.return_value.open.side_effect = fake_open
            up = brokers.Upstox(a['id'], 'test', 'header check')
            up.place({'order_type': 'LIMIT', 'quantity': Decimal('1'), 'limit_price': Decimal('101.05'), 'broker_tag': 'TAG123',
                      'side': 'BUY', 'time_in_force': 'DAY'}, 'NSE_EQ|X', 'D')
            up.funds()
        self.assertEqual(seen[0].get('x-algo-name'), 'corei-ema-v1')
        self.assertNotIn('x-algo-name', seen[1], 'reads carry no algo header')

    # ---------- W0-1 · pre-LIVE policy at the switch, at dispatch and on Home ----------

    def test_live_policy_gates_switch_dispatch_and_home(self):
        _c, a, _b, ins = self.real_account('LIVE')
        from fastapi import HTTPException
        with store.transaction('read') as db:
            live = store.execution_controls(db)['LIVE']
        with self.assertRaises(HTTPException) as refused:
            store.set_execution_control('alice', 'LIVE', True, 'go live now', live['version'], 'LIVE')
        self.assertEqual(refused.exception.status_code, 409)
        codes = {u['code'] for u in refused.exception.detail['unmet']}
        self.assertTrue({'LIVE_LIMITS_MISSING', 'LIVE_FOUR_EYES_NOT_ALL', 'LIVE_NO_ALERT_CHANNEL'} <= codes, codes)
        home = self.ok(self.alice.get('/api/config/overview'))
        self.assertTrue(any(x['area'] == 'LIVE readiness' for x in home['attention']))

        # Switched on earlier (outside this check): new LIVE risk is still held back at the dispatcher.
        self.sending('LIVE')
        with self.connect() as db:
            db.execute("UPDATE tr_settings SET live_four_eyes='OFF'")
        o = self.place(a, ins, 'w0-live-1')
        execution.dispatch()
        d = self.order(o['id'])
        self.assertEqual((d['state'], self.broker.sends), ('REJECTED', 0))
        self.assertTrue(d['reasons'][0].startswith('LIVE_POLICY_UNMET'), d['reasons'])
        home = self.ok(self.alice.get('/api/config/overview'))
        self.assertTrue(any(x['area'] == 'LIVE readiness' and x['severity'] == 'CRITICAL' for x in home['attention']))

        # A reducing order still goes (it removes risk).
        with self.connect() as db:
            db.execute("INSERT INTO pf_positions(id, account_id, instrument_id, mode, quantity, avg_price, realised_pnl, currency) "
                       "VALUES (gen_random_uuid(), %s, %s, 'BROKER', 0.2, 1.08, 0, 'USD')", (UUID(a['id']), UUID(ins['id'])))
        r = self.place(a, ins, 'w0-live-reduce', side='SELL', qty='0.10')
        execution.dispatch()
        self.assertEqual(self.order(r['id'])['state'], 'FILLED')

        # Limits + an alert channel + a recorded, expiring exception for four-eyes → met.
        for kind, value in (('MAX_ORDER_NOTIONAL', '10000000'), ('MAX_DAILY_LOSS', '100000'), ('MAX_POSITION_QTY', '100')):
            self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'FIRM', 'kind': kind, 'value': value, 'reason': 'pre-LIVE policy'}), 201)
        with self.connect() as db:
            db.execute("INSERT INTO cfg_alert_channels(id, kind, name, target) VALUES (gen_random_uuid(), 'WEBHOOK', 'desk', 'https://example.invalid/hook')")
        bad = self.alice.post('/api/config/live-readiness/exceptions', json={'code': 'LIVE_LIMITS_MISSING', 'reason': 'try to waive limits', 'days': 5})
        self.assertEqual(bad.status_code, 422, 'risk limits cannot be waived')
        ex = self.ok(self.alice.post('/api/config/live-readiness/exceptions', json={'code': 'LIVE_FOUR_EYES_NOT_ALL', 'reason': 'single-desk pilot', 'days': 3}), 201)
        status = self.ok(self.alice.get('/api/config/live-readiness'))
        self.assertTrue(status['ok'], status)
        o2 = self.place(a, ins, 'w0-live-2')
        execution.dispatch()
        self.assertEqual(self.order(o2['id'])['state'], 'FILLED')
        # The exception expires → held back again.
        with self.connect() as db:
            db.execute("UPDATE cfg_policy_exceptions SET expires_at = now() - interval '1 minute' WHERE id=%s", (UUID(ex['id']),))
        o3 = self.place(a, ins, 'w0-live-3')
        execution.dispatch()
        self.assertEqual(self.order(o3['id'])['state'], 'REJECTED')

    # ---------- W0-11 · exact deployed identity ----------

    def test_workstation_reports_release_commit_and_schema(self):
        from pathlib import Path
        ws = self.alice.get('/api/workstation').json()
        self.assertEqual(ws['release'], (Path(__file__).resolve().parents[2] / 'VERSION').read_text().strip())
        latest = sorted((Path(__file__).resolve().parents[1] / 'sql').glob('*.sql'))[-1].name
        self.assertIn(ws['schema'], (latest, 'unknown'), ws['schema'])
        self.assertTrue(ws['commit'])
