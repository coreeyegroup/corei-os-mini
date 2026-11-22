"""Symbol catalogue per source: loaded from the provider (MT5 login, Upstox daily file), searched and picked;
picking creates plans, links instruments across sources and starts collection."""
import importlib
import importlib.util
from unittest.mock import patch
from uuid import UUID, uuid4

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
    {'segment': 'NSE_INDEX', 'name': 'Nifty Bank', 'exchange': 'NSE', 'instrument_type': 'INDEX', 'instrument_key': 'NSE_INDEX|Nifty Bank', 'trading_symbol': 'BANKNIFTY', 'tick_size': 5.0},
    {'segment': 'NSE_INDEX', 'name': 'Nifty Fin Service', 'exchange': 'NSE', 'instrument_type': 'INDEX', 'instrument_key': 'NSE_INDEX|Nifty Fin Service', 'trading_symbol': 'FINNIFTY'},
    {'segment': 'NSE_FO', 'name': 'BANKNIFTY', 'exchange': 'NSE', 'instrument_type': 'FUT', 'instrument_key': 'NSE_FO|60001', 'lot_size': 35, 'tick_size': 20.0,
     'trading_symbol': 'BANKNIFTY FUT 24 DEC 99', 'expiry': 4101820199000, 'underlying_symbol': 'BANKNIFTY'},
    {'segment': 'NSE_FO', 'name': 'BANKNIFTY', 'exchange': 'NSE', 'instrument_type': 'FUT', 'instrument_key': 'NSE_FO|60000', 'lot_size': 35, 'tick_size': 20.0,
     'trading_symbol': 'BANKNIFTY FUT 24 NOV 99', 'expiry': 4099228199000, 'underlying_symbol': 'BANKNIFTY'},
    {'segment': 'NSE_FO', 'name': 'NIFTY', 'exchange': 'NSE', 'instrument_type': 'CE', 'instrument_key': 'NSE_FO|50001', 'lot_size': 75, 'tick_size': 5.0,
     'trading_symbol': 'NIFTY 24500 CE 26 NOV 26', 'strike_price': 24500.0, 'expiry': 1795717799000, 'underlying_symbol': 'NIFTY'},
]


