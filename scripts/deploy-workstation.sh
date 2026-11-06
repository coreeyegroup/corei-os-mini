#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(id -u) == 0 ]]; then
    echo 'Run as your normal repository user; sudo is requested for deployment.' >&2
    exit 1
fi
bash scripts/install-workstation.sh
sudo python3 scripts/deploy-workstation.py "$PWD" "$(id -un)"
