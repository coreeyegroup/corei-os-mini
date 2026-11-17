"""Bridge nodes (DEC-75): machines running the COREI MT5 bridge, registered once and shared by many accounts.

Why it exists: entering a host, port, certificate fingerprint and token on every account's route made adding accounts
slow and error-prone, and asked traders to copy secrets between consoles. A node is registered once; the machine
enrols itself with a one-time code; accounts then pick the node.
Purpose: node CRUD; one-time enrolment codes; the public enrolment (the machine sends its fingerprint, token, port,
bridge version and, for tunnel mode, its SSH public key); a health check (TLS to the node with the pinned fingerprint,
then the bridge's "ping": version, clock, terminal pool); attaching a node to accounts as a route; the node token for
the collector and logins; the tunnel keys file for tunnel-mode nodes.
Responsibilities: everything above, audited in the same transaction. Codes are random (about 100 bits), stored only
as a SHA-256 hash, valid 60 minutes and once. The token is stored encrypted and never returned.
Must never: return a token, accept an enrolment without a valid unused code, or start an MT5 terminal (the health
check uses the bridge's ping, which logs nobody in).
Dependencies: config_store, credentials, md.bridge_client, network. Consumers: bridge_api, account_routes, tests.
Architectural position: configuration, beside account routes (Stage 1 connectivity).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from . import config_store as store
from . import credentials

LATEST_BRIDGE = '0.43.0'           # the bridge in this release (connectors/mt5-bridge/corei_mt5_bridge.py)
CODE_MINUTES = 60
FIRST_TUNNEL_PORT = 9450
_FP = re.compile(r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')
_KEY = re.compile(r'^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]{40,700}( [^\s]{0,80})?$')
PUBLIC = ('id', 'name', 'location', 'mode', 'host', 'port', 'tunnel_port', 'fingerprint', 'max_terminals', 'default_for_new', 'bridge_version',
          'machine', 'remote_ip', 'status', 'enroll_expires_at', 'enrolled_at', 'last_check_at', 'last_check_ok', 'last_check', 'notes',
          'version', 'created_by', 'created_at', 'updated_by', 'updated_at', 'token_set_at')


class NodeIn(BaseModel):
    name: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{1,40}$')
    location: Literal['WINDOWS_VPS', 'WINDOWS_PC', 'WINDOWS_SERVER', 'OTHER'] = 'WINDOWS_VPS'
    mode: Literal['DIRECT', 'TUNNEL'] = 'DIRECT'
    host: str | None = Field(default=None, max_length=253)   # DIRECT: the machine's public address (default: where it enrols from)
    port: int = Field(default=9443, ge=1, le=65535)
    max_terminals: int = Field(default=20, ge=1, le=500)
    default_for_new: bool = False
    notes: str = Field(default='', max_length=500)

    @field_validator('name', mode='before')
    @classmethod
    def _lower(cls, v: Any) -> Any:
        return str(v).strip().lower() if isinstance(v, str) else v


class EnrolIn(BaseModel):
    fingerprint: str
    token: str = Field(min_length=16, max_length=400)
    port: int = Field(ge=1, le=65535)
    bridge_version: str = Field(default='', max_length=20)
    machine: str = Field(default='', max_length=80)
    host: str | None = Field(default=None, max_length=253)
    ssh_public_key: str = Field(default='', max_length=800)
    max_terminals: int | None = Field(default=None, ge=1, le=500)

    @field_validator('fingerprint')
    @classmethod
    def _fp(cls, v: str) -> str:
        v = v.strip().upper().replace(' ', '')
        if ':' not in v and len(v) == 64:
            v = ':'.join(v[i:i + 2] for i in range(0, 64, 2))
        if not _FP.match(v):
            raise ValueError('fingerprint must be the SHA-256 of the bridge certificate')
        return v


def _hash(code: str) -> str:
    return hashlib.sha256(re.sub(r'[^A-Z0-9]', '', code.upper()).encode()).hexdigest()


def _code() -> str:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I
    raw = ''.join(secrets.choice(alphabet) for _ in range(20))
    return '-'.join(raw[i:i + 5] for i in range(0, 20, 5))


def _public(row: dict, usage: int | None = None) -> dict:
    out = {k: v for k, v in store.clean(dict(row)).items() if k in PUBLIC}
    out['token_saved'] = row.get('token') is not None
    out['update_available'] = bool(row.get('bridge_version')) and _ver(row['bridge_version']) < _ver(LATEST_BRIDGE)
    out['latest_bridge'] = LATEST_BRIDGE
    if usage is not None:
        out['accounts'] = usage
    return out


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split('.')[:3])
    except ValueError:
        return (0,)


def corei_url() -> str:
    return os.getenv('COREI_PUBLIC_URL', 'https://corei-os-mini.coreeye.in').rstrip('/')


def tunnel_user() -> str:
    return os.getenv('COREI_TUNNEL_USER', 'corei-tunnel')


def instructions(node: dict, code: str | None) -> dict:
    """What to run on the Windows machine: one download and one command (PowerShell as Administrator)."""
    url = corei_url()
    folder = 'C:\\corei-setup'
    steps = [
        {'title': 'Open PowerShell as Administrator', 'detail': 'Start menu › type PowerShell › right-click › Run as administrator.'},
        {'title': 'Download the installer', 'command': f'New-Item -ItemType Directory -Force {folder} | Out-Null; '
                                                       f'Invoke-WebRequest {url}/api/bridge-enroll/files/install-corei-bridge.ps1 -OutFile {folder}\\install-corei-bridge.ps1'},
    ]
    if code:
        steps.append({'title': 'Install and register this machine',
                      'command': f'powershell -ExecutionPolicy Bypass -File {folder}\\install-corei-bridge.ps1 -Corei {url} -Code {code}',
                      'detail': 'It checks Python and the MT5 terminal (and installs Python if missing), installs the bridge as a service that restarts by '
                                'itself, opens the firewall to COREI only (or the outgoing tunnel), then registers with COREI. Takes 2–5 minutes.'})
    if node.get('bridge_version') and _ver(node['bridge_version']) < _ver(LATEST_BRIDGE):
        steps.append({'title': 'Update the bridge on this machine',
                      'command': f"powershell -ExecutionPolicy Bypass -File {folder}\\install-corei-bridge.ps1 -Corei {url} -Update -Name {node['name']}"})
    return {'folder': folder, 'steps': steps, 'code': code, 'code_minutes': CODE_MINUTES if code else None}


# ---------- CRUD ----------

def list_nodes() -> list[dict]:
    with store.transaction('read') as db:
        rows = db.execute('SELECT n.*, (SELECT count(*) FROM cfg_account_routes r WHERE r.node_id=n.id) AS used FROM cfg_bridge_nodes n ORDER BY n.name').fetchall()
    return [_public(r, r['used']) for r in rows]


def get(node_id: UUID) -> dict:
    with store.transaction('read') as db:
        row = db.execute('SELECT n.*, (SELECT count(*) FROM cfg_account_routes r WHERE r.node_id=n.id) AS used FROM cfg_bridge_nodes n WHERE n.id=%s',
                         (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Bridge node not found.')
        accounts = db.execute('SELECT a.id, a.code, r.label, r.priority, r.enabled, r.active_since, r.last_ok_at, r.last_error FROM cfg_account_routes r '
                              'JOIN cfg_accounts a ON a.id=r.account_id WHERE r.node_id=%s ORDER BY a.code', (node_id,)).fetchall()
    out = _public(row, row['used'])
    out['routes'] = [store.clean(a) for a in accounts]
    out['instructions'] = instructions(row, None)
    return out


def _next_tunnel_port(db) -> int:
    used = {r['tunnel_port'] for r in db.execute('SELECT tunnel_port FROM cfg_bridge_nodes WHERE tunnel_port IS NOT NULL').fetchall()}
    port = FIRST_TUNNEL_PORT
    while port in used:
        port += 1
    return port


def create(actor: str, data: NodeIn) -> dict:
    code = _code()
    with store.transaction('write') as db:
        if db.execute('SELECT 1 FROM cfg_bridge_nodes WHERE name=%s', (data.name,)).fetchone():
            raise HTTPException(409, f'A bridge node named {data.name} already exists.')
        if data.default_for_new:
            db.execute('UPDATE cfg_bridge_nodes SET default_for_new=false WHERE default_for_new')
        node_id = uuid4()
        tunnel = _next_tunnel_port(db) if data.mode == 'TUNNEL' else None
        db.execute("""INSERT INTO cfg_bridge_nodes(id, name, location, mode, host, port, tunnel_port, max_terminals, default_for_new, notes,
                      enroll_hash, enroll_expires_at, created_by, updated_by)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now() + make_interval(mins => %s), %s,%s)""",
                   (node_id, data.name, data.location, data.mode, data.host, data.port, tunnel, data.max_terminals, data.default_for_new,
                    data.notes, _hash(code), CODE_MINUTES, actor, actor))
        row = db.execute('SELECT * FROM cfg_bridge_nodes WHERE id=%s', (node_id,)).fetchone()
        store.audit(db, actor, 'CREATE_BRIDGE_NODE', 'bridge_node', node_id, None, _public(row))
    out = _public(row, 0)
    out['instructions'] = instructions(row, code)
    return out


def new_code(actor: str, node_id: UUID) -> dict:
    """A fresh one-time code (re-install, or a code that expired). The node keeps working until the machine re-enrols."""
    code = _code()
    with store.transaction('write') as db:
        row = db.execute('UPDATE cfg_bridge_nodes SET enroll_hash=%s, enroll_expires_at=now() + make_interval(mins => %s), updated_by=%s, updated_at=now() '
                         'WHERE id=%s RETURNING *', (_hash(code), CODE_MINUTES, actor, node_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Bridge node not found.')
        store.audit(db, actor, 'BRIDGE_NODE_CODE', 'bridge_node', node_id, None, {'name': row['name'], 'expires_at': row['enroll_expires_at']})
    out = _public(row)
    out['instructions'] = instructions(row, code)
    return out


class NodeUpdate(BaseModel):
    location: Literal['WINDOWS_VPS', 'WINDOWS_PC', 'WINDOWS_SERVER', 'OTHER']
    host: str | None = Field(default=None, max_length=253)
    port: int = Field(ge=1, le=65535)
    max_terminals: int = Field(ge=1, le=500)
    default_for_new: bool = False
    enabled: bool = True
    notes: str = Field(default='', max_length=500)


def update(actor: str, node_id: UUID, data: NodeUpdate, version: int) -> dict:
    with store.transaction('write') as db:
        before = db.execute('SELECT * FROM cfg_bridge_nodes WHERE id=%s FOR UPDATE', (node_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Bridge node not found.')
        if before['version'] != version:
            raise HTTPException(409, 'This node was changed by someone else: reload and try again.')
        if data.default_for_new:
            db.execute('UPDATE cfg_bridge_nodes SET default_for_new=false WHERE default_for_new AND id<>%s', (node_id,))
        if not data.enabled:
            status = 'DISABLED'
        elif before['status'] == 'DISABLED':
            status = 'ACTIVE' if before['token'] is not None else 'PENDING'
        else:
            status = before['status']
        host = data.host if before['mode'] == 'DIRECT' else before['host']
        port = data.port if before['mode'] == 'DIRECT' else before['port']
        row = db.execute('UPDATE cfg_bridge_nodes SET location=%s, host=%s, port=%s, max_terminals=%s, default_for_new=%s, status=%s, notes=%s, '
                         'version=version+1, updated_by=%s, updated_at=now() WHERE id=%s RETURNING *',
                         (data.location, host, port, data.max_terminals, data.default_for_new, status, data.notes, actor, node_id)).fetchone()
        store.audit(db, actor, 'UPDATE_BRIDGE_NODE', 'bridge_node', node_id, _public(before), _public(row))
    return _public(row)


def delete(actor: str, node_id: UUID) -> None:
    with store.transaction('delete') as db:
        used = db.execute('SELECT a.code FROM cfg_account_routes r JOIN cfg_accounts a ON a.id=r.account_id WHERE r.node_id=%s ORDER BY 1', (node_id,)).fetchall()
        if used:
            names = ', '.join(r['code'] for r in used[:10]) + (' …' if len(used) > 10 else '')
            raise HTTPException(409, f'{len(used)} account route(s) use this node ({names}). Remove those routes first, or use "Detach from all accounts".')
        before = db.execute('DELETE FROM cfg_bridge_nodes WHERE id=%s RETURNING *', (node_id,)).fetchone()
        if not before:
            raise HTTPException(404, 'Bridge node not found.')
        store.audit(db, actor, 'DELETE_BRIDGE_NODE', 'bridge_node', node_id, _public(before), None)
    write_tunnel_keys()


# ---------- enrolment (public, code-authenticated) ----------

def _pending(db, code: str, lock: bool = False) -> dict:
    row = db.execute('SELECT * FROM cfg_bridge_nodes WHERE enroll_hash=%s AND enroll_expires_at > now()' + (' FOR UPDATE' if lock else ''),
                     (_hash(code),)).fetchone()
    if not row:
        raise HTTPException(404, 'This enrolment code is not valid: it was already used, has expired (60 minutes), or was mistyped. '
                                 'Make a new one in COREI: Setup › Bridge nodes › the node › New enrolment code.')
    return row


def enrol_info(code: str) -> dict:
    """What the installer needs to set the machine up (nothing secret)."""
    from . import network
    with store.transaction('read') as db:
        row = _pending(db, code)
    host = corei_url().split('://', 1)[-1].split('/')[0]
    return {'name': row['name'], 'mode': row['mode'], 'port': row['port'], 'max_terminals': row['max_terminals'],
            'corei_ip': network.egress_ip() or '', 'corei_host': host, 'ssh_target': f'{tunnel_user()}@{host}' if row['mode'] == 'TUNNEL' else '',
            'tunnel_port': row['tunnel_port'], 'latest_bridge': LATEST_BRIDGE}


def enrol(code: str, data: EnrolIn, remote_ip: str | None) -> dict:
    if data.ssh_public_key and not _KEY.match(data.ssh_public_key.strip()):
        raise HTTPException(422, 'ssh_public_key is not an OpenSSH public key line.')
    with store.transaction('write') as db:
        row = _pending(db, code, lock=True)
        if row['mode'] == 'TUNNEL' and not data.ssh_public_key:
            raise HTTPException(422, 'A tunnel-mode node sends its SSH public key (the installer does this).')
        host = '127.0.0.1' if row['mode'] == 'TUNNEL' else (row['host'] or data.host or remote_ip)
        port = row['tunnel_port'] if row['mode'] == 'TUNNEL' else data.port
        if not host:
            raise HTTPException(422, 'The node\'s address is unknown: set Host on the node in COREI.')
        actor = f"enrol:{row['name']}"
        db.execute("""UPDATE cfg_bridge_nodes SET fingerprint=%s, token=%s, token_key_id=%s, token_set_at=now(), host=%s, port=%s,
                      bridge_version=%s, machine=%s, remote_ip=%s, ssh_public_key=%s, max_terminals=COALESCE(%s, max_terminals),
                      status=CASE WHEN status='DISABLED' THEN status ELSE 'ACTIVE' END, enrolled_at=now(), enroll_hash=NULL, enroll_expires_at=NULL,
                      version=version+1, updated_by=%s, updated_at=now() WHERE id=%s""",
                   (data.fingerprint, credentials.encrypt(row['id'], {'token': data.token}), credentials.key_id(), host, port, data.bridge_version,
                    data.machine, remote_ip, data.ssh_public_key.strip() or None, data.max_terminals, actor, row['id']))
        after = db.execute('SELECT * FROM cfg_bridge_nodes WHERE id=%s', (row['id'],)).fetchone()
        store.audit(db, actor, 'ENROL_BRIDGE_NODE', 'bridge_node', row['id'], _public(row), _public(after))
    tunnel = write_tunnel_keys() if row['mode'] == 'TUNNEL' else None
    result = check(row['id'], actor) if row['mode'] == 'DIRECT' else None
    return {'node': after['name'], 'mode': after['mode'], 'host': host, 'port': port, 'check': result, 'tunnel': tunnel,
            'next': 'Done. In COREI: Setup › Bridge nodes shows this machine; use it on accounts with "Use for accounts".'}


# ---------- health ----------

def token(node_id: Any, used_by: str, purpose: str) -> dict[str, str]:
    """The node's token for in-process use (bridge connections); every use is logged."""
    with store.transaction('read') as db:
        row = db.execute('SELECT id, token FROM cfg_bridge_nodes WHERE id=%s', (str(node_id),)).fetchone()
        if not row or row['token'] is None:
            raise HTTPException(404, 'This bridge node has not enrolled yet (no token).')
        db.execute("INSERT INTO cfg_credential_use(credential_id, owner, owner_id, used_by, purpose) VALUES (%s,'bridge_node',%s,%s,%s)",
                   (row['id'], row['id'], used_by[:120], purpose[:200]))
    return credentials.decrypt(row['id'], bytes(row['token']))


