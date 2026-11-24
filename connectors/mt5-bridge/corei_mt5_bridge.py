#!/usr/bin/env python3
"""COREI MT5 terminal bridge.

Runs next to the official MetaTrader 5 terminal (Windows, or Wine on Linux) and lets COREI read the
account, stream ticks and fetch bars through MetaQuotes' own MetaTrader5 Python package. The terminal
keeps itself up to date, so the broker protocol is never COREI's problem again.

    pip install MetaTrader5
    python corei_mt5_bridge.py --terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe" --port 9443

First run prints the bridge's TLS certificate fingerprint and generates a token in corei-bridge.token
next to this file; put both on the connection in COREI (Setup › Brokers & accounts › the server › Terminal
bridge). COREI connects over TLS, sends the token and the account's login, password and server name once
per session (never stored here), and then speaks the same line protocol as COREI's direct reader:

  session: one JSON reply  {connected, account facts, symbols, server_time, utc_now, client_build}
  stream : lines {"t":"connected"|"q"|"subscribed"|"hb"|"bars"|"history_error"|"error"|"disconnected"|"stopped"}
           commands from COREI {"cmd":"subscribe"|"unsubscribe"|"history"|"stop"}
  trade  : one reply {"t":"trade", action, results:[{retcode, ok, deal, order, volume, price, ...}]} for
           action check | send | cancel | modify | sltp | close (0.43); needs the master password
  book   : one reply {"t":"book", account, money, positions, orders, deals, tagged} (0.43)

One terminal holds one login at a time. With --terminals-root the bridge serves any number of accounts on one
machine: for each login it makes a portable copy of the installed terminal (once) and runs a worker for it, so
every account of the firm can use the same bridge host and port. Standard library only, apart from MetaTrader5.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import urllib.parse
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import MetaTrader5 as mt5  # type: ignore
except ImportError:  # pragma: no cover - the tests inject a fake
    mt5 = None

HERE = Path(__file__).resolve().parent
UTC = dt.timezone.utc
FRAMES = {'M1': 'TIMEFRAME_M1', 'M5': 'TIMEFRAME_M5', 'M15': 'TIMEFRAME_M15', 'M30': 'TIMEFRAME_M30',
          'H1': 'TIMEFRAME_H1', 'H4': 'TIMEFRAME_H4', 'D1': 'TIMEFRAME_D1'}
terminal_lock = threading.Lock()  # one login at a time per terminal


def field(tick, name: str):
    """A tick field from a named tuple (symbol_info_tick) or a numpy record (copy_ticks_from)."""
    return getattr(tick, name) if hasattr(tick, name) else tick[name]


BRIDGE_VERSION = '0.43.3'  # shown by --version and in the log at start
PORTABLE = False  # the worker's terminal is a portable copy (several accounts on one machine)


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(tzinfo=None).isoformat(timespec='milliseconds')


def _make_cert_python(cert: Path, key: Path) -> None:
    import datetime as _dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'corei-mt5-bridge')])
    now = _dt.datetime.now(_dt.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - _dt.timedelta(days=1))
                   .not_valid_after(now + _dt.timedelta(days=3650)).sign(private, hashes.SHA256()))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def ensure_cert(cert: Path, key: Path) -> str:
    """Self-signed certificate for this bridge; COREI pins its SHA-256 fingerprint, so no CA is needed."""
    if not cert.exists() or not key.exists():
        try:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650', '-subj', '/CN=corei-mt5-bridge',
                            '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            _make_cert_python(cert, key)  # Windows without OpenSSL: the cryptography package (pip install cryptography)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ':'.join(digest[i:i + 2] for i in range(0, len(digest), 2))


def ensure_token(path: Path) -> str:
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text().strip()


# ----- terminal facts in the reader's shape -----

def account_dict() -> dict:
    info = mt5.account_info()
    if info is None:
        return {}
    d = info._asdict() if hasattr(info, '_asdict') else dict(info)
    method = {0: 'Netting', 1: 'Exchange', 2: 'Hedging'}.get(d.get('margin_mode'), str(d.get('margin_mode')))
    return {'AccountBalance': d.get('balance'), 'AccountEquity': d.get('equity'), 'AccountCurrency': d.get('currency'),
            'AccountLeverage': d.get('leverage'), 'AccountMethod': method, 'Account.Group': '',
            'MarginStopOut': d.get('margin_so_so'), 'LimitOrders': d.get('limit_orders'), 'Login': d.get('login'),
            'Company': d.get('company'), 'Server': d.get('server'), 'TradeAllowed': d.get('trade_allowed')}


def symbol_dict(info) -> dict:
    d = info._asdict() if hasattr(info, '_asdict') else dict(info)
    trade_mode = {0: 'Disabled', 1: 'LongOnly', 2: 'ShortOnly', 3: 'CloseOnly', 4: 'Full'}.get(d.get('trade_mode'), 'Full')
    execution = {0: 'Request', 1: 'Instant', 2: 'Market', 3: 'Exchange'}.get(d.get('trade_exemode'), 'Market')
    return {'Digits': d.get('digits'), 'VolumeMin': d.get('volume_min'), 'VolumeMax': d.get('volume_max'), 'VolumeStep': d.get('volume_step'),
            'FillingMode': d.get('filling_mode'), 'TradeMode': trade_mode, 'ExecutionMode': execution,
            'StopsLevel': d.get('trade_stops_level'), 'FreezeLevel': d.get('trade_freeze_level'),
            'ContractSize': d.get('trade_contract_size'), 'MarginCurrency': d.get('currency_margin'),
            'Description': d.get('description'), 'Path': d.get('path'), 'Spread': d.get('spread')}


def login(request: dict, terminal: str | None) -> tuple[bool, str]:
    # A terminal that is starting, updating itself or loading history (slow under Wine) can take longer than the
    # package's default 60 s to answer: wait up to 3 minutes, and try once more if its pipe still did not answer.
    kwargs = {'path': terminal, 'timeout': 180000} if terminal else {'timeout': 180000}
    if PORTABLE:
        # A new portable copy starts with no account: give it the login at start (MetaQuotes' documented way), so it
        # opens logged in instead of showing its "open an account" window, which blocks the package's pipe.
        kwargs.update(portable=True, login=int(request['login']), password=request['password'], server=request.get('server') or '')
    for attempt in (1, 2, 3):
        if mt5.initialize(**kwargs):
            break
        error = mt5.last_error()
        mt5.shutdown()
        # -10003 no answer yet, -10001/-10002 pipe not ready: a terminal still starting or updating itself.
        if attempt == 3 or error[0] not in (-10001, -10002, -10003):
            return False, f'Terminal did not start: {error}'
        time.sleep(10)
    ok = mt5.login(int(request['login']), password=request['password'], server=request.get('server') or '')
    if not ok:
        code, text = mt5.last_error()
        return False, f'ServerException: {text} ({code})' if code in (-6, -8) or 'auth' in str(text).lower() else f'Terminal login failed: {text} ({code})'
    return True, ''


def terminal_build() -> int | None:
    try:
        info = mt5.terminal_info()
        return int(getattr(info, 'build', None) or 0) or None
    except Exception:  # noqa: BLE001
        return None


def server_time() -> str | None:
    """The broker's clock, read from the newest tick of a liquid symbol (the package has no clock call).
    A tick older than two minutes (closed market, weekend) says nothing about the clock: None then."""
    try:
        for name in ('EURUSD', 'XAUUSD', 'GBPUSD', 'USDJPY'):
            tick = mt5.symbol_info_tick(name)
            when = int(getattr(tick, 'time_msc', 0) or 0) / 1000 if tick is not None else 0
            if when and abs(time.time() - when) < 120:
                return dt.datetime.fromtimestamp(when, UTC).replace(tzinfo=None).isoformat(timespec='seconds')
    except Exception:  # noqa: BLE001
        pass
    return None


def session(request: dict) -> dict:
    """What `ServerCatalog --session` returns, from the terminal."""
    started = time.monotonic()
    account = account_dict()
    wanted = request.get('symbols') or None
    max_symbols = int(request.get('max_symbols') or 2000)
    names = [s.name for s in (mt5.symbols_get() or [])]
    symbols: dict[str, dict] = {}
    for name in [n for n in names if not wanted or n in wanted][:max(0, min(max_symbols, 5000))]:
        info = mt5.symbol_info(name)
        if info is not None:
            symbols[name] = symbol_dict(info)
    return {'connected': True, 'host': 'terminal', 'port': 0, 'connect_ms': int((time.monotonic() - started) * 1000),
            'attempts': [{'host': 'terminal', 'port': 0, 'ok': True}], 'utc_now': now_iso(), 'server_time': server_time(),
            'account': account, 'symbols': symbols, 'symbol_names': names, 'symbols_available': len(names), 'client_build': terminal_build(),
            'sdk_version': f"MetaTrader5 {getattr(mt5, '__version__', '?')}"}


# ----- orders (bridge 0.43): COREI decides, the terminal executes, the bridge reports exactly what happened -----
# Prices and volumes arrive as decimal strings; the MetaTrader5 package takes doubles, so they are converted here, at
# the last boundary, and rounded to the symbol's digits and volume step. Every order carries COREI's tag in its
# comment, so COREI can find it again after a lost connection. Before sending, the bridge looks for the tag in the
# terminal (open orders, positions, 7 days of history): a tag already there is never sent a second time.

RET_TEXT = {10004: 'requote', 10006: 'rejected', 10007: 'cancelled by trader', 10008: 'placed', 10009: 'done', 10010: 'done partially',
            10011: 'processing error', 10012: 'timed out', 10013: 'invalid request', 10014: 'invalid volume', 10015: 'invalid price',
            10016: 'invalid stops', 10017: 'trading disabled', 10018: 'market closed', 10019: 'not enough money', 10020: 'prices changed',
            10021: 'no quotes', 10022: 'invalid expiration', 10023: 'order state changed', 10024: 'too many requests', 10025: 'no changes',
            10026: 'server autotrading disabled', 10027: 'terminal autotrading disabled', 10028: 'locked', 10029: 'frozen',
            10030: 'invalid filling type', 10031: 'no connection to the trade server', 10032: 'live accounts only', 10033: 'pending order limit',
            10034: 'volume limit', 10035: 'invalid order type', 10036: 'position already closed', 10038: 'close volume exceeds position',
            10039: 'close order already exists', 10040: 'position limit', 10044: 'close only', 10045: 'FIFO close only'}
OK_CODES = (10008, 10009, 10010)
api_lock = threading.RLock()  # one MetaTrader5 call at a time: the stream and an order may share a terminal


def _d(row) -> dict:
    return row._asdict() if hasattr(row, '_asdict') else dict(row)


def _const(name: str, default: int) -> int:
    return int(getattr(mt5, name, default))


def _ts(seconds_msc) -> str:
    return dt.datetime.fromtimestamp(int(seconds_msc) / 1000, UTC).replace(tzinfo=None).isoformat(timespec='milliseconds')


def ensure_trading_login(request: dict, terminal: str | None) -> tuple[bool, str]:
    """Logged in to this account with a password that may trade. A terminal already streaming this login is reused
    (an investor session is upgraded with the master password); a terminal busy with another login says so."""
    with api_lock:
        info = mt5.account_info() if mt5 is not None else None
        same = info is not None and int(getattr(info, 'login', 0) or 0) == int(request['login'])
    if not same:
        if not terminal_lock.acquire(timeout=5):
            return False, 'TERMINAL_BUSY: this terminal is serving another account; use a terminal pool (--terminals-root)'
        try:
            ok, error = login(request, terminal)
        finally:
            terminal_lock.release()
        if not ok:
            return False, error
    with api_lock:
        info = mt5.account_info()
        if info is not None and not getattr(info, 'trade_allowed', True):
            if not mt5.login(int(request['login']), password=request['password'], server=request.get('server') or ''):
                code, text = mt5.last_error()
                return False, f'TRADE_LOGIN_FAILED: {text} ({code})'
            info = mt5.account_info()
            if info is not None and not getattr(info, 'trade_allowed', True):
                return False, 'TRADING_NOT_ALLOWED: the broker does not allow trading with this login (investor password?)'
        term = mt5.terminal_info()
        if term is not None and getattr(term, 'trade_allowed', True) is False:
            return False, 'TERMINAL_ALGO_TRADING_OFF: press "Algo Trading" in the terminal (Tools › Options › Expert Advisors › Allow algorithmic trading)'
    return True, ''


def _tagged(tag: str, since_days: int = 7) -> dict:
    """Everything in the terminal carrying this tag: open orders, positions, history orders and deals."""
    if not tag:
        return {'orders': [], 'positions': [], 'history_orders': [], 'deals': []}
    start = dt.datetime.now(UTC) - dt.timedelta(days=since_days)
    end = dt.datetime.now(UTC) + dt.timedelta(days=1)
    with api_lock:
        pick = lambda rows: [_d(r) for r in (rows or []) if str(_d(r).get('comment') or '').startswith(tag)]  # noqa: E731
        return {'orders': pick(mt5.orders_get()), 'positions': pick(mt5.positions_get()),
                'history_orders': pick(mt5.history_orders_get(start, end)), 'deals': pick(mt5.history_deals_get(start, end))}


def _deal(d: dict) -> dict:
    return {'ticket': d.get('ticket'), 'order': d.get('order'), 'position_id': d.get('position_id'), 'symbol': d.get('symbol'),
            'side': 'BUY' if int(d.get('type', 0)) == 0 else 'SELL', 'entry': {0: 'IN', 1: 'OUT', 2: 'INOUT', 3: 'OUT_BY'}.get(int(d.get('entry', 0)), '?'),
            'volume': repr(float(d.get('volume') or 0)), 'price': repr(float(d.get('price') or 0)), 'commission': repr(float(d.get('commission') or 0)),
            'swap': repr(float(d.get('swap') or 0)), 'fee': repr(float(d.get('fee') or 0)), 'profit': repr(float(d.get('profit') or 0)),
            'time': _ts(d.get('time_msc') or int(d.get('time') or 0) * 1000), 'comment': d.get('comment'), 'magic': d.get('magic'),
            'reason': {0: 'CLIENT', 1: 'MOBILE', 2: 'WEB', 3: 'EXPERT', 4: 'SL', 5: 'TP', 6: 'SO', 7: 'ROLLOVER', 8: 'VMARGIN', 9: 'SPLIT'}.get(int(d.get('reason') or 0), str(d.get('reason')))}


def _position(p: dict) -> dict:
    return {'ticket': p.get('ticket'), 'symbol': p.get('symbol'), 'side': 'BUY' if int(p.get('type', 0)) == 0 else 'SELL',
            'volume': repr(float(p.get('volume') or 0)), 'price_open': repr(float(p.get('price_open') or 0)),
            'price_current': repr(float(p.get('price_current') or 0)), 'profit': repr(float(p.get('profit') or 0)),
            'swap': repr(float(p.get('swap') or 0)), 'sl': repr(float(p.get('sl') or 0)), 'tp': repr(float(p.get('tp') or 0)),
            'time': _ts(p.get('time_msc') or int(p.get('time') or 0) * 1000), 'magic': p.get('magic'), 'comment': p.get('comment')}


def _order(o: dict) -> dict:
    kinds = {0: 'BUY', 1: 'SELL', 2: 'BUY_LIMIT', 3: 'SELL_LIMIT', 4: 'BUY_STOP', 5: 'SELL_STOP', 6: 'BUY_STOP_LIMIT', 7: 'SELL_STOP_LIMIT'}
    states = {0: 'STARTED', 1: 'PLACED', 2: 'CANCELED', 3: 'PARTIAL', 4: 'FILLED', 5: 'REJECTED', 6: 'EXPIRED', 7: 'REQUEST_ADD',
              8: 'REQUEST_MODIFY', 9: 'REQUEST_CANCEL'}
    return {'ticket': o.get('ticket'), 'symbol': o.get('symbol'), 'type': kinds.get(int(o.get('type', 0)), str(o.get('type'))),
            'state': states.get(int(o.get('state', 0)), str(o.get('state'))), 'volume_initial': repr(float(o.get('volume_initial') or 0)),
            'volume_current': repr(float(o.get('volume_current') or 0)), 'price_open': repr(float(o.get('price_open') or 0)),
            'time_setup': _ts(o.get('time_setup_msc') or int(o.get('time_setup') or 0) * 1000),
            'time_done': _ts(o.get('time_done_msc') or int(o.get('time_done') or 0) * 1000) if (o.get('time_done_msc') or o.get('time_done')) else None,
            'comment': o.get('comment'), 'magic': o.get('magic')}


def _result(r, request: dict) -> dict:
    if r is None:
        code, text = mt5.last_error()
        return {'retcode': None, 'ok': False, 'error': f'order_send gave no result: {text} ({code})', 'request': _safe(request)}
    d = _d(r)
    code = int(d.get('retcode') or 0)
    return {'retcode': code, 'ok': code in OK_CODES, 'text': RET_TEXT.get(code, d.get('comment') or ''), 'deal': d.get('deal'), 'order': d.get('order'),
            'volume': repr(float(d.get('volume') or 0)), 'price': repr(float(d.get('price') or 0)), 'bid': repr(float(d.get('bid') or 0)),
            'ask': repr(float(d.get('ask') or 0)), 'comment': d.get('comment'), 'request_id': d.get('request_id'),
            'retcode_external': d.get('retcode_external'), 'request': _safe(request)}


def _safe(request: dict) -> dict:
    return {k: v for k, v in request.items() if k in ('action', 'symbol', 'volume', 'type', 'price', 'sl', 'tp', 'deviation', 'magic', 'comment',
                                                       'type_filling', 'type_time', 'position', 'order')}


def _filling(info) -> int:
    modes = int(getattr(info, 'filling_mode', 0) or 0)
    if modes & 1:
        return _const('ORDER_FILLING_FOK', 0)
    if modes & 2:
        return _const('ORDER_FILLING_IOC', 1)
    return _const('ORDER_FILLING_RETURN', 2)


def _round(value: str | float | None, step: float) -> float:
    if value in (None, '', 0):
        return 0.0
    v = float(value)
    return round(round(v / step) * step, 10) if step else v


def _requests(o: dict) -> list[dict]:
    """The MetaTrader requests for one COREI order. On a hedging account a COREI order is net: it first closes this
    firm's opposite positions (oldest first, by ticket), and only the rest opens a new position."""
    info = mt5.symbol_info(o['symbol'])
    if info is None:
        raise ValueError(f"SYMBOL_UNKNOWN: {o['symbol']} is not in this terminal's market watch")
    mt5.symbol_select(o['symbol'], True)
    digits, step = int(getattr(info, 'digits', 5) or 5), float(getattr(info, 'volume_step', 0.01) or 0.01)
    volume = _round(o['volume'], step)
    tick = mt5.symbol_info_tick(o['symbol'])
    buy = o['side'] == 'BUY'
    base = {'symbol': o['symbol'], 'magic': int(o.get('magic') or 0), 'comment': o['tag'], 'deviation': int(o.get('deviation') or 10),
            'type_time': _const('ORDER_TIME_DAY', 1) if o.get('time_in_force') == 'DAY' else _const('ORDER_TIME_GTC', 0)}
    sl, tp = round(float(o.get('sl') or 0), digits), round(float(o.get('tp') or 0), digits)
    if sl:
        base['sl'] = sl
    if tp:
        base['tp'] = tp
    kind = o.get('order_type') or 'MARKET'
    if kind != 'MARKET':
        types_ = {('LIMIT', True): 'ORDER_TYPE_BUY_LIMIT', ('LIMIT', False): 'ORDER_TYPE_SELL_LIMIT', ('STOP', True): 'ORDER_TYPE_BUY_STOP',
                  ('STOP', False): 'ORDER_TYPE_SELL_STOP'}
        return [{**base, 'action': _const('TRADE_ACTION_PENDING', 5), 'type': _const(types_[(kind, buy)], 2), 'volume': volume,
                 'price': round(float(o['price']), digits), 'type_filling': _const('ORDER_FILLING_RETURN', 2)}]
    price = float(tick.ask if buy else tick.bid) if tick is not None else 0.0
    deal = {**base, 'action': _const('TRADE_ACTION_DEAL', 1), 'type': _const('ORDER_TYPE_BUY' if buy else 'ORDER_TYPE_SELL', 0 if buy else 1),
            'price': price, 'type_filling': _filling(info)}
    acc = mt5.account_info()
    hedging = acc is not None and int(getattr(acc, 'margin_mode', 0) or 0) == 2
    out, remaining = [], volume
    if hedging and o.get('net', True):
        opposite = 1 if buy else 0  # closing a SELL position needs a BUY
        mine = sorted([_d(p) for p in (mt5.positions_get(symbol=o['symbol']) or [])
                       if int(_d(p).get('type', 0)) == opposite and int(_d(p).get('magic') or 0) == base['magic']], key=lambda p: p.get('ticket') or 0)
        for p in mine:
            if remaining <= 0:
                break
            part = min(remaining, float(p.get('volume') or 0))
            out.append({**deal, 'volume': round(part, 10), 'position': p['ticket']})
            remaining = round(remaining - part, 10)
    if remaining > 0:
        out.append({**deal, 'volume': round(remaining, 10)})
    return out


