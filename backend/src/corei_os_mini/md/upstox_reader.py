"""Upstox market-data reader: the same line protocol as the MT5 reader (--stream), so the
collector treats both alike.

stdin, first line: {"token": "...", "api_base": "https://api.upstox.com", "symbols": [instrument keys],
                    "heartbeat_seconds": 5, "poll_ms": 1000}
then commands: {"cmd": "subscribe"|"unsubscribe", "symbols": [...]}, {"cmd": "history", "id", "symbol",
               "timeframe", "from", "to"}, {"cmd": "stop"}.
stdout, one JSON object per line: connected, q (quote), subscribed, hb, bars, history_error, failed, stopped.

Quotes come from GET /v2/market-quote/quotes (best bid/ask from depth, last price, volume), polled once a
second for up to 500 instrument keys per call (Upstox allows 50 requests/s). A quote is emitted only when
it changed. Bars come from /v3/historical-candle (and its intraday form for today). Times are UTC; the
token arrives on stdin only and is never printed. Run: python -m corei_os_mini.md.upstox_reader
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
FRAMES = {'M1': ('minutes', 1), 'M5': ('minutes', 5), 'M15': ('minutes', 15), 'M30': ('minutes', 30),
          'H1': ('hours', 1), 'H4': ('hours', 4), 'D1': ('days', 1)}
out_lock = threading.Lock()


def emit(message: dict) -> None:
    with out_lock:
        sys.stdout.write(json.dumps(message, separators=(',', ':')) + '\n')
        sys.stdout.flush()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Refused(Exception):
    """The broker answered with an error that retrying now cannot fix (expired login, bad key)."""


class Client:
    def __init__(self, api_base: str, token: str):
        self.base = api_base.rstrip('/').removesuffix('/v2')
        self._auth = 'Bearer ' + token

    def get(self, path: str, params: dict | None = None) -> dict:
        url = self.base + path + ('?' + urllib.parse.urlencode(params, safe='|,') if params else '')
        request = urllib.request.Request(url, headers={'Accept': 'application/json', 'Authorization': self._auth})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - https base from the connection
                return json.loads(response.read(20_000_000))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read(100_000))
                detail = '; '.join(f"{e.get('errorCode', '')} {e.get('message', '')}".strip() for e in body.get('errors') or [])
            except (ValueError, AttributeError):
                detail = ''
            if exc.code in (401, 403):
                raise Refused(f'Upstox refused the session ({exc.code}{": " + detail if detail else ""}). '
                              'The daily login expired or was revoked: log in at the broker again.') from None
            raise OSError(f'Upstox HTTP {exc.code}{": " + detail if detail else ""}') from None


def parse_quote(item: dict) -> dict | None:
    """One entry of /v2/market-quote/quotes → bid, ask, last, volume, source time (UTC)."""
    depth = item.get('depth') or {}
    buy, sell = (depth.get('buy') or [{}])[0], (depth.get('sell') or [{}])[0]
    bid, ask = buy.get('price') or None, sell.get('price') or None
    last = item.get('last_price')
    if bid is None and ask is None and last is None:
        return None
    source = None
    ltt = item.get('last_trade_time')
    if ltt and str(ltt).isdigit():
        source = datetime.fromtimestamp(int(ltt) / 1000, UTC)
    elif item.get('timestamp'):
        try:
            source = datetime.fromisoformat(str(item['timestamp'])).astimezone(UTC)
        except ValueError:
            source = None
    return {'b': bid, 'a': ask, 'l': last, 'v': item.get('volume'), 'st': source.isoformat() if source else None}


class Reader:
    def __init__(self, request: dict):
        self.client = Client(request.get('api_base') or 'https://api.upstox.com', request['token'])
        self.symbols: set[str] = set(request.get('symbols') or [])
        self.heartbeat = float(request.get('heartbeat_seconds') or 5)
        self.poll = max(0.5, float(request.get('poll_ms') or 1000) / 1000)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.last: dict[str, tuple] = {}
        self.seq = 0

    # ----- commands -----
    def commands(self) -> None:
        for raw in sys.stdin:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            cmd = m.get('cmd')
            if cmd == 'stop':
                self.stop.set()
                return
            if cmd in ('subscribe', 'unsubscribe'):
                with self.lock:
                    (self.symbols.update if cmd == 'subscribe' else self.symbols.difference_update)(m.get('symbols') or [])
                    emit({'t': 'subscribed', 'symbols': sorted(self.symbols)})
            elif cmd == 'history':
                threading.Thread(target=self.history, args=(m,), daemon=True).start()
        self.stop.set()

    def history(self, m: dict) -> None:
        try:
            unit, interval = FRAMES[m['timeframe']]
            start = datetime.fromisoformat(m['from']).replace(tzinfo=UTC)
            end = datetime.fromisoformat(m['to']).replace(tzinfo=UTC)
            key = urllib.parse.quote(m['symbol'], safe='')
            candles = []
            if start.date() < date.today():
                to_day = min(end.date(), date.today() - timedelta(days=1))
                data = self.client.get(f'/v3/historical-candle/{key}/{unit}/{interval}/{to_day.isoformat()}/{start.date().isoformat()}')
                candles += (data.get('data') or {}).get('candles') or []
            if end.date() >= date.today() and unit != 'days':
                data = self.client.get(f'/v3/historical-candle/intraday/{key}/{unit}/{interval}')
                candles += (data.get('data') or {}).get('candles') or []
            bars = []
            for c in candles:
                t = datetime.fromisoformat(c[0]).astimezone(UTC)
                if start <= t < end:
                    bars.append({'t': t.isoformat(), 'o': c[1], 'h': c[2], 'l': c[3], 'c': c[4], 'v': c[5] if len(c) > 5 else None})
            bars.sort(key=lambda b: b['t'])
            emit({'t': 'bars', 'id': m.get('id'), 'bars': bars})
        except (KeyError, ValueError, OSError, Refused) as exc:
            emit({'t': 'history_error', 'id': m.get('id'), 'msg': f'{type(exc).__name__}: {exc}'[:300]})

    # ----- quotes -----
    def fetch(self, keys: list[str]) -> dict:
        found: dict[str, dict] = {}
        for i in range(0, len(keys), 500):
            data = self.client.get('/v2/market-quote/quotes', {'instrument_key': ','.join(keys[i:i + 500])}).get('data') or {}
            for item in data.values():
                key = item.get('instrument_token') or ''
                q = parse_quote(item)
                if key and q:
                    found[key.replace(':', '|', 1) if '|' not in key else key] = q
        return found

    def run(self) -> int:
        try:
            with self.lock:
                keys = sorted(self.symbols)
            self.fetch(keys[:1]) if keys else self.client.get('/v2/user/profile')
        except Refused as exc:
            emit({'t': 'failed', 'attempts': [{'host': 'api.upstox.com', 'port': 443, 'ok': False, 'error': str(exc), 'refused': True}]})
            return 3
        except OSError as exc:
            emit({'t': 'failed', 'attempts': [{'host': 'api.upstox.com', 'port': 443, 'ok': False, 'error': f'TimeoutException: {exc}'}]})
            return 3
        utc = now_iso()
        emit({'t': 'connected', 'host': 'api.upstox.com', 'port': 443, 'server_time': utc, 'utc_now': utc, 'client_build': None})
        emit({'t': 'subscribed', 'symbols': sorted(self.symbols)})
        threading.Thread(target=self.commands, daemon=True).start()
        beat = time.monotonic()
        errors = 0
        while not self.stop.is_set():
            started = time.monotonic()
            with self.lock:
                keys = sorted(self.symbols)
            if keys:
                try:
                    quotes = self.fetch(keys)
                    errors = 0
                except Refused as exc:
                    emit({'t': 'error', 'msg': str(exc)})
                    emit({'t': 'disconnected'})
                    return 4
                except OSError as exc:
                    errors += 1
                    emit({'t': 'error', 'msg': str(exc)[:300]})
                    if errors >= 10:
                        emit({'t': 'disconnected'})
                        return 4
                    self.stop.wait(min(30, 2 ** errors))
                    continue
                rx = now_iso()
                for key, q in quotes.items():
                    sig = (q['b'], q['a'], q['l'], q['v'])
                    if self.last.get(key) == sig:
                        continue
                    self.last[key] = sig
                    self.seq += 1
                    emit({'t': 'q', 's': key, **q, 'rx': rx, 'n': self.seq})
            if time.monotonic() - beat >= self.heartbeat:
                beat = time.monotonic()
                emit({'t': 'hb', 'utc_now': now_iso()})
            self.stop.wait(max(0.0, self.poll - (time.monotonic() - started)))
        emit({'t': 'stopped'})
        return 0


def main() -> int:
    first = sys.stdin.readline()
    try:
        request = json.loads(first)
        request['token']
    except (ValueError, KeyError, TypeError):
        emit({'t': 'failed', 'attempts': [{'error': 'Bad request: expected the session token on stdin.'}]})
        return 2
    reader = Reader(request)
    request.clear()
    return reader.run()


if __name__ == '__main__':
    sys.exit(main())
