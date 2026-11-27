"""Integration administration for the workstation: connections, keys, permissions, usage, webhooks, signals.

Why it exists: administrators create one connection per external application, choose exactly what it may do, issue
keys (shown once), rotate and revoke them, and see what each application did — from the workstation, audited.
Purpose: /api/config/integrations — catalogue (scopes, profiles, events), overview, connection CRUD, keys (issue,
rotate with a grace period, revoke), usage and recent requests, webhooks (add, delete, test, deliveries), signals.
Must never: return a stored key or signing secret (each is shown once, at creation); grant an UNAVAILABLE scope;
offer LIVE; change anything without an audit record in the same transaction.
Dependencies: config_store, operator_auth, integration.auth/catalog/gateway. Consumers: ui Integrations screen.
Architectural position: integration gateway, administration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb

from .. import config_store as store
from ..operator_auth import Principal, require_role
from . import catalog
from .auth import GatewayError, new_key
from .gateway import create_webhook, queue_test, webhook_list

router = APIRouter(prefix='/api/config/integrations', tags=['integrations'])
_admin = require_role('ADMIN')
_viewer = require_role('ADMIN', 'TRADING_OPS', 'RISK_APPROVER')


def _no_store(r: Response) -> None:
    r.headers['Cache-Control'] = 'no-store'


class Rule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    all: bool = False
    ids: list[UUID] = Field(default_factory=list, max_length=2000)


class Access(BaseModel):
    model_config = ConfigDict(extra='forbid')
    accounts: Rule = Field(default_factory=Rule)
    instruments: Rule = Field(default_factory=lambda: Rule(all=True))
    strategies: Rule = Field(default_factory=lambda: Rule(all=True))


class ClientIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{1,39}$')
    name: str = Field(min_length=1, max_length=120)
    client_type: Literal['CORE_AI', 'N8N', 'AI_AGENT', 'PYTHON', 'JAVASCRIPT', 'RESEARCH', 'DASHBOARD', 'CUSTOM'] = 'CUSTOM'
    description: str = Field('', max_length=1000)
    status: Literal['ACTIVE', 'DISABLED'] = 'ACTIVE'
    environment: Literal['PAPER', 'DEMO'] = 'PAPER'
    scopes: list[str] | None = None            # None = the profile's recommended scopes
    access: Access = Field(default_factory=Access)
    requests_per_minute: int = Field(60, ge=1, le=6000)
    max_concurrent_jobs: int = Field(2, ge=1, le=50)
    allowed_networks: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field('Integration connection', min_length=3, max_length=300)

    @field_validator('allowed_networks')
    @classmethod
    def networks(cls, v: list[str]) -> list[str]:
        import ipaddress
        for n in v:
            ipaddress.ip_network(n, strict=False)
        return v


class ClientUpdate(ClientIn):
    code: str | None = None  # fixed after creation
    version: int


def _check_scopes(scopes: list[str]) -> list[str]:
    unknown = [s for s in scopes if s not in catalog.SCOPES]
    if unknown:
        raise HTTPException(422, f"Unknown permission(s): {', '.join(unknown)}.")
    unavailable = [s for s in scopes if s not in catalog.GRANTABLE]
    if unavailable:
        raise HTTPException(422, f"Not available in this release: {', '.join(unavailable)}.")
    return sorted(set(scopes))


def _client_row(db, cid) -> dict:
    row = db.execute('SELECT * FROM int_clients WHERE id=%s', (cid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Integration connection not found.')
    return dict(row)


def _public(db, row: dict) -> dict:
    out = store.clean(row)
    out['keys'] = [store.clean(dict(k)) for k in db.execute(
        """SELECT id, prefix, label, expires_at, revoked_at, revoked_by, last_used_at, last_used_ip, created_by, created_at,
                  CASE WHEN revoked_at IS NOT NULL THEN 'REVOKED' WHEN expires_at IS NOT NULL AND expires_at <= now() THEN 'EXPIRED' ELSE 'ACTIVE' END AS state
           FROM int_keys WHERE client_id=%s ORDER BY created_at DESC""", (row['id'],)).fetchall()]
    u = db.execute("""SELECT count(*) AS requests, count(*) FILTER (WHERE status >= 400) AS errors,
                             count(*) FILTER (WHERE status IN (401, 403)) AS auth_failures, max(at) FILTER (WHERE status < 400) AS last_success,
                             avg(duration_ms)::int AS avg_ms
                      FROM int_usage WHERE client_id=%s AND at > now() - interval '24 hours'""", (row['id'],)).fetchone()
    out['usage_24h'] = store.clean(dict(u))
    out['jobs'] = store.clean(dict(db.execute(
        """SELECT count(*) FILTER (WHERE status IN ('QUEUED','RUNNING')) AS running, count(*) FILTER (WHERE status='FAILED') AS failed,
                  count(*) AS total FROM studio_scan_runs WHERE operator=%s""", (f"api:{row['code']}",)).fetchone()))
    out['signals'] = db.execute('SELECT count(*) AS n FROM int_signals WHERE client_id=%s', (row['id'],)).fetchone()['n']
    out['webhooks'] = db.execute('SELECT count(*) AS n FROM int_webhooks WHERE client_id=%s', (row['id'],)).fetchone()['n']
    return out


@router.get('/catalog')
def catalog_view(response: Response, _: Principal = Depends(_viewer)):
    _no_store(response)
    return {'scopes': [{'scope': s, 'group': g, 'label': label, 'status': st, 'note': note} for s, (g, label, st, note) in catalog.SCOPES.items()],
            'profiles': catalog.PROFILES, 'events': catalog.EVENTS}


@router.get('')
def list_clients(response: Response, _: Principal = Depends(_viewer)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute('SELECT * FROM int_clients ORDER BY name').fetchall()
        items = [_public(db, dict(r)) for r in rows]
        totals = db.execute("""SELECT count(*) AS requests_24h, count(*) FILTER (WHERE status IN (401,403)) AS auth_failures_24h,
                                      avg(duration_ms)::int AS avg_ms, max(at) FILTER (WHERE status < 400) AS last_success
                               FROM int_usage WHERE at > now() - interval '24 hours'""").fetchone()
    return {'items': items, 'overview': {'connections': len(items), 'active': sum(1 for i in items if i['status'] == 'ACTIVE'),
                                         'disabled': sum(1 for i in items if i['status'] != 'ACTIVE'),
                                         'running_jobs': sum(i['jobs']['running'] or 0 for i in items),
                                         'failed_jobs': sum(i['jobs']['failed'] or 0 for i in items), **store.clean(dict(totals))}}


@router.post('', status_code=201)
def create_client(body: ClientIn, user: Principal = Depends(_admin)):
    scopes = _check_scopes(body.scopes if body.scopes is not None else catalog.PROFILES[body.client_type]['scopes'])
    with store.transaction('write') as db:
        row = db.execute("""INSERT INTO int_clients(id, code, name, client_type, description, status, environment, scopes, access, requests_per_minute,
                                                    max_concurrent_jobs, allowed_networks, created_by)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                         (uuid4(), body.code, body.name, body.client_type, body.description, body.status, body.environment, scopes,
                          Jsonb(body.access.model_dump(mode='json')), body.requests_per_minute, body.max_concurrent_jobs, body.allowed_networks,
                          user.username)).fetchone()
        store.audit(db, user.username, 'CREATE_INTEGRATION', 'int_client', row['id'], None, dict(row), body.reason)
        return _public(db, dict(row))