def trade(request: dict) -> dict:
    """mode 'trade': action check | send | cancel | modify | close. Returns what the terminal answered, every time."""
    action, o = request.get('action'), request.get('order') or {}
    tag = str(o.get('tag') or '')[:31]
    o['tag'] = tag
    try:
        with api_lock:
            if action in ('send', 'check'):
                if action == 'send':
                    seen = _tagged(tag)
                    if any(seen.values()):  # already at the broker: report it, never send twice
                        return {'t': 'trade', 'action': action, 'duplicate': True, 'found': {k: [(_deal if k == 'deals' else _position if k == 'positions' else _order)(x) for x in v]
                                                                                          for k, v in seen.items()}, 'results': []}
                reqs = _requests(o)
                if action == 'check':
                    out = []
                    for r in reqs:
                        c = mt5.order_check(r)
                        d = _d(c) if c is not None else {}
                        code = int(d.get('retcode', -1)) if c is not None else None
                        out.append({'retcode': code, 'ok': code == 0, 'text': d.get('comment') or RET_TEXT.get(code or 0, ''),
                                    'margin': repr(float(d.get('margin') or 0)), 'margin_free': repr(float(d.get('margin_free') or 0)),
                                    'margin_level': repr(float(d.get('margin_level') or 0)), 'request': _safe(r)})
                    return {'t': 'trade', 'action': 'check', 'results': out}
                results = []
                for r in reqs:
                    res = _result(mt5.order_send(r), r)
                    results.append(res)
                    if not res['ok']:
                        break  # the rest is not sent; COREI sees exactly which part went through
                return {'t': 'trade', 'action': 'send', 'results': results, 'utc_now': now_iso()}
            if action == 'cancel':
                tickets = [x['ticket'] for x in (_tagged(tag)['orders'])] if not o.get('ticket') else [int(o['ticket'])]
                results = [_result(mt5.order_send(r), r) for r in [{'action': _const('TRADE_ACTION_REMOVE', 8), 'order': int(t)} for t in tickets]]
                return {'t': 'trade', 'action': 'cancel', 'results': results, 'nothing_open': not tickets}
            if action == 'modify':
                info = mt5.symbol_info(o['symbol'])
                digits = int(getattr(info, 'digits', 5) or 5) if info else 5
                r = {'action': _const('TRADE_ACTION_MODIFY', 7), 'order': int(o['ticket']), 'price': round(float(o['price']), digits),
                     'sl': round(float(o.get('sl') or 0), digits), 'tp': round(float(o.get('tp') or 0), digits)}
                return {'t': 'trade', 'action': 'modify', 'results': [_result(mt5.order_send(r), r)]}
            if action == 'sltp':
                info = mt5.symbol_info(o['symbol'])
                digits = int(getattr(info, 'digits', 5) or 5) if info else 5
                r = {'action': _const('TRADE_ACTION_SLTP', 6), 'symbol': o['symbol'], 'position': int(o['position']),
                     'sl': round(float(o.get('sl') or 0), digits), 'tp': round(float(o.get('tp') or 0), digits)}
                return {'t': 'trade', 'action': 'sltp', 'results': [_result(mt5.order_send(r), r)]}
            if action == 'close':  # one broker position by ticket (operator's choice on the reconciliation screen)
                p = next((_d(x) for x in (mt5.positions_get(ticket=int(o['position'])) or [])), None)
                if p is None:
                    return {'t': 'trade', 'action': 'close', 'results': [], 'error': 'POSITION_NOT_FOUND'}
                info = mt5.symbol_info(p['symbol'])
                tick = mt5.symbol_info_tick(p['symbol'])
                sell = int(p.get('type', 0)) == 0
                r = {'action': _const('TRADE_ACTION_DEAL', 1), 'symbol': p['symbol'], 'position': p['ticket'],
                     'volume': float(o.get('volume') or p['volume']), 'type': _const('ORDER_TYPE_SELL' if sell else 'ORDER_TYPE_BUY', 1 if sell else 0),
                     'price': float(tick.bid if sell else tick.ask) if tick else 0.0, 'deviation': int(o.get('deviation') or 10),
                     'magic': int(o.get('magic') or 0), 'comment': tag, 'type_filling': _filling(info)}
                return {'t': 'trade', 'action': 'close', 'results': [_result(mt5.order_send(r), r)]}
        return {'t': 'trade', 'action': action, 'results': [], 'error': f'UNKNOWN_ACTION: {action}'}
    except ValueError as exc:
        return {'t': 'trade', 'action': action, 'results': [], 'error': str(exc)[:300]}


