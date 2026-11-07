"""Single-operator HTTPS session. No broker secrets are accepted here."""
import base64
from collections import deque
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/operator')
COOKIE = 'corei_os_mini_operator'
TTL = 8 * 60 * 60
attempts: deque[float] = deque()
lock = threading.Lock()


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=256)


def credentials():
    user = os.getenv('COREI_OPERATOR_USER', '')
    encoded = os.getenv('COREI_OPERATOR_PASSWORD_HASH', '')
    if not user or not encoded:
        raise HTTPException(503, 'Operator login is not configured. Run the server setup command.')
    return user, encoded


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or rounds != '600000':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def mutation_guard(request: Request):
    if request.headers.get('x-corei-request') != 'workstation':
        raise HTTPException(403, 'Missing workstation request header')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Cross-origin request rejected')


def token_for(user, encoded):
    payload = base64.urlsafe_b64encode(json.dumps({'user':user,'expires':int(time.time())+TTL,'nonce':secrets.token_hex(16)}, separators=(',', ':')).encode()).decode()
    signature = hmac.new(encoded.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + signature


def require_operator(request: Request):
    user, encoded = credentials()
    try:
        payload, signature = request.cookies.get(COOKIE, '').split('.')
        expected = hmac.new(encoded.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data['user'] != user or data['expires'] <= time.time():
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, 'Sign in to access operational records') from None
    if request.method not in ('GET','HEAD'):
        mutation_guard(request)
    return user


@router.post('/login')
def login(payload: Login, request: Request, response: Response):
    mutation_guard(request)
    user, encoded = credentials()
    now = time.monotonic()
    with lock:
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, 'Too many sign-in attempts. Wait one minute.', headers={'Retry-After':'60'})
        attempts.append(now)
    valid = verify_password(payload.password, encoded)
    if not valid or not hmac.compare_digest(payload.username.encode(), user.encode()):
        raise HTTPException(401, 'Invalid username or password')
    response.set_cookie(COOKIE, token_for(user, encoded), max_age=TTL, httponly=True, secure=True, samesite='strict', path='/api')
    response.headers['Cache-Control'] = 'no-store'
    return {'username':user}


@router.get('/session')
def session(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return {'username':require_operator(request)}


@router.post('/logout')
def logout(request: Request, response: Response):
    mutation_guard(request)
    response.delete_cookie(COOKIE, path='/api', secure=True, httponly=True, samesite='strict')
    return {'signed_out':True}