def check(node_id: Any, actor: str = 'health') -> dict:
    """TLS to the node (pinned fingerprint), token, the bridge's ping: version, clock, pool. Starts no terminal."""
    from datetime import datetime, timezone
    from .md.bridge_client import BridgeError, BridgeTransport
    with store.transaction('read') as db:
        node = db.execute('SELECT * FROM cfg_bridge_nodes WHERE id=%s', (str(node_id),)).fetchone()
    if not node:
        raise HTTPException(404, 'Bridge node not found.')
    if not node['fingerprint'] or node['token'] is None:
        return {'ok': False, 'error': 'The machine has not enrolled yet: run the installer with the enrolment code.'}
    if node['bridge_version'] and _ver(node['bridge_version']) < (0, 42, 0):
        return {'ok': False, 'error': f"Bridge {node['bridge_version']} has no health check: update it (the node shows the command)."}
    started = datetime.now(timezone.utc)
    result: dict
    try:
        t = BridgeTransport(node['host'], node['port'], node['fingerprint'], timeout=8.0)
        try:
            t.send({'token': token(node_id, actor, 'Bridge node health check')['token'], 'mode': 'ping'})
            line = next(iter(t.lines()), b'')
        finally:
            t.close()
        answer = json.loads(line or b'{}')
        if answer.get('t') == 'pong':
            ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            skew = None
            try:
                remote = datetime.fromisoformat(answer['utc_now'].replace('Z', '+00:00'))
                remote = remote if remote.tzinfo else remote.replace(tzinfo=timezone.utc)  # the bridge prints UTC without an offset
                skew = round((remote - started).total_seconds() * 1000 - ms / 2)
            except (KeyError, ValueError):
                pass
            result = {'ok': True, 'round_trip_ms': round(ms), 'clock_skew_ms': skew, **{k: answer.get(k) for k in
                      ('version', 'machine', 'terminals_open', 'max_terminals', 'streaming')}}
        else:
            result = {'ok': False, 'error': (answer.get('attempts') or [{}])[0].get('error') or 'The bridge did not answer the health check.'}
    except BridgeError as exc:
        result = {'ok': False, 'error': str(exc)}
    except (OSError, ValueError, StopIteration) as exc:
        result = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}
    with store.transaction('write') as db:
        db.execute("""UPDATE cfg_bridge_nodes SET last_check_at=now(), last_check_ok=%s, last_check=%s,
                      bridge_version=COALESCE(%s, bridge_version),
                      status=CASE WHEN status IN ('DISABLED', 'PENDING') THEN status WHEN %s THEN 'ACTIVE' ELSE 'UNREACHABLE' END WHERE id=%s""",
                   (result['ok'], json.dumps(result), result.get('version'), result['ok'], str(node_id)))
    return result


