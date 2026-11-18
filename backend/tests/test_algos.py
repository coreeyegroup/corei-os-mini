"""Execution algorithms (DEC-94): a parent order worked as child orders through the normal order engine.
TWAP slices evenly over time; ICEBERG shows one clip at a time; POV follows the market volume; VWAP weights slices by
the usual volume at that time of day; a price cap holds children while the market is beyond it; a refused child
pauses the parent (a person resumes it, with a fresh risk check); cancel stops it; the same ticket is one parent; a
strategy decision waiting in the execution queue can be worked by an algorithm, and the deployment's next decision
waits for it. Every child is an ordinary order with its own risk check and client order ID; every change is audited."""
from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

tt = importlib.import_module('test_trading') if importlib.util.find_spec('test_trading') else importlib.import_module('tests.test_trading')
from corei_os_mini.trading import algos, handoff, runtime  # noqa: E402

UTC = timezone.utc
NOW = tt.NOW


class Algos(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def ready(self, tag):
        connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, tag))
        now = datetime.now(UTC)
        self.quote(a['id'], 1.08000, 1.08010, now)
        return connection, a, ins, now

    def tick(self, a, at, bid=1.08000, ask=1.08010):
        self.quote(a['id'], bid, ask, at)            # a fresh price at that moment
        return algos.tick(at)

    def body(self, a, ins, **kw):
        return {'account_id': a['id'], 'instrument_id': ins['id'], 'side': 'BUY', 'quantity': '1.00', 'ticket_key': kw.pop('key', 'algo-ticket-1'),
                'reason': 'work the order', **kw}

    def test_twap_iceberg_cap_pause_resume_cancel(self):
        _c, a, ins, now = self.ready('EU_ALGO')
        if now.weekday() >= 5:
            return  # FX is closed at the weekend: the market check refuses every child
        cat = self.ok(self.alice.get('/api/trading/execution/algos'))
        self.assertEqual(set(cat['algos']), {'TWAP', 'VWAP', 'POV', 'ICEBERG'})
        # TWAP: 1.00 in 5 slices over a minute — the first slice goes at once, then one every 12 s.
        p = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, algo='TWAP', duration_minutes=1, slices=5)), 201)
        self.assertEqual((p['state'], p['children'], Decimal(p['filled_qty'])), ('WORKING', 1, Decimal('0.2')))
        self.assertEqual(len(p['plan']), 5)
        dup = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, algo='TWAP', duration_minutes=1, slices=5)), 201)
        self.assertTrue(dup.get('duplicate') and dup['id'] == p['id'], 'the same ticket is one parent')
        self.tick(a, now + timedelta(seconds=5))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{p['id']}"))
        self.assertEqual(d['children'], 1, 'nothing before the next slice is due')
        self.assertIn('1 of 5 slices due', d['last_note'])
        for k in range(2, 6):
            self.tick(a, now + timedelta(seconds=12 * (k - 1) + 1))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{p['id']}"))
        self.assertEqual((d['children'], Decimal(d['filled_qty'])), (5, Decimal('1.0')))
        self.tick(a, now + timedelta(seconds=62))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{p['id']}"))
        self.assertEqual(d['state'], 'COMPLETED')
        self.assertEqual([Decimal(k['quantity']) for k in d['children_orders']], [Decimal('0.2')] * 5)
        self.assertTrue(all(k['state'] == 'FILLED' and k['client_order_id'].startswith('C') for k in d['children_orders']))
        self.assertEqual(Decimal(d['arrival']), Decimal('1.08010'))
        self.assertIsNotNone(d['slippage_bp'])
        orders = self.ok(self.alice.get('/api/trading/orders'))['items']
        self.assertEqual(len([o for o in orders if o.get('parent_id') == p['id']]), 5)

        # ICEBERG with a price cap: one 0.10 clip at a time; while the ask is above the cap nothing is sent.
        ice = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, key='algo-ice-1', side='SELL', quantity='0.30', algo='ICEBERG',
                                                                                     display_qty='0.10', price_cap='1.07990', duration_minutes=30)), 201)
        self.assertEqual(ice['children'], 1)
        self.tick(a, now + timedelta(seconds=70), bid=1.07980, ask=1.07990)
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{ice['id']}"))
        self.assertEqual(d['children'], 1)
        self.assertIn('beyond the cap', d['last_note'])
        self.tick(a, now + timedelta(seconds=72))
        self.tick(a, now + timedelta(seconds=74))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{ice['id']}"))
        self.assertEqual((d['state'], d['children'], Decimal(d['filled_qty'])), ('COMPLETED', 3, Decimal('0.3')))

        # A kill switch refuses the next child: the parent pauses with the reason; release and resume, it carries on.
        tw = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, key='algo-tw-2', quantity='0.40', algo='TWAP', duration_minutes=1, slices=2)), 201)
        kill = self.ok(self.alice.post('/api/trading/risk/kill-switches', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'reason': 'stop the account'}), 201)
        self.tick(a, now + timedelta(seconds=31) + timedelta(seconds=80))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{tw['id']}"))
        self.assertEqual(d['state'], 'PAUSED')
        self.assertIn('refused', d['last_note'])
        self.assertEqual(self.alice.post(f"/api/trading/execution/algos/{tw['id']}/pause", json={'reason': 'already paused'}).status_code, 409)
        self.ok(self.alice.post(f"/api/trading/risk/kill-switches/{kill['id']}/release", json={'reason': 'review done'}))
        self.ok(self.alice.post(f"/api/trading/execution/algos/{tw['id']}/resume", json={'reason': 'carry on'}))
        self.tick(a, now + timedelta(seconds=115))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{tw['id']}"))
        self.assertEqual((d['state'], Decimal(d['filled_qty'])), ('COMPLETED', Decimal('0.4')))
        self.assertEqual([k['state'] for k in d['children_orders']], ['FILLED', 'REJECTED', 'FILLED'], 'the refused child is kept as it was; a new one went')

        # Cancel: no more children.
        slow = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, key='algo-slow-1', algo='TWAP', duration_minutes=60, slices=10)), 201)
        self.ok(self.alice.post(f"/api/trading/execution/algos/{slow['id']}/cancel", json={'reason': 'enough for today'}))
        self.tick(a, now + timedelta(minutes=30))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{slow['id']}"))
        self.assertEqual((d['state'], d['children']), ('CANCELLED', 1))
        with self.connect() as db:
            acts = {r['action'] for r in db.execute("SELECT action FROM cfg_audit_log WHERE entity='algo_order'").fetchall()}
        self.assertTrue({'CREATE_ALGO_ORDER', 'RESUME_ALGO_ORDER', 'CANCEL_ALGO_ORDER'} <= acts, acts)
        bad = self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, key='algo-bad-1', algo='ICEBERG'))
        self.assertEqual(bad.status_code, 422, 'an iceberg needs its visible clip')

    def test_pov_and_vwap_follow_volume(self):
        _c, a, ins, now = self.ready('EU_ALGO2')
        if now.weekday() >= 5:
            return
        start = now.replace(second=0, microsecond=0)
        # VWAP: the last days traded mostly in the second half of this hour.
        with self.connect() as db:
            with db.cursor() as cur:
                rows = []
                for day in range(1, 4):
                    for m in range(60):
                        t = start - timedelta(days=day) + timedelta(minutes=m)
                        rows.append((a['id'], t, 1.08, 1.0801, 1.0799, 1.08, 5 if m < 30 else 45))
                cur.executemany("INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks, source) VALUES "
                                "(%s,'EURUSD','M1',%s,%s,%s,%s,%s,%s,'BROKER') ON CONFLICT DO NOTHING", rows)
        v = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, key='algo-vwap-1', algo='VWAP', duration_minutes=60, slices=2)), 201)
        w = v['params']['weights']
        self.assertAlmostEqual(w[0], 0.1, places=2)
        self.assertAlmostEqual(w[1], 0.9, places=2)
        self.assertEqual(Decimal(v['filled_qty']), Decimal('0.1'), 'the first half-hour gets its 10 % share')
        # POV: 10 % of the market volume since the start.
        pov = self.ok(self.alice.post('/api/trading/execution/algos', json=self.body(a, ins, key='algo-pov-1', algo='POV', participation_pct=10, duration_minutes=60)), 201)
        self.assertEqual(pov['children'], 0, 'nothing has traded since the start yet')
        with self.connect() as db:
            db.execute("INSERT INTO md_bars(account_id, broker_symbol, timeframe, bar_time, open, high, low, close, ticks, source) VALUES "
                       "(%s,'EURUSD','M1',%s,1.08,1.0801,1.0799,1.08,300,'BROKER') ON CONFLICT DO NOTHING", (a['id'], pov['start_at']))
        self.tick(a, datetime.fromisoformat(pov['start_at']) + timedelta(seconds=70))
        d = self.ok(self.alice.get(f"/api/trading/execution/algos/{pov['id']}"))
        child = next(e for e in d['events'] if e['kind'] == 'CHILD')
        self.assertIn('market volume 300', child['detail']['why'])
        self.assertEqual(d['state'], 'COMPLETED')
        self.assertEqual(Decimal(d['filled_qty']), Decimal('1.0'), 'at most the whole order (10 % of 300 = 30 > 1.00)')

    def test_decision_worked_by_an_algorithm(self):
        connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, 'EU_ALGO3'))
        strat, ver, dep = self.deploy(connection, a, ins, sizing={'method': 'FIXED_QTY', 'quantity': '0.10'})
        start = NOW - timedelta(minutes=5 * 41)
        self.bars(a['id'], tt.series(40, False), start)
        self.quote(a['id'], 1.08000, 1.08010)
        runtime.cycle(NOW)
        cur = next(p for p in self.ok(self.alice.get('/api/trading/execution/policies'))['items'] if p['id'] == dep['id'])
        self.ok(self.bob.put(f"/api/trading/execution/policies/{dep['id']}", json={**cur['policy'], 'handoff': 'APPROVE', 'version': cur['version'],
                                                                                   'reason': 'trader works the orders'}))
        self.bars(a['id'], tt.series(41, True)[40:], start + timedelta(minutes=5 * 40))
        runtime.cycle(NOW)
        it = self.ok(self.alice.get('/api/trading/execution/flow'))['items'][0]
        self.assertEqual(it['outcome'], 'PENDING')
        with patch.object(handoff, '_now', return_value=NOW + timedelta(minutes=1)):
            done = self.ok(self.alice.post(f"/api/trading/execution/decisions/{it['id']}/execute",
                                           json={'algo': {'algo': 'TWAP', 'duration_minutes': 10, 'slices': 2}, 'note': 'work it'}))
            self.assertEqual(done['state'], 'ORDERED')
            p = done['algo_order']
            self.assertEqual((p['algo'], p['deployment_id'], p['intent_id'], p['children']), ('TWAP', dep['id'], it['id'], 1))
            self.assertEqual(self.alice.post(f"/api/trading/execution/decisions/{it['id']}/execute", json={}).status_code, 409, 'never twice')
        it2 = next(x for x in self.ok(self.alice.get('/api/trading/execution/flow'))['items'] if x['id'] == it['id'])
        self.assertEqual((it2['outcome'], it2['order_type']), ('ORDERED', 'TWAP'))
        # While the algorithm works, the deployment's next decision waits for it.
        from corei_os_mini import config_store as store
        from corei_os_mini.trading import signals as sg
        with store.transaction('write') as db:
            depr = runtime.deployments(db, 'ACTIVE')[0]
            sig = sg.create(db, depr, side='SELL', strength=80, bar_time=NOW + timedelta(minutes=5), timeframe='M5', features={}, explanation=['test: reverse'],
                            source='RUNTIME', actor='runtime', price=Decimal('1.08'))
            self.quote(a['id'], 1.08000, 1.08010)
            r = sg.process(db, sig, depr, NOW + timedelta(minutes=5))
        self.assertIn('WORKING_ORDER', str(r), r)
        self.assertIn('TWAP', str(r))

    def test_amend_a_working_order(self):
        _c, a, ins, now = self.ready('EU_AMEND')
        if now.weekday() >= 5:
            return
        body = {'account_id': a['id'], 'instrument_id': ins['id'], 'side': 'BUY', 'quantity': '0.50', 'order_type': 'LIMIT', 'limit_price': '1.07000',
                'ticket_key': 'amend-0001'}
        o = self.ok(self.alice.post('/api/trading/orders', json=body), 201)
        self.assertEqual(o['state'], 'ACKNOWLEDGED')
        m = self.ok(self.alice.post(f"/api/trading/orders/{o['id']}/modify", json={'limit_price': '1.07500', 'quantity': '0.30', 'stop_loss': '1.06000',
                                                                                   'reason': 'closer to the market'}))
        self.assertEqual((Decimal(m['limit_price']), Decimal(m['quantity']), Decimal(m['stop_loss']), m['state']),
                         (Decimal('1.075'), Decimal('0.3'), Decimal('1.06'), 'ACKNOWLEDGED'))
        d = self.ok(self.alice.get(f"/api/trading/orders/{o['id']}"))
        ev = d['events'][-1]
        self.assertEqual((ev['state_from'], ev['state_to']), ('ACKNOWLEDGED', 'ACKNOWLEDGED'))
        self.assertEqual(ev['detail']['modified']['limit_price'], ['1.07', '1.075'])
        self.assertEqual(self.alice.post(f"/api/trading/orders/{o['id']}/modify", json={'quantity': '0.60', 'reason': 'bigger please'}).status_code, 422)
        self.assertEqual(self.alice.post(f"/api/trading/orders/{o['id']}/modify", json={'stop_price': '1.1', 'reason': 'wrong field'}).status_code, 422)
        self.ok(self.alice.post('/api/trading/risk/limits', json={'scope': 'ACCOUNT', 'target_id': a['id'], 'kind': 'MAX_PRICE_DEVIATION_PCT', 'value': '1',
                                                                 'reason': 'fat-finger guard'}), 201)
        far = self.alice.post(f"/api/trading/orders/{o['id']}/modify", json={'limit_price': '0.90000', 'reason': 'fat finger'})
        self.assertEqual(far.status_code, 409)
        self.assertIn('MAX_PRICE_DEVIATION_PCT', far.json()['detail'])
        mk = self.ok(self.alice.post('/api/trading/orders', json={**body, 'order_type': 'MARKET', 'limit_price': None, 'ticket_key': 'amend-0002'}), 201)
        self.assertEqual(self.alice.post(f"/api/trading/orders/{mk['id']}/modify", json={'stop_loss': '1.0', 'reason': 'too late'}).status_code, 409)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM cfg_audit_log WHERE action='MODIFY_ORDER'").fetchone()['n'], 1)
