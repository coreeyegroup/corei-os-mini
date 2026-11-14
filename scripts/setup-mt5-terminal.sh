#!/usr/bin/env bash
# Install the official MetaTrader 5 terminal on this Ubuntu server (under Wine, on a virtual screen) with COREI's
# terminal bridge, as one systemd service per instance. Use it when the direct SDK (mt5api.dll) is older than the
# broker's server ("MT5 SDK too old for this server"): the official terminal always speaks the server's protocol.
#
#   sudo bash scripts/setup-mt5-terminal.sh <instance> <port> [broker-installer-url]
#   e.g. sudo bash scripts/setup-mt5-terminal.sh metaquotes-demo 9443
#        sudo bash scripts/setup-mt5-terminal.sh vt-live 9444 <the MT5 installer link from the broker's download page>
# A broker's own installer already knows its servers; the MetaQuotes installer (the default) knows MetaQuotes-Demo.
#
# Then point the COREI account at it (dry run first, then --apply):
#   sudo bash scripts/apply-test-setup.sh --bridge <ACCOUNT_CODE> --port <port> --state /var/lib/corei-mt5/<instance>/state
#
# One instance serves one account at a time (the terminal holds one login). The bridge listens on 127.0.0.1 only,
# over TLS with a pinned certificate and a token. COREI sends the account's login once per session; nothing is
# written to disk by this script except the certificate, the token (mode 600) and the terminal itself.
set -euo pipefail

INSTANCE="${1:-}"; PORT="${2:-}"; INSTALLER_URL="${3:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe}"
PYTHON_URL="https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ "$INSTANCE" =~ ^[a-z0-9][a-z0-9-]{1,30}$ ]] || { echo "Usage: $0 <instance (a-z0-9-)> <port> [broker-installer-url]"; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1024 && "$PORT" -le 65535 ]] || { echo "Port must be 1024-65535."; exit 1; }
[[ "$INSTALLER_URL" == https://* ]] || { echo "The installer URL must be https."; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SVC_USER=corei-mt5
BASE=/var/lib/corei-mt5/$INSTANCE
PREFIX=$BASE/wine
STATE=$BASE/state
UNIT=corei-mt5-$INSTANCE

step() { printf '\n== %s\n' "$*"; }

step "1/7 Packages: Wine, virtual screen, OpenSSL"
dpkg --add-architecture i386
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine wine64 xvfb xauth openssl curl ca-certificates >/dev/null
wine --version

step "2/7 Service user and folders"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/corei-mt5 --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 750 "$BASE" "$STATE" "$BASE/downloads"
install -o "$SVC_USER" -g "$SVC_USER" -m 640 "$REPO/connectors/mt5-bridge/corei_mt5_bridge.py" "$BASE/corei_mt5_bridge.py"
if ss -ltn "( sport = :$PORT )" | grep -q LISTEN && ! systemctl is-active --quiet "$UNIT"; then
  echo "Port $PORT is already in use by something else. Pick another port."; exit 1
fi

as_svc() { sudo -u "$SVC_USER" env HOME=/var/lib/corei-mt5 WINEPREFIX="$PREFIX" WINEARCH=win64 WINEDEBUG=-all "$@"; }
in_screen() { as_svc xvfb-run -a -s '-screen 0 1280x800x24' "$@"; }

step "3/7 Wine prefix $PREFIX"
[[ -d "$PREFIX/drive_c" ]] || in_screen wineboot --init
as_svc true

step "4/7 Windows Python + MetaTrader5 package"
PYEXE="$PREFIX/drive_c/Program Files/Python311/python.exe"
if [[ ! -f "$PYEXE" ]]; then
  as_svc curl -fsSL -o "$BASE/downloads/python.exe" "$PYTHON_URL"
  in_screen wine "$BASE/downloads/python.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
fi
in_screen wine "$PYEXE" -m pip install --quiet --upgrade pip MetaTrader5
in_screen wine "$PYEXE" -c "import MetaTrader5 as m; print('MetaTrader5 package', m.__version__)"

step "5/7 MetaTrader 5 terminal ($INSTALLER_URL)"
TERMINAL=$(find "$PREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe 2>/dev/null | head -1 || true)
if [[ -z "$TERMINAL" ]]; then
  as_svc curl -fsSL -o "$BASE/downloads/mt5setup.exe" "$INSTALLER_URL"
  # /auto installs silently; the installer then downloads the current terminal build from MetaQuotes.
  in_screen timeout 900 wine "$BASE/downloads/mt5setup.exe" /auto || true
  TERMINAL=$(find "$PREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe 2>/dev/null | head -1 || true)
fi
[[ -n "$TERMINAL" ]] || { echo "terminal64.exe was not installed. Check the installer URL and run again."; exit 1; }
WIN_TERMINAL=$(as_svc winepath -w "$TERMINAL")
echo "Terminal: $WIN_TERMINAL"

step "6/7 Bridge certificate and token (made here, so the bridge never needs openssl inside Wine)"
if [[ ! -f "$STATE/corei-bridge.crt" || ! -f "$STATE/corei-bridge.key" ]]; then
  as_svc openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=corei-mt5-bridge \
    -keyout "$STATE/corei-bridge.key" -out "$STATE/corei-bridge.crt" 2>/dev/null
fi
[[ -f "$STATE/corei-bridge.token" ]] || as_svc sh -c "umask 077; head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > '$STATE/corei-bridge.token'"
chmod 600 "$STATE/corei-bridge.key" "$STATE/corei-bridge.token"
FINGERPRINT=$(openssl x509 -in "$STATE/corei-bridge.crt" -noout -fingerprint -sha256 | cut -d= -f2)
echo "Certificate fingerprint: $FINGERPRINT"

step "7/7 Service $UNIT on 127.0.0.1:$PORT"
WIN_STATE=$(as_svc winepath -w "$STATE")
WIN_BRIDGE=$(as_svc winepath -w "$BASE/corei_mt5_bridge.py")
cat > "/etc/systemd/system/$UNIT.service" <<UNITFILE
[Unit]
Description=COREI MT5 terminal bridge ($INSTANCE)
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
Environment=HOME=/var/lib/corei-mt5 WINEPREFIX=$PREFIX WINEARCH=win64 WINEDEBUG=-all
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1280x800x24" /usr/bin/wine "$PYEXE" "$WIN_BRIDGE" --terminal "$WIN_TERMINAL" --bind 127.0.0.1 --port $PORT --state "$WIN_STATE"
Restart=always
RestartSec=15
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNITFILE
systemctl daemon-reload
systemctl enable --now "$UNIT"
sleep 10
if systemctl is-active --quiet "$UNIT" && ss -ltn "( sport = :$PORT )" | grep -q LISTEN; then
  echo "Bridge is listening on 127.0.0.1:$PORT."
else
  echo "The service did not start listening yet. Logs: journalctl -u $UNIT -n 50"
fi

cat <<NEXT

Next: point the COREI account at this terminal (dry run, then --apply):
  sudo bash scripts/apply-test-setup.sh --bridge <ACCOUNT_CODE> --port $PORT --state $STATE
  sudo bash scripts/apply-test-setup.sh --bridge <ACCOUNT_CODE> --port $PORT --state $STATE --apply
Then press Log in on the account in COREI. Logs: journalctl -u $UNIT -f
NEXT
