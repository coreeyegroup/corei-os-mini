#!/usr/bin/env bash
# Writes the installed MT5 SDK's connection surface (constructors, methods, properties,
# events and relevant enums) to ~/mt5-api-<timestamp>.json, so the account adapter is
# built against the exact SDK on this server. Reads only type information; no login.
set -euo pipefail
READER="${COREI_MT5_CATALOG_PARSER:-/opt/corei-os-mini/server-catalog/ServerCatalog}"
[ -x "$READER" ] || { echo "Reader not installed. Run: bash scripts/setup-broker-accounts.sh" >&2; exit 1; }
OUT="$HOME/mt5-api-$(date +%Y%m%d%H%M%S).json"
"$READER" --describe-api > "$OUT"
echo "SDK description written to $OUT ($(wc -c < "$OUT") bytes)"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print('SDK version:', d.get('sdk_version')); print('Client build settings:', ', '.join(d.get('client_build_settings') or []) or 'none (the SDK must be updated to fix OLD_VERSION)')" "$OUT"
