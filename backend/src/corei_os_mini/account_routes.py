"""Routes (DEC-69): the ordered ways COREI reaches one MT5 account, with automatic failover.

An account without routes uses its server's method, as before. An account with enabled routes uses them in priority
order (lowest number first): the direct SDK, the official terminal through the COREI bridge (a Windows VPS, a Windows
PC — directly or through an SSH tunnel — or the Docker container) or the COREI EA pushing from a terminal. The
collector moves to the next route when the active one fails (ROUTE_FALLBACK alert, FEED_SWITCH event) and tries the
first route again at the next reconnect once FAILBACK_SECONDS have passed.

Each bridge and push route has its own token: written once, stored encrypted, never shown again, every use logged.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from . import config_store as store
from . import credentials

METHODS = ('DIRECT', 'TERMINAL_BRIDGE', 'TERMINAL_PUSH')
LOCATIONS = ('WINDOWS_VPS', 'WINDOWS_PC', 'DOCKER', 'COREI_SERVER', 'OTHER')
SWITCH_AFTER_FAILURES = 2      # failed sessions on one route before moving to the next
FAILBACK_SECONDS = 300         # time on a lower route before a reconnect tries the first route again
FIELDS = ('label', 'method', 'location', 'host', 'port', 'fingerprint', 'priority', 'enabled', 'notes')
_FP = re.compile(r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')


class RouteIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    method: Literal['DIRECT', 'TERMINAL_BRIDGE', 'TERMINAL_PUSH']
    location: Literal['WINDOWS_VPS', 'WINDOWS_PC', 'DOCKER', 'COREI_SERVER', 'OTHER'] = 'OTHER'
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    fingerprint: str | None = None
    priority: int = Field(default=10, ge=0, le=1000)
    enabled: bool = True
    notes: str = Field(default='', max_length=500)

    @field_validator('fingerprint')
    @classmethod
    def _fingerprint(cls, v: str | None) -> str | None:
        if v in (None, ''):
            return None
        v = v.strip().upper().replace(' ', '')
        if ':' not in v and len(v) == 64:
            v = ':'.join(v[i:i + 2] for i in range(0, 64, 2))
        if not _FP.match(v):
            raise ValueError('The certificate fingerprint is the SHA-256 the bridge prints: 32 pairs like AB:CD:… .')
        return v

    @model_validator(mode='after')
    def _bridge_needs_address(self) -> 'RouteIn':
        if self.method == 'TERMINAL_BRIDGE':
            missing = [n for n in ('host', 'port', 'fingerprint') if not getattr(self, n)]
            if missing:
                raise ValueError(f"A terminal bridge route needs: {', '.join(missing)}.")
        else:
            self.host = self.port = self.fingerprint = None
        return self


def _public(row: dict) -> dict:
    out = {k: v for k, v in store.clean(dict(row)).items() if k not in ('token', 'token_key_id')}
    out['token_saved'] = row.get('token') is not None
    out['needs_token'] = row['method'] in ('TERMINAL_BRIDGE', 'TERMINAL_PUSH')
    return out


def _account(db, account_id: UUID) -> dict:
    row = db.execute('SELECT a.id, a.code, c.platform, c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                     'WHERE a.id=%s', (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Account not found.')
    if row['platform'] != 'MT5':
        raise HTTPException(409, 'Routes are for MT5 accounts (direct SDK, terminal bridge, terminal push).')
    return row


def list_routes(account_id: UUID) -> list[dict]:
    with store.transaction('read') as db:
        _account(db, account_id)
        return [_public(r) for r in db.execute('SELECT * FROM cfg_account_routes WHERE account_id=%s ORDER BY priority, created_at',
                                               (account_id,)).fetchall()]


def create(actor: str, account_id: UUID, data: RouteIn) -> dict:
    with store.transaction('write') as db:
        account = _account(db, account_id)
        route_id = uuid4()
        values = data.model_dump()
        db.execute('INSERT INTO cfg_account_routes(id, account_id, label, method, location, host, port, fingerprint, priority, enabled, notes, '
                   'created_by, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (route_id, account_id, *[values[k] for k in FIELDS], actor, actor))
        store.audit(db, actor, 'CREATE_ROUTE', 'account', account_id, None, {'route': str(route_id), 'account': account['code'], **values})
        return _public(db.execute('SELECT * FROM cfg_account_routes WHERE id=%s', (route_id,)).fetchone())


def _route(db, account_id: UUID, route_id: UUID, version: int | None = None) -> dict:
    row = db.execute('SELECT * FROM cfg_account_routes WHERE id=%s AND account_id=%s FOR UPDATE', (route_id, account_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Route not found.')
    if version is not None and row['version'] != version:
        raise HTTPException(409, 'This route was changed by someone else: reload and try again.')
    return row


def update(actor: str, account_id: UUID, route_id: UUID, data: RouteIn, version: int) -> dict:
    with store.transaction('write') as db:
        before = _route(db, account_id, route_id, version)
        values = data.model_dump()
        db.execute('UPDATE cfg_account_routes SET label=%s, method=%s, location=%s, host=%s, port=%s, fingerprint=%s, priority=%s, enabled=%s, '
                   'notes=%s, version=version+1, updated_by=%s, updated_at=now() WHERE id=%s',
                   (*[values[k] for k in FIELDS], actor, route_id))
        if before['method'] != values['method']:
            db.execute('UPDATE cfg_account_routes SET token=NULL, token_key_id=NULL, token_set_at=NULL WHERE id=%s', (route_id,))
        store.audit(db, actor, 'UPDATE_ROUTE', 'account', account_id, {k: store.clean(dict(before)).get(k) for k in FIELDS}, values)
        return _public(db.execute('SELECT * FROM cfg_account_routes WHERE id=%s', (route_id,)).fetchone())


def delete(actor: str, account_id: UUID, route_id: UUID) -> None:
    with store.transaction('delete') as db:
        before = _route(db, account_id, route_id)
        db.execute('DELETE FROM cfg_account_routes WHERE id=%s', (route_id,))
        store.audit(db, actor, 'DELETE_ROUTE', 'account', account_id, {k: store.clean(dict(before)).get(k) for k in FIELDS}, None)


def set_token(actor: str, account_id: UUID, route_id: UUID, token: str) -> None:
    if not 16 <= len(token) <= 400:
        raise HTTPException(422, 'The token is the text in the bridge\'s corei-bridge.token file (or one COREI issued).')
    with store.transaction('write') as db:
        row = _route(db, account_id, route_id)
        if row['method'] == 'DIRECT':
            raise HTTPException(422, 'A direct SDK route uses the account\'s own login; it has no token.')
        db.execute('UPDATE cfg_account_routes SET token=%s, token_key_id=%s, token_set_at=now(), updated_by=%s, updated_at=now() WHERE id=%s',
                   (credentials.encrypt(route_id, {'token': token}), credentials.key_id(), actor, route_id))
        store.audit(db, actor, 'REPLACE_CREDENTIAL' if row['token'] else 'SET_CREDENTIAL', 'account', account_id, None,
                    {'kind': 'ROUTE_TOKEN', 'route': row['label']})


def use_token(route: dict, used_by: str, purpose: str) -> dict[str, str]:
    """A route's token for in-process use; every use is logged like any credential's."""
    with store.transaction('read') as db:
        row = db.execute('SELECT id, account_id, token FROM cfg_account_routes WHERE id=%s', (route['id'],)).fetchone()
        if not row or row['token'] is None:
            raise HTTPException(404, f"No token is saved on route {route.get('label')}.")
        db.execute("INSERT INTO cfg_credential_use(credential_id, owner, owner_id, used_by, purpose) VALUES (%s,'account',%s,%s,%s)",
                   (row['id'], row['account_id'], used_by[:120], purpose[:200]))
    return credentials.decrypt(row['id'], bytes(row['token']))


# ----- the collector's side -----

def enabled_routes(account_id: Any) -> list[dict]:
    """The account's enabled routes in order (empty: use the server's method)."""
    try:
        with store.transaction('read') as db:
            return [dict(r) for r in db.execute('SELECT id, label, method, location, host, port, fingerprint, priority, token IS NOT NULL AS has_token '
                                                'FROM cfg_account_routes WHERE account_id=%s AND enabled ORDER BY priority, created_at',
                                                (str(account_id),)).fetchall()]
    except HTTPException:
        return []  # schema not migrated yet: behave as before


def apply(account: dict, route: dict) -> dict:
    """The account as the readers see it through one route: adapter and bridge come from the route."""
    out = dict(account)
    out['route'] = route
    out['adapter'] = {'DIRECT': 'MTAPI_DIRECT'}.get(route['method'], route['method'])
    settings = dict(out.get('connection_settings') or {})
    settings['bridge'] = ({'host': route['host'], 'port': route['port'], 'fingerprint': route['fingerprint']}
                          if route['method'] == 'TERMINAL_BRIDGE' else None)
    out['connection_settings'] = settings
    return out


def bridge_token(account: dict, used_by: str, purpose: str) -> dict[str, str]:
    """The token for the account's bridge: the route's when it goes through a route, else the server's."""
    if account.get('route'):
        return use_token(account['route'], used_by, purpose)
    return store.use_credential('connections', UUID(str(account['connection_id'])), used_by, purpose)


