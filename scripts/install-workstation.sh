#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TASK_NODE_BIN=/opt/corei-os-mini/toolchain/node-v26.13.0/bin
if [[ ! -x "$TASK_NODE_BIN/node" || ! -x "$TASK_NODE_BIN/npm" ]]; then
  echo "Required project Node toolchain missing: $TASK_NODE_BIN" >&2; exit 1
fi
command -v python3.14 >/dev/null || { echo 'Python 3.14 is required.' >&2; exit 1; }
export PATH="$TASK_NODE_BIN:$PATH"
python3.14 -m venv .venv-workstation
.venv-workstation/bin/python -m pip install -e ./backend
npm ci --prefix ui
npm run build --prefix ui
printf '%s\n' 'Workstation installed. Start: .venv-workstation/bin/python scripts/run-workstation.py'
