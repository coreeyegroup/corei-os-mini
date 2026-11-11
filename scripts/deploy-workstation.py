"""Deploy the new read-only workstation; preserve other Caddy sites and old apps."""
from pathlib import Path
import datetime
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HOST = 'corei-os-mini.coreeye.in'
SERVICE = 'corei-os-mini-workstation'
COLLECTOR = 'corei-os-mini-collector'
NIGHTLY = 'corei-os-mini-md-nightly'
PORT = 18786
MD_ROOT = Path('/var/lib/corei-os-mini/market-data')
ENV_FILE = Path('/etc/corei-os-mini/workstation.env')


def replace_site(text, block):
    """Replace only an unambiguous standalone host block; ignore placeholders."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines)
              if re.fullmatch(r'\s*' + re.escape(HOST) + r'\s*\{\s*', line)]
    mentions = [i for i, line in enumerate(lines)
                if HOST in line.split('#', 1)[0]]
    if len(starts) > 1 or mentions != starts:
        raise RuntimeError('Ambiguous domain configuration; no Caddy change made.')
    if not starts:
        return text.rstrip() + '\n\n' + block
    start = starts[0]
    depth = 0
    for end in range(start, len(lines)):
        code = lines[end].split('#', 1)[0]
        # Caddy placeholders such as {path} are not block delimiters.
        tokens = re.findall(r'(?<!\S)[{}](?!\S)', code)
        depth += tokens.count('{') - tokens.count('}')
        if depth == 0:
            return ''.join(lines[:start]) + block + ''.join(lines[end + 1:])
    raise RuntimeError('Unclosed domain block; no Caddy change made.')


def run(*args):
    return subprocess.run(args, check=True)


def env_value(name):
    """A value from the workstation env file (KEY=value lines), or None."""
    try:
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.strip().partition('=')
            if key == name and value.strip():
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def install_market_data(root, user, python):
    """The market-data collector service and the nightly archive/verify/backup timer.
    Data lives in /var/lib/corei-os-mini/market-data (owner: the repository user, 0750);
    backups go to COREI_BACKUP_DIR from the env file when it is set."""
    account = pwd.getpwnam(user)
    # The parent is only passed through (0755, root); the data folder itself is private to the service user.
    MD_ROOT.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(MD_ROOT.parent, 0o755)
    MD_ROOT.mkdir(mode=0o750, exist_ok=True)
    os.chown(MD_ROOT, account.pw_uid, account.pw_gid)
    os.chmod(MD_ROOT, 0o750)
    writable = [str(MD_ROOT)]
    backup_dir = env_value('COREI_BACKUP_DIR')
    if backup_dir:
        target = Path(backup_dir)
        if not target.is_absolute() or not re.fullmatch(r'[A-Za-z0-9_./-]+', backup_dir):
            print('COREI_BACKUP_DIR must be an absolute path without special characters; backups stay off.')
        else:
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chown(target, account.pw_uid, account.pw_gid)
            writable.append(str(target))
    common = f'''User={user}
WorkingDirectory={root}/backend
Environment=COREI_MD_ROOT={MD_ROOT}
EnvironmentFile=-{ENV_FILE}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={' '.join(writable)}
UMask=0027
'''
    units = {
        COLLECTOR + '.service': f'''[Unit]
Description=COREI-OS-MINI market-data collector
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
{common}ExecStart={python} -m corei_os_mini.md.collector
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
''',
        NIGHTLY + '.service': f'''[Unit]
Description=COREI-OS-MINI market-data archive, fingerprint check and backup
After=postgresql.service

[Service]
Type=oneshot
{common}ExecStart={python} -m corei_os_mini.md.jobs nightly
TimeoutStartSec=6h
''',
        NIGHTLY + '.timer': '''[Unit]
Description=Nightly COREI-OS-MINI market-data jobs (02:30 IST)

[Timer]
OnCalendar=*-*-* 21:00:00 UTC
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
''',
    }
    for name, text in units.items():
        Path('/etc/systemd/system/' + name).write_text(text)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', COLLECTOR, NIGHTLY + '.timer')
    run('systemctl', 'restart', COLLECTOR)
    run('systemctl', 'start', NIGHTLY + '.timer')
    print('Market data: collector', COLLECTOR, 'running; nightly jobs at 02:30 IST;',
          'backups to ' + backup_dir if backup_dir and len(writable) > 1 else 'backups OFF until COREI_BACKUP_DIR is set in ' + str(ENV_FILE))


def main():
    if os.geteuid() != 0:
        sys.exit('Use bash scripts/deploy-workstation.sh')
    root = Path(sys.argv[1]).resolve()
    user = pwd.getpwnam(sys.argv[2]).pw_name
    if user == 'root' or not re.fullmatch(r'[A-Za-z0-9_./-]+', str(root)):
        sys.exit('Expected a normal user and a repository path without special characters.')
    python = root / '.venv-workstation/bin/python'
    dist = root / 'ui/dist'
    if not python.exists() or not (dist / 'index.html').is_file():
        sys.exit('Build the workstation before deployment.')
    config = Path('/etc/caddy/Caddyfile')
    original = config.read_text()
    candidate = replace_site(original, (root / 'deploy/workstation.Caddyfile').read_text())
    unit = Path('/etc/systemd/system/' + SERVICE + '.service')
    old_unit = unit.read_bytes() if unit.exists() else None
    active = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0
    enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', SERVICE]).returncode == 0
    if old_unit is None:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', PORT))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + str(os.getpid())
    backup = config.with_name('Caddyfile.corei-os-mini-workstation.' + stamp + '.bak')
    shutil.copy2(config, backup)
    if old_unit is not None:
        unit.with_name(unit.name + '.' + stamp + '.bak').write_bytes(old_unit)
    base = Path('/var/www/corei-os-mini-workstation')
    release = base / 'releases' / stamp
    release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist, release)
    for entry in [release, *release.rglob('*')]:
        if entry.is_symlink():
            sys.exit('Unexpected symlink in frontend build; deployment stopped.')
        entry.chmod(0o755 if entry.is_dir() else 0o644)
    current = base / 'current'
    if current.exists() and not current.is_symlink():
        sys.exit('Deployment current path must be a symlink; no existing directory replaced.')
    previous = os.readlink(current) if current.is_symlink() else None
    temp_link = base / ('current-' + stamp)
    temp_link.symlink_to(release)
    try:
        unit.write_text(f'''[Unit]
Description=COREI-OS-MINI new workstation backend
After=network.target

[Service]
User={user}
WorkingDirectory={root}/backend
Environment=COREI_EXECUTION_MODE=paper
Environment=COREI_ENABLE_DEMO=false
Environment=COREI_MD_ROOT={MD_ROOT}
EnvironmentFile=-/etc/corei-os-mini/workstation.env
ExecStart={python} -m uvicorn corei_os_mini.api:app --host 127.0.0.1 --port {PORT}
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
UMask=0077

[Install]
WantedBy=multi-user.target
''')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'restart', SERVICE)
        for attempt in range(30):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/workstation', timeout=2) as response:
                    identity = json.load(response)
                if identity['project_id'] != 'corei-os-mini' or identity['demo_enabled']:
                    raise RuntimeError('Unexpected backend identity or enabled demo mutations.')
                break
            except (OSError, ValueError, KeyError):
                if attempt == 29:
                    raise RuntimeError('New backend did not become ready.')
                time.sleep(0.5)
        os.replace(temp_link, current)
        config.write_text(candidate)
        run('caddy', 'validate', '--config', str(config), '--adapter', 'caddyfile')
        run('systemctl', 'reload', 'caddy')
        run('systemctl', 'enable', SERVICE)
    except Exception:
        shutil.copy2(backup, config)
        if current.is_symlink():
            current.unlink()
        if previous is not None:
            current.symlink_to(previous)
        if old_unit is None:
            subprocess.run(['systemctl', 'stop', SERVICE])
            unit.unlink(missing_ok=True)
        else:
            unit.write_bytes(old_unit)
        subprocess.run(['systemctl', 'daemon-reload'])
        subprocess.run(['systemctl', 'restart' if active else 'stop', SERVICE])
        if not enabled:
            subprocess.run(['systemctl', 'disable', SERVICE])
        subprocess.run(['systemctl', 'reload', 'caddy'])
        raise
    finally:
        temp_link.unlink(missing_ok=True)
    try:
        install_market_data(root, user, python)
    except Exception as exc:  # the web workstation stays deployed; the collector is reported
        print('WARNING: market-data services were not installed:', exc)
    print('Deployed: https://' + HOST + '/')
    print('Caddy backup:', backup)
    print('Workstation deployed. Registry routes require operator login; provider connectivity and order submission remain unavailable.')


if __name__ == '__main__':
    main()