class SymbolCatalogue(test_market_data.MarketData):
    test_plans_estimate_controls_and_jobs = test_collector_end_to_end = None

    def setup_accounts(self, environment='DEMO'):
        self.ok(self.alice.post('/api/operator/reauth', json={'password': test_market_data.PASSWORD}))
        return super().setup_accounts(environment)

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

    def test_studio_discovers_collects_and_scans_beyond_the_setup_list_limit(self):
        _, accounts, _ = self.setup_accounts()
        account_id = UUID(accounts[0]['id'])
        with self.connect() as db:
            db.execute("""
                INSERT INTO cfg_instruments (id,symbol,name,asset_class,base_currency,quote_currency,
                    price_precision,quantity_precision,tick_size,min_quantity,quantity_step,contract_size,contract_unit)
                SELECT gen_random_uuid(), 'REG' || n, 'Registered ' || n, 'EQUITY', 'USD', 'USD',
                    2,0,0.01,1,1,1,'share' FROM generate_series(1, 1100) n
            """)
            db.execute("UPDATE cfg_instruments SET status='INACTIVE' WHERE symbol='REG1'")
            db.execute("""
                INSERT INTO md_symbol_catalog (account_id,broker_symbol,display_symbol,asset_class,suggested_instrument,source)
                SELECT %s, 'BROKER' || n || '.r', 'BROKER' || n, 'EQUITY', 'BROKER' || n, 'MT5_LOGIN'
                FROM generate_series(1, 1256) n
            """, (account_id,))
            db.execute("INSERT INTO cfg_strategies(id,code,name) VALUES (%s,'SCAN_DEMO','Scan demo')", (uuid4(),))

        before = self.ok(self.alice.get('/api/trading/studio/scan-universe'))
        registered = {item['symbol'] for item in before['instruments']}
        self.assertEqual(len(registered), 1100)
        self.assertIn('REG999', registered)
        self.assertNotIn('REG1', registered)

        root = f'/api/md/catalog/{account_id}'
        page = self.ok(self.alice.get(f'{root}?offset=1000&limit=25'))
        self.assertEqual((page['total'], len(page['items'])), (1256, 25))
        found = self.ok(self.alice.get(f'{root}?q=BROKER1256'))['items'][0]
        self.assertIsNone(found['linked_instrument'])
        result = self.ok(self.alice.post(f'{root}/collect', json={
            'items': [{'broker_symbol': found['broker_symbol']}],
            'purpose': 'RESEARCH', 'data_types': ['BARS'], 'bar_timeframes': ['M1'],
        }), 201)
        self.assertEqual(result['created'][0]['active_instrument'], 'BROKER1256')
        mapped = self.ok(self.alice.get(f'{root}?q=BROKER1256'))['items'][0]
        self.assertEqual((mapped['linked_instrument'], mapped['linked_instrument_status']), ('BROKER1256', 'ACTIVE'))
        universe = self.ok(self.alice.get('/api/trading/studio/scan-universe'))
        instrument = next(item for item in universe['instruments'] if item['symbol'] == 'BROKER1256')
        self.assertTrue(instrument['has_source'])

        request = {'instrument_symbols': ['BROKER1256', 'REG999'], 'strategy_codes': ['SCAN_DEMO']}
        readiness = self.ok(self.alice.post('/api/trading/studio/scan-readiness', json=request))
        self.assertEqual(readiness['requested'], 2)
        quick = self.ok(self.alice.post('/api/trading/studio/signal-scan', json=request))
        self.assertEqual({item['instrument_symbol'] for item in quick['items']}, set(request['instrument_symbols']))
        queued = self.ok(self.alice.post('/api/trading/studio/scan-runs', json={'mode': 'instrument', 'request': request}), 202)
        self.assertEqual(queued['request']['instrument_symbols'], request['instrument_symbols'])
        self.assertEqual(queued['request']['strategy_codes'], request['strategy_codes'])

    def test_upstox_daily_file(self):
        made = self.ok(self.alice.post('/api/config/upstox/account-setup', json={
            'account': {'code': 'UPSTOX_7AB123', 'name': 'Upstox', 'external_account_id': '7AB123', 'base_currency': 'INR', 'trading_permission': 'READ_ONLY'},
            'credential': {'client_id': 'app-key', 'client_secret': 'app-secret'}}), 201)
        a = made['account']
        with patch('corei_os_mini.md.catalog._download_upstox', return_value=UPSTOX):
            loaded = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/refresh"))
            nightly = __import__('corei_os_mini.md.catalog', fromlist=['refresh_due']).refresh_due()
        self.assertEqual((loaded['source'], loaded['symbols']), ('UPSTOX_FILE', 8))
        self.assertEqual(nightly[0]['symbols'], 8, 'the nightly job reloads the daily file')
        items = {i['broker_symbol']: i for i in self.ok(self.alice.get(f"/api/md/catalog/{a['id']}"))['items']}
        nifty, rel, gold, opt = items['NSE_INDEX|Nifty 50'], items['NSE_EQ|INE002A01018'], items['MCX_FO|440000'], items['NSE_FO|50001']
        self.assertEqual((nifty['asset_class'], nifty['suggested_instrument']), ('INDEX', 'NIFTY50'))
        self.assertEqual((rel['asset_class'], str(rel['tick_size']), rel['display_symbol']), ('EQUITY', '0.05', 'RELIANCE'))
        self.assertEqual((gold['asset_class'], gold['expiry'], str(gold['lot_size'])), ('COMMODITY', '2026-12-05', '100'))
        self.assertEqual((opt['asset_class'], str(opt['strike'])), ('OPTION', '24500'))
        facets = self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?exchange=NSE"))
        self.assertEqual(facets['total'], 7)
        self.assertEqual({f['k']: f['n'] for f in facets['facets']['exchange']}, {'NSE': 7, 'MCX': 1})
        picked = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/collect", json={'items': [{'broker_symbol': 'NSE_INDEX|Nifty 50'}], 'purpose': 'RESEARCH'}), 201)
        self.assertEqual((picked['started'], picked['created'][0]['instrument']), (1, 'NIFTY50'), picked)
        self.assertEqual(picked['created'][0]['code'], 'UPSTOX_7AB123_NSE_INDEX_NIFTY_50')

        # Indian names: traders' names find the exchange's, the main indices and nearest futures are one click away,
        # and an instrument is created with where it trades and its contract.
        found = {i['broker_symbol'] for i in self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?q=banknifty"))['items']}
        self.assertEqual(found, {'NSE_INDEX|Nifty Bank', 'NSE_FO|60000', 'NSE_FO|60001'})
        self.assertEqual(self.ok(self.alice.get(f"/api/md/catalog/{a['id']}?q=FINNIFTY"))['items'][0]['suggested_instrument'], 'NIFTYFIN')
        pop = self.ok(self.alice.get(f"/api/md/catalog/{a['id']}/popular"))
        self.assertEqual([i['broker_symbol'] for i in pop['indices']], ['NSE_INDEX|Nifty 50', 'NSE_INDEX|Nifty Bank', 'NSE_INDEX|Nifty Fin Service'])
        bank_futs = [f['broker_symbol'] for f in pop['futures'] if f['underlying'] == 'BANKNIFTY']
        self.assertEqual(bank_futs, ['NSE_FO|60000'], 'nearest BANKNIFTY future only')
        bank = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/instrument", json={'broker_symbol': 'NSE_INDEX|Nifty Bank'}))
        self.assertEqual((bank['symbol'], bank['market'], bank['exchange'], bank['aliases']), ('NIFTYBANK', 'NSE', 'NSE', ['BANKNIFTY', 'NIFTY BANK']))
        again = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/instrument", json={'broker_symbol': 'NSE_INDEX|Nifty Bank', 'instrument': 'BANKNIFTY'}))
        self.assertEqual(again['id'], bank['id'], 'an alias finds the same instrument, never a duplicate')
        fut = self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/instrument", json={'broker_symbol': 'NSE_FO|60001'}))
        with self.connect() as db:
            f = db.execute('SELECT * FROM cfg_instruments WHERE id=%s', (UUID(fut['id']),)).fetchone()
            mapped = db.execute("SELECT count(*) AS n FROM cfg_symbol_mappings WHERE broker_symbol='NSE_FO|60001'").fetchone()['n']
        self.assertEqual((f['asset_class'], f['market'], str(f['lot_size'].normalize()), str(f['expiry']), f['underlying'], str(f['min_quantity'].normalize()), f['trading_session']),
                         ('FUTURE', 'NSE_FO', '35', '2099-12-24', 'BANKNIFTY', '35', 'Mon–Fri 09:15–15:30 IST'))
        self.assertEqual(mapped, 1)
        self.ok(self.alice.post(f"/api/md/catalog/{a['id']}/instrument", json={'broker_symbol': 'NSE_FO|99'}), 404)
