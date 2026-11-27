#!/usr/bin/env python3
"""COREI-OS-MINI updater: checks GitHub `main` for a newer release and, on an administrator's request, updates to it.

Why it exists: updating used to need SSH and five commands. The workstation must not run a deployment itself: it runs
without root, with a read-only system and home folder. This separate, fixed program (installed root-owned in
/usr/local/lib/corei-os-mini by scripts/deploy-workstation.py, run by systemd) does the work and the workstation only
leaves a request.
Purpose:
  CHECK (every 6 hours and on request): `git fetch origin main` as the repository user; write what is available —
    current and latest commit and release, the commits in between, new database migrations, the GitHub check status —
    to OUTBOX/available.json.
  APPLY (request from Settings › Update): re-check everything itself (never trusts the request beyond its id, target
    and acknowledgements), back up the database, fast-forward to exactly the requested latest commit, build, migrate,
    deploy (deploy-workstation.py health-checks and restores the service and Caddy on failure), verify the running
    commit; if anything fails after the code moved, roll the code back to the previous commit and redeploy it.
    Progress and the log go to OUTBOX/status.json and OUTBOX/update-<id>.log.
Must never: update to anything but the current tip of origin/main; move a branch other than main, a checkout with
local changes or local commits (fast-forward only); follow symbolic links in the request folder; run two updates at
once; downgrade the database (migrations are additive; a code rollback keeps them); print secrets (the env file is
read only to run the backup as the repository user).
Dependencies: git, runuser, systemd; the repository's scripts/install-workstation.sh, configure-workstation-database.py,
deploy-workstation.py; corei_os_mini.md.jobs backup.
Consumers: corei-os-mini-updater.service (path and timer units), the workstation's update module (reads OUTBOX).
Architectural position: operations, outside the application; root.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SHA = re.compile(r'^[0-9a-f]{40}$')
REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
VERSION = re.compile(r'^\d+\.\d+\.\d+$')
MAX_REQUEST = 8192
REQUEST_MAX_AGE = 15 * 60
ENV_FILE = Path('/etc/corei-os-mini/workstation.env')
PORT = 18786
# runuser lives in /usr/sbin on Ubuntu (0.67.1: without the sbin folders every check failed with "No such file: 'runuser'").
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


class Refused(Exception):
    """The update must not start; nothing was changed."""


class Updater:
    def __init__(self, repo: Path, user: str, inbox: Path, outbox: Path, runner=None):
        self.repo, self.user, self.inbox, self.outbox = repo, user, inbox, outbox
        self.pw = pwd.getpwnam(user)
        self.runner = runner or subprocess.run
        self.log_path: Path | None = None
        self.status: dict = {}

    # ----- running commands -----
    def _log(self, text: str) -> None:
        if self.log_path:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(text if text.endswith('\n') else text + '\n')

    def run(self, args: list[str], as_user: bool = True, timeout: int = 600, env: dict | None = None, check: bool = True) -> str:
        cmd = (['runuser', '-u', self.user, '--', 'env', f'HOME={self.pw.pw_dir}'] if as_user and os.geteuid() == 0 else []) + args
        self._log('$ ' + ' '.join(args))
        result = self.runner(cmd, cwd=str(self.repo), capture_output=True, text=True, timeout=timeout,
                             env=env if env is not None else {'PATH': SAFE_PATH, 'LANG': 'C.UTF-8'})
        if result.stdout:
            self._log(result.stdout[-20000:])
        if result.stderr:
            self._log(result.stderr[-20000:])
        if check and result.returncode:
            raise RuntimeError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed (exit {result.returncode}): "
                               + (result.stderr or result.stdout or '').strip()[-400:])
        return (result.stdout or '').strip()

    def git(self, *args: str, timeout: int = 120) -> str:
        return self.run(['git', '-C', str(self.repo), *args], timeout=timeout)

    # ----- outbox -----
    def write(self, name: str, data: dict) -> None:
        """Atomic, root-owned, readable by the repository user's group only."""
        tmp = self.outbox / (name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=1, default=str)
        if os.geteuid() == 0:
            os.chown(tmp, 0, self.pw.pw_gid)
        os.replace(tmp, self.outbox / name)

    def step(self, name: str, state: str, detail: str = '') -> None:
        steps = self.status.setdefault('steps', [])
        found = next((s for s in steps if s['name'] == name), None)
        if found:
            found.update(state=state, at=now(), detail=detail[:500])
        else:
            steps.append({'name': name, 'state': state, 'at': now(), 'detail': detail[:500]})
        self.write('status.json', self.status)

    # ----- request -----
    def read_request(self) -> dict | None:
        path = self.inbox / 'request.json'
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise Refused(f'The request file cannot be opened safely ({exc.strerror}).') from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST or (os.geteuid() == 0 and info.st_uid != self.pw.pw_uid):
                raise Refused('The request file is not a small regular file owned by the workstation user.')
            raw = os.read(fd, MAX_REQUEST + 1)
        finally:
            os.close(fd)
            with contextlib.suppress(OSError):
                path.unlink()
        try:
            request = json.loads(raw)
        except ValueError:
            raise Refused('The request is not valid JSON.') from None
        if not isinstance(request, dict) or request.get('kind') not in ('CHECK', 'APPLY') or not REQUEST_ID.fullmatch(str(request.get('id', ''))):
            raise Refused('The request has no valid kind and id.')
        if request['kind'] == 'APPLY':
            if not SHA.fullmatch(str(request.get('target_sha', ''))):
                raise Refused('The request names no valid target commit.')
            try:
                age = time.time() - dt.datetime.fromisoformat(request['requested_at']).timestamp()
            except (KeyError, TypeError, ValueError):
                raise Refused('The request has no valid time.') from None
            if not -60 <= age <= REQUEST_MAX_AGE:
                raise Refused('The request is older than 15 minutes; ask again so the checks are current.')
        return request

    # ----- check -----
    def check(self) -> dict:
        info: dict = {'checked_at': now(), 'error': None}
        try:
            info['branch'] = self.git('symbolic-ref', '--quiet', '--short', 'HEAD')
            info['current_sha'] = self.git('rev-parse', 'HEAD')
            info['current_version'] = (self.repo / 'VERSION').read_text().strip()
            info['dirty'] = bool(self.git('status', '--porcelain', '--untracked-files=no'))
            self.git('fetch', '--quiet', 'origin', 'main', timeout=120)
            info['latest_sha'] = self.git('rev-parse', 'origin/main')
            info['latest_version'] = self.git('show', 'origin/main:VERSION').strip()
            info['ahead'] = int(self.git('rev-list', '--count', 'origin/main..HEAD') or 0)
            info['behind'] = int(self.git('rev-list', '--count', 'HEAD..origin/main') or 0)
            info['commits'] = [dict(zip(('sha', 'date', 'subject'), line.split('\t', 2)))
                               for line in self.git('log', '--max-count=200', '--no-merges', '--format=%H%x09%cI%x09%s', 'HEAD..origin/main').splitlines() if line]
            info['migrations'] = [Path(p).name for p in self.git('diff', '--name-only', '--diff-filter=A', 'HEAD', 'origin/main', '--', 'backend/sql').splitlines() if p]
            info['ci'] = self.ci_status(info['latest_sha'])
        except Exception as exc:  # noqa: BLE001 - reported to the screen, never raised
            info['error'] = str(exc)[:500]
        self.write('available.json', info)
        return info

    def ci_status(self, sha: str) -> str:
        """SUCCESS, FAILURE, PENDING or UNKNOWN (private repository, no network, rate limit)."""
        try:
            url = self.git('remote', 'get-url', 'origin')
            m = re.search(r'github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$', url)
            if not m:
                return 'UNKNOWN'
            req = urllib.request.Request(f'https://api.github.com/repos/{m[1]}/{m[2]}/commits/{sha}/check-runs',
                                         headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'corei-updater'})
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - fixed https host
                runs = json.load(r).get('check_runs', [])
        except Exception:  # noqa: BLE001
            return 'UNKNOWN'
        if not runs:
            return 'UNKNOWN'
        if any(r.get('status') != 'completed' for r in runs):
            return 'PENDING'
        return 'SUCCESS' if all(r.get('conclusion') in ('success', 'skipped', 'neutral') for r in runs) else 'FAILURE'

    # ----- apply -----
    def env(self) -> dict:
        env = {'PATH': SAFE_PATH, 'LANG': 'C.UTF-8'}
        with contextlib.suppress(OSError):
            for line in ENV_FILE.read_text().splitlines():
                key, sep, value = line.strip().partition('=')
                if sep and re.fullmatch(r'[A-Z_][A-Z0-9_]*', key):
                    env[key] = value.strip().strip('"').strip("'")
        return env

    def preflight(self, request: dict, info: dict) -> None:
        if info.get('error'):
            raise Refused('Could not check GitHub: ' + info['error'])
        if info.get('branch') != 'main':
            raise Refused(f"The checkout is on branch {info.get('branch') or '(detached)'}, not main.")
        if info.get('dirty'):
            raise Refused('The checkout has local changes; update by hand or discard them first.')
        if info.get('ahead'):
            raise Refused('The checkout has local commits that are not on GitHub; a fast-forward is impossible.')
        if not info.get('behind'):
            raise Refused('Already up to date.')
        if request['target_sha'] != info.get('latest_sha'):
            raise Refused('GitHub main has moved since the request; check again and confirm the new version.')
        if not VERSION.fullmatch(info.get('latest_version') or ''):
            raise Refused('The latest commit has no valid VERSION.')
        ci = info.get('ci')
        if ci == 'FAILURE':
            raise Refused('GitHub checks failed for this commit.')
        if ci != 'SUCCESS' and not request.get('allow_unverified_ci'):
            raise Refused(f'GitHub checks are {ci}; confirm "update without a passing check" to continue.')

    def backup(self, info: dict, request: dict) -> None:
        self.step('Back up database', 'RUNNING')
        env = self.env()
        if not env.get('COREI_BACKUP_DIR'):
            if info.get('migrations') and not request.get('allow_without_backup'):
                raise Refused('This update changes the database and backups are off (COREI_BACKUP_DIR); confirm "update without a backup".')
            self.step('Back up database', 'SKIPPED', 'COREI_BACKUP_DIR is not set')
            return
        out = self.run([str(self.repo / '.venv-workstation/bin/python'), '-m', 'corei_os_mini.md.jobs', 'backup'],
                       timeout=3600, env=env, check=False)
        try:
            done = json.loads(out[out.index('{'):]).get('database')
        except ValueError:
            done = None
        if not done:
            if info.get('migrations') and not request.get('allow_without_backup'):
                raise Refused('The database backup failed; nothing was changed. See the log.')
            self.step('Back up database', 'FAILED', 'continuing: no database change in this update')
            return
        self.step('Back up database', 'DONE', str(done.get('path')))

    def deploy(self) -> None:
        self.step('Build', 'RUNNING')
        self.run(['bash', 'scripts/install-workstation.sh'], timeout=3600)
        self.step('Build', 'DONE')
        self.step('Database migrations', 'RUNNING')
        self.run(['python3', 'scripts/configure-workstation-database.py'], as_user=False, timeout=1800)
        self.step('Database migrations', 'DONE')
        self.step('Deploy and restart', 'RUNNING')
        self.run(['python3', 'scripts/deploy-workstation.py', str(self.repo), self.user], as_user=False, timeout=1800)
        self.step('Deploy and restart', 'DONE')

    def running_commit(self) -> str:
        for _ in range(30):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/workstation', timeout=3) as r:  # noqa: S310 - loopback
                    return str(json.load(r).get('commit') or '')
            except Exception:  # noqa: BLE001 - restarting
                time.sleep(2)
        return ''

    def apply(self, request: dict) -> None:
        self.status = {'id': request['id'], 'kind': 'APPLY', 'state': 'RUNNING', 'requested_by': request.get('requested_by'),
                       'reason': request.get('reason'), 'started_at': now(), 'steps': [], 'log': self.log_path.name if self.log_path else None}
        self.step('Check GitHub', 'RUNNING')
        info = self.check()
        self.status.update(from_sha=info.get('current_sha'), from_version=info.get('current_version'),
                           to_sha=info.get('latest_sha'), to_version=info.get('latest_version'))
        try:
            self.preflight(request, info)
            self.step('Check GitHub', 'DONE', f"{info['current_version']} → {info['latest_version']} · {info['behind']} commit(s) · checks {info['ci']}")
            self.backup(info, request)
        except Refused as exc:
            self.step('Check GitHub' if not any(s['name'] == 'Back up database' for s in self.status['steps']) else 'Back up database', 'REFUSED', str(exc))
            self.finish('REFUSED', str(exc))
            return
        previous = info['current_sha']
        try:
            self.step('Update code', 'RUNNING')
            self.git('merge', '--ff-only', request['target_sha'])
            self.step('Update code', 'DONE', request['target_sha'][:12])
            self.deploy()
            self.step('Verify', 'RUNNING')
            running = self.running_commit()
            if not running or not request['target_sha'].startswith(running):
                raise RuntimeError(f'The service reports commit {running or "nothing"}, expected {request["target_sha"][:12]}.')
            self.step('Verify', 'DONE', f'running {running}')
            self.finish('SUCCEEDED')
        except Exception as exc:  # noqa: BLE001 - roll back the code, keep the database
            failed = str(exc)[:500]
            self._log('FAILED: ' + failed)
            try:
                self.step('Roll back', 'RUNNING', f'to {previous[:12]}')
                self.git('reset', '--hard', previous)
                self.deploy()
                self.step('Roll back', 'DONE', f'back on {previous[:12]}; database changes kept (additive)')
                self.finish('ROLLED_BACK', failed)
            except Exception as again:  # noqa: BLE001
                self.step('Roll back', 'FAILED', str(again))
                self.finish('FAILED', f'{failed} · Rollback also failed: {again}. Run by hand: git reset --hard {previous[:12]} && bash scripts/deploy-workstation.sh')

    def finish(self, state: str, error: str | None = None) -> None:
        self.status.update(state=state, error=error, finished_at=now())
        self.write('status.json', self.status)
        with contextlib.suppress(Exception):
            self.check()

    # ----- entry -----
    def handle(self) -> int:
        try:
            request = self.read_request()
        except Refused as exc:
            self.status = {'id': 'invalid', 'kind': 'APPLY', 'state': 'REFUSED', 'error': str(exc), 'started_at': now(), 'finished_at': now(), 'steps': []}
            self.write('status.json', self.status)
            return 1
        if not request or request['kind'] == 'CHECK':
            self.check()
            return 0
        self.log_path = self.outbox / f"update-{request['id']}.log"
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
        os.close(fd)
        if os.geteuid() == 0:
            os.chown(self.log_path, 0, self.pw.pw_gid)
        self.apply(request)
        return 0 if self.status.get('state') == 'SUCCEEDED' else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='COREI-OS-MINI updater (run by systemd)')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--inbox', default='/var/lib/corei-os-mini/update/inbox')
    parser.add_argument('--outbox', default='/var/lib/corei-os-mini/update/outbox')
    args = parser.parse_args()
    if os.geteuid() != 0:
        sys.exit('The updater runs as root from systemd.')
    repo = Path(args.repo).resolve()
    if not (repo / '.git').is_dir() or not re.fullmatch(r'[A-Za-z0-9_./-]+', str(repo)):
        sys.exit('Not a repository path.')
    with open('/run/corei-os-mini-updater.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('Another update is running.')
            return 0
        return Updater(repo, args.user, Path(args.inbox), Path(args.outbox)).handle()


if __name__ == '__main__':
    sys.exit(main())
