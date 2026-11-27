"""Gateway authentication and authorization: per-application Bearer keys, scopes, resource scope, limits, errors.

Why it exists: external applications (CORE-AI, n8n, scripts, agents) need their own credentials with exactly the
permissions an administrator granted — never the operator's session, never broker credentials.
Purpose: key generation and hashing (only a SHA-256 of a 256-bit key is stored; the key is shown once); the
`client` dependency (Authorization: Bearer corei_…), which checks the key, its expiry and revocation, the client's
status, network allow-list and per-minute request limit; `need(scope)`; resource-scope checks (accounts, instruments,
strategies: {"all": true} or {"ids": [...]}, an empty list grants nothing); GatewayError with one error shape:
{"error": {"code", "message", "request_id"}}.
Must never: log or return a key; accept a key in a query string; treat a missing scope as granted; let an
UNAVAILABLE scope pass; let a client widen its own permissions.
Dependencies: config_store, integration.catalog. Consumers: integration.gateway, integration.admin, api (usage log).
Architectural position: integration gateway, security boundary.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import Request

from .. import config_store as store
from . import catalog

KEY_RE = re.compile(r'^corei_([A-Za-z0-9]{10})_([A-Za-z0-9_-]{43})$')


class GatewayError(Exception):
    """A refused gateway request with a stable, documented code."""

    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def new_key() -> tuple[str, str, bytes]:
    """(full key, prefix, hash). The full key is returned to the administrator once and never stored."""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    prefix = ''.join(secrets.choice(alphabet) for _ in range(10))
    key = f'corei_{prefix}_{secrets.token_urlsafe(32)}'
    return key, prefix, key_hash(key)


def key_hash(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


@dataclass
class Client:
    id: UUID
    code: str
    name: str
    client_type: str
    environment: str
    scopes: frozenset[str]
    access: dict
    key_prefix: str
    key_expires_at: Any
    requests_per_minute: int
    max_concurrent_jobs: int
    extra: dict = field(default_factory=dict)

    @property
    def actor(self) -> str:
        return f'api:{self.code}'

    def can(self, scope: str) -> bool:
        return scope in self.scopes and scope in catalog.GRANTABLE

    def allows(self, kind: str, rid: Any) -> bool:
        rule = (self.access or {}).get(kind) or {}
        if rule.get('all') is True:
            return True
        return str(rid) in {str(x) for x in rule.get('ids') or []}

    def ids(self, kind: str) -> set[str] | None:
        """None = every resource of this kind; otherwise the explicit set (possibly empty)."""
        rule = (self.access or {}).get(kind) or {}
        return None if rule.get('all') is True else {str(x) for x in rule.get('ids') or []}


def request_id(request: Request) -> str:
    rid = getattr(request.state, 'int_request_id', None)
    if not rid:
        given = request.headers.get('x-request-id', '')
        rid = given if re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', given or '') else 'req_' + secrets.token_hex(8)
        request.state.int_request_id = rid
    return rid


def _ip_allowed(ip: str, networks: list[str]) -> bool:
    if not networks:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in networks:
        try:
            if addr in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def client(request: Request) -> Client:
    """FastAPI dependency: the authenticated application. Every refusal says why, without revealing key details."""
    request_id(request)
    request.state.int_started = getattr(request.state, 'int_started', time.monotonic())
    header = request.headers.get('authorization', '')
    if not header:
        raise GatewayError(401, 'MISSING_CREDENTIAL', 'Send the key as "Authorization: Bearer <key>".', {'WWW-Authenticate': 'Bearer'})
    scheme, _, token = header.partition(' ')
    match = KEY_RE.fullmatch(token.strip()) if scheme.lower() == 'bearer' else None
    if not match:
        raise GatewayError(401, 'INVALID_CREDENTIAL', 'The credential is not a COREI integration key.', {'WWW-Authenticate': 'Bearer'})
    request.state.int_key_prefix = match[1]
    ip = request.client.host if request.client else ''
    with store.transaction('write') as db:
        row = db.execute("""SELECT k.id AS key_id, k.prefix, k.expires_at, k.revoked_at, c.*
                            FROM int_keys k JOIN int_clients c ON c.id = k.client_id WHERE k.key_hash = %s""",
                         (key_hash(token.strip()),)).fetchone()
        if not row:
            raise GatewayError(401, 'INVALID_CREDENTIAL', 'The key is not recognised.', {'WWW-Authenticate': 'Bearer'})
        request.state.int_client_id = row['id']
        if row['revoked_at'] is not None:
            raise GatewayError(401, 'REVOKED_CREDENTIAL', 'This key was revoked.', {'WWW-Authenticate': 'Bearer'})
        if row['expires_at'] is not None and db.execute('SELECT %s::timestamptz <= now() AS x', (row['expires_at'],)).fetchone()['x']:
            raise GatewayError(401, 'EXPIRED_CREDENTIAL', 'This key has expired. Ask an administrator to rotate it.', {'WWW-Authenticate': 'Bearer'})
        if row['status'] != 'ACTIVE':
            raise GatewayError(403, 'CONNECTION_DISABLED', 'This connection is disabled.')
        if not _ip_allowed(ip, list(row['allowed_networks'] or [])):
            raise GatewayError(403, 'NETWORK_NOT_ALLOWED', 'Requests from this network are not allowed for this connection.')
        recent = db.execute("SELECT count(*) AS n FROM int_usage WHERE client_id=%s AND at > now() - interval '1 minute'", (row['id'],)).fetchone()['n']
        if recent >= row['requests_per_minute']:
            raise GatewayError(429, 'RATE_LIMITED', f"More than {row['requests_per_minute']} requests in the last minute.", {'Retry-After': '30'})
        db.execute('UPDATE int_keys SET last_used_at=now(), last_used_ip=%s WHERE id=%s', (ip[:64], row['key_id']))
    return Client(id=row['id'], code=row['code'], name=row['name'], client_type=row['client_type'], environment=row['environment'],
                  scopes=frozenset(row['scopes'] or []), access=row['access'] or {}, key_prefix=row['prefix'], key_expires_at=row['expires_at'],
                  requests_per_minute=row['requests_per_minute'], max_concurrent_jobs=row['max_concurrent_jobs'])


def need(c: Client, *scopes: str) -> None:
    for s in scopes:
        if s in catalog.SCOPES and catalog.SCOPES[s][2] == catalog.UNAVAILABLE:
            raise GatewayError(501, 'CAPABILITY_UNAVAILABLE', f'{s}: {catalog.SCOPES[s][3] or "not available in this release."}')
        if not c.can(s):
            raise GatewayError(403, 'PERMISSION_DENIED', f'This connection lacks the "{s}" permission.')


def in_scope(c: Client, kind: str, rid: Any, label: str = '') -> None:
    if not c.allows(kind, rid):
        raise GatewayError(403, 'OUT_OF_SCOPE', f"{label or kind[:-1].title()} {rid} is outside this connection's {kind} scope.")


def log_usage(request: Request, status: int, size: int | None, error_code: str | None) -> None:
    """One row per gateway request. Never raises: usage logging must not break a response."""
    try:
        started = getattr(request.state, 'int_started', None)
        ms = int((time.monotonic() - started) * 1000) if started else 0
        with store.transaction('write') as db:
            db.execute("""INSERT INTO int_usage(client_id, key_prefix, method, path, status, error_code, duration_ms, response_bytes, request_id, ip)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                       (getattr(request.state, 'int_client_id', None), getattr(request.state, 'int_key_prefix', None), request.method,
                        request.url.path[:300], status, error_code, ms, size, getattr(request.state, 'int_request_id', None),
                        (request.client.host if request.client else '')[:64]))
            # Keep the usage log bounded (30 days).
            if secrets.randbelow(200) == 0:
                db.execute("DELETE FROM int_usage WHERE at < now() - interval '30 days'")
    except Exception:  # noqa: BLE001 - logging is best effort
        pass
