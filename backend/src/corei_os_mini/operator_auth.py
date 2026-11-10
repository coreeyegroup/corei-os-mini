"""Operator authentication, sessions and role checks.

Owns: user sign-in, server-side sessions (revocable), password changes, account
lockout and the role dependencies used by every protected route. User management
routes for administrators live here too (/api/admin/users).

Roles (Process Flow v1.4 §7, Blueprint §15):
  ADMIN          manage users and roles; no trading configuration by itself
  TRADING_OPS    change configuration and credentials; may turn order sending OFF
  RISK_APPROVER  turn order sending ON (and OFF)
  OBSERVER       read only
Every signed-in user can read configuration and the audit log.

Bootstrap: while no user exists, the server login from COREI_OPERATOR_USER /
COREI_OPERATOR_PASSWORD_HASH may sign in once; it becomes the first ADMIN with all
roles. After that the database is the only authority.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from . import config_store as store

router = APIRouter(prefix='/api/operator')
admin_router = APIRouter(prefix='/api/admin')
COOKIE = 'corei_os_mini_operator'
TTL = 8 * 60 * 60
ROUNDS = 600_000
MAX_FAILURES, LOCK_SECONDS = 5, 15 * 60
ROLES = ('ADMIN', 'TRADING_OPS', 'RISK_APPROVER', 'OBSERVER')
Role = Literal['ADMIN', 'TRADING_OPS', 'RISK_APPROVER', 'OBSERVER']
attempts: deque[float] = deque()
lock = threading.Lock()


@dataclass(frozen=True)
class Principal:
    username: str
    roles: frozenset[str]
    user_id: UUID | None = None
    display_name: str = ''
    must_change_password: bool = False

    def has(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f'pbkdf2_sha256${ROUNDS}${salt.hex()}${hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ROUNDS).hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or rounds != str(ROUNDS):
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


Password = Annotated[str, StringConstraints(min_length=12, max_length=256)]


def password_rules(password: str, username: str) -> None:
    if len(password) < 12:
        raise HTTPException(422, 'Password must be at least 12 characters.')
    if username.lower() in password.lower():
        raise HTTPException(422, 'Password must not contain the username.')
    if len(set(password)) < 6:
        raise HTTPException(422, 'Password is too simple.')


# ---------- request guards and sessions ----------

def mutation_guard(request: Request) -> None:
    if request.headers.get('x-corei-request') != 'workstation':
        raise HTTPException(403, 'Missing workstation request header')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Cross-origin request rejected')


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _principal(row: dict) -> Principal:
    return Principal(row['username'], frozenset(row['roles']), row['id'], row['display_name'], row['must_change_password'])


def current_user(request: Request) -> Principal:
    """Any signed-in user. Users who must change their password can only do that."""
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 128:
        raise HTTPException(401, 'Sign in to access operational records')
    with store.transaction('read') as db:
        row = db.execute(
            "UPDATE cfg_sessions s SET last_seen_at=now() FROM cfg_users u "
            "WHERE s.token_hash=%s AND s.expires_at > now() AND u.id=s.user_id AND u.status='ACTIVE' "
            "RETURNING u.id, u.username, u.display_name, u.roles, u.must_change_password", (_token_hash(token),)).fetchone()
    if not row:
        raise HTTPException(401, 'Sign in to access operational records')
    if request.method not in ('GET', 'HEAD'):
        mutation_guard(request)
    principal = _principal(row)
    if principal.must_change_password and request.url.path not in ('/api/operator/password', '/api/operator/logout', '/api/operator/session'):
        raise HTTPException(403, 'Change your password before continuing.')
    return principal


def require_role(*roles: str):
    def dependency(user: Principal = Depends(current_user)) -> Principal:
        if not user.has(*roles):
            names = ' or '.join(r.replace('_', ' ').title() for r in roles)
            raise HTTPException(403, f'This action needs the {names} role.')
        return user
    return dependency


def require_operator(user: Principal = Depends(current_user)) -> str:
    """Compatibility dependency: any signed-in user, returning the username."""
    return user.username


# ---------- sign-in ----------

class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _rate_limit() -> None:
    now = time.monotonic()
    with lock:
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, 'Too many sign-in attempts. Wait one minute.', headers={'Retry-After': '60'})
        attempts.append(now)


def _bootstrap(db, username: str, password: str) -> dict | None:
    """First sign-in on a fresh install: the server login becomes the first ADMIN."""
    if db.execute('SELECT 1 FROM cfg_users LIMIT 1').fetchone():
        return None
    env_user, env_hash = os.getenv('COREI_OPERATOR_USER', ''), os.getenv('COREI_OPERATOR_PASSWORD_HASH', '')
    if not env_user or not env_hash:
        raise HTTPException(503, 'No users exist and the server login is not configured. Run the server setup command.')
    if not (hmac.compare_digest(username.encode(), env_user.encode()) and verify_password(password, env_hash)):
        return None
    name = env_user.lower()
    row = db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                     "VALUES (%s,%s,%s,%s,%s,false) RETURNING *",
                     (uuid4(), name, env_user, env_hash, list(ROLES))).fetchone()
    store.audit(db, name, 'BOOTSTRAP_ADMIN', 'user', row['id'], None, _public(row),
                'First sign-in: server login became the first administrator')
    return row


@router.post('/login')
def login(payload: Login, request: Request, response: Response):
    mutation_guard(request)
    _rate_limit()
    username = payload.username.strip().lower()
    failure = None
    with store.transaction('update') as db:
        row = _bootstrap(db, payload.username.strip(), payload.password)
        if row is None:
            row = db.execute('SELECT * FROM cfg_users WHERE username=%s FOR UPDATE', (username,)).fetchone()
            locked = bool(row and row['locked_until'] and row['locked_until'].timestamp() > time.time())
            if not row or row['status'] != 'ACTIVE' or locked or not verify_password(payload.password, row['password_hash']):
                failure = ('Account is locked after repeated failures. Try again later or ask an administrator.'
                           if locked else 'Invalid username or password')
                if row and not locked:
                    failures = row['failed_attempts'] + 1
                    db.execute("UPDATE cfg_users SET failed_attempts=%s, locked_until=CASE WHEN %s >= %s "
                               "THEN now() + %s * interval '1 second' END WHERE id=%s",
                               (0 if failures >= MAX_FAILURES else failures, failures, MAX_FAILURES, LOCK_SECONDS, row['id']))
                    store.audit(db, username, 'SIGN_IN_FAILED', 'user', row['id'], None, None,
                                'Account locked for 15 minutes' if failures >= MAX_FAILURES else '')
        if failure is None:
            db.execute('UPDATE cfg_users SET failed_attempts=0, locked_until=NULL, last_login_at=now() WHERE id=%s', (row['id'],))
            db.execute('DELETE FROM cfg_sessions WHERE expires_at <= now()')
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO cfg_sessions(token_hash, user_id, expires_at) VALUES (%s,%s, now() + %s * interval '1 second')",
                       (_token_hash(token), row['id'], TTL))
            store.audit(db, row['username'], 'SIGN_IN', 'user', row['id'], None, None)
    # Raised after commit so the failure count and its audit record are kept.
    if failure:
        raise HTTPException(401, failure)
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, secure=True, samesite='strict', path='/api')
    response.headers['Cache-Control'] = 'no-store'
    return _session_body(_principal(row))


def _session_body(user: Principal) -> dict:
    return {'username': user.username, 'display_name': user.display_name, 'roles': sorted(user.roles),
            'must_change_password': user.must_change_password}


@router.get('/session')
def session(response: Response, user: Principal = Depends(current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return _session_body(user)


@router.post('/logout')
def logout(request: Request, response: Response):
    mutation_guard(request)
    token = request.cookies.get(COOKIE, '')
    if token and len(token) <= 128:
        with store.transaction('delete') as db:
            db.execute('DELETE FROM cfg_sessions WHERE token_hash=%s', (_token_hash(token),))
    response.delete_cookie(COOKIE, path='/api', secure=True, httponly=True, samesite='strict')
    return {'signed_out': True}


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=256)
    new_password: Password


@router.post('/password')
def change_password(payload: PasswordChange, request: Request, response: Response, user: Principal = Depends(current_user)):
    response.headers['Cache-Control'] = 'no-store'
    password_rules(payload.new_password, user.username)
    if payload.new_password == payload.current_password:
        raise HTTPException(422, 'Choose a password different from the current one.')
    token = request.cookies.get(COOKIE, '')
    with store.transaction('update') as db:
        row = db.execute('SELECT password_hash FROM cfg_users WHERE id=%s FOR UPDATE', (user.user_id,)).fetchone()
        if not row or not verify_password(payload.current_password, row['password_hash']):
            raise HTTPException(422, 'Current password is not correct.')
        db.execute('UPDATE cfg_users SET password_hash=%s, must_change_password=false, updated_at=now(), version=version+1 WHERE id=%s',
                   (hash_password(payload.new_password), user.user_id))
        # Other sessions of this user end; this one continues.
        db.execute('DELETE FROM cfg_sessions WHERE user_id=%s AND token_hash<>%s', (user.user_id, _token_hash(token)))
        store.audit(db, user.username, 'CHANGE_PASSWORD', 'user', user.user_id, None, None)
    return {'changed': True}


# ---------- user administration (ADMIN) ----------

UserName = Annotated[str, BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
                     StringConstraints(pattern=r'^[a-z][a-z0-9._-]{2,63}$')]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: UserName
    display_name: DisplayName
    roles: Annotated[list[Role], Field(min_length=1, max_length=4)]
    temporary_password: Password


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: DisplayName
    roles: Annotated[list[Role], Field(min_length=1, max_length=4)]
    status: Literal['ACTIVE', 'DISABLED']
    version: int = Field(ge=1)


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    temporary_password: Password


def _public(row: dict) -> dict:
    return {k: (str(v) if isinstance(v, UUID) else v) for k, v in row.items() if k not in ('password_hash',)}


def _admins_remaining(db, excluding: UUID) -> int:
    return db.execute("SELECT count(*) AS n FROM cfg_users WHERE id<>%s AND status='ACTIVE' AND 'ADMIN' = ANY(roles)",
                      (excluding,)).fetchone()['n']


@admin_router.get('/users')
def list_users(response: Response, _: Principal = Depends(require_role('ADMIN'))):
    response.headers['Cache-Control'] = 'no-store'
    with store.transaction('read') as db:
        rows = db.execute('SELECT u.*, (SELECT count(*) FROM cfg_sessions s WHERE s.user_id=u.id AND s.expires_at>now()) AS active_sessions '
                          'FROM cfg_users u ORDER BY username').fetchall()
    return {'items': [_public(r) for r in rows]}


@admin_router.post('/users', status_code=201)
def create_user(payload: UserCreate, response: Response, admin: Principal = Depends(require_role('ADMIN'))):
    response.headers['Cache-Control'] = 'no-store'
    password_rules(payload.temporary_password, payload.username)
    with store.transaction('create') as db:
        if db.execute('SELECT 1 FROM cfg_users WHERE username=%s', (payload.username,)).fetchone():
            raise HTTPException(409, 'A user with this username already exists.')
        row = db.execute('INSERT INTO cfg_users(id, username, display_name, password_hash, roles) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(), payload.username, payload.display_name, hash_password(payload.temporary_password),
                          sorted(set(payload.roles)))).fetchone()
        store.audit(db, admin.username, 'CREATE', 'user', row['id'], None, _public(row))
    return _public(row)


@admin_router.put('/users/{user_id}')
def update_user(user_id: UUID, payload: UserUpdate, response: Response, admin: Principal = Depends(require_role('ADMIN'))):
    response.headers['Cache-Control'] = 'no-store'
    roles = sorted(set(payload.roles))
    with store.transaction('update') as db:
        before = db.execute('SELECT * FROM cfg_users WHERE id=%s FOR UPDATE', (user_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'User not found.')
        if before['version'] != payload.version:
            raise HTTPException(409, 'This user was changed by someone else. Reload before saving.')
        if user_id == admin.user_id and (roles != sorted(before['roles']) or payload.status != before['status']):
            raise HTTPException(422, 'You cannot change your own roles or status. Ask another administrator.')
        losing_admin = 'ADMIN' in before['roles'] and before['status'] == 'ACTIVE' and ('ADMIN' not in roles or payload.status != 'ACTIVE')
        if losing_admin and _admins_remaining(db, user_id) == 0:
            raise HTTPException(422, 'At least one active administrator must remain.')
        after = db.execute('UPDATE cfg_users SET display_name=%s, roles=%s, status=%s, version=version+1, updated_at=now() '
                           'WHERE id=%s RETURNING *', (payload.display_name, roles, payload.status, user_id)).fetchone()
        if payload.status != 'ACTIVE' or roles != sorted(before['roles']):
            db.execute('DELETE FROM cfg_sessions WHERE user_id=%s', (user_id,))
        store.audit(db, admin.username, 'UPDATE', 'user', user_id, _public(before), _public(after))
    return _public(after)


@admin_router.post('/users/{user_id}/password')
def reset_password(user_id: UUID, payload: PasswordReset, response: Response, admin: Principal = Depends(require_role('ADMIN'))):
    response.headers['Cache-Control'] = 'no-store'
    if user_id == admin.user_id:
        raise HTTPException(422, 'Use Change password for your own account.')
    with store.transaction('update') as db:
        row = db.execute('SELECT username FROM cfg_users WHERE id=%s FOR UPDATE', (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'User not found.')
        password_rules(payload.temporary_password, row['username'])
        db.execute('UPDATE cfg_users SET password_hash=%s, must_change_password=true, failed_attempts=0, locked_until=NULL, '
                   'version=version+1, updated_at=now() WHERE id=%s', (hash_password(payload.temporary_password), user_id))
        db.execute('DELETE FROM cfg_sessions WHERE user_id=%s', (user_id,))
        store.audit(db, admin.username, 'RESET_PASSWORD', 'user', user_id, None, None, 'Temporary password issued; sessions ended')
    return {'reset': True}
