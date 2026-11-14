#!/usr/bin/env bash
# One command per MT5 account: install the official MT5 terminal for it and switch the account to it.
#   sudo bash scripts/use-mt5-terminal.sh METAQUOTES_DEMO_5056977914
#   sudo bash scripts/use-mt5-terminal.sh VTMARKET_LIVE_35822869 <broker's MT5 installer link>
# Everything is also written to /var/log/corei-mt5-<name>.log (send that file if something fails).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }
CODE="${1:-}"; INSTALLER="${2:-}"
[[ "$CODE" =~ ^[A-Z0-9][A-Z0-9_.-]+$ ]] || { echo "Usage: sudo bash $0 <ACCOUNT_CODE> [broker installer link]"; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=$(echo "$CODE" | tr 'A-Z_.' 'a-z--' | cut -c1-31 | sed 's/-*$//')
LOG=/var/log/corei-mt5-$NAME.log
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date -Is) use-mt5-terminal $CODE"

# Reuse this account's port if it already has a terminal; otherwise the first free port from 9443.
UNIT_FILE=/etc/systemd/system/corei-mt5-$NAME.service
if [[ -f "$UNIT_FILE" ]]; then
  PORT=$(grep -o -- '--port [0-9]*' "$UNIT_FILE" | awk '{print $2}')
else
  PORT=9443
  while ss -ltn "( sport = :$PORT )" | grep -q LISTEN || grep -qs -- "--port $PORT " /etc/systemd/system/corei-mt5-*.service; do PORT=$((PORT+1)); done
fi
echo "Terminal instance: $NAME on 127.0.0.1:$PORT"

bash "$ROOT/scripts/setup-mt5-terminal.sh" "$NAME" "$PORT" ${INSTALLER:+"$INSTALLER"}
bash "$ROOT/scripts/apply-test-setup.sh" --bridge "$CODE" --port "$PORT" --state "/var/lib/corei-mt5/$NAME/state" --apply

echo
echo "Done. COREI retries this account within 30 seconds. Watch the terminal bridge: journalctl -u corei-mt5-$NAME -f"
echo "Log of this run: $LOG"
