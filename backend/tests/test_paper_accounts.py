"""Paper accounts (release 0.60.0, migration 051): many simulated accounts, each with its own currency, amount, scope,
costs and price source; any instrument with a feed and any strategy.

Evidence for: a paper account needs no broker and is ready at once; it fills at the live feed of the instrument with
its own slippage and charges its own costs (netted into realised P&L); a USD and an INR account value the same trade
in their own currency (manual and cross FX rates); the market scope (forex, India, custom) is enforced and closing is
always allowed; a paper account never sends to a broker (orders and deployments); a paper deployment activates without
a broker symbol; funds change and clone ("start again") keep history; a risk profile sizes limits on the amount.
"""
from __future__ import annotations

import importlib
import importlib.util
from decimal import Decimal
from uuid import UUID

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
from corei_os_mini import config_store as store  # noqa: E402
from corei_os_mini.trading import common, oms, portfolio  # noqa: E402

NOW = te.NOW


class PaperAccounts(te.Execution):
    test_sending_disabled_during_precheck = None
    test_real_orders_on_demo_every_path = None
    test_four_eyes_on_live = None
    test_risk_extras = None
    test_portfolio_extras = None
    test_optimise_walk_forward_and_drift = None

    def feed(self):
        """A DEMO broker account collecting EURUSD (1.08000 / 1.08010): the feed paper accounts fill on."""
        connection, a, _b, ins = self.real_account('DEMO')
        with self.connect() as db:
            db.execute("""INSERT INTO md_collection_plans(id, code, account_id, broker_symbol, instrument_id, purpose, created_by, updated_by)
                          VALUES (gen_random_uuid(), 'FEED_EURUSD', %s, 'EURUSD', %s, 'RESEARCH', 'alice', 'alice')""", (a['id'], ins['id']))
        return connection, a, ins

    def paper(self, code, **extra):
        body = {'code': code, 'name': code.title(), 'currency': 'USD', 'amount': '50000', 'reason': 'test paper account', **extra}
        return self.ok(self.alice.post('/api/trading/paper-accounts', json=body), 201)

    def buy(self, acc, ins, seed, qty='0.10', mode='PAPER', side='BUY'):
        with store.transaction('write') as db:
            return oms.place(db, account_id=acc['id'], instrument_id=ins['id'], side=side, quantity=Decimal(qty), mode=mode, seed=seed, actor='alice', at=NOW)

    def test_paper_account_trades_on_the_feed_with_its_own_costs(self):
        _c, _a, ins = self.feed()
        usd = self.paper('PAPER_USD', commission_per_lot='7', slippage_ticks=0)
        self.assertEqual((usd['currency'], usd['starting_amount'], usd['equity'], usd['scope']), ('USD', '50000.00', '50000.00', 'ANY'))
        acc = next(x for x in self.ok(self.alice.get('/api/config/accounts'))['items'] if x['code'] == 'PAPER_USD')
        self.assertEqual((acc['environment'], acc['trading_blockers']), ('PAPER', []), 'no broker, nothing to set up')
        o = self.buy(usd, ins, 'p1')
        self.assertEqual((o['state'], Decimal(o['avg_fill_price'])), ('FILLED', Decimal('1.08010')), 'fills at the live ask, no slippage')
        with self.connect() as db:
            f = db.execute('SELECT commission, realised_pnl FROM oms_fills WHERE order_id=%s', (o['id'],)).fetchone()
        self.assertEqual((Decimal(f['commission']), Decimal(f['realised_pnl'])), (Decimal('0.7'), Decimal('0')), '7 per lot × 0.1 lot; the fill keeps gross P&L')
        with store.transaction('read') as db:
            eq = portfolio.equity(db, usd['id'], 'PAPER')
        self.assertEqual(eq['realised'], Decimal('-0.7'), 'equity is net of the cost')
        self.assertEqual(eq['unrealised'], Decimal('-0.5'), 'marked at the mid: half a pip of spread on 0.1 lot')

    def test_usd_and_inr_accounts_value_the_same_trade_in_their_own_currency(self):
        _c, _a, ins = self.feed()
        inr = self.paper('PAPER_INR', currency='INR', amount='1000000', commission_pct='0.03', slippage_ticks=0)
        self.buy(inr, ins, 'i1')
        listed = {x['code']: x for x in self.ok(self.alice.get('/api/trading/paper-accounts'))['items']}
        self.assertEqual(listed['PAPER_INR']['missing_fx'], ['USD'], 'no USD→INR rate yet: not guessed')
        self.ok(self.alice.put('/api/trading/fx-rates', json={'base': 'USD', 'quote': 'INR', 'rate': '84', 'note': 'RBI reference'}))
        listed = {x['code']: x for x in self.ok(self.alice.get('/api/trading/paper-accounts'))['items']}
        self.assertTrue(listed['PAPER_INR']['complete'])
        self.assertEqual(listed['PAPER_INR']['currency'], 'INR')
        with store.transaction('read') as db:
            eur_inr = common.fx_rate(db, 'EUR', 'INR')
            self.assertEqual(common.fx_rate(db, 'INR', 'USD'), Decimal(1) / Decimal(84))
        self.assertEqual(eur_inr, Decimal('1.080050') * 84, 'EUR→INR through USD: collected EURUSD × entered USDINR')

    def test_scope_is_enforced_and_closing_is_always_allowed(self):
        _c, _a, ins = self.feed()
        india = self.paper('PAPER_IN', currency='INR', amount='500000', scope='INDIA')
        o = self.buy(india, ins, 's1')
        self.assertEqual(o['state'], 'REJECTED')
        self.assertIn('OUTSIDE_PAPER_SCOPE', ' '.join(o['risk_reasons'] if 'risk_reasons' in o else self.order(o['id'])['risk']['reasons']))
        fx = self.paper('PAPER_FX', scope='FOREX')
        self.assertEqual(self.buy(fx, ins, 's2')['state'], 'FILLED')
        custom = self.paper('PAPER_CU', scope='CUSTOM', asset_classes=['EQUITY'])
        with self.connect() as db:   # a position that predates the scope can always be closed
            db.execute("""INSERT INTO pf_positions(id, account_id, instrument_id, mode, quantity, avg_price, realised_pnl, currency, opened_at)
                          VALUES (gen_random_uuid(), %s, %s, 'PAPER', 0.10, 1.08, 0, 'USD', now())""", (custom['id'], ins['id']))
        self.assertEqual(self.buy(custom, ins, 's3', side='SELL')['state'], 'FILLED')
        self.ok(self.alice.post('/api/trading/paper-accounts', json={'code': 'PAPER_BAD', 'name': 'x', 'scope': 'ANY', 'markets': ['NSE']}), 422)

    def test_a_paper_account_never_reaches_a_broker(self):
        connection, _a, ins = self.feed()
        p = self.paper('PAPER_NB')
        self.sending('DEMO')
        o = self.buy(p, ins, 'b1', mode='BROKER')
        self.assertEqual(o['state'], 'REJECTED')
        s = self.ok(self.alice.post('/api/config/strategies', json={'code': 'PA_EMA', 'name': 'PA'}), 201)
        v = self.ok(self.alice.post(f"/api/config/strategies/{s['id']}/versions",
                                    json={'algorithm': 'EMA_CROSSOVER', 'parameters': {'timeframe': 'M5', 'fast': 3, 'slow': 5}}), 201)
        self.ok(self.bob.post(f"/api/config/strategy-versions/{v['id']}/approve", json={'version': v['version']}))
        with self.connect() as db:
            mapping = db.execute('SELECT id FROM cfg_symbol_mappings LIMIT 1').fetchone()
        base = {'strategy_id': s['id'], 'strategy_version_id': v['id'], 'instrument_id': ins['id'], 'account_id': p['id'], 'data_mapping_id': str(mapping['id'])}
        broker = self.ok(self.alice.post('/api/config/deployments', json={**base, 'mode': 'BROKER'}), 201)
        refused = self.ok(self.bob.post(f"/api/config/deployments/{broker['id']}/activate", json={'version': broker['version'], 'reason': 'try broker mode'}), 422)
        self.assertIn('paper account is simulated', str(refused))
        with self.connect() as db:
            db.execute('DELETE FROM cfg_deployments WHERE id=%s', (UUID(broker['id']),))
        dep = self.ok(self.alice.post('/api/config/deployments', json={**base, 'mode': 'PAPER'}), 201)
        active = self.ok(self.bob.post(f"/api/config/deployments/{dep['id']}/activate", json={'version': dep['version'], 'reason': 'paper run'}))
        self.assertEqual((active['status'], active['blockers']), ('ACTIVE', []), 'a paper run needs no broker symbol')

    def test_funds_clone_and_risk_profile(self):
        _c, _a, ins = self.feed()
        p = self.paper('PAPER_A', amount='20000', risk_profile='PROFESSIONAL', commission_per_lot='5')
        limits = {x['kind']: x['value'] for x in self.ok(self.alice.get('/api/trading/risk/limits'))['items'] if x['target_id'] == p['id']}
        self.assertEqual(Decimal(limits['MAX_DAILY_LOSS']), Decimal('600'), '3% of the 20,000 paper amount')
        self.buy(p, ins, 'f1')
        p = self.ok(self.alice.put(f"/api/trading/paper-accounts/{p['id']}/funds", json={'amount': '30000', 'reason': 'top up'}))
        self.assertEqual(p['starting_amount'], '30000.00')
        new = self.ok(self.alice.post(f"/api/trading/paper-accounts/{p['id']}/clone",
                                      json={'code': 'PAPER_A2', 'amount': '10000', 'currency': 'EUR', 'reason': 'start again in EUR'}), 201)
        self.assertEqual((new['currency'], new['starting_amount'], new['commission_per_lot'], new['cloned_from_code'], new['fills']),
                         ('EUR', '10000.00', '5', 'PAPER_A', 0))
        old = {x['code']: x for x in self.ok(self.alice.get('/api/trading/paper-accounts?archived=true'))['items']}['PAPER_A']
        self.assertEqual((old['status'], old['fills']), ('ARCHIVED', 1), 'archived with its history, never wiped')
        self.assertNotIn('PAPER_A', [x['code'] for x in self.ok(self.alice.get('/api/trading/paper-accounts'))['items']])

    def test_account_page_shows_what_a_broker_terminal_shows(self):
        _c, _a, ins = self.feed()
        p = self.paper('PAPER_T', commission_per_lot='7', slippage_ticks=0)
        self.buy(p, ins, 't1')
        page = self.ok(self.alice.get(f"/api/trading/paper-accounts/{p['id']}"))
        self.assertEqual((len(page['positions']), page['positions'][0]['quantity']), (1, '0.1'))
        self.assertEqual(page['balance'], '49999.30', 'balance = start + realised net of the 0.70 cost')
        self.assertEqual(page['exposure'], '10800.50', 'open position at the mid, in USD')
        self.quote(_a['id'], 1.08100, 1.08110)                 # +10 pips
        self.buy(p, ins, 't2', side='SELL')
        page = self.ok(self.alice.get(f"/api/trading/paper-accounts/{p['id']}"))
        perf = page['performance']
        self.assertEqual((page['positions'], perf['closed_trades'], perf['wins'], perf['gross_profit'], perf['costs']), ([], 1, 1, '9.00', '1.40'))
        self.assertEqual(page['equity'], '50007.60', '50,000 + 9.00 gross − 1.40 costs')
        self.assertEqual(page['equity_curve'][-1]['equity'], '50007.60')
        self.assertEqual([f['side'] for f in page['fills']], ['SELL', 'BUY'])
        self.assertTrue(any(a['action'] == 'CREATE' for a in page['activity']))
        self.ok(self.alice.get(f"/api/trading/paper-accounts/{_a['id']}"), 404)
        flags = {x['account_code']: x['paper'] for x in self.ok(self.alice.get('/api/trading/portfolio/accounts'))['items']}
        self.assertIs(flags['PAPER_T'], True, 'Portfolio knows a paper account (its amount is set on its own page, not as estimate capital)')
        bar = self.ok(self.alice.get('/api/trading/topbar'))
        self.assertEqual((bar['kill_switches'], bar['unknown_orders']), ({}, 0))
        self.assertTrue(bar['server_time'])
