"""Integration webhooks: event fan-out to subscribed applications, signed, at least once, never blocking trading.

Why it exists: n8n and CORE-AI should react to COREI events (a job finished, a signal was checked, an order was refused,
a kill switch engaged) without polling. Delivery must never slow or stop the collector, intelligence engine or
trading engine, and a webhook must not become a way to reach internal network services (SSRF).
Purpose: validate_destination (https only, no private, loopback or link-local address unless
COREI_WEBHOOK_ALLOW_PRIVATE=true, no redirects); emit (queue one delivery per subscribed active webhook in the caller's
transaction, with a stable event_id); deliver_pending (claim rows, send outside any transaction, exponential backoff,
FAILED after max attempts); job_notices (one job.completed / job.failed per gateway scan job); run_forever.
Signature: X-COREI-Timestamp: <unix seconds>; X-COREI-Signature: sha256=HMAC_SHA256(secret, "<timestamp>.<body>")
(same scheme as CORE-AI); X-COREI-Event-Id for de-duplication; X-COREI-Event: the event name.
Must never: follow redirects; deliver to a private address by default; hold a database transaction during a network
call; raise out of the delivery loop.
Dependencies: config_store, credentials, integration.catalog. Consumers: alerts (fan-out of workstation alerts),
integration.gateway, integration.admin, api (background task).
Architectural position: integration gateway, outbound events.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .. import config_store as store
from .. import credentials

log = logging.getLogger(__name__)
heartbeat: dict[str, float | None] = {'at': None, 'failed_at': None}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _private(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def validate_destination(url: str, resolve: bool = True) -> str:
    """Returns the URL or raises ValueError with a reason a person can act on."""
    parts = urllib.parse.urlsplit(url)
    allow_private = os.getenv('COREI_WEBHOOK_ALLOW_PRIVATE', 'false').lower() == 'true'
    if parts.scheme != 'https' and not (allow_private and parts.scheme == 'http'):
        raise ValueError('Webhook destinations must use https.')
    if not parts.hostname or parts.username or parts.password:
        raise ValueError('Give a host name, without user name or password in the address.')
    if len(url) > 500:
        raise ValueError('The address is longer than 500 characters.')
    if not allow_private:
        host = parts.hostname
        try:
            addrs = {host} if _is_ip(host) else (_resolve(host, parts.port or 443) if resolve else set())
        except socket.gaierror:
            raise ValueError(f'The host {host} could not be resolved.') from None
        if any(_private(a) for a in addrs):
            raise ValueError('The destination resolves to a private or local address. Set COREI_WEBHOOK_ALLOW_PRIVATE=true on the server '
                             'only if this is intended (for example n8n on the same private network).')
    return url


def _resolve(host: str, port: int) -> set[str]:
    return {a[4][0] for a in socket.getaddrinfo(host, port)}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(secret.encode(), timestamp.encode() + b'.' + body, hashlib.sha256).hexdigest()


def emit(db, event: str, data: dict, client_id: UUID | None = None) -> int:
    """Queue `event` for every active webhook subscribed to it (only the client's own webhooks when client_id is given)."""
    sql = "SELECT w.id FROM int_webhooks w JOIN int_clients c ON c.id=w.client_id WHERE w.status='ACTIVE' AND c.status='ACTIVE' " \
          "AND %s = ANY(w.events) AND 'events.subscribe' = ANY(c.scopes)"
    args: list = [event]
    if client_id:
        sql += ' AND w.client_id=%s'
        args.append(str(client_id))
    hooks = db.execute(sql, args).fetchall()
    event_id = uuid4()
    payload = {'event_id': str(event_id), 'event': event, 'occurred_at': datetime.now(timezone.utc).isoformat(), 'source': 'corei-os-mini',
               'data': data}
    for h in hooks:
        db.execute('INSERT INTO int_webhook_deliveries(webhook_id, event_id, event, payload) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                   (h['id'], event_id, event, json.dumps(payload, default=str)))
    return len(hooks)


def _claim(limit: int) -> list[dict]:
    with store.transaction('write') as db:
        rows = db.execute("""UPDATE int_webhook_deliveries d SET status='SENDING', attempts=attempts+1
                             WHERE d.id IN (SELECT id FROM int_webhook_deliveries
                                            WHERE (status='PENDING' AND next_at <= now())
                                               OR (status='SENDING' AND next_at <= now() - interval '5 minutes')
                                            ORDER BY next_at LIMIT %s FOR UPDATE SKIP LOCKED)
                             RETURNING d.*""", (limit,)).fetchall()
        hooks = {}
        for r in rows:
            if r['webhook_id'] not in hooks:
                hooks[r['webhook_id']] = db.execute('SELECT * FROM int_webhooks WHERE id=%s', (r['webhook_id'],)).fetchone()
        return [{**dict(r), 'hook': dict(hooks[r['webhook_id']])} for r in rows]


def send(hook: dict, payload: dict) -> int:
    """POST one signed delivery; returns the HTTP status (raises on network errors and non-2xx)."""
    secret = credentials.decrypt(hook['id'], bytes(hook['secret']))['secret']
    body = json.dumps(payload, separators=(',', ':'), default=str).encode()
    ts = str(int(time.time()))
    validate_destination(hook['url'])
    req = urllib.request.Request(hook['url'], data=body, method='POST', headers={
        'Content-Type': 'application/json', 'User-Agent': 'COREI-OS-MINI-webhooks/1', 'X-COREI-Event': payload['event'],
        'X-COREI-Event-Id': payload['event_id'], 'X-COREI-Timestamp': ts, 'X-COREI-Signature': sign(secret, ts, body)})
    with _opener.open(req, timeout=hook['timeout_seconds']) as resp:  # noqa: S310 - https and destination checked above
        if not 200 <= resp.status < 300:
            raise RuntimeError(f'HTTP {resp.status}')
        return resp.status


def deliver_pending(limit: int = 20) -> int:
    sent = 0
    for d in _claim(limit):
        status, error = None, None
        try:
            status = send(d['hook'], d['payload'])
        except urllib.error.HTTPError as exc:
            status, error = exc.code, f'HTTP {exc.code}'
        except Exception as exc:  # noqa: BLE001 - every failure is recorded and retried
            error = str(exc)[:300]
        with store.transaction('write') as db:
            if error is None:
                db.execute("UPDATE int_webhook_deliveries SET status='SENT', sent_at=now(), last_status=%s, last_error=NULL WHERE id=%s", (status, d['id']))
                sent += 1
            else:
                final = d['attempts'] >= d['hook']['max_attempts']
                delay = min(3600, 15 * 2 ** max(0, d['attempts'] - 1))
                db.execute("""UPDATE int_webhook_deliveries SET status=%s, last_status=%s, last_error=%s, next_at=now() + make_interval(secs => %s)
                              WHERE id=%s""", ('FAILED' if final else 'PENDING', status, error, delay, d['id']))
    return sent


def job_notices() -> int:
    """One job.completed / job.failed per gateway scan job (operators named api:<code>)."""
    n = 0
    with store.transaction('write') as db:
        rows = db.execute("""SELECT r.id, r.status, r.operator, r.name, c.id AS client_id FROM studio_scan_runs r
                             JOIN int_clients c ON 'api:' || c.code = r.operator
                             LEFT JOIN int_job_notices j ON j.run_id = r.id
                             WHERE r.status IN ('COMPLETED','FAILED','CANCELLED') AND j.run_id IS NULL
                             ORDER BY r.updated_at LIMIT 50""").fetchall()
        for r in rows:
            db.execute('INSERT INTO int_job_notices(run_id, status) VALUES (%s,%s) ON CONFLICT DO NOTHING', (r['id'], r['status']))
            event = 'job.completed' if r['status'] == 'COMPLETED' else 'job.failed'
            emit(db, event, {'job_id': str(r['id']), 'status': {'COMPLETED': 'SUCCEEDED'}.get(r['status'], r['status']), 'name': r['name']},
                 client_id=r['client_id'])
            n += 1
    return n


async def run_forever(interval: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, job_notices)
            await loop.run_in_executor(None, deliver_pending)
            heartbeat['at'] = time.time()
        except Exception:  # noqa: BLE001 - the loop must survive a database restart
            heartbeat['failed_at'] = time.time()
            log.warning('Webhook delivery cycle failed', exc_info=True)
        await asyncio.sleep(interval)
