#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TASK_NODE_BIN=/opt/corei-os-mini/toolchain/node-v26.13.0/bin
if [[ ! -x "$TASK_NODE_BIN/node" || ! -x "$TASK_NODE_BIN/npm" ]]; then
  echo "Required project Node toolchain missing: $TASK_NODE_BIN" >&2; exit 1
fi
TASK_PYTHON=/opt/corei-os-mini/toolchain/python-3.14.10/bin/python3.14
if [[ ! -x "$TASK_PYTHON" ]]; then
  TASK_PYTHON=$(command -v python3.14) || { echo 'Python 3.14 is required.' >&2; exit 1; }
fi
if ! "$TASK_PYTHON" -I -c 'import sys, ssl, hashlib, zlib, ctypes, sqlite3, bz2, lzma, ensurepip; assert sys.version_info[:2] == (3, 14); ssl.create_default_context(); print(ssl.OPENSSL_VERSION)'; then
  echo 'Python runtime is incomplete. Run: bash scripts/repair-python.sh' >&2
  exit 1
fi
TASK_BASE=$("$TASK_PYTHON" -I -c 'import sys; print(sys.base_prefix)')
if [[ -d .venv-workstation ]]; then
  TASK_VENV_BASE=$(.venv-workstation/bin/python -I -c 'import sys, ssl; print(sys.base_prefix)' 2>/dev/null || true)
  if [[ "$TASK_VENV_BASE" != "$TASK_BASE" ]]; then
    TASK_BACKUP=".venv-workstation.backup.$(date +%Y%m%d%H%M%S).$$"
    mv .venv-workstation "$TASK_BACKUP"
    printf 'Preserved previous virtual environment: %s\n' "$TASK_BACKUP"
  fi
fi
export PATH="$TASK_NODE_BIN:$PATH"
"$TASK_PYTHON" -m venv .venv-workstation
.venv-workstation/bin/python -I -c 'import ssl; print(ssl.OPENSSL_VERSION)'
.venv-workstation/bin/python -m pip install -e ./backend
npm ci --prefix ui
npm run build --prefix ui
printf '%s\n' 'Workstation built. Server deployment: bash scripts/deploy-workstation.sh' 'Optional local development: .venv-workstation/bin/python scripts/run-workstation.py'
