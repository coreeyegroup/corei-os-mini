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

One terminal holds one login at a time, so run one bridge (and one portable terminal copy) per account
that must stream at the same time. Standard library only, apart from MetaTrader5.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
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


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(tzinfo=None).isoformat(timespec='seconds')


def ensure_cert(cert: Path, key: Path) -> str:
    """Self-signed certificate for this bridge; COREI pins its SHA-256 fingerprint, so no CA is needed."""
    if not cert.exists() or not key.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650', '-subj', '/CN=corei-mt5-bridge',
                        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
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
    kwargs = {'path': terminal} if terminal else {}
    if not mt5.initialize(**kwargs):
        return False, f'Terminal did not start: {mt5.last_error()}'
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
            'account': account, 'symbols': symbols, 'symbols_available': len(names), 'client_build': terminal_build(),
            'sdk_version': f"MetaTrader5 {getattr(mt5, '__version__', '?')}"}


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
            if not mt5.terminal_info() or not getattr(mt5.terminal_info(), 'connected', True):
                self.send({'t': 'disconnected'})
                return
            if time.monotonic() - beat >= self.heartbeat:
                beat = time.monotonic()
                self.send({'t': 'hb', 'utc_now': now_iso()})
            self.stop.wait(0.2)
        self.send({'t': 'stopped'})


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
        if not isinstance(request, dict) or not secrets.compare_digest(str(request.get('token', '')), token):
            send({'t': 'failed', 'attempts': [{'host': 'bridge', 'port': 0, 'ok': False, 'error': 'BRIDGE_TOKEN_REJECTED: the bridge token does not match.', 'refused': True}]})
            return
        mode = request.get('mode') or 'stream'
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
    args = parser.parse_args(argv)
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
    print(f'COREI MT5 bridge listening on {args.bind}:{args.port}')
    print(f'Certificate fingerprint (SHA-256): {fingerprint}')
    print(f'Token file: {state / "corei-bridge.token"} (paste its contents into COREI; keep it secret)')
    with socket.create_server((args.bind, args.port), reuse_port=False) as server:
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
            threading.Thread(target=serve_client, args=(conn, token, args.terminal), daemon=True).start()


if __name__ == '__main__':
    sys.exit(main())