# ---------- accounts ----------

class AttachIn(BaseModel):
    account_ids: list[UUID] | Literal['ALL_MT5'] = 'ALL_MT5'
    priority: int = Field(default=20, ge=0, le=1000)


def attach(actor: str, node_id: UUID, data: AttachIn) -> dict:
    """A terminal-bridge route through this node on each chosen MT5 account that has none yet (audited per account)."""
    with store.transaction('write') as db:
        node = db.execute('SELECT * FROM cfg_bridge_nodes WHERE id=%s', (node_id,)).fetchone()
        if not node:
            raise HTTPException(404, 'Bridge node not found.')
        q = ("SELECT a.id, a.code FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE c.platform='MT5' AND a.status <> 'ARCHIVED'")
        rows = db.execute(q + (' AND a.id = ANY(%s)' if data.account_ids != 'ALL_MT5' else '') + ' ORDER BY a.code',
                          ([list(map(str, data.account_ids))] if data.account_ids != 'ALL_MT5' else [])).fetchall()
        added, skipped = [], []
        for a in rows:
            if db.execute('SELECT 1 FROM cfg_account_routes WHERE account_id=%s AND node_id=%s', (a['id'], node_id)).fetchone():
                skipped.append(a['code'])
                continue
            label = f"Bridge {node['name']}"[:80]
            n = 2
            while db.execute('SELECT 1 FROM cfg_account_routes WHERE account_id=%s AND label=%s', (a['id'], label)).fetchone():
                label = f"Bridge {node['name']} ({n})"[:80]
                n += 1
            rid = uuid4()
            db.execute("""INSERT INTO cfg_account_routes(id, account_id, label, method, location, node_id, priority, enabled, notes, created_by, updated_by)
                          VALUES (%s,%s,%s,'TERMINAL_BRIDGE',%s,%s,%s,true,'',%s,%s)""",
                       (rid, a['id'], label, node['location'] if node['location'] in ('WINDOWS_VPS', 'WINDOWS_PC') else 'OTHER', node_id, data.priority, actor, actor))
            store.audit(db, actor, 'CREATE_ROUTE', 'account', a['id'], None, {'route': str(rid), 'account': a['code'], 'node': node['name'], 'priority': data.priority})
            added.append(a['code'])
    return {'added': added, 'already': skipped}


