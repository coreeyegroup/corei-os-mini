#!/usr/bin/env bash
# Order: a recent upstream Wine 11 (Kron4ek's build), then WineHQ's development release. Wine 10.0 is not a candidate:
# it runs, but the current MT5 terminal refuses it.
# History: WineHQ's development release first. Current MT5 builds refuse late Wine 10 and early Wine 11 ("A debugger has
# been found running in your system", MQL5 forum threads 503128 and 518725: the installer passed on 10.0, the updated
# terminal did not); MetaQuotes pairs its own macOS MT5 with Wine 11.7, so the newest Wine is the one to use.
# Finds a Wine build that actually runs on this server inside Docker, once, and remembers it for every MT5 container.
# Each candidate is built and tested in a throw-away container (fresh Windows folder, `wine cmd /c ver`), first with
# Docker's normal system-call filter, then without it. The first one that works is written to
# /var/lib/corei-mt5-docker/wine-choice-v4. Full output: /var/log/corei-mt5-wine-doctor.log
#   sudo bash scripts/mt5-docker-wine-doctor.sh            (called by use-mt5-docker.sh; skipped once a choice exists)
#   sudo COREI_WINE_RECHECK=1 bash scripts/mt5-docker-wine-doctor.sh   (test again)
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHOICE=/var/lib/corei-mt5-docker/wine-choice-v4
LOG=/var/log/corei-mt5-wine-doctor.log
read -ra CANDIDATES <<<"${COREI_WINE_CANDIDATES:-kron4ek-11.22 devel}"
mkdir -p "$(dirname "$CHOICE")"
if [[ -f "$CHOICE" && -z "${COREI_WINE_RECHECK:-}" ]]; then cat "$CHOICE"; exit 0; fi

echo "=== $(date -Is) wine doctor · host $(uname -m) · kernel $(uname -r) · Docker $(docker version --format '{{.Server.Version}}')" >>"$LOG"
echo "Finding a Wine build that runs on this server (each candidate: build ~3-5 min, test ~1 min). Details: $LOG"

build() {  # $1 = candidate; registries refuse now and then (429): retry
  local try
  for try in 1 2 3 4; do
    docker build -q --build-arg WINE="$1" -t "corei-mt5-wine:$1" -f "$ROOT/connectors/mt5-docker/Dockerfile" "$ROOT" >>"$LOG" 2>&1 && return 0
    sleep $((try * 30))
  done
  return 1
}
# A fresh Windows folder as the container's user (911), outside /config, without the desktop session.
wine_runs() {  # $1 = image, $2 = extra docker option or empty
  local out rc
  out=$(docker run --rm ${2:+"$2"} --user 911:911 -e HOME=/tmp/h -e WINEDEBUG=err+all -e 'WINEDLLOVERRIDES=mscoree=;mshtml=' --entrypoint bash "$1" -c '
      mkdir -p /tmp/h; export WINEPREFIX=/tmp/h/wine; s=$(date +%s)
      timeout 300 wine cmd /c ver; rc=$?
      echo "exit $rc after $(( $(date +%s) - s )) s"; wineserver -k 2>/dev/null; exit $rc' 2>&1); rc=$?
  { echo "--- $1 ${2:-default filter}"; echo "$out" | tail -n 40; } >>"$LOG"
  LAST="$out"
  [[ $rc -eq 0 ]] && echo "$out" | grep -qi 'windows'
}

FIRST_FAIL=""
for c in "${CANDIDATES[@]}"; do
  printf '  Wine %-7s build… ' "$c"
  if ! build "$c"; then echo "could not be built:"; grep -E 'ERROR|E: |error|Unable|404|curl' "$LOG" | tail -n 6 | sed 's/^/      /'; continue; fi
  ver=$(docker run --rm --entrypoint wine "corei-mt5-wine:$c" --version 2>/dev/null)
  printf '%s · test… ' "$ver"
  if wine_runs "corei-mt5-wine:$c" ""; then
    echo "WORKS"; printf 'WINE=%s\nSECCOMP=default\nVERSION=%s\n' "$c" "$ver" >"$CHOICE"; cat "$CHOICE"; exit 0
  fi
  [[ -n "$FIRST_FAIL" ]] || FIRST_FAIL="$LAST"
  printf 'fails · without the system-call filter… '
  if wine_runs "corei-mt5-wine:$c" "--security-opt=seccomp=unconfined"; then
    echo "WORKS"; printf 'WINE=%s\nSECCOMP=unconfined\nVERSION=%s\n' "$c" "$ver" >"$CHOICE"; cat "$CHOICE"; exit 0
  fi
  echo "fails"
done
echo
echo "No Wine build runs in Docker on this server, so the cause is the server itself. Wine's own report (first candidate):"
echo "$FIRST_FAIL" | tail -n 25 | sed 's/^/  /'
echo "Send me this output and: sudo tail -n 120 $LOG"
exit 1