def book(request: dict) -> dict:
    """mode 'book': the account as the broker holds it — balance, positions, working orders, deals since a time, and
    (with a tag) everything carrying that tag."""
    days = max(1, min(int(request.get('days') or 2), 90))
    start = dt.datetime.now(UTC) - dt.timedelta(days=days)
    end = dt.datetime.now(UTC) + dt.timedelta(days=1)
    with api_lock:
        acc = mt5.account_info()
        expected = request.get('login')
        if acc is None or (expected and int(getattr(acc, 'login', 0) or 0) != int(expected)):
            return {'t': 'failed', 'mode': 'book', 'error': 'BROKER_BOOK_ACCOUNT_UNAVAILABLE', 'sent': False}
        positions = mt5.positions_get()
        orders = mt5.orders_get()
        deals = mt5.history_deals_get(start, end)
        if any(rows is None for rows in (positions, orders, deals)):
            return {'t': 'failed', 'mode': 'book', 'error': 'BROKER_BOOK_DATA_UNAVAILABLE', 'sent': False}
        out = {'t': 'book', 'utc_now': now_iso(), 'server_time': server_time(), 'account': account_dict(),
               'positions': [_position(_d(p)) for p in positions],
               'orders': [_order(_d(o)) for o in orders],
               'deals': [_deal(_d(d)) for d in deals
                         if int(_d(d).get('type', 0)) in (0, 1)]}  # buys and sells; balance and credit rows are not trades
        a = _d(acc)
        out['money'] = {k: repr(float(a.get(k) or 0)) for k in ('balance', 'equity', 'margin', 'margin_free', 'margin_level', 'profit', 'credit')}
    if request.get('tag'):
        found = _tagged(str(request['tag'])[:31], days)
        out['tagged'] = {'orders': [_order(x) for x in found['orders']], 'positions': [_position(x) for x in found['positions']],
                         'history_orders': [_order(x) for x in found['history_orders']], 'deals': [_deal(x) for x in found['deals']]}
    return out


