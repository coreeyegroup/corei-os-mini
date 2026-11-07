#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(id -u) == 0 ]]; then
  echo 'Run as your normal repository user; sudo is requested for database setup.' >&2
  exit 1
fi
sudo python3 scripts/configure-workstation-database.py
