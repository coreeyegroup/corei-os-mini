"""Run this repository's backend and UI on loopback; stop both on exit."""
from pathlib import Path
import os
import signal
import socket
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
python = root / '.venv-workstation/bin/python'
node_bin = Path('/opt/corei-os-mini/toolchain/node-v26.13.0/bin')
if not python.is_file() or not (root / 'ui/node_modules').is_dir():
    sys.exit('Run bash scripts/install-workstation.sh first.')
for port in (8786, 5186):
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            sys.exit(f'Port {port} is occupied. No existing process was stopped.')
env = dict(os.environ)
env['PATH'] = str(node_bin) + os.pathsep + env.get('PATH', '')
env['COREI_EXECUTION_MODE'] = 'paper'
# The shell is read-only. Enabling demo mutations requires a separate explicit run.
env['COREI_ENABLE_DEMO'] = 'false'
children = []
def stop(signum, frame):
    raise SystemExit(128 + signum)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
try:
    children.append(subprocess.Popen([str(python), '-m', 'uvicorn', 'corei_os_mini.api:app', '--host', '127.0.0.1', '--port', '8786'], cwd=root/'backend', env=env, start_new_session=True))
    children.append(subprocess.Popen([str(node_bin/'npm'), 'run', 'dev'], cwd=root/'ui', env=env, start_new_session=True))
    print('New repository workstation: http://127.0.0.1:5186 · Ctrl+C stops both processes.', flush=True)
    while all(child.poll() is None for child in children):
        time.sleep(.3)
    sys.exit('A workstation process stopped. Both processes are being closed.')
finally:
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
