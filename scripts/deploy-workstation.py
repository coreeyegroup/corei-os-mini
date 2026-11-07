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
PORT = 18786


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
    print('Deployed: https://' + HOST + '/')
    print('Caddy backup:', backup)
    print('Workstation deployed. Registry routes require operator login; provider connectivity and order submission remain unavailable.')


if __name__ == '__main__':
    main()