def detach_all(actor: str, node_id: UUID) -> dict:
    with store.transaction('delete') as db:
        rows = db.execute('DELETE FROM cfg_account_routes r USING cfg_accounts a WHERE a.id=r.account_id AND r.node_id=%s RETURNING r.*, a.code', (node_id,)).fetchall()
        for r in rows:
            store.audit(db, actor, 'DELETE_ROUTE', 'account', r['account_id'], {'route': str(r['id']), 'label': r['label'], 'node': str(node_id)}, None)
    return {'removed': [r['code'] for r in rows]}


def attach_default(actor: str, account_id: UUID) -> dict | None:
    """A new MT5 account gets a route through the default node, when one is set (Setup › Bridge nodes)."""
    try:
        with store.transaction('read') as db:
            node = db.execute("SELECT id FROM cfg_bridge_nodes WHERE default_for_new AND status <> 'DISABLED'").fetchone()
    except HTTPException:
        return None
    if not node:
        return None
    return attach(actor, node['id'], AttachIn(account_ids=[account_id]))


# ---------- tunnel keys ----------

def write_tunnel_keys() -> dict:
    """Tunnel-mode nodes: the authorized_keys lines that let each machine open ONLY its own reverse port on this
    server (no shell, no other forwarding). Written to COREI_TUNNEL_KEYS when set up (scripts/enable-bridge-tunnels.sh);
    otherwise the line is returned for an administrator to add."""
    with store.transaction('read') as db:
        rows = db.execute("SELECT name, tunnel_port, ssh_public_key FROM cfg_bridge_nodes WHERE mode='TUNNEL' AND ssh_public_key IS NOT NULL "
                          "AND status <> 'DISABLED' ORDER BY name").fetchall()
    lines = [f'restrict,port-forwarding,permitlisten="127.0.0.1:{r["tunnel_port"]}",command="/bin/false" {r["ssh_public_key"].split()[0]} '
             f'{r["ssh_public_key"].split()[1]} corei-node-{r["name"]}' for r in rows]
    path = os.getenv('COREI_TUNNEL_KEYS', '')
    if path:
        target = Path(path)
        try:
            tmp = target.with_suffix('.tmp')
            tmp.write_text('\n'.join(lines) + ('\n' if lines else ''))
            os.chmod(tmp, 0o644)  # public keys; sshd's key command (user nobody) reads it
            os.replace(tmp, target)
            return {'automatic': True, 'keys': len(lines)}
        except OSError as exc:
            return {'automatic': False, 'error': f'could not write {path}: {exc}', 'lines': lines}
    return {'automatic': False, 'lines': lines,
            'note': 'Tunnel keys are not managed automatically on this server yet: run scripts/enable-bridge-tunnels.sh once (as root).'}
