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


def _http(url: str, data: bytes, headers: dict) -> dict:
    request = urllib.request.Request(url, data=data, headers={'accept': 'application/json', **headers}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - https enforced on the connection
            return json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read(100_000))
            message = '; '.join(e.get('message', '') for e in body.get('errors', [])) or body.get('message') or ''
        except (ValueError, AttributeError):
            message = ''
        raise HTTPException(502, f'The broker refused the login{": " + message[:200] if message else ""}.') from None
    except (OSError, ValueError):
        raise HTTPException(502, 'The broker could not be reached for login.') from None


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
    db.execute('UPDATE cfg_accounts SET last_login_at=now() WHERE id=%s', (account['id'],))
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
    token = _http(f"{_api_base(account['settings'])}/login/authorization/token", urllib.parse.urlencode({
        'code': code, 'client_id': client['client_id'], 'client_secret': client['client_secret'],
        'redirect_uri': account['settings']['oauth_redirect_url'], 'grant_type': 'authorization_code'}).encode(),
        {'Content-Type': 'application/x-www-form-urlencoded'})
    with store.transaction('update') as db:
        return _store_token(db, account, token, row['username'], 'OAUTH_REDIRECT')


def totp_login(account_id: UUID, totp: str, pin: str, user: str) -> dict:
    with store.transaction('read') as db:
        account = _account(db, account_id)
    client = store.use_credential('connections', account['cid'], f'broker-login:{user}', f'TOTP login for {account["code"]}')
    token = _http(f"{_api_base(account['settings'])}/totp-login/authorization/token", json.dumps({'totp': totp, 'pin': pin}).encode(),
                  {'Content-Type': 'application/json', 'x-api-key': client['client_id']})
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
