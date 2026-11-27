"""Settings › Update: see whether GitHub `main` has a newer release, and ask the updater to install it.

Why it exists: updating needed SSH and five commands. The workstation must not deploy itself (it runs without root, with
a read-only system); it only checks that an update is safe now and leaves a request for the root updater
(scripts/corei-updater.py), then shows the updater's progress and records the outcome.
Purpose:
  - state(): what is running, what is available (outbox/available.json), the last update (outbox/status.json), the
    blockers and the acknowledgements an update needs now, and whether the updater is installed.
  - request_check(): ask for a fresh GitHub check.
  - request_apply(): administrator, re-authenticated (route), reason, typed UPDATE, the exact latest commit, every
    acknowledgement; refused while an order's outcome is unknown or awaits the broker, while another update runs, or
    when the checkout cannot fast-forward. Audited in the same transaction; alert.
  - Outcomes reported by the updater are audited once each (UPDATE_SUCCEEDED / _ROLLED_BACK / _FAILED / _REFUSED).
Must never: run git, a build or a deployment; write anywhere but the inbox; accept a target other than the latest
commit the updater found; hide that trading is interrupted for a minute or two during the restart.
Dependencies: config_store (audit, transaction), alerts, platform_mode, the updater's inbox/outbox folders.
Consumers: controls_api (/api/config/update…), ui/src/UpdatePanel.tsx.
Architectural position: operations; Stage 7 · Settings.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import config_store as store

ROOT = Path(os.environ.get('COREI_UPDATE_ROOT', '/var/lib/corei-os-mini/update'))
FINAL = ('SUCCEEDED', 'ROLLED_BACK', 'FAILED', 'REFUSED')
IN_FLIGHT = ('SUBMITTED', 'CANCEL_PENDING', 'UNKNOWN')
ACKS = {
    'RESTART': 'The workstation, market-data collector and order sending restart: about 1–3 minutes without prices or order handling.',
    'LIVE_SENDING_ON': 'LIVE order sending is ON: strategies may want to send LIVE orders during the restart.',
    'PRODUCTION': 'The platform is in Production mode.',
    'DEPLOYMENTS_RUNNING': 'Strategy deployments are running; they resume after the restart.',
    'CI_NOT_PASSED': 'GitHub checks have not passed (pending or unknown) for this commit.',
    'NO_BACKUP': 'This update changes the database and backups are off (COREI_BACKUP_DIR).',
}


def _read(name: str) -> dict | None:
    try:
        with open(ROOT / 'outbox' / name, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def log_tail(update_id: str, lines: int = 80) -> str:
    if not update_id.replace('-', '').replace('_', '').isalnum():
        return ''
    try:
        text = (ROOT / 'outbox' / f'update-{update_id}.log').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    return '\n'.join(text.splitlines()[-lines:])


def _pending_request() -> dict | None:
    try:
        return json.loads((ROOT / 'inbox' / 'request.json').read_text())
    except (OSError, ValueError):
        return None


def _record_outcome(status: dict | None) -> None:
    """Audit each finished update once, with its outcome."""
    if not status or status.get('state') not in FINAL or status.get('kind') != 'APPLY' or not status.get('id'):
        return
    with store.transaction('write') as db:
        if db.execute("SELECT 1 FROM cfg_audit_log WHERE entity='platform update' AND entity_id=%s AND action LIKE 'UPDATE_%%' "
                      "AND action <> 'UPDATE_REQUESTED'", (status['id'],)).fetchone():
            return
        store.audit(db, 'updater', f"UPDATE_{status['state']}", 'platform update', status['id'],
                    {'version': status.get('from_version'), 'commit': status.get('from_sha')},
                    {'version': status.get('to_version'), 'commit': status.get('to_sha'), 'error': status.get('error')},
                    status.get('reason') or '')
        from .alerts import enqueue
        enqueue(db, 'LIVE_CHANGE', 'INFO' if status['state'] == 'SUCCEEDED' else 'WARNING',
                f"Update {status['state'].lower().replace('_', ' ')}: {status.get('from_version')} → {status.get('to_version')}",
                status.get('error') or 'The workstation runs the new release.')


def _conditions(db, available: dict | None) -> tuple[list[str], list[str]]:
    """(blockers, acknowledgements needed)."""
    from . import platform_mode
    blockers, acks = [], ['RESTART']
    n = db.execute('SELECT count(*) AS n FROM oms_orders WHERE state = ANY(%s)', (list(IN_FLIGHT),)).fetchone()['n']
    if n:
        blockers.append(f'{n} order(s) are waiting for the broker or have an unknown outcome (Submitted, Cancel pending, Unknown). '
                        'Resolve them first: a restart must not interrupt them.')
    if db.execute("SELECT 1 FROM cfg_execution_controls WHERE environment='LIVE' AND trading_enabled").fetchone():
        acks.append('LIVE_SENDING_ON')
    if platform_mode.get(db)['mode'] == 'PRODUCTION':
        acks.append('PRODUCTION')
    if db.execute("SELECT 1 FROM cfg_deployments WHERE status='ACTIVE'").fetchone():
        acks.append('DEPLOYMENTS_RUNNING')
    if available:
        if available.get('error'):
            blockers.append('The last GitHub check failed: ' + str(available['error']))
        if available.get('branch') not in (None, 'main'):
            blockers.append(f"The server's checkout is on branch {available['branch']}, not main.")
        if available.get('dirty'):
            blockers.append("The server's checkout has local changes.")
        if available.get('ahead'):
            blockers.append("The server's checkout has commits that are not on GitHub.")
        if available.get('ci') == 'FAILURE':
            blockers.append('GitHub checks failed for the latest commit.')
        elif available.get('ci') in ('PENDING', 'UNKNOWN'):
            acks.append('CI_NOT_PASSED')
        if available.get('migrations') and not os.environ.get('COREI_BACKUP_DIR'):
            acks.append('NO_BACKUP')
    return blockers, acks


def state() -> dict:
    available, status = _read('available.json'), _read('status.json')
    _record_outcome(status)
    from .api import build_identity
    identity = build_identity()
    installed = (ROOT / 'outbox').is_dir() and (ROOT / 'inbox').is_dir()
    running = bool(status and status.get('state') == 'RUNNING') or bool((_pending_request() or {}).get('kind') == 'APPLY')
    with store.transaction('read') as db:
        blockers, acks = _conditions(db, available)
        history = [dict(r) for r in db.execute(
            "SELECT occurred_at AS at, actor, action, entity_id, before_state, after_state, reason FROM cfg_audit_log WHERE entity='platform update' "
            'ORDER BY id DESC LIMIT 20').fetchall()]
    newer = bool(available and available.get('behind'))
    if not installed:
        blockers.insert(0, 'The updater is not installed on this server yet. Run the update commands once by hand; later updates can start here.')
    if running:
        blockers.insert(0, 'An update is already running.')
    if not newer:
        blockers.append('No newer version on GitHub main.' if available else 'Not checked yet: press Check now.')
    return {'running': {'version': identity.get('release'), 'commit': identity.get('commit'), 'migration': identity.get('migration')},
            'installed': installed, 'available': available, 'status': status, 'update_running': running,
            'log': log_tail(status['id']) if status and status.get('id') and status.get('kind') == 'APPLY' else '',
            'blockers': blockers, 'acknowledgements': [{'code': a, 'text': ACKS[a]} for a in acks],
            'can_update': not blockers and newer and installed, 'history': history,
            'manual_commands': ['cd ~/corei-os-mini', 'git fetch origin', 'git checkout main', 'git merge --ff-only origin/main',
                                'cat VERSION', 'bash scripts/deploy-workstation.sh']}


def _write_request(request: dict) -> None:
    inbox = ROOT / 'inbox'
    if not inbox.is_dir():
        raise HTTPException(409, 'The updater is not installed on this server yet. Run the update commands once by hand.')
    tmp = inbox / f'.request-{request["id"]}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(request, f)
    os.replace(tmp, inbox / 'request.json')


def request_check(actor: str) -> dict:
    if (_pending_request() or {}).get('kind') == 'APPLY':
        raise HTTPException(409, 'An update is already waiting to start.')
    _write_request({'id': 'chk-' + secrets.token_hex(6), 'kind': 'CHECK', 'requested_by': actor,
                    'requested_at': datetime.now(timezone.utc).isoformat()})
    return {'requested': True}


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_sha: str = Field(pattern=r'^[0-9a-f]{40}$')
    reason: str = Field(min_length=5, max_length=500)
    confirm: str = Field(max_length=20)
    acknowledged: list[str] = Field(default_factory=list, max_length=10)


def request_apply(actor: str, data: ApplyIn) -> dict:
    if data.confirm != 'UPDATE':
        raise HTTPException(422, 'To update, confirm by typing UPDATE.')
    current = state()
    if current['blockers']:
        raise HTTPException(409, 'The update cannot start: ' + ' '.join(current['blockers']))
    available = current['available'] or {}
    if data.target_sha != available.get('latest_sha'):
        raise HTTPException(409, 'GitHub main has a different latest commit now. Check again and confirm that version.')
    missing = [a['code'] for a in current['acknowledgements'] if a['code'] not in data.acknowledged]
    if missing:
        raise HTTPException(422, 'Confirm each point before updating: ' + ', '.join(missing))
    update_id = 'upd-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + secrets.token_hex(3)
    request = {'id': update_id, 'kind': 'APPLY', 'target_sha': data.target_sha, 'target_version': available.get('latest_version'),
               'requested_by': actor, 'reason': data.reason, 'requested_at': datetime.now(timezone.utc).isoformat(),
               'allow_unverified_ci': 'CI_NOT_PASSED' in data.acknowledged, 'allow_without_backup': 'NO_BACKUP' in data.acknowledged}
    with store.transaction('write') as db:
        # Checked again inside the transaction that records the request.
        blockers, _ = _conditions(db, available)
        if blockers:
            raise HTTPException(409, 'The update cannot start: ' + ' '.join(blockers))
        store.audit(db, actor, 'UPDATE_REQUESTED', 'platform update', update_id,
                    {'version': current['running']['version'], 'commit': current['running']['commit']},
                    {'version': available.get('latest_version'), 'commit': data.target_sha, 'acknowledged': sorted(data.acknowledged)},
                    data.reason)
        from .alerts import enqueue
        enqueue(db, 'LIVE_CHANGE', 'WARNING', f"Update requested: {current['running']['version']} → {available.get('latest_version')}",
                f'By {actor}. The workstation restarts for 1–3 minutes. Reason: {data.reason}')
        _write_request(request)
    return {'id': update_id, 'target_version': available.get('latest_version')}
