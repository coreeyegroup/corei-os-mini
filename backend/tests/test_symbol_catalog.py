"""Symbol catalogue per source: loaded from the provider (MT5 login, Upstox daily file), searched and picked;
picking creates plans, links instruments across sources and starts collection."""
import importlib
import importlib.util
from unittest.mock import patch
from uuid import UUID

test_market_data = importlib.import_module('test_market_data') if importlib.util.find_spec('test_market_data') \
    else importlib.import_module('tests.test_market_data')

MT5_SYMBOLS = {
    'XAUUSD.r': {'Info.Digits': 2, 'Info.VolumeMin': 0.01, 'Info.VolumeMax': 100.0, 'Info.VolumeStep': 0.01, 'Info.ContractSize': 100.0,
                 'Info.Path': 'Metals\\Spot', 'Info.Description': 'Gold vs US Dollar', 'Info.CurrencyBase': 'XAU', 'Info.CurrencyProfit': 'USD'},
    'EURUSD.r': {'Info.Digits': 5, 'Info.VolumeMin': 0.01, 'Info.VolumeMax': 100.0, 'Info.VolumeStep': 0.01, 'Info.ContractSize': 100000.0,
                 'Info.Path': 'Forex\\Majors', 'Info.Description': 'Euro vs US Dollar', 'Info.CurrencyBase': 'EUR', 'Info.CurrencyProfit': 'USD'},
}
UPSTOX = [
    {'segment': 'NSE_INDEX', 'name': 'Nifty 50', 'exchange': 'NSE', 'instrument_type': 'INDEX', 'instrument_key': 'NSE_INDEX|Nifty 50', 'trading_symbol': 'NIFTY'},
    {'segment': 'NSE_EQ', 'name': 'RELIANCE INDUSTRIES LTD', 'exchange': 'NSE', 'instrument_type': 'EQ', 'instrument_key': 'NSE_EQ|INE002A01018',
     'lot_size': 1, 'tick_size': 5.0, 'trading_symbol': 'RELIANCE'},
    {'segment': 'MCX_FO', 'name': 'GOLD', 'exchange': 'MCX', 'instrument_type': 'FUT', 'instrument_key': 'MCX_FO|440000', 'lot_size': 100,
     'tick_size': 100.0, 'trading_symbol': 'GOLD FUT 05 DEC 26', 'expiry': 1796495399000, 'underlying_symbol': 'GOLD'},
    {'segment': 'NSE_FO', 'name': 'NIFTY', 'exchange': 'NSE', 'instrument_type': 'CE', 'instrument_key': 'NSE_FO|50001', 'lot_size': 75, 'tick_size': 5.0,
     'trading_symbol': 'NIFTY 24500 CE 26 NOV 26', 'strike_price': 24500.0, 'expiry': 1795717799000, 'underlying_symbol': 'NIFTY'},
]


