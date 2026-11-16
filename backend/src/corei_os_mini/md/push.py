"""MT5 terminal push (DEC-68): the COREI EA running inside an MT5 terminal sends what the terminal sees — quotes, bars,
its own login facts and the broker's symbol list — to COREI over HTTPS, and picks up commands (subscribe, history).

Why: a terminal always speaks its own broker's protocol, so no SDK version can fall behind; the terminal makes only
outgoing HTTPS calls (no inbound port on the Windows PC, VPS or container); COREI needs no MT5 password (the terminal
is logged in where it runs). Authentication: the server's push token (Bearer), the account code, and the terminal's
login must match the account.

Flow: POST /api/md/push  →  lines go to md_push_inbox  →  the collector's push session reads them with the same code
as every other source  →  commands from the collector wait in md_push_outbox and go back in the response.
The response is plain text, one command per line, so the EA (MQL5) parses it without a JSON library:
    subscribe EURUSD,XAUUSD
    unsubscribe GBPUSD
    history <job id> <symbol> <timeframe> <from server time> <to server time>
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from .. import config_store as cfg

LINE_KINDS = {'connected', 'q', 'bars', 'hb', 'subscribed', 'history_error', 'error', 'disconnected', 'catalog'}
MAX_LINES = 20000
_tokens: dict[str, tuple[float, str, dict]] = {}  # account code -> (fetched at, sha256(token), account) for 60 s
TOKEN_CACHE_SECONDS = 60


def _account(code: str) -> dict:
    with cfg.transaction('read') as db:
        row = db.execute(
            """SELECT a.id, a.code, a.external_account_id, a.status, a.connection_id, c.adapter, c.platform,
                      (SELECT r.id FROM cfg_account_routes r WHERE r.account_id=a.id AND r.method='TERMINAL_PUSH' AND r.enabled
                         AND r.token IS NOT NULL ORDER BY r.priority LIMIT 1) AS push_route_id
               FROM cfg_accounts a JOIN cfg_connections c ON c.id = a.connection_id WHERE a.code = %s""", (code,)).fetchone()
    if not row or row['status'] == 'ARCHIVED':
        raise HTTPException(401, 'Unknown account or token.')
    if row['platform'] != 'MT5' or (row['adapter'] != 'TERMINAL_PUSH' and not row['push_route_id']):
        raise HTTPException(409, f"{code} is not set to receive from a terminal: add a Terminal push route to the account "
                                 "(or set its server's connection method to MT5 terminal push).")
    return row


def authenticate(code: str, token: str) -> dict:
    now = time.monotonic()
    cached = _tokens.get(code)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if cached and now - cached[0] < TOKEN_CACHE_SECONDS:
        if hmac.compare_digest(cached[1], digest):
            return cached[2]
        raise HTTPException(401, 'Unknown account or token.')
    account = _account(code)
    try:
        if account.get('push_route_id'):
            from .. import account_routes
            saved = account_routes.use_token({'id': account['push_route_id'], 'label': 'push'}, 'terminal-push', 'Check the MT5 terminal push token')
        else:
            saved = cfg.use_credential('connections', account['connection_id'], 'terminal-push', 'Check the MT5 terminal push token')
    except HTTPException:
        raise HTTPException(401, 'No push token is saved for this account yet (Setup › the account › Routes › Terminal push › New token).') from None
    expected = hashlib.sha256(saved.get('token', '').encode()).hexdigest()
    saved.clear()
    _tokens[code] = (now, expected, account)
    if not hmac.compare_digest(expected, digest):
        raise HTTPException(401, 'Unknown account or token.')
    return account


def forget(code: str | None = None) -> None:
    """After a new token is issued: check against the database again."""
    if code is None:
        _tokens.clear()
    else:
        _tokens.pop(code, None)


def receive(code: str, token: str, lines: list[Any], remote_ip: str | None) -> str:
    account = authenticate(code, token)
    if len(lines) > MAX_LINES:
        raise HTTPException(413, f'At most {MAX_LINES} lines per push.')
    keep: list[dict] = []
    catalog: list[dict] = []
    facts: dict = {}
    for line in lines:
        if not isinstance(line, dict) or line.get('t') not in LINE_KINDS:
            continue
        if line['t'] == 'connected':
            if str(line.get('login') or '') != str(account['external_account_id']):
                raise HTTPException(409, f"The terminal is logged in to {line.get('login')}, but {code} is login {account['external_account_id']}. "
                                         'Log the terminal in to that account, or set the EA to the matching account code.')
            facts = line
        if line['t'] == 'catalog':
            catalog.extend(s for s in (line.get('symbols') or []) if isinstance(s, dict) and s.get('Name'))
            continue
        keep.append(line)
    with cfg.transaction('write') as db:
        if keep:
            with db.cursor() as cur:
                cur.executemany('INSERT INTO md_push_inbox(account_id, line) VALUES (%s, %s)', [(account['id'], json.dumps(x)) for x in keep])
        # A standby EA (its route is not the one in use) keeps pushing: lines nobody reads for 2 minutes are dropped,
        # so the inbox stays small; when the collector switches to this route it starts from current prices.
        db.execute("DELETE FROM md_push_inbox WHERE account_id=%s AND received_at < now() - interval '2 minutes'", (account['id'],))
        db.execute(
            """INSERT INTO md_push_terminals(account_id, login, server, company, build, ea_version, remote_ip, last_push_at, pushes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,now(),1)
               ON CONFLICT (account_id) DO UPDATE SET last_push_at=now(), pushes=md_push_terminals.pushes+1, remote_ip=EXCLUDED.remote_ip,
                 login=COALESCE(EXCLUDED.login, md_push_terminals.login), server=COALESCE(EXCLUDED.server, md_push_terminals.server),
                 company=COALESCE(EXCLUDED.company, md_push_terminals.company), build=COALESCE(EXCLUDED.build, md_push_terminals.build),
                 ea_version=COALESCE(EXCLUDED.ea_version, md_push_terminals.ea_version)""",
            (account['id'], facts.get('login'), facts.get('host'), facts.get('company'), facts.get('client_build'), facts.get('ea_version'), remote_ip))
        commands = [r['command'] for r in db.execute(
            'DELETE FROM md_push_outbox WHERE id IN (SELECT id FROM md_push_outbox WHERE account_id=%s ORDER BY id LIMIT 200) RETURNING id, command',
            (account['id'],)).fetchall()]
    if catalog:
        from . import catalog as cat
        cat.store_mt5(account['id'], {'symbol_names': [s['Name'] for s in catalog], 'symbols': {s['Name']: s for s in catalog}})
    return '\n'.join(commands) + ('\n' if commands else '')


# ----- the collector's side -----

def take(account_id: UUID | str, limit: int = 5000) -> list[bytes]:
    with cfg.transaction('write') as db:
        rows = db.execute('DELETE FROM md_push_inbox WHERE id IN (SELECT id FROM md_push_inbox WHERE account_id=%s ORDER BY id LIMIT %s) '
                          'RETURNING id, line, received_at', (str(account_id), limit)).fetchall()
    out = []
    for r in sorted(rows, key=lambda r: r['id']):
        line = r['line']
        if isinstance(line, dict) and line.get('t') == 'q':
            # Receipt = when COREI received the batch (its own clock, milliseconds), not the terminal PC's clock.
            line = {**line, 'rx': r['received_at'].astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='milliseconds')}
        out.append(json.dumps(line).encode())
    return out


def send(account_id: UUID | str, message: dict) -> None:
    """A collector command for the EA, as one text line."""
    cmd = message.get('cmd')
    if cmd in ('subscribe', 'unsubscribe'):
        line = f"{cmd} {','.join(message.get('symbols') or [])}"
    elif cmd == 'history':
        line = f"history {message['id']} {message['symbol']} {message['timeframe']} {message['from']} {message['to']}"
    else:
        return
    with cfg.transaction('write') as db:
        db.execute('INSERT INTO md_push_outbox(account_id, command) VALUES (%s, %s)', (str(account_id), line))


def reset(account_id: UUID | str) -> None:
    """A new push session: drop stale commands and old lines (the EA re-sends its state)."""
    with cfg.transaction('write') as db:
        db.execute('DELETE FROM md_push_outbox WHERE account_id=%s', (str(account_id),))
        # Ask the EA to state its login again (the collector may have restarted while the EA kept running).
        db.execute("INSERT INTO md_push_outbox(account_id, command) VALUES (%s, 'hello')", (str(account_id),))


def last_push(account_id: UUID | str) -> dict | None:
    with cfg.transaction('read') as db:
        return db.execute('SELECT * FROM md_push_terminals WHERE account_id=%s', (str(account_id),)).fetchone()
