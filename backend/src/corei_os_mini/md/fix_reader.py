"""FIX 4.4 / 4.2 market-data reader: the same line protocol as the MT5 and Upstox readers, so the collector,
journal, quality, feed switching, bars and archive treat a FIX venue like any other source.

stdin, first line: {"host", "port", "tls", "tls_verify", "begin_string", "sender_comp_id", "target_comp_id",
                    "sender_sub_id", "target_sub_id", "username"?, "password"?, "heartbeat_seconds",
                    "reset_seq_on_logon", "md_depth", "md_snapshots_only", "symbols": [...], "timeout_seconds"}
then commands:     {"cmd": "subscribe"|"unsubscribe", "symbols": [...]}, {"cmd": "history", "id", ...}, {"cmd": "stop"}
stdout:            connected, q, subscribed, hb, error, history_error, failed, disconnected, stopped.
With --session it logs on, reports, and logs out ("Log in and read now"); with --securities it asks for the
venue's instrument list (SecurityListRequest) for the symbol catalogue.

Messages used (FIX 4.4): Logon A, Heartbeat 0, TestRequest 1, ResendRequest 2 (answered with SequenceReset-GapFill),
Reject 3, SequenceReset 4, Logout 5, MarketDataRequest V, MarketDataSnapshotFullRefresh W,
MarketDataIncrementalRefresh X, MarketDataRequestReject Y, BusinessMessageReject j. Times are UTC (SendingTime 52).
The password arrives on stdin only and is never printed. Run: python -m corei_os_mini.md.fix_reader [--session]
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone

SOH = '\x01'
UTC = timezone.utc
out_lock = threading.Lock()
MD_REJECT = {'0': 'unknown symbol', '1': 'duplicate request', '2': 'insufficient bandwidth', '3': 'insufficient permissions',
             '4': 'unsupported subscription type', '5': 'unsupported market depth', '6': 'unsupported update type',
             '7': 'unsupported aggregated book', '8': 'unsupported entry type'}


def emit(message: dict) -> None:
    with out_lock:
        sys.stdout.write(json.dumps(message, separators=(',', ':')) + '\n')
        sys.stdout.flush()


def utc_stamp() -> str:
    now = datetime.now(UTC)
    return now.strftime('%Y%m%d-%H:%M:%S.') + f'{now.microsecond // 1000:03d}'


def parse_fix_time(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ('%Y%m%d-%H:%M:%S.%f', '%Y%m%d-%H:%M:%S'):
        try:
            return datetime.strptime(value[:21] if '.' in value else value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def encode(begin: str, fields: list[tuple[int, str]]) -> bytes:
    body = ''.join(f'{tag}={value}{SOH}' for tag, value in fields)
    head = f'8={begin}{SOH}9={len(body.encode())}{SOH}'
    checksum = sum((head + body).encode()) % 256
    return (head + body + f'10={checksum:03d}{SOH}').encode()


def split_fields(raw: bytes) -> list[tuple[int, str]]:
    out = []
    for part in raw.decode('latin-1').split(SOH):
        if '=' in part:
            tag, _, value = part.partition('=')
            if tag.isdigit():
                out.append((int(tag), value))
    return out


class FrameReader:
    """Cuts a byte stream into complete FIX messages (BodyLength 9 + checksum 10)."""

    def __init__(self):
        self.buffer = b''

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer += data
        messages = []
        while True:
            start = self.buffer.find(b'8=FIX')
            if start < 0:
                self.buffer = b''
                return messages
            self.buffer = self.buffer[start:]
            length_at = self.buffer.find(b'\x019=')
            if length_at < 0:
                return messages
            length_end = self.buffer.find(b'\x01', length_at + 3)
            if length_end < 0:
                return messages
            try:
                body_length = int(self.buffer[length_at + 3:length_end])
            except ValueError:
                self.buffer = self.buffer[length_end:]
                continue
            end = length_end + 1 + body_length + 7  # "10=xxx\x01"
            if len(self.buffer) < end:
                return messages
            messages.append(self.buffer[:end])
            self.buffer = self.buffer[end:]


class Refused(Exception):
    pass


class Session:
    def __init__(self, request: dict):
        self.r = request
        self.begin = request.get('begin_string') or 'FIX.4.4'
        self.seq = 1
        self.sock: socket.socket | None = None
        self.frames = FrameReader()
        self.send_lock = threading.Lock()
        self.last_sent = self.last_received = time.monotonic()
        self.hb = int(request.get('heartbeat_seconds') or 30)

    def connect(self) -> None:
        raw = socket.create_connection((self.r['host'], int(self.r['port'])), timeout=float(self.r.get('timeout_seconds') or 15))
        if self.r.get('tls', True):
            context = ssl.create_default_context()
            if not self.r.get('tls_verify', True):
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            raw = context.wrap_socket(raw, server_hostname=self.r['host'])
        self.sock = raw

    def send(self, msg_type: str, fields: list[tuple[int, str]]) -> None:
        header = [(35, msg_type), (49, self.r['sender_comp_id']), (56, self.r['target_comp_id'])]
        if self.r.get('sender_sub_id'):
            header.append((50, self.r['sender_sub_id']))
        if self.r.get('target_sub_id'):
            header.append((57, self.r['target_sub_id']))
        with self.send_lock:
            header += [(34, str(self.seq)), (52, utc_stamp())]
            self.seq += 1
            assert self.sock is not None
            self.sock.sendall(encode(self.begin, header + fields))
            self.last_sent = time.monotonic()

    def receive(self, timeout: float) -> list[list[tuple[int, str]]]:
        assert self.sock is not None
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(65536)
        except (socket.timeout, TimeoutError):
            return []
        if not data:
            raise ConnectionError('The FIX venue closed the connection.')
        self.last_received = time.monotonic()
        return [split_fields(m) for m in self.frames.feed(data)]

    def logon(self) -> dict:
        fields = [(98, '0'), (108, str(self.hb))]
        if self.r.get('reset_seq_on_logon', True):
            fields.append((141, 'Y'))
        if self.r.get('username'):
            fields.append((553, self.r['username']))
        if self.r.get('password'):
            fields.append((554, self.r['password']))
        self.send('A', fields)
        deadline = time.monotonic() + float(self.r.get('timeout_seconds') or 15)
        while time.monotonic() < deadline:
            for m in self.receive(1.0):
                d = dict(m)
                kind = d.get(35)
                if kind == 'A':
                    return d
                if kind == '5':
                    raise Refused(f"The FIX venue refused the logon: {d.get(58) or 'no reason given'}")
                if kind == '3':
                    raise Refused(f"The FIX venue rejected the logon message: {d.get(58) or 'reason ' + d.get(373, '?')}")
        raise TimeoutError('No Logon reply from the FIX venue.')

    def logout(self) -> None:
        try:
            self.send('5', [])
        except OSError:
            pass

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass


class Feed:
    """Subscriptions and a top-of-book per symbol; emits a quote when bid or ask changes."""

    def __init__(self, session: Session, request: dict):
        self.s = session
        self.r = request
        self.symbols: set[str] = set(request.get('symbols') or [])
        self.req_ids: dict[str, str] = {}
        self.book: dict[str, dict] = {}
        self.ids_to_symbol: dict[str, str] = {}
        self.seq_out = 0
        self.next_req = 1
        self.lock = threading.Lock()

    def subscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            if symbol in self.req_ids:
                continue
            rid = f'COREI-{self.next_req}'
            self.next_req += 1
            self.req_ids[symbol] = rid
            self.ids_to_symbol[rid] = symbol
            snapshots = self.r.get('md_snapshots_only')
            self.s.send('V', [(262, rid), (263, '1'), (264, str(int(self.r.get('md_depth') or 1))),
                              *([] if snapshots else [(265, '1')]),
                              (267, '2'), (269, '0'), (269, '1'), (146, '1'), (55, symbol)])
        self.symbols.update(symbols)

    def unsubscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            rid = self.req_ids.pop(symbol, None)
            if rid:
                self.s.send('V', [(262, rid), (263, '2'), (264, '0'), (267, '2'), (269, '0'), (269, '1'), (146, '1'), (55, symbol)])
            self.book.pop(symbol, None)
        self.symbols.difference_update(symbols)

    def handle(self, m: list[tuple[int, str]]) -> str | None:
        d = dict(m)
        kind = d.get(35)
        if kind == '1':
            self.s.send('0', [(112, d.get(112, ''))])
        elif kind == '2':
            # We keep no resend store: fill the requested range with a gap fill (market data is not replayed).
            self.s.send('4', [(123, 'Y'), (36, str(self.s.seq + 1))])
        elif kind == '5':
            emit({'t': 'error', 'msg': f"FIX venue logged out: {d.get(58) or 'no reason given'}"})
            return 'end'
        elif kind == '3':
            emit({'t': 'error', 'msg': f"FIX reject (ref {d.get(45)}, tag {d.get(371, '?')}): {d.get(58, '')}"[:300]})
        elif kind == 'j':
            emit({'t': 'error', 'msg': f"FIX business reject: {d.get(58, '')} (reason {d.get(380, '?')})"[:300]})
        elif kind == 'Y':
            symbol = self.ids_to_symbol.get(d.get(262, ''), d.get(262, '?'))
            reason = MD_REJECT.get(d.get(281, ''), d.get(281, ''))
            emit({'t': 'error', 'msg': f"Market data refused for {symbol}: {d.get(58) or reason}"[:300]})
        elif kind in ('W', 'X'):
            self.market_data(m, kind, parse_fix_time(d.get(52)))
        return None

    def market_data(self, m: list[tuple[int, str]], kind: str, sent: datetime | None) -> None:
        touched: set[str] = set()
        symbol = dict(m).get(55) if kind == 'W' else None
        if symbol is None and kind == 'W':
            symbol = self.ids_to_symbol.get(dict(m).get(262, ''))
        if kind == 'W' and symbol:
            self.book[symbol] = {}
            touched.add(symbol)
        # Repeating group: W entries start at 269 (MDEntryType); X entries start at 279 (MDUpdateAction).
        entry: dict[int, str] | None = None
        entries: list[dict[int, str]] = []
        for tag, value in m:
            if tag == 279 or (tag == 269 and (entry is None or 269 in entry)):
                entry = {}
                entries.append(entry)
            if entry is not None and tag in (269, 270, 271, 279, 55, 290):
                entry[tag] = value
        for e in entries:
            sym = e.get(55) or symbol
            if not sym or e.get(269) not in ('0', '1') or e.get(290, '1') != '1':
                continue  # top of book only: bid (0) and offer (1) at position 1
            side = 'b' if e[269] == '0' else 'a'
            book = self.book.setdefault(sym, {})
            if e.get(279) == '2':
                book.pop(side, None)
            elif e.get(270):
                try:
                    book[side] = float(e[270])
                except ValueError:
                    continue
            touched.add(sym)
        rx = datetime.now(UTC).isoformat()
        for sym in touched:
            book = self.book.get(sym) or {}
            if not book:
                continue
            signature = (book.get('b'), book.get('a'))
            if book.get('_last') == signature:
                continue
            book['_last'] = signature
            self.seq_out += 1
            emit({'t': 'q', 's': sym, 'b': book.get('b'), 'a': book.get('a'), 'l': None, 'v': None,
                  'st': (sent or datetime.now(UTC)).isoformat(), 'rx': rx, 'n': self.seq_out})


def run_session(request: dict) -> int:
    s = Session(request)
    started = time.monotonic()
    try:
        s.connect()
        reply = s.logon()
    except Refused as exc:
        emit({'connected': False, 'attempts': [{'host': request['host'], 'port': request['port'], 'ok': False, 'error': str(exc), 'refused': True}]})
        return 3
    except (OSError, TimeoutError, ConnectionError, ssl.SSLError) as exc:
        emit({'connected': False, 'attempts': [{'host': request['host'], 'port': request['port'], 'ok': False,
                                                'error': f'{type(exc).__name__}: {exc}'[:300]}]})
        return 3
    finally:
        request.pop('password', None)
    sent = parse_fix_time(reply.get(52))
    utc = datetime.now(UTC)
    s.logout()
    s.close()
    emit({'connected': True, 'host': request['host'], 'port': request['port'], 'connect_ms': int((time.monotonic() - started) * 1000),
          'server_time': (sent or utc).replace(tzinfo=None).isoformat(timespec='seconds'), 'utc_now': utc.replace(tzinfo=None).isoformat(timespec='seconds'),
          'client_build': None, 'account': {}, 'symbols': {}, 'symbols_available': 0, 'attempts': [{'host': request['host'], 'port': request['port'], 'ok': True}]})
    return 0


SECURITY_FIELDS = {55: 'symbol', 48: 'security_id', 107: 'description', 167: 'security_type', 461: 'cfi_code', 15: 'currency',
                   207: 'exchange', 231: 'contract_multiplier', 969: 'min_price_increment', 541: 'maturity', 202: 'strike', 311: 'underlying',
                   1147: 'unit_of_measure_qty'}


def run_securities(request: dict, wait: float = 60.0) -> int:
    """The venue's instrument list: SecurityListRequest (x, all securities), answered by one or more
    SecurityList (y) messages; for the symbol catalogue."""
    s = Session(request)
    try:
        s.connect()
        s.logon()
    except Refused as exc:
        emit({'connected': False, 'error': str(exc)})
        return 3
    except (OSError, TimeoutError, ConnectionError, ssl.SSLError) as exc:
        emit({'connected': False, 'error': f'{type(exc).__name__}: {exc}'[:300]})
        return 3
    finally:
        request.pop('password', None)
    req_id = f'COREI-SL-{int(time.time())}'
    s.send('x', [(320, req_id), (559, '4')])
    found: list[dict] = []
    error, done, deadline = None, False, time.monotonic() + wait
    while not done and time.monotonic() < deadline:
        for m in s.receive(2.0):
            kind = next((v for t, v in m if t == 35), '')
            if kind == '1':
                s.send('0', [(112, next((v for t, v in m if t == 112), ''))])
            elif kind in ('3', 'j'):
                error = next((v for t, v in m if t == 58), 'Security list request rejected')
                done = True
            elif kind == 'y':
                result = next((v for t, v in m if t == 560), '0')
                if result != '0':
                    error = {'1': 'invalid or unsupported request', '2': 'no instruments match', '3': 'not authorised',
                             '4': 'instrument data temporarily unavailable', '5': 'request for instrument data not supported'}.get(result, f'result {result}')
                current: dict | None = None
                for tag, value in m:
                    if tag == 55:
                        current = {}
                        found.append(current)
                    if current is not None and tag in SECURITY_FIELDS:
                        current.setdefault(SECURITY_FIELDS[tag], value)
                if next((v for t, v in m if t == 893), 'Y') == 'Y' or result != '0':
                    done = True
    s.logout()
    s.close()
    emit({'connected': True, 'securities': found, 'complete': done and error is None, 'error': error})
    return 0


def run_stream(request: dict) -> int:
    s = Session(request)
    try:
        s.connect()
        reply = s.logon()
    except Refused as exc:
        emit({'t': 'failed', 'attempts': [{'host': request['host'], 'port': request['port'], 'ok': False, 'error': str(exc), 'refused': True}]})
        return 3
    except (OSError, TimeoutError, ConnectionError, ssl.SSLError) as exc:
        emit({'t': 'failed', 'attempts': [{'host': request['host'], 'port': request['port'], 'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}]})
        return 3
    finally:
        request.pop('password', None)
    sent = parse_fix_time(reply.get(52))
    utc = datetime.now(UTC)
    emit({'t': 'connected', 'host': request['host'], 'port': request['port'],
          'server_time': (sent or utc).replace(tzinfo=None).isoformat(), 'utc_now': utc.replace(tzinfo=None).isoformat(), 'client_build': None})
    feed = Feed(s, request)
    stop = threading.Event()
    commands: list[dict] = []
    commands_lock = threading.Lock()

    def read_commands():
        for raw in sys.stdin:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            with commands_lock:
                commands.append(m)
            if m.get('cmd') == 'stop':
                break
        with commands_lock:
            commands.append({'cmd': 'stop'})

    threading.Thread(target=read_commands, daemon=True).start()
    feed.subscribe(sorted(feed.symbols))
    emit({'t': 'subscribed', 'symbols': sorted(feed.symbols)})
    beat = time.monotonic()
    try:
        while not stop.is_set():
            with commands_lock:
                pending, commands[:] = list(commands), []
            for c in pending:
                if c.get('cmd') == 'stop':
                    stop.set()
                elif c.get('cmd') == 'subscribe':
                    feed.subscribe(c.get('symbols') or [])
                    emit({'t': 'subscribed', 'symbols': sorted(feed.symbols)})
                elif c.get('cmd') == 'unsubscribe':
                    feed.unsubscribe(c.get('symbols') or [])
                    emit({'t': 'subscribed', 'symbols': sorted(feed.symbols)})
                elif c.get('cmd') == 'history':
                    emit({'t': 'history_error', 'id': c.get('id'), 'msg': 'A FIX market-data session has no history; bars are built from live ticks.'})
            if stop.is_set():
                break
            for m in s.receive(0.2):
                if feed.handle(m) == 'end':
                    emit({'t': 'disconnected'})
                    return 4
            now = time.monotonic()
            if now - s.last_sent >= s.hb:
                s.send('0', [])
            if now - s.last_received > s.hb * 2 + 5:
                s.send('1', [(112, f'COREI-{int(now)}')])
                if now - s.last_received > s.hb * 3 + 10:
                    emit({'t': 'error', 'msg': 'The FIX venue stopped answering (no heartbeat).'})
                    emit({'t': 'disconnected'})
                    return 4
            if now - beat >= 5:
                beat = now
                emit({'t': 'hb', 'utc_now': datetime.now(UTC).isoformat()})
    except (OSError, ConnectionError, ssl.SSLError) as exc:
        emit({'t': 'error', 'msg': f'{type(exc).__name__}: {exc}'[:300]})
        emit({'t': 'disconnected'})
        return 4
    s.logout()
    s.close()
    emit({'t': 'stopped'})
    return 0


def main(argv: list[str]) -> int:
    try:
        request = json.loads(sys.stdin.readline())
        for key in ('host', 'port', 'sender_comp_id', 'target_comp_id'):
            request[key]
    except (ValueError, KeyError, TypeError):
        emit({'t': 'failed', 'attempts': [{'error': 'Bad request: expected the FIX session settings on stdin.'}]})
        return 2
    if '--securities' in argv:
        return run_securities(request)
    return run_session(request) if '--session' in argv else run_stream(request)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