class SymbolCatalogue(test_market_data.MarketData):
    test_plans_estimate_controls_and_jobs = test_collector_end_to_end = None

    def test_mt5_list_from_login_then_pick(self):
        _, accounts, instrument = self.setup_accounts()
        a = accounts[0]
        login = {'connected': True, 'host': '198.51.100.10', 'port': 443, 'connect_ms': 40, 'attempts': [], 'account': {}, 'utc_now': '2026-11-14T10:00:00',
                 'server_time': '2026-11-14T12:00:00', 'symbols': MT5_SYMBOLS, 'symbols_available': 4,
                 'symbol_names': ['XAUUSD.r', 'EURUSD.r', 'US30.cash', 'BTCUSD']}
        with patch('corei_os_mini.broker_session._run_session', return_value=login), \
             patch('corei_os_mini.broker_session.store.refresh_latency_if_stale', return_value=False):
            loaded = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/refresh"))
        self.assertEqual((loaded['source'], loaded['symbols']), ('MT5_LOGIN', 4), 'every name the server lists, not only those with rules')
        cat = self.ok(self.alice.get(f"/api/md/catalog/{a['id']}"))
        self.assertEqual(cat['catalogue_size'], 4)
        self.assertEqual({f['k']: f['n'] for f in cat['facets']['asset_class']}, {'METAL': 1, 'FX': 1, 'INDEX': 1, 'CRYPTO': 1})
        gold = self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?asset_class=METAL"))['items'][0]
        self.assertEqual((gold['broker_symbol'], gold['suggested_instrument'], gold['digits'], gold['description']), ('XAUUSD.r', 'XAUUSD', 2, 'Gold vs US Dollar'))
        self.assertEqual(self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?q=euro"))['total'], 1)
        euro = self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?q=euro"))['items'][0]
        self.assertEqual(euro['instrument_id'], instrument['id'], 'EURUSD.r is matched to the existing EURUSD instrument')

        picked = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/collect", json={
            'items': [{'broker_symbol': 'XAUUSD.r'}, {'broker_symbol': 'EURUSD.r'}, {'broker_symbol': 'US30.cash', 'instrument': 'US30'}],
            'purpose': 'RESEARCH', 'bar_timeframes': ['M1', 'H1'], 'start': True}), 201)
        self.assertEqual((len(picked['created']), picked['started'], picked['errors']), (3, 3, []), picked)
        made = {c['broker_symbol']: c for c in picked['created']}
        self.assertEqual((made['XAUUSD.r']['instrument'], made['EURUSD.r']['instrument'], made['US30.cash']['instrument']), ('XAUUSD', 'EURUSD', 'US30'))
        with self.connect() as db:
            gold_i = db.execute("SELECT * FROM cfg_instruments WHERE symbol='XAUUSD'").fetchone()
            maps = db.execute('SELECT broker_symbol FROM cfg_symbol_mappings WHERE account_id=%s ORDER BY 1', (UUID(a['id']),)).fetchall()
            plan = db.execute("SELECT * FROM md_collection_plans WHERE broker_symbol='XAUUSD.r'").fetchone()
        self.assertEqual((gold_i['asset_class'], gold_i['price_precision'], str(gold_i['contract_size'].normalize())), ('METAL', 2, '1E+2'))
        self.assertEqual([m['broker_symbol'] for m in maps], ['EURUSD.r', 'US30.cash', 'XAUUSD.r'])
        self.assertEqual((plan['status'], plan['bar_timeframes']), ('ACTIVE', ['M1', 'H1']))
        self.assertEqual(self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?show=collected"))['total'], 3)
        again = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/collect", json={'items': [{'broker_symbol': 'XAUUSD.r'}]}), 201)
        self.assertEqual(again['skipped'], [{'broker_symbol': 'XAUUSD.r', 'reason': 'already collected'}])
        # The same instrument from a second account links to the same COREI instrument.
        with patch('corei_os_mini.broker_session._run_session', return_value=login), \
             patch('corei_os_mini.broker_session.store.refresh_latency_if_stale', return_value=False):
            self.ok(self.alice.post(f"/api/md/catalog/{accounts[1]['id']}/refresh"))
        second = self.ok(self.alice.post(f"/api/md/catalog/{accounts[1]['id']}/collect", json={'items': [{'broker_symbol': 'XAUUSD.r'}], 'start': False}), 201)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM cfg_instruments WHERE symbol='XAUUSD'").fetchone()['n'], 1)
        self.assertEqual(second['created'][0]['instrument'], 'XAUUSD')

    def test_upstox_daily_file(self):
        made = self.ok(self.alice.post('/api/config/upstox/account-setup', json={
            'account': {'code': 'UPSTOX_7AB123', 'name': 'Upstox', 'external_account_id': '7AB123', 'base_currency': 'INR', 'trading_permission': 'READ_ONLY'},
            'credential': {'client_id': 'app-key', 'client_secret': 'app-secret'}}), 201)
        a = made['account']
        with patch('corei_os_mini.md.catalog._download_upstox', return_value=UPSTOX):
            loaded = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/refresh"))
            nightly = __import__('corei_os_mini.md.catalog', fromlist=['refresh_due']).refresh_due()
        self.assertEqual((loaded['source'], loaded['symbols']), ('UPSTOX_FILE', 4))
        self.assertEqual(nightly[0]['symbols'], 4, 'the nightly job reloads the daily file')
        items = {i['broker_symbol']: i for i in self.ok(self.alice.get(f"/api/md/catalog/{a['id']}"))['items']}
        nifty, rel, gold, opt = items['NSE_INDEX|Nifty 50'], items['NSE_EQ|INE002A01018'], items['MCX_FO|440000'], items['NSE_FO|50001']
        self.assertEqual((nifty['asset_class'], nifty['suggested_instrument']), ('INDEX', 'NIFTY50'))
        self.assertEqual((rel['asset_class'], str(rel['tick_size']), rel['display_symbol']), ('EQUITY', '0.05', 'RELIANCE'))
        self.assertEqual((gold['asset_class'], gold['expiry'], str(gold['lot_size'])), ('COMMODITY', '2026-12-05', '100'))
        self.assertEqual((opt['asset_class'], str(opt['strike'])), ('OPTION', '24500'))
        facets = self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?exchange=NSE"))
        self.assertEqual(facets['total'], 3)
        self.assertEqual({f['k']: f['n'] for f in facets['facets']['exchange']}, {'NSE': 3, 'MCX': 1})
        picked = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/collect", json={'items': [{'broker_symbol': 'NSE_INDEX|Nifty 50'}], 'purpose': 'RESEARCH'}), 201)
        self.assertEqual((picked['started'], picked['created'][0]['instrument']), (1, 'NIFTY50'), picked)
        self.assertEqual(picked['created'][0]['code'], 'UPSTOX_7AB123_NSE_INDEX_NIFTY_50')
