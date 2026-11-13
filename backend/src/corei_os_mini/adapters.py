"""The adapter catalogue: every access technology COREI connects through, what each is for and how far it
is built. A provider (broker, ECN, prime broker, fast feed, data vendor, exchange) is reached through one
of these; the same adapter serves every provider that speaks it.

Connections have a role (`purpose`): a DATA SOURCE (a few chosen, high-quality price feeds per instrument),
an EXECUTION connection (the trading accounts, watched for positions, orders and fills), or both.
Prices are taken from data sources; accounts are not used as price feeds unless their connection is one.
"""
from __future__ import annotations

# state: BUILT, PARTIAL, PLANNED. 'platform' is the value stored on the connection when it can be created.
CATALOGUE: list[dict] = [
    {'id': 'MT5_DIRECT', 'platform': 'MT5', 'adapter': 'MTAPI_DIRECT', 'label': 'MetaTrader 5 — direct',
     'family': 'Broker platform', 'data': 'BUILT', 'orders': 'PLANNED',
     'how': 'Login, password and server; COREI speaks the MT5 protocol through the licensed mtapi SDK (as Westernpips does). '
            'Keep the SDK on its update subscription: brokers move to new MT5 builds within weeks.',
     'credential': 'MT5 password per account', 'examples': 'Any MT5 broker: VT Markets, IC Markets, Exness, MetaQuotes-Demo'},
    {'id': 'MT5_TERMINAL', 'platform': 'MT5', 'adapter': 'TERMINAL_BRIDGE', 'label': 'MetaTrader 5 — terminal bridge',
     'family': 'Broker platform', 'data': 'BUILT', 'orders': 'PLANNED',
     'how': 'Fallback when the SDK is behind a broker: the official terminal on a Windows host, read through MetaQuotes\' '
            'Python package. One login per terminal; for tests and single accounts.',
     'credential': 'Bridge token on the connection; MT5 password per account', 'examples': 'Any MT5 broker'},
    {'id': 'FIX44', 'platform': 'FIX', 'adapter': 'NATIVE', 'label': 'FIX 4.4 / 4.2',
     'family': 'Industry protocol', 'data': 'BUILT', 'orders': 'PLANNED',
     'how': 'One FIX session per venue: CompIDs, host and port, optional username and password. Market data now; '
            'orders and drop-copy on the same session later.',
     'credential': 'Session login on the connection (optional)', 'examples': 'Broker FIX APIs, LMAX, prime brokers, ECNs, FIX fast feeds'},
    {'id': 'UPSTOX', 'platform': 'REST_API', 'adapter': 'NATIVE', 'label': 'Upstox API',
     'family': 'Broker API (India)', 'data': 'BUILT', 'orders': 'PLANNED',
     'how': 'OAuth app key and secret on the connection, a login each trading day (until 03:30 IST).',
     'credential': 'App key and secret on the connection', 'examples': 'Upstox (NSE, BSE, MCX)'},
    {'id': 'MT4_DIRECT', 'platform': None, 'adapter': None, 'label': 'MetaTrader 4 — direct',
     'family': 'Broker platform', 'data': 'PLANNED', 'orders': 'PLANNED',
     'how': 'Same as MT5 direct, through the mtapi MT4 SDK.', 'credential': 'MT4 password per account', 'examples': 'MT4 brokers'},
    {'id': 'CTRADER', 'platform': None, 'adapter': None, 'label': 'cTrader Open API',
     'family': 'Broker platform', 'data': 'PLANNED', 'orders': 'PLANNED',
     'how': 'One OAuth token covers every account of a cTrader ID; official, documented protocol.',
     'credential': 'App client ID and secret', 'examples': 'IC Markets, Pepperstone, FxPro (cTrader)'},
    {'id': 'LMAX', 'platform': None, 'adapter': None, 'label': 'LMAX', 'family': 'Exchange / ECN', 'data': 'PLANNED', 'orders': 'PLANNED',
     'how': 'LMAX FIX or its client library; reach it today through FIX 4.4.', 'credential': 'Session login', 'examples': 'LMAX Global'},
    {'id': 'RITHMIC', 'platform': None, 'adapter': None, 'label': 'Rithmic', 'family': 'Futures', 'data': 'PLANNED', 'orders': 'PLANNED',
     'how': 'R|Protocol API for CME and other futures data and execution.', 'credential': 'Rithmic login', 'examples': 'Futures brokers on Rithmic'},
    {'id': 'CRYPTO', 'platform': None, 'adapter': None, 'label': 'Crypto exchanges', 'family': 'Exchange', 'data': 'PLANNED', 'orders': 'PLANNED',
     'how': 'Each exchange\'s REST and WebSocket API.', 'credential': 'API key and secret', 'examples': 'Binance, Kraken, BingX'},
    {'id': 'VENDOR_FEED', 'platform': None, 'adapter': None, 'label': 'Data vendors and fast feeds', 'family': 'Data vendor',
     'data': 'PLANNED', 'orders': None,
     'how': 'Vendor TCP/WebSocket feeds; most also offer FIX market data, usable now through FIX 4.4.',
     'credential': 'Vendor login', 'examples': 'Westernpips feed, ACTIV, other low-latency feeds'},
]

ROLES = {'MARKET_DATA': 'Data source', 'TRADING': 'Execution', 'BOTH': 'Data source and execution'}


def catalogue() -> dict:
    return {'adapters': CATALOGUE, 'roles': ROLES}
