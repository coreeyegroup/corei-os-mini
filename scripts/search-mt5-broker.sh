#!/usr/bin/env bash
# Asks the MT5 broker directory (the SDK's Broker.Search / SearchMQ, the same lookup as
# MT5 "Open an Account" and Westernpips Browse) for a broker's current servers and
# access points, and prints a summary. No login; public directory data only.
# Usage: bash scripts/search-mt5-broker.sh VTMarkets
#        bash scripts/search-mt5-broker.sh --describe   (SDK search signatures)
set -euo pipefail
TERM_="${1:?Usage: bash scripts/search-mt5-broker.sh <broker name> | --describe}"
READER="${COREI_MT5_CATALOG_PARSER:-/opt/corei-os-mini/server-catalog/ServerCatalog}"
[ -x "$READER" ] || { echo "Reader not installed. Run: bash scripts/setup-broker-accounts.sh" >&2; exit 1; }
if [ "$TERM_" = "--describe" ]; then exec "$READER" --describe-search; fi
OUT="$HOME/broker-search-$(date +%Y%m%d%H%M%S).json"
set +e; "$READER" --search "$TERM_" > "$OUT"; CODE=$?; set -e
if [ "$CODE" != 0 ]; then echo "Search failed (exit $CODE). Messages above explain why." >&2; exit "$CODE"; fi
python3 - "$OUT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"{d['server_count']} servers, {d['access_point_count']} access points for '{d['query']}'")
for s in d['servers']:
    print(f"  {s['server_name']:40} {s['details'].get('CompanyName', '')[:40]:40} {len(s['access_points'])} access points")
PY
echo "Full result: $OUT"