@router.put('/{client_id}')
def update_client(client_id: UUID, body: ClientUpdate, user: Principal = Depends(_admin)):
    scopes = _check_scopes(body.scopes if body.scopes is not None else catalog.PROFILES[body.client_type]['scopes'])
    with store.transaction('write') as db:
        before = _client_row(db, client_id)
        if before['version'] != body.version:
            raise HTTPException(409, 'Someone else changed this connection. Reload and try again.')
        row = db.execute("""UPDATE int_clients SET name=%s, client_type=%s, description=%s, status=%s, environment=%s, scopes=%s, access=%s,
                                requests_per_minute=%s, max_concurrent_jobs=%s, allowed_networks=%s, updated_at=now(), version=version+1
                            WHERE id=%s RETURNING *""",
                         (body.name, body.client_type, body.description, body.status, body.environment, scopes, Jsonb(body.access.model_dump(mode='json')),
                          body.requests_per_minute, body.max_concurrent_jobs, body.allowed_networks, client_id)).fetchone()
        store.audit(db, user.username, 'UPDATE_INTEGRATION', 'int_client', client_id, before, dict(row), body.reason)
        return _public(db, dict(row))


@router.delete('/{client_id}')
def delete_client(client_id: UUID, user: Principal = Depends(_admin)):
    with store.transaction('write') as db:
        before = _client_row(db, client_id)
        db.execute('DELETE FROM int_clients WHERE id=%s', (client_id,))
        store.audit(db, user.username, 'DELETE_INTEGRATION', 'int_client', client_id, before, None, 'Integration connection deleted')
    return {'deleted': True}


class KeyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field('', max_length=80)
    expires_days: int | None = Field(90, ge=1, le=3650)   # None = never expires


def _issue(db, client_id, actor: str, body: KeyIn) -> dict:
    key, prefix, digest = new_key()
    expires = datetime.now(timezone.utc) + timedelta(days=body.expires_days) if body.expires_days else None
    kid = uuid4()
    db.execute('INSERT INTO int_keys(id, client_id, prefix, key_hash, label, expires_at, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)',
               (kid, client_id, prefix, digest, body.label, expires, actor))
    store.audit(db, actor, 'ISSUE_INTEGRATION_KEY', 'int_key', kid, None, {'client_id': str(client_id), 'prefix': prefix, 'expires_at': expires},
                body.label or 'Integration key')
    return {'id': str(kid), 'prefix': prefix, 'expires_at': expires, 'key': key,
            'note': 'Copy this key now. COREI stores only a hash and cannot show it again.'}


@router.post('/{client_id}/keys', status_code=201)
def issue_key(client_id: UUID, body: KeyIn, user: Principal = Depends(_admin)):
    with store.transaction('write') as db:
        _client_row(db, client_id)
        return _issue(db, client_id, user.username, body)


class RotateIn(KeyIn):
    old_key_id: UUID
    grace_hours: int = Field(24, ge=0, le=720)


@router.post('/{client_id}/keys/rotate', status_code=201)
def rotate_key(client_id: UUID, body: RotateIn, user: Principal = Depends(_admin)):
    """Issue a new key; the old one keeps working for `grace_hours` (0 = revoke now) so workflows can switch over."""
    with store.transaction('write') as db:
        old = db.execute('SELECT * FROM int_keys WHERE id=%s AND client_id=%s AND revoked_at IS NULL', (body.old_key_id, client_id)).fetchone()
        if not old:
            raise HTTPException(404, 'Active key not found for this connection.')
        new = _issue(db, client_id, user.username, KeyIn(label=body.label or f"rotated from {old['prefix']}", expires_days=body.expires_days))
        if body.grace_hours == 0:
            db.execute('UPDATE int_keys SET revoked_at=now(), revoked_by=%s WHERE id=%s', (user.username, body.old_key_id))
        else:
            db.execute("UPDATE int_keys SET expires_at=LEAST(coalesce(expires_at, 'infinity'), now() + make_interval(hours => %s)) WHERE id=%s",
                       (body.grace_hours, body.old_key_id))
        store.audit(db, user.username, 'ROTATE_INTEGRATION_KEY', 'int_key', body.old_key_id, {'prefix': old['prefix']},
                    {'new_prefix': new['prefix'], 'grace_hours': body.grace_hours}, 'Key rotation')
        return {**new, 'old_key': {'prefix': old['prefix'], 'valid_for_hours': body.grace_hours}}


