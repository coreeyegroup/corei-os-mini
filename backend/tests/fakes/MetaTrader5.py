"""A fake of MetaQuotes' MetaTrader5 package for the bridge tests: one account, two symbols, ticks that advance."""
import time
from collections import namedtuple

__version__ = '5.0.5000-fake'
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30, TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 1, 5, 15, 30, 16385, 16388, 16408
COPY_TICKS_ALL = -1
_state = {'init': False, 'login': None, 'err': (1, 'Success'), 'price': 2650.0, 'last_msc': 0}

AccountInfo = namedtuple('AccountInfo', 'login balance equity currency leverage margin_mode margin_so_so limit_orders company server trade_allowed')
TerminalInfo = namedtuple('TerminalInfo', 'build connected')
SymbolInfo = namedtuple('SymbolInfo', 'name digits volume_min volume_max volume_step filling_mode trade_mode trade_exemode trade_stops_level trade_freeze_level trade_contract_size currency_margin description path spread')
Tick = namedtuple('Tick', 'time bid ask last volume time_msc')


def initialize(path=None, **kw):
    _state['init'] = True
    return True


def login(login, password='', server=''):
    if password != 'secret':
        _state['err'] = (-6, 'Authorization failed')
        return False
    _state['login'] = login
    return True


def last_error():
    return _state['err']


def terminal_info():
    return TerminalInfo(build=6241, connected=True)


def account_info():
    if not _state['login']:
        return None
    return AccountInfo(_state['login'], 1000.5, 1000.5, 'USD', 500, 2, 50.0, 200, 'VT Markets', 'VTMarkets-Live 3', True)


def symbols_get():
    return [SymbolInfo('XAUUSD', 2, 0.01, 100.0, 0.01, 1, 4, 2, 10, 0, 100.0, 'USD', 'Gold', 'Metals\\XAUUSD', 20),
            SymbolInfo('EURUSD', 5, 0.01, 200.0, 0.01, 1, 4, 2, 0, 0, 100000.0, 'EUR', 'Euro', 'Forex\\EURUSD', 1)]


def symbol_info(name):
    return next((s for s in symbols_get() if s.name == name), None)


def symbol_select(name, on=True):
    return True


def _tick(name):
    _state['price'] += 0.1
    _state['last_msc'] = max(_state['last_msc'] + 1, int(time.time() * 1000))
    p = _state['price'] if name == 'XAUUSD' else 1.08
    return Tick(_state['last_msc'] // 1000, p - 0.05, p + 0.05, 0.0, 0, _state['last_msc'])


def symbol_info_tick(name):
    return _tick(name)


def copy_ticks_from(name, since, count, flags):
    return [_tick(name)]


def copy_rates_range(name, frame, start, end):
    return [{'time': int(start.timestamp()), 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'tick_volume': 10},
            {'time': int(start.timestamp()) + 60, 'open': 1.5, 'high': 2, 'low': 1, 'close': 1.8, 'tick_volume': 12}]
