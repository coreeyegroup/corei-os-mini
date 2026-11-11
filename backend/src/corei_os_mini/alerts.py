"""Alerts: e-mail, Telegram and webhook channels with a durable outbox.

Owns: channel records (secrets encrypted in the credential vault), queuing an alert in
the same transaction as the change that caused it (so an alert is never lost or sent
for a change that rolled back), and delivering queued alerts with retries.

Delivery settings: e-mail uses COREI_SMTP_HOST, COREI_SMTP_PORT (587), COREI_SMTP_USER,
COREI_SMTP_PASSWORD, COREI_SMTP_FROM, COREI_SMTP_STARTTLS (true). Telegram uses the bot
token stored on the channel. Webhooks are POSTed JSON, signed with HMAC-SHA256 of the
body in X-COREI-Signature when the channel has a secret.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
import urllib.request
from uuid import UUID, uuid4

from fastapi import HTTPException

from . import credentials

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
SEVERITY_MARK = {'INFO': 'ℹ️', 'WARNING': '⚠️', 'CRITICAL': '🛑'}


def enqueue(db, event: str, severity: str, title: str, body: str) -> int:
    """Queue one alert per active channel subscribed to the event. Returns the count."""
    channels = db.execute("SELECT id FROM cfg_alert_channels WHERE status='ACTIVE' AND %s = ANY(events)", (event,)).fetchall()
    for channel in channels:
        db.execute('INSERT INTO cfg_alert_outbox(event, severity, title, body, channel_id) VALUES (%s,%s,%s,%s,%s)',
                   (event, severity, title[:200], body[:2000], channel['id']))
    return len(channels)


# ---------- channels ----------

def _public(row: dict) -> dict:
    out = {k: (str(v) if isinstance(v, UUID) else v) for k, v in row.items() if k != 'secret'}
    out['has_secret'] = row.get('secret') is not None
    return out


def list_channels(db) -> list[dict]:
    rows = db.execute('SELECT c.*, (SELECT max(sent_at) FROM cfg_alert_outbox o WHERE o.channel_id=c.id) AS last_sent_at, '
                      "(SELECT count(*) FROM cfg_alert_outbox o WHERE o.channel_id=c.id AND o.status='FAILED') AS failed_count "
                      'FROM cfg_alert_channels c ORDER BY c.name').fetchall()
    return [_public(r) for r in rows]


def save_channel(db, actor: str, data, channel_id: UUID | None, version: int | None, audit) -> dict:
    values = data.model_dump(mode='python')
    secret = values.pop('secret', None)
    if data.kind == 'TELEGRAM' and secret is None and channel_id is None:
        raise HTTPException(422, 'A Telegram channel needs the bot token.')
    record_id = channel_id or uuid4()
    encrypted = credentials.encrypt(record_id, {'secret': secret.get_secret_value()}) if secret is not None else None
    if channel_id is None:
        db.execute('INSERT INTO cfg_alert_channels(id, kind, name, target, secret, events, status) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                   (record_id, values['kind'], values['name'], values['target'], encrypted, list(values['events']), values['status']))
        before = None
    else:
        before = db.execute('SELECT * FROM cfg_alert_channels WHERE id=%s FOR UPDATE', (channel_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Alert channel not found.')
        if before['version'] != version:
            raise HTTPException(409, 'This alert channel was changed by someone else. Reload it before saving.')
        db.execute('UPDATE cfg_alert_channels SET kind=%s, name=%s, target=%s, events=%s, status=%s, '
                   'secret=COALESCE(%s, secret), version=version+1, updated_at=now() WHERE id=%s',
                   (values['kind'], values['name'], values['target'], list(values['events']), values['status'], encrypted, channel_id))
    after = db.execute('SELECT * FROM cfg_alert_channels WHERE id=%s', (record_id,)).fetchone()
    audit(db, actor, 'UPDATE' if before else 'CREATE', 'alert channel', record_id,
          _public(before) if before else None, _public(after))
    return _public(after)


def delete_channel(db, actor: str, channel_id: UUID, audit) -> None:
    before = db.execute('DELETE FROM cfg_alert_channels WHERE id=%s RETURNING *', (channel_id,)).fetchone()
    if not before:
        raise HTTPException(404, 'Alert channel not found.')
    audit(db, actor, 'DELETE', 'alert channel', channel_id, _public(before), None)


# ---------- delivery ----------

def _send_email(target: str, title: str, body: str) -> None:
    host = os.getenv('COREI_SMTP_HOST')
    if not host:
        raise RuntimeError('E-mail is not configured on the server (COREI_SMTP_HOST).')
    message = EmailMessage()
    message['Subject'] = f'[COREI] {title}'
    message['From'] = os.getenv('COREI_SMTP_FROM', os.getenv('COREI_SMTP_USER', 'corei@localhost'))
    message['To'] = target
    message.set_content(body)
    with smtplib.SMTP(host, int(os.getenv('COREI_SMTP_PORT', '587')), timeout=15) as smtp:
        if os.getenv('COREI_SMTP_STARTTLS', 'true').lower() != 'false':
            smtp.starttls(context=ssl.create_default_context())
        if os.getenv('COREI_SMTP_USER'):
            smtp.login(os.environ['COREI_SMTP_USER'], os.getenv('COREI_SMTP_PASSWORD', ''))
        smtp.send_message(message)


def _post(url: str, payload: bytes, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json', **headers}, method='POST')
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - https enforced on save
        if response.status >= 300:
            raise RuntimeError(f'HTTP {response.status}')


def deliver(channel: dict, severity: str, title: str, body: str) -> None:
    secret = None
    if channel.get('secret') is not None:
        secret = credentials.decrypt(channel['id'], bytes(channel['secret']))['secret']
    if channel['kind'] == 'EMAIL':
        _send_email(channel['target'], title, body)
    elif channel['kind'] == 'TELEGRAM':
        if not secret:
            raise RuntimeError('No bot token stored.')
        text = f"{SEVERITY_MARK.get(severity, '')} COREI · {title}\n\n{body}"
        _post(f'https://api.telegram.org/bot{secret}/sendMessage',
              json.dumps({'chat_id': channel['target'], 'text': text[:4000], 'disable_web_page_preview': True}).encode(), {})
    else:
        payload = json.dumps({'source': 'corei-os-mini', 'severity': severity, 'title': title, 'body': body}).encode()
        headers = {'X-COREI-Signature': 'sha256=' + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()} if secret else {}
        _post(channel['target'], payload, headers)


def send_pending(transaction, limit: int = 20) -> int:
    """Deliver queued alerts. Each is marked before and after so a crash never sends twice
    in a tight loop; failures are retried up to MAX_ATTEMPTS."""
    sent = 0
    with transaction('update') as db:
        rows = db.execute("SELECT o.*, c.kind, c.target, c.secret, c.id AS cid FROM cfg_alert_outbox o JOIN cfg_alert_channels c ON c.id=o.channel_id "
                          "WHERE o.status='PENDING' ORDER BY o.id LIMIT %s FOR UPDATE OF o SKIP LOCKED", (limit,)).fetchall()
        for row in rows:
            channel = {'id': row['cid'], 'kind': row['kind'], 'target': row['target'], 'secret': row['secret']}
            try:
                deliver(channel, row['severity'], row['title'], row['body'])
                db.execute("UPDATE cfg_alert_outbox SET status='SENT', sent_at=now(), attempts=attempts+1 WHERE id=%s", (row['id'],))
                sent += 1
            except Exception as exc:  # noqa: BLE001 - any delivery failure is recorded, never raised
                attempts = row['attempts'] + 1
                db.execute('UPDATE cfg_alert_outbox SET attempts=%s, last_error=%s, status=%s WHERE id=%s',
                           (attempts, str(exc)[:300], 'FAILED' if attempts >= MAX_ATTEMPTS else 'PENDING', row['id']))
    return sent


def daily_checks(transaction) -> int:
    """Queue EXPIRY alerts for accounts and mandates about to expire (once per day each)."""
    with transaction('update') as db:
        days = db.execute("SELECT value FROM cfg_policies WHERE key='expiry_warning_days'").fetchone()
        days = int(days['value']) if days else 7
        rows = db.execute("SELECT code, expires_on, mandate_expires_on FROM cfg_accounts WHERE status='ACTIVE' AND "
                          "(expires_on BETWEEN current_date AND current_date + %s OR mandate_expires_on BETWEEN current_date AND current_date + %s)",
                          (days, days)).fetchall()
        if not rows or db.execute("SELECT 1 FROM cfg_alert_outbox WHERE event='EXPIRY' AND created_at > now() - interval '20 hours'").fetchone():
            return 0
        body = '\n'.join(f"{r['code']}: account expires {r['expires_on'] or '—'}, mandate expires {r['mandate_expires_on'] or '—'}" for r in rows)
        return enqueue(db, 'EXPIRY', 'WARNING', f'{len(rows)} account(s) expiring within {days} days', body)


async def run_forever(transaction, interval: float = 15.0) -> None:
    last_daily = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, send_pending, transaction)
            if loop.time() - last_daily > 3600:
                await loop.run_in_executor(None, daily_checks, transaction)
                last_daily = loop.time()
        except Exception:  # noqa: BLE001 - the loop must survive a database restart
            log.warning('Alert delivery cycle failed', exc_info=True)
        await asyncio.sleep(interval)


def queue_test(db, channel_id: UUID, actor: str) -> None:
    if not db.execute('SELECT 1 FROM cfg_alert_channels WHERE id=%s', (channel_id,)).fetchone():
        raise HTTPException(404, 'Alert channel not found.')
    db.execute("INSERT INTO cfg_alert_outbox(event, severity, title, body, channel_id) VALUES ('TEST','INFO',%s,%s,%s)",
               ('Test alert', f'Test sent by {actor}. If you can read this, the channel works.', channel_id))


def outbox(db, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute('SELECT o.id, o.event, o.severity, o.title, o.created_at, o.status, o.attempts, o.last_error, o.sent_at, c.name AS channel '
                      'FROM cfg_alert_outbox o LEFT JOIN cfg_alert_channels c ON c.id=o.channel_id ORDER BY o.id DESC LIMIT %s', (limit,)).fetchall()
    return [dict(r) for r in rows]

