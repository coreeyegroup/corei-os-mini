#!/usr/bin/env bash
# Writes the correct provider, account, instrument, group, symbol-mapping and collection-plan values for the
# test setup through COREI's own rules (approvals and audit log apply). Dry run unless --apply.
#   sudo bash scripts/apply-test-setup.sh            # show what would change
#   sudo bash scripts/apply-test-setup.sh --apply    # change it
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo bash scripts/apply-test-setup.sh [--apply]" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE=/etc/corei-os-mini/workstation.env
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE: run the database setup first." >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
export PYTHONPATH="$ROOT/backend/src"
exec "$ROOT/.venv-workstation/bin/python" -m corei_os_mini.setup_seed "$@"
