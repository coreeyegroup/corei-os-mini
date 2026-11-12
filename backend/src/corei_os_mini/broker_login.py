"""Daily broker login for OAuth broker APIs (Upstox first; audit blocker 5).

Owns: starting the broker's login in the operator's browser, receiving the redirect,
exchanging the single-use code for the day's access token, the TOTP + PIN alternative,
and storing the token encrypted with its expiry. Under the SEBI retail algo framework
the account holder completes 2FA every trading day; COREI never stores the TOTP seed
or PIN, so this cannot be bypassed.

Upstox facts (developer documentation, checked 11 Nov 2026):
  authorize  GET  {base}/v2/login/authorization/dialog?client_id&redirect_uri&response_type=code&state
  token      POST {base}/v2/login/authorization/token  (form: code, client_id, client_secret, redirect_uri, grant_type)
  TOTP       POST {base}/v2/totp-login/authorization/token  (json: totp, pin; header x-api-key)
  expiry     03:30 Asia/Kolkata the next day (same day if issued before 03:30); each code is single-use.
The token's user_id (the UCC) must equal the account's broker client ID, so a token for
the wrong person is never attached to an account.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import config_store as store
from . import credentials
from .operator_auth import Principal, require_role

IST = ZoneInfo('Asia/Kolkata')
STATE_TTL = timedelta(minutes=10)
oauth_router = APIRouter(prefix='/api/oauth')
router = APIRouter(prefix='/api/config')
_ops = require_role('TRADING_OPS')


def upstox_expiry(issued: datetime) -> datetime:
    local = issued.astimezone(IST)
    cutoff = datetime.combine(local.date(), dtime(3, 30), IST)
    return (cutoff if local < cutoff else cutoff + timedelta(days=1)).astimezone(timezone.utc)


def _api_base(settings: dict) -> str:
    base = settings['api_base_url'].rstrip('/')
    return base if base.endswith('/v2') else base + '/v2'


class BrokerRefused(HTTPException):
    """The broker answered the login with an error. Carries what it said, for the audit log and the operator."""

    def __init__(self, status: int, message: str, blocked: bool = False):
        self.broker_status, self.broker_message, self.blocked = status, message, blocked
        super().__init__(502, message)


def _explain_http_error(exc: urllib.error.HTTPError) -> BrokerRefused:
    raw = b''
    try:
        raw = exc.read(100_000)
    except OSError:
        pass
    text = raw.decode('utf-8', 'replace')
    content_type = exc.headers.get('Content-Type', '') if exc.headers else ''
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        parts = [f"{e.get('errorCode') or e.get('error_code') or ''} {e.get('message', '')}".strip() for e in body.get('errors') or [] if isinstance(e, dict)]
        message = '; '.join(p for p in parts if p) or body.get('message') or body.get('error_description') or body.get('error') or ''
        return BrokerRefused(exc.code, f'The broker refused the login (HTTP {exc.code}){": " + str(message)[:300] if message else ""}.')
    lower = text.lower()
    if exc.code in (403, 503) and ('cloudflare' in lower or 'you have been blocked' in lower or 'cf-ray' in lower or 'attention required' in lower):
        from . import network
        ip = network.egress_ip(cached_only=True) or 'this server'
        return BrokerRefused(exc.code, f"Upstox's edge network (Cloudflare) blocked the request from {ip} (HTTP {exc.code}) before it reached the "
                             "broker. Upstox serves its API only to networks it allows; a foreign data-centre IP is usually refused. "
                             "Run COREI (or its outbound traffic) from an Indian server or egress, or ask Upstox support to allow this IP.", blocked=True)
    snippet = ' '.join(text.split())[:160]
    return BrokerRefused(exc.code, f"The broker refused the login (HTTP {exc.code}, {content_type.split(';')[0] or 'no content type'}): {snippet or 'empty reply'}.")


def _http(url: str, data: bytes, headers: dict) -> dict:
    request = urllib.request.Request(url, data=data, headers={'accept': 'application/json', **headers}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - https enforced on the connection
            return json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise _explain_http_error(exc) from None
    except (OSError, ValueError) as exc:
        raise BrokerRefused(0, f'The broker could not be reached for login ({type(exc).__name__}: {str(exc)[:120]}).') from None


def api_check(settings: dict) -> dict:
    """Can this server reach the broker's API at all? An unauthenticated call to a protected endpoint should
    answer 401 as JSON; an HTML 403 means an edge block (Cloudflare) of this server's IP, a network error means
    no route. Nothing secret is sent."""
    url = f"{_api_base(settings)}/user/profile"
    request = urllib.request.Request(url, headers={'accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return {'reachable': True, 'status': response.status, 'detail': 'The broker API answered.'}
    except urllib.error.HTTPError as exc:
        refused = _explain_http_error(exc)
        if exc.code == 401 or (exc.code in (400, 403) and not refused.blocked):
            return {'reachable': True, 'status': exc.code, 'detail': 'The broker API answered (it asked for a login, as expected).'}
        return {'reachable': False, 'status': exc.code, 'blocked': refused.blocked, 'detail': refused.broker_message}
    except (OSError, ValueError) as exc:
        return {'reachable': False, 'status': 0, 'detail': f'No route to the broker API ({type(exc).__name__}: {str(exc)[:120]}).'}


def _account(db, account_id: UUID) -> dict:
    row = db.execute('''SELECT a.*, c.platform, c.settings, c.id AS cid, c.code AS connection_code, p.code AS provider_code
        FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id JOIN cfg_providers p ON p.id=c.provider_id
        WHERE a.id=%s''', (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Account not found.')
    if row['platform'] != 'REST_API' or row['settings'].get('auth_method') != 'OAUTH2':
        raise HTTPException(422, 'Broker login applies to accounts on an OAuth 2 API connection.')
    return row


def start(account_id: UUID, user: str) -> dict:
    with store.transaction('update') as db:
        account = _account(db, account_id)
        state = secrets.token_urlsafe(32)
        db.execute("DELETE FROM cfg_oauth_states WHERE created_at < now() - interval '1 hour'")
        db.execute('INSERT INTO cfg_oauth_states(state_hash, account_id, username) VALUES (%s,%s,%s)',
                   (hashlib.sha256(state.encode()).digest(), account_id, user))
    client = store.use_credential('connections', account['cid'], f'broker-login:{user}', f'Start broker login for {account["code"]}')
    query = urllib.parse.urlencode({'client_id': client['client_id'], 'redirect_uri': account['settings']['oauth_redirect_url'],
                                    'response_type': 'code', 'state': state})
    return {'authorize_url': f"{_api_base(account['settings'])}/login/authorization/dialog?{query}"}


def _store_token(db, account: dict, token: dict, user: str, how: str) -> dict:
    ucc = str(token.get('user_id') or '')
    if not token.get('access_token'):
        raise HTTPException(502, 'The broker did not return an access token.')
    if ucc.upper() != account['external_account_id'].upper():
        store.audit(db, user, 'BROKER_LOGIN_REJECTED', 'account', account['id'], None,
                    {'reason': 'token belongs to a different client ID', 'token_user': ucc})
        raise HTTPException(422, f'This login belongs to client {ucc or "unknown"}, not {account["external_account_id"]}. '
                                 'Log in with the account holder’s own credentials.')
    issued = datetime.now(timezone.utc)
    expires = upstox_expiry(issued)
    ciphertext = credentials.encrypt(account['id'], {'access_token': token['access_token'],
                                                     **({'extended_token': token['extended_token']} if token.get('extended_token') else {})})
    db.execute('''INSERT INTO cfg_session_tokens(account_id, ciphertext, key_id, obtained_at, expires_at, obtained_by)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (account_id) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, key_id=EXCLUDED.key_id,
        obtained_at=EXCLUDED.obtained_at, expires_at=EXCLUDED.expires_at, obtained_by=EXCLUDED.obtained_by''',
               (account['id'], ciphertext, credentials.key_id(), issued, expires, user))
    db.execute('UPDATE cfg_accounts SET last_login_at=now(), last_login_error=NULL, last_login_error_at=NULL WHERE id=%s', (account['id'],))
    store.audit(db, user, 'BROKER_LOGIN', 'account', account['id'], None, {'method': how, 'expires_at': expires.isoformat()})
    return {'account_id': str(account['id']), 'expires_at': expires.isoformat()}


def finish(code: str, state: str) -> dict:
    with store.transaction('update') as db:
        row = db.execute('DELETE FROM cfg_oauth_states WHERE state_hash=%s RETURNING account_id, username, created_at',
                         (hashlib.sha256(state.encode()).digest(),)).fetchone()
        if not row or row['created_at'] < datetime.now(timezone.utc) - STATE_TTL:
            raise HTTPException(400, 'This login link has expired or was already used. Start the broker login again.')
        account = _account(db, row['account_id'])
    client = store.use_credential('connections', account['cid'], f'broker-login:{row["username"]}', f'Exchange login code for {account["code"]}')
    try:
        token = _http(f"{_api_base(account['settings'])}/login/authorization/token", urllib.parse.urlencode({
            'code': code, 'client_id': client['client_id'], 'client_secret': client['client_secret'],
            'redirect_uri': account['settings']['oauth_redirect_url'], 'grant_type': 'authorization_code'}).encode(),
            {'Content-Type': 'application/x-www-form-urlencoded'})
    except BrokerRefused as exc:
        _record_failure(account, row['username'], 'OAUTH_REDIRECT', exc)
        raise
    with store.transaction('update') as db:
        return _store_token(db, account, token, row['username'], 'OAUTH_REDIRECT')


def _record_failure(account: dict, user: str, how: str, exc: BrokerRefused) -> None:
    """A refused login is audited (status and the broker's words, never a secret) and shown on the account."""
    with store.transaction('update') as db:
        store.audit(db, user, 'BROKER_LOGIN_FAILED', 'account', account['id'], None,
                    {'method': how, 'http_status': exc.broker_status, 'blocked': exc.blocked, 'message': exc.broker_message[:400]})
        db.execute('UPDATE cfg_accounts SET last_login_error=%s, last_login_error_at=now() WHERE id=%s', (exc.broker_message[:400], account['id']))


def totp_login(account_id: UUID, totp: str, pin: str, user: str) -> dict:
    with store.transaction('read') as db:
        account = _account(db, account_id)
    client = store.use_credential('connections', account['cid'], f'broker-login:{user}', f'TOTP login for {account["code"]}')
    try:
        token = _http(f"{_api_base(account['settings'])}/totp-login/authorization/token", json.dumps({'totp': totp, 'pin': pin}).encode(),
                      {'Content-Type': 'application/json', 'x-api-key': client['client_id']})
    except BrokerRefused as exc:
        _record_failure(account, user, 'TOTP', exc)
        raise
    with store.transaction('update') as db:
        return _store_token(db, account, token, user, 'TOTP')


def session_token(account_id: UUID, used_by: str, purpose: str) -> str:
    """For adapters: today's access token (logged like any credential use)."""
    with store.transaction('read') as db:
        row = db.execute('SELECT ciphertext, expires_at FROM cfg_session_tokens WHERE account_id=%s', (account_id,)).fetchone()
        if not row or row['expires_at'] <= datetime.now(timezone.utc):
            raise HTTPException(409, 'The broker login for today is missing or expired. Log in again.')
        db.execute("INSERT INTO cfg_credential_use(credential_id, owner, owner_id, used_by, purpose) VALUES (%s,'account',%s,%s,%s)",
                   (account_id, account_id, used_by[:120], purpose[:200]))
    return credentials.decrypt(account_id, bytes(row['ciphertext']))['access_token']


# ---------- routes ----------

@router.post('/accounts/{account_id}/broker-login/start')
def start_route(account_id: UUID, response: Response, user: Principal = Depends(_ops)):
    response.headers['Cache-Control'] = 'no-store'
    return start(account_id, user.username)


class TotpIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    totp: str = Field(pattern=r'^\d{6}$')
    pin: str = Field(pattern=r'^\d{6}$')


@router.post('/accounts/{account_id}/broker-login/totp')
def totp_route(account_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        data = TotpIn.model_validate(payload)
    except ValueError:
        raise HTTPException(422, 'Enter the 6-digit code from the authenticator app and the 6-digit PIN.') from None
    return totp_login(account_id, data.totp, data.pin, user.username)


@router.post('/connections/{connection_id}/api-check')
def api_check_route(connection_id: UUID, response: Response, _: Principal = Depends(_ops)):
    """Is the broker's API reachable from this server (no secret sent)?"""
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('read') as db:
        row = db.execute('SELECT platform, settings FROM cfg_connections WHERE id=%s', (connection_id,)).fetchone()
    if not row or row['platform'] != 'REST_API':
        raise HTTPException(422, 'The API check applies to broker API connections.')
    return api_check(row['settings'])


@oauth_router.get('/upstox/callback')
def upstox_callback(code: str = Query('', max_length=512), state: str = Query('', max_length=128),
                    error: str = Query('', max_length=200)):
    """The broker redirects the operator's browser here. Session cookies are SameSite=strict
    and are not sent on this cross-site redirect, so the single-use state is the proof."""
    if error or not code or not state:
        return RedirectResponse('/?broker_login=failed', status_code=302)
    try:
        finish(code, state)
    except HTTPException as exc:
        return RedirectResponse('/?broker_login=failed&reason=' + urllib.parse.quote(str(exc.detail)[:160]), status_code=302)
    return RedirectResponse('/?broker_login=ok', status_code=302)
