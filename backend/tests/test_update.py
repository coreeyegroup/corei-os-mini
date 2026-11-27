"""Settings › Update (release 0.67.0): the workstation side and the root updater.

Evidence for:
  Workstation: only an administrator sees or requests updates; without the updater installed, or before a check, nothing
  can start; a check request is a file in the inbox; an update needs the password again (428), typed UPDATE, the exact
  latest commit and every acknowledgement (restart, Production, LIVE sending, unverified checks); it is refused while an
  order's outcome is unknown; the request is audited with the alert; outcomes are audited exactly once.
  Updater (scripts/corei-updater.py, real git repositories, deploy steps faked): it lists commits and new migrations;
  refuses a moved target, local changes, local commits, a failed check, an old or symlinked request; fast-forwards and
  verifies; when a step fails after the code moved it rolls the code back to the previous commit and redeploys.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

te = importlib.import_module('test_execution') if importlib.util.find_spec('test_execution') else importlib.import_module('tests.test_execution')
tc = importlib.import_module('test_controls') if importlib.util.find_spec('test_controls') else importlib.import_module('tests.test_controls')
from corei_os_mini import update  # noqa: E402
from corei_os_mini.operator_auth import hash_password  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('corei_updater', REPO / 'scripts' / 'corei-updater.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)
SHA = 'a' * 40


class Workstation(te.Execution):
    test_sending_disabled_during_precheck = test_real_orders_on_demo_every_path = test_four_eyes_on_live = None
    test_risk_extras = test_portfolio_extras = test_optimise_walk_forward_and_drift = None

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        patcher = mock.patch.object(update, 'ROOT', root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.root = root

    def install(self, **available):
        (self.root / 'inbox').mkdir()
        (self.root / 'outbox').mkdir()
        if available is not None:
            info = {'checked_at': '2026-11-27T18:00:00+00:00', 'error': None, 'branch': 'main', 'dirty': False, 'ahead': 0, 'behind': 3,
                    'current_sha': 'b' * 40, 'current_version': '0.66.2', 'latest_sha': SHA, 'latest_version': '0.67.0', 'ci': 'SUCCESS',
                    'migrations': [], 'commits': [{'sha': SHA, 'date': '2026-11-27T18:00:00+00:00', 'subject': 'Settings › Update'}]}
            info.update(available)
            (self.root / 'outbox' / 'available.json').write_text(json.dumps(info))

    def apply(self, status=200, **body):
        payload = {'target_sha': SHA, 'reason': 'install the update', 'confirm': 'UPDATE', 'acknowledged': ['RESTART', 'PRODUCTION'], **body}
        r = self.alice.post('/api/config/update/apply', json=payload)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_nothing_starts_before_install_or_check_and_only_administrators(self):
        s = self.ok(self.alice.get('/api/config/update'))
        self.assertFalse(s['installed'])
        self.assertTrue(s['blockers'][0].startswith('The updater is not installed'))
        self.assertIn('git merge --ff-only origin/main', s['manual_commands'])
        self.ok(self.alice.post('/api/config/update/check'), 409)
        with self.connect() as db:
            db.execute("INSERT INTO cfg_users(id, username, display_name, password_hash, roles, must_change_password) "
                       "VALUES (%s,'carol','Carol',%s,ARRAY['RISK_APPROVER','TRADING_OPS'],false)", (uuid4(), hash_password(tc.PASSWORD)))
        self.ok(self.sign_in('carol').get('/api/config/update'), 403)
        (self.root / 'inbox').mkdir()
        (self.root / 'outbox').mkdir()
        s = self.ok(self.alice.get('/api/config/update'))
        self.assertIn('Not checked yet: press Check now.', s['blockers'])
        self.ok(self.alice.post('/api/config/update/check'))
        self.assertEqual(json.loads((self.root / 'inbox' / 'request.json').read_text())['kind'], 'CHECK')

    def test_update_request_needs_password_confirmation_acknowledgements_and_quiet_orders(self):
        self.install()
        s = self.ok(self.alice.get('/api/config/update'))
        self.assertTrue(s['can_update'], s['blockers'])
        self.assertEqual([a['code'] for a in s['acknowledgements']], ['RESTART', 'PRODUCTION'])
        self.apply(428)
        self.ok(self.alice.post('/api/operator/reauth', json={'password': tc.PASSWORD}))
        self.apply(422, confirm='update')
        self.apply(422, acknowledged=['RESTART'])
        self.apply(409, target_sha='c' * 40)
        # An order whose outcome is unknown blocks the restart.
        _connection, account, _b, ins = self.real_account('DEMO')
        order = self.place(account, ins, 'upd-1')
        with self.connect() as db:
            db.execute("UPDATE oms_orders SET state='UNKNOWN' WHERE id=%s", (order['id'],))
        self.assertTrue(any('unknown outcome' in b for b in self.ok(self.alice.get('/api/config/update'))['blockers']))
        self.apply(409)
        with self.connect() as db:
            db.execute("UPDATE oms_orders SET state='CANCELLED' WHERE id=%s", (order['id'],))
        done = self.apply(200)
        request = json.loads((self.root / 'inbox' / 'request.json').read_text())
        self.assertEqual((request['id'], request['kind'], request['target_sha'], request['target_version'], request['requested_by'], request['allow_unverified_ci']),
                         (done['id'], 'APPLY', SHA, '0.67.0', 'alice', False))
        self.assertEqual(oct((self.root / 'inbox' / 'request.json').stat().st_mode & 0o777), '0o600')
        audit = self.ok(self.alice.get('/api/config/audit', params={'entity': 'platform update', 'entity_id': done['id']}))['items']
        self.assertEqual([a['action'] for a in audit], ['UPDATE_REQUESTED'])
        # While the request waits, a second one is refused.
        self.assertTrue(self.ok(self.alice.get('/api/config/update'))['update_running'])
        self.apply(409)
        # The updater's outcome is audited once, however often the screen reads it.
        (self.root / 'inbox' / 'request.json').unlink()
        (self.root / 'outbox' / 'status.json').write_text(json.dumps({'id': done['id'], 'kind': 'APPLY', 'state': 'ROLLED_BACK', 'from_version': '0.66.2',
                                                                       'to_version': '0.67.0', 'error': 'Build failed', 'steps': []}))
        (self.root / 'outbox' / f"update-{done['id']}.log").write_text('$ git merge\nbuild failed\n')
        for _ in range(3):
            s = self.ok(self.alice.get('/api/config/update'))
        self.assertEqual(s['log'], '$ git merge\nbuild failed')
        audit = self.ok(self.alice.get('/api/config/audit', params={'entity': 'platform update', 'entity_id': done['id']}))['items']
        self.assertEqual(sorted(a['action'] for a in audit), ['UPDATE_REQUESTED', 'UPDATE_ROLLED_BACK'])

    def test_unverified_checks_and_missing_backup_need_their_own_acknowledgement(self):
        self.install(ci='UNKNOWN', migrations=['053_platform_mode.sql'])
        with mock.patch.dict(os.environ, {'COREI_BACKUP_DIR': ''}):
            acks = [a['code'] for a in self.ok(self.alice.get('/api/config/update'))['acknowledgements']]
            self.assertEqual(acks, ['RESTART', 'PRODUCTION', 'CI_NOT_PASSED', 'NO_BACKUP'])
            self.ok(self.alice.post('/api/operator/reauth', json={'password': tc.PASSWORD}))
            self.apply(422)
            self.apply(200, acknowledged=acks)
        request = json.loads((self.root / 'inbox' / 'request.json').read_text())
        self.assertEqual((request['allow_unverified_ci'], request['allow_without_backup']), (True, True))

    def test_failed_checks_or_unclean_checkout_block(self):
        self.install(ci='FAILURE', dirty=True, ahead=1)
        blockers = self.ok(self.alice.get('/api/config/update'))['blockers']
        self.assertTrue(any('local changes' in b for b in blockers))
        self.assertTrue(any('not on GitHub' in b for b in blockers))
        self.assertTrue(any('checks failed' in b for b in blockers))


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class Updater(unittest.TestCase):
    """The real updater against real git repositories; build, migrations and deploy are faked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        origin, self.repo = base / 'origin.git', base / 'repo'
        subprocess.run(['git', 'init', '--bare', '-b', 'main', str(origin)], check=True, capture_output=True)
        seed = base / 'seed'
        subprocess.run(['git', 'init', '-b', 'main', str(seed)], check=True, capture_output=True)
        for k, v in (('user.email', 't@example.com'), ('user.name', 'T')):
            git(seed, 'config', k, v)
        (seed / 'VERSION').write_text('0.66.2\n')
        (seed / 'backend' / 'sql').mkdir(parents=True)
        (seed / 'backend' / 'sql' / '053_platform_mode.sql').write_text('--\n')
        git(seed, 'add', '.')
        git(seed, 'commit', '-m', 'release 0.66.2')
        git(seed, 'remote', 'add', 'origin', str(origin))
        git(seed, 'push', '-q', 'origin', 'main')
        subprocess.run(['git', 'clone', '-q', str(origin), str(self.repo)], check=True, capture_output=True)
        for k, v in (('user.email', 't@example.com'), ('user.name', 'T')):
            git(self.repo, 'config', k, v)
        self.previous = git(self.repo, 'rev-parse', 'HEAD')
        (seed / 'VERSION').write_text('0.67.0\n')
        (seed / 'backend' / 'sql' / '054_update.sql').write_text('--\n')
        git(seed, 'commit', '-qam', 'x')
        git(seed, 'add', '.')
        git(seed, 'commit', '-qm', 'Settings › Update')
        git(seed, 'push', '-q', 'origin', 'main')
        self.latest = git(seed, 'rev-parse', 'HEAD')
        self.inbox, self.outbox = base / 'inbox', base / 'outbox'
        self.inbox.mkdir()
        self.outbox.mkdir()
        self.calls = []

    def runner(self, fail=None):
        def run(cmd, **kw):
            args = cmd[cmd.index('--') + 3:] if cmd[0] == 'runuser' else cmd   # runuser -u USER -- env HOME=… <command>
            if args[0] == 'git':
                return subprocess.run(args, **kw)
            self.calls.append(' '.join(args[:2]))
            if fail and fail in ' '.join(args):
                return subprocess.CompletedProcess(args, 1, '', f'{fail} broke')
            return subprocess.CompletedProcess(args, 0, '{"database": {"path": "/b/x.dump"}}' if 'jobs' in ' '.join(args) else '', '')
        return run

    def make(self, fail=None):
        u = updater.Updater(self.repo, os.environ.get('USER') or 'root', self.inbox, self.outbox, runner=self.runner(fail))
        u.ci_status = lambda sha: 'SUCCESS'
        u.running_commit = lambda: git(self.repo, 'rev-parse', '--short=12', 'HEAD')
        u.env = lambda: {'PATH': os.environ['PATH'], 'COREI_BACKUP_DIR': '/b'}
        return u

    def request(self, **extra):
        body = {'id': 'upd-test-0001', 'kind': 'APPLY', 'target_sha': self.latest, 'requested_by': 'alice', 'reason': 'test',
                'requested_at': datetime.now(timezone.utc).isoformat(), **extra}
        (self.inbox / 'request.json').write_text(json.dumps(body))

    def status(self):
        return json.loads((self.outbox / 'status.json').read_text())

    def test_check_lists_commits_and_new_migrations(self):
        info = self.make().check()
        self.assertEqual((info['behind'], info['ahead'], info['latest_version'], info['latest_sha'], info['dirty']), (2, 0, '0.67.0', self.latest, False))
        self.assertEqual(info['migrations'], ['054_update.sql'])
        self.assertEqual([c['subject'] for c in info['commits']], ['Settings › Update', 'x'])
        self.assertEqual(json.loads((self.outbox / 'available.json').read_text())['latest_sha'], self.latest)

    def test_update_fast_forwards_builds_migrates_deploys_and_verifies(self):
        self.request()
        self.assertEqual(self.make().handle(), 0)
        s = self.status()
        self.assertEqual((s['state'], s['from_version'], s['to_version']), ('SUCCEEDED', '0.66.2', '0.67.0'))
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.latest)
        self.assertEqual([x['name'] for x in s['steps']],
                         ['Check GitHub', 'Back up database', 'Update code', 'Build', 'Database migrations', 'Deploy and restart', 'Verify'])
        self.assertTrue(all(x['state'] == 'DONE' for x in s['steps']))
        self.assertFalse((self.inbox / 'request.json').exists(), 'the request is consumed')
        self.assertTrue((self.outbox / 'update-upd-test-0001.log').read_text().startswith('$ git'))

    def test_failure_after_the_code_moved_rolls_back(self):
        self.request()
        self.assertEqual(self.make(fail='deploy-workstation.py').handle(), 1)
        s = self.status()
        self.assertEqual(s['state'], 'FAILED', 'the rollback deploy fails the same way here')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.previous, 'the code went back to the previous commit')
        self.assertIn('git reset --hard', s['error'])
        self.request(id='upd-test-0002')
        u = self.make()
        u.deploy = mock.Mock(side_effect=[RuntimeError('build broke'), None])   # the new release fails; the old one redeploys
        u.handle()
        s = self.status()
        self.assertEqual(s['state'], 'ROLLED_BACK')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.previous)
        self.assertEqual(s['steps'][-1]['name'], 'Roll back')

    def test_refusals_change_nothing(self):
        cases = [
            ({'target_sha': 'c' * 40}, 'moved since the request'),
            ({'requested_at': (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()}, 'older than 15 minutes'),
        ]
        for extra, words in cases:
            self.request(**extra)
            self.make().handle()
            self.assertIn(words, self.status()['error'])
            self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.previous)
        (self.repo / 'VERSION').write_text('local edit\n')
        self.request()
        self.make().handle()
        self.assertIn('local changes', self.status()['error'])
        git(self.repo, 'checkout', 'VERSION')
        u = self.make()
        u.ci_status = lambda sha: 'FAILURE'
        self.request()
        u.handle()
        self.assertIn('checks failed', self.status()['error'])
        u = self.make()
        u.ci_status = lambda sha: 'UNKNOWN'
        self.request()
        u.handle()
        self.assertIn('confirm "update without a passing check"', self.status()['error'])
        self.assertEqual(self.calls, [], 'no build, migration or deployment ran')

    def test_symlinked_request_is_refused(self):
        target = Path(self.tmp.name) / 'elsewhere.json'
        target.write_text('{}')
        (self.inbox / 'request.json').symlink_to(target)
        self.assertEqual(self.make().handle(), 1)
        self.assertIn('cannot be opened safely', self.status()['error'])