class Stream:
    def __init__(self, request: dict, send, recv_lines):
        self.send, self.recv = send, recv_lines
        self.symbols: set[str] = set(request.get('symbols') or [])
        self.heartbeat = float(request.get('heartbeat_seconds') or 5)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.last_msc: dict[str, int] = {}
        self.seq = 0

    def commands(self) -> None:
        try:
            for line in self.recv():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                cmd = m.get('cmd')
                if cmd == 'stop':
                    break
                if cmd in ('subscribe', 'unsubscribe'):
                    with self.lock:
                        for s in m.get('symbols') or []:
                            (self.symbols.add if cmd == 'subscribe' else self.symbols.discard)(s)
                            if cmd == 'subscribe':
                                mt5.symbol_select(s, True)
                        self.send({'t': 'subscribed', 'symbols': sorted(self.symbols)})
                elif cmd == 'history':
                    threading.Thread(target=self.history, args=(m,), daemon=True).start()
        finally:
            self.stop.set()

    def history(self, m: dict) -> None:
        try:
            frame = getattr(mt5, FRAMES[m['timeframe']])
            start = dt.datetime.fromisoformat(m['from']).replace(tzinfo=UTC)
            end = dt.datetime.fromisoformat(m['to']).replace(tzinfo=UTC)
            with api_lock:
                rates = mt5.copy_rates_range(m['symbol'], frame, start, end)
            bars = [{'t': dt.datetime.fromtimestamp(int(r['time']), UTC).replace(tzinfo=None).isoformat(), 'o': float(r['open']), 'h': float(r['high']),
                     'l': float(r['low']), 'c': float(r['close']), 'v': int(r['tick_volume'])} for r in (rates if rates is not None else [])]
            self.send({'t': 'bars', 'id': m.get('id'), 'bars': bars})
        except Exception as exc:  # noqa: BLE001
            self.send({'t': 'history_error', 'id': m.get('id'), 'msg': f'{type(exc).__name__}: {exc}'[:300]})

    def run(self) -> None:
        for s in list(self.symbols):
            mt5.symbol_select(s, True)
        utc = now_iso()
        self.send({'t': 'connected', 'host': 'terminal', 'port': 0, 'attempts': [{'host': 'terminal', 'port': 0, 'ok': True}],
                   'server_time': server_time(), 'utc_now': utc, 'client_build': terminal_build()})
        self.send({'t': 'subscribed', 'symbols': sorted(self.symbols)})
        threading.Thread(target=self.commands, daemon=True).start()
        beat = time.monotonic()
        while not self.stop.is_set():
            with self.lock:
                names = sorted(self.symbols)
            for name in names:
                since = self.last_msc.get(name)
                try:
                    with api_lock:
                        if since is None:
                            tick = mt5.symbol_info_tick(name)
                            ticks = [tick] if tick is not None else []
                        else:
                            ticks = mt5.copy_ticks_from(name, dt.datetime.fromtimestamp(since / 1000 + 0.001, UTC), 2000, mt5.COPY_TICKS_ALL)
                            ticks = list(ticks) if ticks is not None else []
                except Exception as exc:  # noqa: BLE001
                    self.send({'t': 'error', 'msg': f'{name}: {type(exc).__name__}: {exc}'[:300]})
                    continue
                rx = now_iso()
                for tick in ticks:
                    msc = int(field(tick, 'time_msc'))
                    if since is not None and msc <= since:
                        continue
                    bid, ask, last, vol = (float(field(tick, 'bid')), float(field(tick, 'ask')), float(field(tick, 'last')), int(field(tick, 'volume')))
                    self.last_msc[name] = msc
                    since = msc
                    self.seq += 1
                    self.send({'t': 'q', 's': name, 'b': bid or None, 'a': ask or None, 'l': last or None, 'v': vol or None,
                               'st': dt.datetime.fromtimestamp(msc / 1000, UTC).replace(tzinfo=None).isoformat(timespec='milliseconds'), 'rx': rx, 'n': self.seq})
            with api_lock:
                term = mt5.terminal_info()
            if not term or not getattr(term, 'connected', True):
                self.send({'t': 'disconnected'})
                return
            if time.monotonic() - beat >= self.heartbeat:
                beat = time.monotonic()
                self.send({'t': 'hb', 'utc_now': now_iso()})
            self.stop.wait(0.1)
        self.send({'t': 'stopped'})


