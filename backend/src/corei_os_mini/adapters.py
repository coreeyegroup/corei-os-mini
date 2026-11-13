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
    {'id': 'EXCHANGE_NATIVE', 'platform': None, 'adapter': None, 'label': 'Exchange native protocols', 'family': 'Exchange',
     'data': 'PLANNED', 'orders': 'PLANNED',
     'how': 'The exchange\'s own feed and order protocols over a cross-connect in its data centre (e.g. ITCH/OUCH, CME MDP 3.0 / iLink, NSE NNF).',
     'credential': 'Exchange membership session', 'examples': 'CME, LSE, NSE co-location'},
    {'id': 'VENDOR_FEED', 'platform': None, 'adapter': None, 'label': 'Data vendors and fast feeds', 'family': 'Data vendor',
     'data': 'PLANNED', 'orders': None,
     'how': 'Vendor TCP/WebSocket feeds; most also offer FIX market data, usable now through FIX 4.4.',
     'credential': 'Vendor login', 'examples': 'Westernpips feed, ACTIV, other low-latency feeds'},
]

# Level 1 of the Connectivity tree: the technology a provider is reached through. Each technology lists the
# adapters (catalogue entries) behind it, the kinds of provider usually found there and whether a "who"
# (a provider's server, session or API) can be added today ('platform' set) or is planned. state: BUILT (every
# usual provider works), PARTIAL (some providers work, e.g. Upstox among broker APIs), PLANNED.
TECHNOLOGIES: list[dict] = [
    {'id': 'MT5', 'state': 'BUILT', 'label': 'MetaTrader 5', 'platform': 'MT5', 'adapters': ['MT5_DIRECT', 'MT5_TERMINAL'],
     'kinds': ['BROKER'], 'who_hint': 'Broker and its MT5 server (e.g. VT Markets › VTMarkets-Live 3)',
     'account': 'MT5 login', 'summary': 'Retail and prime-of-prime brokers on MetaTrader 5.'},
    {'id': 'MT4', 'state': 'PLANNED', 'label': 'MetaTrader 4', 'platform': None, 'adapters': ['MT4_DIRECT'],
     'kinds': ['BROKER'], 'who_hint': 'Broker and its MT4 server', 'account': 'MT4 login',
     'summary': 'Brokers still on MetaTrader 4.'},
    {'id': 'CTRADER', 'state': 'PLANNED', 'label': 'cTrader', 'platform': None, 'adapters': ['CTRADER'],
     'kinds': ['BROKER'], 'who_hint': 'Broker on cTrader', 'account': 'cTrader account (one cTrader ID can hold many)',
     'summary': 'Brokers on cTrader, through the official Open API.'},
    {'id': 'FIX', 'state': 'BUILT', 'label': 'FIX 4.4 / 4.2', 'platform': 'FIX', 'adapters': ['FIX44', 'LMAX'],
     'kinds': ['BROKER', 'ECN', 'PRIME_BROKER', 'LIQUIDITY_PROVIDER', 'EXCHANGE', 'FAST_FEED', 'DATA_VENDOR'],
     'who': 'Venue and its FIX session (e.g. LMAX › price session)', 'account': 'Trading account on the session',
     'summary': 'The industry protocol: broker FIX APIs, ECNs, prime brokers, LPs, exchanges and fast feeds.'},
    {'id': 'API', 'state': 'PARTIAL', 'label': 'Broker & exchange APIs', 'platform': 'REST_API', 'adapters': ['UPSTOX', 'CRYPTO'],
     'kinds': ['BROKER', 'EXCHANGE'], 'who_hint': 'Broker or exchange and its API app (e.g. Upstox › API)',
     'account': 'Client ID / API account', 'summary': 'Each firm\'s own REST and WebSocket API.'},
    {'id': 'FEED', 'state': 'PLANNED', 'label': 'Fast feeds & data vendors', 'platform': None, 'adapters': ['VENDOR_FEED'],
     'kinds': ['FAST_FEED', 'DATA_VENDOR'], 'who_hint': 'Feed provider and its feed', 'account': 'Feed subscription',
     'summary': 'Low-latency aggregated feeds and data vendors. Today: add a vendor that offers FIX under FIX 4.4.'},
    {'id': 'EXCHANGE', 'state': 'PLANNED', 'label': 'Exchange direct & data centre', 'platform': None, 'adapters': ['EXCHANGE_NATIVE', 'RITHMIC'],
     'kinds': ['EXCHANGE', 'DATA_CENTRE'], 'who_hint': 'Exchange, or the data centre hosting the cross-connect', 'account': 'Membership / trading ID',
     'summary': 'Exchange native protocols from co-location (LD4, NY4, TY3, NSE colo) and futures gateways.'},
]
PLATFORM_TECHNOLOGY = {t['platform']: t['id'] for t in TECHNOLOGIES if t['platform']}

PROVIDER_KINDS = {'BROKER': 'Broker', 'EXCHANGE': 'Exchange', 'ECN': 'ECN', 'PRIME_BROKER': 'Prime broker',
                  'LIQUIDITY_PROVIDER': 'Liquidity provider', 'FAST_FEED': 'Fast feed', 'DATA_VENDOR': 'Data vendor',
                  'DATA_CENTRE': 'Data centre'}

ROLES = {'MARKET_DATA': 'Data source', 'TRADING': 'Execution', 'BOTH': 'Data source and execution'}


def technologies() -> list[dict]:
    by_id = {a['id']: a for a in CATALOGUE}
    return [{**t, 'adapter_details': [by_id[a] for a in t['adapters']]} for t in TECHNOLOGIES]


def catalogue() -> dict:
    return {'adapters': CATALOGUE, 'roles': ROLES, 'technologies': technologies(), 'provider_kinds': PROVIDER_KINDS}


def tree(providers: list[dict], connections: list[dict], accounts: list[dict]) -> dict:
    """Technology › who (provider) › server/session/API (connection) › accounts. Rows are the
    health rows (readiness already worked out); the client can also group by provider type."""
    by_conn: dict = {}
    for a in accounts:
        by_conn.setdefault(str(a['connection_id']), []).append(a)
    conns = [{**c, 'technology': PLATFORM_TECHNOLOGY.get(c['platform']), 'accounts': by_conn.get(str(c['id']), [])} for c in connections]
    techs = []
    for t in technologies():
        who = []
        for p in providers:
            mine = [c for c in conns if c['technology'] == t['id'] and str(c['provider_id']) == str(p['id'])]
            if mine:
                who.append({'provider': p, 'connections': mine})
        techs.append({**t, 'who': who,
                      'counts': {'who': len(who), 'connections': sum(len(w['connections']) for w in who),
                                 'accounts': sum(len(c['accounts']) for w in who for c in w['connections'])}})
    used = {str(c['provider_id']) for c in conns}
    return {'technologies': techs, 'providers': providers, 'connections': conns,
            'unconnected': [p for p in providers if str(p['id']) not in used],
            'provider_kinds': PROVIDER_KINDS, 'roles': ROLES}