def record(route_id: Any, ok: bool, error: str | None = None) -> None:
    try:
        with store.transaction('write') as db:
            if ok:
                db.execute('UPDATE cfg_account_routes SET last_ok_at=now() WHERE id=%s', (str(route_id),))
            else:
                db.execute('UPDATE cfg_account_routes SET last_error=%s, last_error_at=now() WHERE id=%s', ((error or 'failed')[:300], str(route_id)))
    except HTTPException:
        pass


def mark_active(route_id: Any) -> None:
    """The route the collector is using now (shown on Setup); the account's other routes are not active."""
    try:
        with store.transaction('write') as db:
            db.execute('UPDATE cfg_account_routes r SET active_since=CASE WHEN r.id=%s THEN now() ELSE NULL END '
                       'WHERE r.account_id=(SELECT account_id FROM cfg_account_routes WHERE id=%s)', (str(route_id), str(route_id)))
    except HTTPException:
        pass


def fallback_alert(account_code: str, old: dict, new: dict, error: str | None) -> None:
    try:
        from . import alerts
        with store.transaction('write') as db:
            alerts.enqueue(db, 'ROUTE_FALLBACK', 'WARNING', f'{account_code}: switched to route {new["label"]}',
                           f'Route {old["label"]} ({old["method"]}) failed: {(error or "no answer")[:300]}. Now trying {new["label"]} ({new["method"]}).')
    except Exception:  # noqa: BLE001 - an alert must never stop collection
        pass
