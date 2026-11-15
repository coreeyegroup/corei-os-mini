#!/usr/bin/env bash
# MT5 through the official terminal in a Docker container — one command per account, nothing installed on the host
# except Docker. The container runs on Debian (WineHQ installs cleanly there), so the host's Ubuntu packages, PPAs and
# 32-bit libraries never matter.
#
#   sudo bash scripts/use-mt5-docker.sh METAQUOTES_DEMO_5056977914
#   sudo bash scripts/use-mt5-docker.sh VTMARKET_LIVE_35822869 <broker's MT5 installer link>
#
# What it does: installs Docker if missing, builds the COREI MT5 image, starts one container for the account
# (restarts on boot), waits for MT5 and the COREI bridge, then switches the account to it (dry run printed first).
# Ports are published on 127.0.0.1 only: the bridge for COREI, and a browser view of the terminal (see the end).
# Log of this run: /var/log/corei-mt5-docker-<name>.log
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }
CODE="${1:-}"; INSTALLER="${2:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe}"
[[ "$CODE" =~ ^[A-Z0-9][A-Z0-9_.-]+$ ]] || { echo "Usage: sudo bash $0 <ACCOUNT_CODE> [broker's MT5 installer link]"; exit 1; }
[[ "$INSTALLER" == https://* ]] || { echo "The installer link must be https."; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=$(echo "$CODE" | tr 'A-Z_.' 'a-z--' | cut -c1-40 | sed 's/-*$//')
CONTAINER="corei-mt5-$NAME"
BASE=/var/lib/corei-mt5-docker/$NAME
STATE=$BASE/config/corei-state
LOG=/var/log/corei-mt5-docker-$NAME.log
exec > >(tee -a "$LOG") 2>&1
trap 'echo; echo "FAILED at line $LINENO: $BASH_COMMAND"; echo "Run the same command again after fixing the cause (finished steps are skipped). Log: $LOG"' ERR
step() { printf '\n== %s\n' "$*"; }
echo "=== $(date -Is) use-mt5-docker $CODE"

step "1/6 Docker"
if ! command -v docker >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker version --format 'Docker {{.Server.Version}}'

step "2/6 Ports (127.0.0.1 only)"
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  PORT=$(docker port "$CONTAINER" 9443/tcp | head -1 | awk -F: '{print $NF}')
  VIEW=$(docker port "$CONTAINER" 3000/tcp | head -1 | awk -F: '{print $NF}')
else
  used() { ss -ltn "( sport = :$1 )" | grep -q LISTEN || docker ps --format '{{.Ports}}' | grep -q "127.0.0.1:$1->"; }
  PORT=9443; while used "$PORT"; do PORT=$((PORT+1)); done
  VIEW=13000; while used "$VIEW"; do VIEW=$((VIEW+1)); done
fi
echo "Bridge for COREI: 127.0.0.1:$PORT   ·   Terminal view: 127.0.0.1:$VIEW"

step "3/6 Image corei-mt5:local (first build ~5-10 minutes)"
# Registries refuse now and then ("429 Too Many Requests"): wait and try again.
for try in 1 2 3 4 5 6; do
  if docker build -q -t corei-mt5:local -f "$ROOT/connectors/mt5-docker/Dockerfile" "$ROOT"; then break; fi
  (( try == 6 )) && { echo "The image could not be built after 6 tries (see the error above)."; false; }
  echo "Build failed (try $try of 6); waiting $((try * 30)) s and trying again…"; sleep $((try * 30))
done

step "4/6 Bridge certificate, token and terminal-view password (made here, kept in $BASE)"
install -d -m 750 "$BASE" "$BASE/config" "$STATE"
if [[ ! -f "$STATE/corei-bridge.crt" || ! -f "$STATE/corei-bridge.key" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=corei-mt5-bridge -keyout "$STATE/corei-bridge.key" -out "$STATE/corei-bridge.crt" 2>/dev/null
fi
[[ -f "$STATE/corei-bridge.token" ]] || (umask 077; head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > "$STATE/corei-bridge.token")
[[ -f "$BASE/view-password" ]] || (umask 077; head -c 18 /dev/urandom | base64 | tr -d '/+=\n' > "$BASE/view-password")
chown -R 911:911 "$BASE/config"; chmod 600 "$STATE/corei-bridge.key" "$STATE/corei-bridge.token"
openssl x509 -in "$STATE/corei-bridge.crt" -noout -fingerprint -sha256 | sed 's/^/Certificate /'

step "5/6 Container $CONTAINER"
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  docker start "$CONTAINER" >/dev/null
else
  docker run -d --name "$CONTAINER" --restart unless-stopped --shm-size 1g \
    -e PUID=911 -e PGID=911 -e TZ=Etc/UTC -e CUSTOM_USER=corei -e PASSWORD="$(cat "$BASE/view-password")" \
    -e MT5_INSTALLER_URL="$INSTALLER" -e COREI_BRIDGE_PORT=9443 \
    -p "127.0.0.1:$PORT:9443" -p "127.0.0.1:$VIEW:3000" \
    -v "$BASE/config:/config" corei-mt5:local >/dev/null
fi
echo "Waiting for MT5 and the bridge (first start installs MT5 and Python: up to ~15 minutes)…"
for i in $(seq 1 120); do
  if timeout 3 openssl s_client -connect "127.0.0.1:$PORT" </dev/null >/dev/null 2>&1; then echo "Bridge is answering on 127.0.0.1:$PORT."; break; fi
  if (( i % 6 == 0 )); then tail -n 2 "$BASE/config/corei-start.log" 2>/dev/null | sed 's/^/  /' || true; fi
  if (( i == 120 )); then echo "Not answering yet. Progress: sudo tail -f $BASE/config/corei-start.log (it keeps going in the background)"; exit 1; fi
  sleep 10
done

step "6/6 Switch $CODE to this terminal"
bash "$ROOT/scripts/apply-test-setup.sh" --bridge "$CODE" --port "$PORT" --state "$STATE" --apply

cat <<NEXT

Done. COREI retries $CODE within 30 seconds through the official MT5 terminal.
- See the terminal in your browser: on your PC run  ssh -L $VIEW:127.0.0.1:$VIEW coreeye@<server>  then open http://localhost:$VIEW
  (user corei, password: sudo cat $BASE/view-password)
- Container: sudo docker logs -f $CONTAINER   ·   start log: sudo tail -f $BASE/config/corei-start.log
- It starts again by itself after a reboot. Remove it: sudo docker rm -f $CONTAINER
NEXT
