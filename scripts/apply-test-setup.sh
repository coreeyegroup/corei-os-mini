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
# Read KEY=value lines literally (as systemd does): values may contain $ and other shell characters.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"; value="${line#*=}"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  case "$value" in \"*\") value="${value:1:${#value}-2}" ;; \'*\') value="${value:1:${#value}-2}" ;; esac
  export "$key=$value"
done < "$ENV_FILE"
export PYTHONPATH="$ROOT/backend/src"
exec "$ROOT/.venv-workstation/bin/python" -m corei_os_mini.setup_seed "$@"