# ----- several accounts on one machine: one portable terminal copy and one worker per login -----

class Terminals:
    def __init__(self, master: str, root: Path, max_terminals: int = 0):
        self.master, self.root = Path(master), root
        self.max_terminals = max_terminals  # 0 = no limit; else a pool: the least recently used idle terminal makes room
        self.workers: dict[str, dict] = {}
        self.lock = threading.Lock()

    def busy(self, login: str, delta: int) -> None:
        with self.lock:
            w = self.workers.get(login)
            if w:
                w['sessions'] = max(0, w['sessions'] + delta)
                w['used'] = time.monotonic()

    def _stop(self, login: str) -> None:
        """Stop a login's worker and its terminal copy (the pool needs the room)."""
        w = self.workers.pop(login)
        try:
            w['proc'].terminate()
        except OSError:
            pass
        exe = str(self.root / login / 'terminal64.exe')
        if os.name == 'nt':
            subprocess.run(['powershell', '-NoProfile', '-Command',
                            f"Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq '{exe}' }} | Stop-Process -Force"],
                           capture_output=True, timeout=30)
        print(f'{now_iso()} login {login}: terminal closed to make room in the pool ({self.max_terminals} terminals)', flush=True)

    def copy_for(self, login: str) -> Path:
        folder = self.root / login
        exe = folder / 'terminal64.exe'
        if not exe.exists():
            print(f'{now_iso()} login {login}: making a portable terminal copy in {folder} (once; about a minute)')
            shutil.copytree(self.master.parent, folder, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('logs', 'Tester', 'Bases', '*.log', 'Config'))
            self._server_list(folder)
        return exe

    def _server_list(self, folder: Path) -> None:
        """The installed terminal's broker server list (servers.dat, no passwords) from its data folder, so the copy
        knows the broker's servers as the installed terminal does."""
        appdata = Path(os.environ.get('APPDATA', '')) / 'MetaQuotes' / 'Terminal'
        for origin in appdata.glob('*/origin.txt') if appdata.exists() else []:
            try:
                text = origin.read_text(encoding='utf-16', errors='ignore').strip() or origin.read_text(errors='ignore').strip()
            except OSError:
                continue
            if Path(text.strip('\ufeff').strip()).resolve() == self.master.parent.resolve():
                servers = origin.parent / 'config' / 'servers.dat'
                if servers.exists():
                    (folder / 'Config').mkdir(exist_ok=True)
                    shutil.copy2(servers, folder / 'Config' / 'servers.dat')
                    print(f'{now_iso()} copied the broker server list from {servers}', flush=True)
                return

    def port_for(self, login: str) -> int:
        """The local port of this login's worker, starting it (and its terminal copy) when needed."""
        with self.lock:
            w = self.workers.get(login)
            if w and w['proc'].poll() is None:
                w['used'] = time.monotonic()
                return w['port']
            alive = {k: v for k, v in self.workers.items() if v['proc'].poll() is None}
            if self.max_terminals and len(alive) >= self.max_terminals:
                idle = sorted((v['used'], k) for k, v in alive.items() if v['sessions'] == 0)
                if not idle:
                    raise OSError(f'BRIDGE_FULL: all {self.max_terminals} terminals of this bridge are streaming; add a bridge node or raise --max-terminals.')
                self._stop(idle[0][1])
            exe = self.copy_for(login)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', '--terminal', str(exe), '--port', str(port)])
            for _ in range(300):
                time.sleep(0.1)
                try:
                    socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
                    break
                except OSError:
                    continue
            self.workers[login] = {'port': port, 'proc': proc, 'sessions': 0, 'used': time.monotonic()}
            print(f'{now_iso()} login {login}: worker on 127.0.0.1:{port} (pid {proc.pid})')
            return port


