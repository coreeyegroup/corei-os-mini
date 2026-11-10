#!/usr/bin/env bash
# Prints how the installed MT5 SDK reads a servers.dat: counts per record type, the
# field structure of extended access records, and samples of records that could not
# be decoded. servers.dat holds public broker server lists; no credentials are read.
# Usage: bash scripts/inspect-servers-dat.sh /path/to/servers.dat
set -euo pipefail
FILE="${1:?Usage: bash scripts/inspect-servers-dat.sh /path/to/servers.dat}"
READER="${COREI_MT5_CATALOG_PARSER:-/opt/corei-os-mini/server-catalog/ServerCatalog}"
[ -f "$FILE" ] || { echo "File not found: $FILE" >&2; exit 1; }
[ -x "$READER" ] || { echo "Reader not installed. Run: bash scripts/setup-broker-accounts.sh" >&2; exit 1; }
OUT="$HOME/servers-dat-inspect-$(date +%Y%m%d%H%M%S).json"
"$READER" --inspect "$FILE" > "$OUT"
python3 - "$OUT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for k in ('sdk_version', 'file_bytes', 'server_count', 'servers_with_accesses', 'servers_with_accesses_ex',
          'endpoints_from_accesses', 'endpoints_from_accesses_ex', 'accesses_ex_records', 'accesses_ex_records_unread',
          'servers_without_any_endpoint'):
    print(f'{k:32} {d.get(k)}')
print('examples without endpoint:', ', '.join(d.get('examples_without_endpoint', [])[:10]))
PY
echo "Full report: $OUT"