@router.post('/keys/{key_id}/revoke')
def revoke_key(key_id: UUID, user: Principal = Depends(_admin)):
    with store.transaction('write') as db:
        row = db.execute('UPDATE int_keys SET revoked_at=now(), revoked_by=%s WHERE id=%s AND revoked_at IS NULL RETURNING id, prefix, client_id',
                         (user.username, key_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Active key not found.')
        store.audit(db, user.username, 'REVOKE_INTEGRATION_KEY', 'int_key', key_id, {'prefix': row['prefix']}, {'revoked': True}, 'Key revoked')
    return {'revoked': True, 'prefix': row['prefix']}


@router.get('/{client_id}/usage')
def client_usage(client_id: UUID, response: Response, _: Principal = Depends(_viewer), limit: int = 100):
    _no_store(response)
    with store.transaction('read') as db:
        _client_row(db, client_id)
        rows = db.execute('SELECT at, key_prefix, method, path, status, error_code, duration_ms, response_bytes, request_id, ip FROM int_usage '
                          'WHERE client_id=%s ORDER BY at DESC LIMIT %s', (client_id, min(max(limit, 1), 500))).fetchall()
        by = db.execute("""SELECT method, path, count(*) AS n, count(*) FILTER (WHERE status >= 400) AS errors, avg(duration_ms)::int AS avg_ms
                           FROM int_usage WHERE client_id=%s AND at > now() - interval '24 hours' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30""", (client_id,)).fetchall()
    return {'recent': [store.clean(dict(r)) for r in rows], 'by_endpoint_24h': [dict(r) for r in by]}


@router.get('/{client_id}/signals')
def client_signals(client_id: UUID, response: Response, _: Principal = Depends(_viewer)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute('SELECT s.*, i.symbol FROM int_signals s JOIN cfg_instruments i ON i.id=s.instrument_id WHERE s.client_id=%s '
                          'ORDER BY s.created_at DESC LIMIT 200', (client_id,)).fetchall()
    return {'items': [store.clean(dict(r)) for r in rows]}


@router.get('/{client_id}/webhooks')
def client_webhooks(client_id: UUID, response: Response, _: Principal = Depends(_viewer)):
    _no_store(response)
    with store.transaction('read') as db:
        hooks = webhook_list(db, client_id)
        recent = db.execute("""SELECT d.id, d.webhook_id, d.event, d.status, d.attempts, d.last_status, d.last_error, d.created_at, d.sent_at
                               FROM int_webhook_deliveries d JOIN int_webhooks w ON w.id=d.webhook_id WHERE w.client_id=%s ORDER BY d.id DESC LIMIT 50""",
                            (client_id,)).fetchall()
    return {'items': hooks, 'deliveries': [store.clean(dict(r)) for r in recent]}


class HookIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(max_length=500)
    events: list[str] = Field(min_length=1, max_length=30)


def _http(fn):
    try:
        return fn()
    except GatewayError as exc:
        raise HTTPException(exc.status, exc.message) from None


@router.post('/{client_id}/webhooks', status_code=201)
def add_hook(client_id: UUID, body: HookIn, user: Principal = Depends(_admin)):
    with store.transaction('write') as db:
        _client_row(db, client_id)
        return _http(lambda: create_webhook(db, client_id, user.username, body.url, body.events))


@router.delete('/webhooks/{webhook_id}')
def delete_hook(webhook_id: UUID, user: Principal = Depends(_admin)):
    with store.transaction('write') as db:
        row = db.execute('DELETE FROM int_webhooks WHERE id=%s RETURNING url', (webhook_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Webhook not found.')
        store.audit(db, user.username, 'DELETE_WEBHOOK', 'int_webhook', webhook_id, {'url': row['url']}, None, 'Integration webhook')
    return {'deleted': True}


@router.post('/webhooks/{webhook_id}/test', status_code=202)
def test_hook(webhook_id: UUID, user: Principal = Depends(_admin)):
    with store.transaction('write') as db:
        if not db.execute('SELECT 1 FROM int_webhooks WHERE id=%s', (webhook_id,)).fetchone():
            raise HTTPException(404, 'Webhook not found.')
        queue_test(db, webhook_id, user.username)
    return {'queued': True}