def proxy_to_worker(conn: socket.socket, token: str, terminals: Terminals) -> None:
    """Token check here; then the whole session is passed line by line to the login's worker and back."""
    reader = conn.makefile('r', encoding='utf-8', newline='\n')
    writer = conn.makefile('w', encoding='utf-8', newline='\n')
    worker = None
    try:
        first = reader.readline()
        try:
            request = json.loads(first or '')
        except ValueError:
            request = {}
        if not isinstance(request, dict) or not secrets.compare_digest(str(request.get('token', '')), token):
            writer.write(json.dumps({'t': 'failed', 'attempts': [{'host': 'bridge', 'port': 0, 'ok': False,
                                     'error': 'BRIDGE_TOKEN_REJECTED: the bridge token does not match.', 'refused': True}]}) + '\n')
            writer.flush()
            return
        if request.get('mode') == 'ping':  # COREI's health check: no login, no terminal started
            writer.write(json.dumps(pong(terminals)) + '\n')
            writer.flush()
            return
        login = str(int(request.get('login') or 0))
        worker = socket.create_connection(('127.0.0.1', terminals.port_for(login)), timeout=30)
        terminals.busy(login, +1)
        worker.settimeout(None)
        wr = worker.makefile('r', encoding='utf-8', newline='\n')
        ww = worker.makefile('w', encoding='utf-8', newline='\n')
        ww.write(first)
        ww.flush()

        def upstream() -> None:  # COREI's commands → worker
            try:
                for line in reader:
                    ww.write(line)
                    ww.flush()
            except (OSError, ValueError):
                pass
            finally:
                try:
                    worker.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        threading.Thread(target=upstream, daemon=True).start()
        for line in wr:  # worker's lines → COREI
            writer.write(line)
            writer.flush()
    except (OSError, ValueError) as exc:
        try:
            writer.write(json.dumps({'t': 'error', 'msg': f'{type(exc).__name__}: {exc}'[:200]}) + '\n')
            writer.flush()
        except OSError:
            pass
    finally:
        if worker is not None:
            terminals.busy(login, -1)
        for part in (worker, writer, reader, conn):
            try:
                part.close()
            except (OSError, AttributeError):
                pass


