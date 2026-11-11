#!/usr/bin/env python3
"""Stand-in for `ServerCatalog --stream` in tests: same message shapes as the real reader.
FAKE_SILENT_LOGINS=1002,... : those logins stop sending prices after 3 updates.
FAKE_WRONG_PASSWORD_LOGINS : those logins fail to log in."""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

request = json.loads(sys.stdin.readline())
lock = threading.Lock()


def emit(m):
    with lock:
        sys.stdout.write(json.dumps(m) + '\n')
        sys.stdout.flush()


login = str(request['login'])
if login in os.getenv('FAKE_WRONG_PASSWORD_LOGINS', '').split(','):
    emit({'t': 'failed', 'attempts': [{'host': h['host'], 'port': h['port'], 'ok': False, 'error': 'Invalid account'} for h in request['hosts']]})
    sys.exit(3)
silent = login in os.getenv('FAKE_SILENT_LOGINS', '').split(',')
host = request['hosts'][-1]
now = datetime.now(timezone.utc)
emit({'t': 'connected', 'host': host['host'], 'port': host['port'], 'attempts': [], 'utc_now': now.strftime('%Y-%m-%dT%H:%M:%S'),
      'server_time': (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S'), 'sdk_version': 'fake'})
subs = set(request.get('symbols') or [])
emit({'t': 'subscribed', 'symbols': sorted(subs)})
stop = threading.Event()


def commands():
    for line in sys.stdin:
        cmd = json.loads(line)
        if cmd['cmd'] == 'stop':
            stop.set()
            return
        if cmd['cmd'] == 'subscribe':
            subs.update(cmd['symbols']); emit({'t': 'subscribed', 'symbols': sorted(subs)})
        elif cmd['cmd'] == 'unsubscribe':
            subs.difference_update(cmd['symbols']); emit({'t': 'subscribed', 'symbols': sorted(subs)})
        elif cmd['cmd'] == 'history':
            start = datetime.fromisoformat(cmd['from']); end = datetime.fromisoformat(cmd['to'])
            bars, t = [], start
            while t < end:
                bars.append({'t': t.strftime('%Y-%m-%dT%H:%M:%S'), 'o': 1.1, 'h': 1.2, 'l': 1.0, 'c': 1.15, 'v': 10, 'sp': None})
                t += timedelta(minutes=1)
            emit({'t': 'bars', 'id': cmd['id'], 'symbol': cmd['symbol'], 'timeframe': cmd['timeframe'], 'bars': bars})
    stop.set()


threading.Thread(target=commands, daemon=True).start()
n = 0
while not stop.is_set():
    for s in sorted(subs):
        if silent and n >= 3:
            continue
        n += 1
        rx = datetime.now(timezone.utc)
        bid = (1.1000 if login != '1003' else 1.1200) + (n % 7) * 0.0001
        emit({'t': 'q', 's': s, 'n': n, 'b': round(bid, 5), 'a': round(bid + 0.0002, 5), 'l': 0, 'v': 1,
              'st': (rx + timedelta(hours=2) - timedelta(milliseconds=40)).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3],
              'rx': rx.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'})
    time.sleep(0.02)
emit({'t': 'stopped'})