def listen(host: str, port: int) -> socket.socket:
    """The listening socket, exclusively: on Windows a second program may otherwise share the port, and connections
    would go to one or the other at random (two bridges on one machine). A port in use is said plainly."""
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):  # Windows
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        raise SystemExit(f'{now_iso()} PORT_IN_USE: port {port} is already used by another program ({exc}). Only one COREI bridge can listen on a port: '
                         'stop the other one (the update script does this: install-corei-bridge.ps1 -Update -Name <node>), or give this bridge another port.')
    sock.listen(128)
    return sock


def pong(terminals: 'Terminals | None') -> dict:
    """What COREI's health check sees: version, clock, and the terminal pool."""
    out = {'t': 'pong', 'version': BRIDGE_VERSION, 'utc_now': now_iso(), 'machine': platform.node()[:80]}
    if terminals is not None:
        with terminals.lock:
            out.update({'terminals_open': len(terminals.workers), 'max_terminals': terminals.max_terminals,
                        'streaming': sum(1 for w in terminals.workers.values() if w.get('sessions'))})
    return out


def enroll(url: str, code: str, state: Path, port: int, extra: dict) -> int:
    """Register this bridge with COREI using the one-time code from Setup › Bridge nodes: sends the certificate
    fingerprint, the token (over HTTPS, once) and how to reach it, so nobody copies them by hand."""
    import urllib.error
    import urllib.request
    state.mkdir(parents=True, exist_ok=True)
    body = {'fingerprint': ensure_cert(state / 'corei-bridge.crt', state / 'corei-bridge.key'), 'token': ensure_token(state / 'corei-bridge.token'),
            'port': port, 'bridge_version': BRIDGE_VERSION, 'machine': platform.node()[:80], **extra}
    req = urllib.request.Request(url.rstrip('/') + '/api/bridge-enroll/' + urllib.parse.quote(code.strip()), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'User-Agent': f'corei-mt5-bridge/{BRIDGE_VERSION}'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - the COREI URL the operator gave
            answer = json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b'{}').get('detail')
        except ValueError:
            detail = None
        print(json.dumps({'ok': False, 'status': exc.code, 'error': detail or exc.reason}))
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': f'cannot reach COREI at {url}: {exc}'}))
        return 1
    print(json.dumps({'ok': True, **answer}))
    return 0


# ----- the TLS server -----

def serve_client(conn: socket.socket, token: str, terminal: str | None) -> None:
    reader = conn.makefile('r', encoding='utf-8', newline='\n')
    writer = conn.makefile('w', encoding='utf-8', newline='\n')
    out_lock = threading.Lock()

    gone = threading.Event()

    def send(message: dict) -> None:
        if gone.is_set():
            return
        with out_lock:
            try:
                writer.write(json.dumps(message, separators=(',', ':')) + '\n')
                writer.flush()
            except OSError:
                gone.set()  # COREI closed the connection; the stream loop ends on the next check

    try:
        try:
            request = json.loads(reader.readline() or '')
        except ValueError:
            request = {}
        # A worker (token '') serves the supervisor on 127.0.0.1 only; the supervisor has already checked the token.
        if not isinstance(request, dict) or (token and not secrets.compare_digest(str(request.get('token', '')), token)):
            send({'t': 'failed', 'attempts': [{'host': 'bridge', 'port': 0, 'ok': False, 'error': 'BRIDGE_TOKEN_REJECTED: the bridge token does not match.', 'refused': True}]})
            return
        mode = request.get('mode') or 'stream'
        if mode == 'ping':
            send(pong(None))
            return
        if mode in ('trade', 'book'):  # orders and the broker's book: works beside a running stream of the same login
            ok, error = ensure_trading_login(request, terminal) if mode == 'trade' else (True, '')
            if mode == 'book':
                with api_lock:
                    info = mt5.account_info()
                if info is None or int(getattr(info, 'login', 0) or 0) != int(request.get('login') or 0):
                    if not terminal_lock.acquire(timeout=5):
                        ok, error = False, 'TERMINAL_BUSY: this terminal is serving another account; use a terminal pool (--terminals-root)'
                    else:
                        try:
                            ok, error = login(request, terminal)
                        finally:
                            terminal_lock.release()
            request['password'] = ''
            if not ok:
                send({'t': 'failed', 'mode': mode, 'error': error, 'sent': False})
                return
            send(trade(request) if mode == 'trade' else book(request))
            return
        with terminal_lock:
            ok, error = login(request, terminal)
            request['password'] = ''
            if not ok:
                failed = {'host': 'terminal', 'port': 0, 'ok': False, 'error': error, 'refused': error.startswith('ServerException')}
                send({'connected': False, 'attempts': [failed], 'error': error} if mode == 'session' else {'t': 'failed', 'attempts': [failed]})
                return
            if mode == 'session':
                send(session(request))
            else:
                stream = Stream(request, send, lambda: iter(reader.readline, ''))
                threading.Thread(target=lambda: (gone.wait(), stream.stop.set()), daemon=True).start()
                try:
                    stream.run()
                finally:
                    gone.set()  # releases the waiter thread, which otherwise keeps the socket's reader alive
    except (OSError, ValueError) as exc:
        try:
            send({'t': 'error', 'msg': f'{type(exc).__name__}: {exc}'[:200]})
        except OSError:
            pass
    finally:
        gone.set()
        for part in (writer, reader, conn):
            try:
                part.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='COREI MT5 terminal bridge')
    parser.add_argument('--terminal', help='terminal64.exe path (default: the terminal the package finds)')
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9443)
    parser.add_argument('--state', default=str(HERE), help='folder for the certificate and token')
    parser.add_argument('--init', action='store_true', help='make the certificate and token, print what COREI needs, and exit')
    parser.add_argument('--fingerprint', action='store_true', help='print this bridge\'s certificate fingerprint and exit (no token shown)')
    parser.add_argument('--terminals-root', help='serve every account: one portable terminal copy per login under this folder')
    parser.add_argument('--version', action='version', version=f'corei_mt5_bridge {BRIDGE_VERSION}')
    parser.add_argument('--max-terminals', type=int, default=0, help='with --terminals-root: at most this many terminals run at once (0 = no limit); '
                        'an idle one (no stream) is closed to make room, least recently used first')
    parser.add_argument('--enroll', metavar='COREI_URL', help='register this bridge with COREI (with --code from Setup › Bridge nodes) and exit')
    parser.add_argument('--code', help='the one-time enrolment code')
    parser.add_argument('--ssh-public-key', default='', help='tunnel mode: the public key this machine uses for its SSH tunnel')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)  # internal: one login, plain TCP on 127.0.0.1
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)  # the log file shows each line at once
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    global PORTABLE
    if args.worker:
        PORTABLE = True
        with socket.create_server(('127.0.0.1', args.port), reuse_port=False) as server:
            while True:
                raw, _ = server.accept()
                threading.Thread(target=serve_client, args=(raw, '', args.terminal), daemon=True).start()
    if args.enroll:
        if not args.code:
            print('--enroll needs --code (Setup › Bridge nodes › Connect a Windows machine)', file=sys.stderr)
            return 2
        return enroll(args.enroll, args.code, Path(args.state), args.port,
                      {'ssh_public_key': args.ssh_public_key, 'max_terminals': args.max_terminals or None})
    if args.init:
        state = Path(args.state)
        state.mkdir(parents=True, exist_ok=True)
        print(json.dumps({'fingerprint': ensure_cert(state / 'corei-bridge.crt', state / 'corei-bridge.key'),
                          'token_file': str(state / 'corei-bridge.token'), 'token': ensure_token(state / 'corei-bridge.token')}))
        return 0
    if args.fingerprint:
        state = Path(args.state)
        print(ensure_cert(state / 'corei-bridge.crt', state / 'corei-bridge.key'))
        return 0
    if mt5 is None:
        print('MetaTrader5 package missing: pip install MetaTrader5 (Windows, or Wine with a Windows Python)', file=sys.stderr)
        return 2
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    fingerprint = ensure_cert(state / 'corei-bridge.crt', state / 'corei-bridge.key')
    token = ensure_token(state / 'corei-bridge.token')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(state / 'corei-bridge.crt'), str(state / 'corei-bridge.key'))
    terminals = Terminals(args.terminal, Path(args.terminals_root), args.max_terminals) if args.terminals_root and args.terminal else None
    print(f'{now_iso()} COREI MT5 bridge {BRIDGE_VERSION} listening on {args.bind}:{args.port}' + (f' · every account, terminal copies under {args.terminals_root}' if terminals else ''))
    print(f'Certificate fingerprint (SHA-256): {fingerprint}')
    print(f'Token file: {state / "corei-bridge.token"} (paste its contents into COREI; keep it secret)')
    with listen(args.bind, args.port) as server:
        server.settimeout(1.0)
        while True:
            try:
                raw, peer = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return 0
            try:
                conn = context.wrap_socket(raw, server_side=True)
            except ssl.SSLError:
                raw.close()
                continue
            print(f'{now_iso()} connection from {peer[0]}')
            if terminals:
                threading.Thread(target=proxy_to_worker, args=(conn, token, terminals), daemon=True).start()
            else:
                threading.Thread(target=serve_client, args=(conn, token, args.terminal), daemon=True).start()


if __name__ == '__main__':
    sys.exit(main())
