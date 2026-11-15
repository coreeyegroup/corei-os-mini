#!/bin/bash
# Runs in the container's desktop session (user abc). First start: Wine prefix, MT5 terminal, Windows Python and the
# MetaTrader5 package (about 5-10 minutes). Every start: the terminal, then COREI's bridge, restarted if it stops.
# Progress: docker logs <container>, and /config/corei-start.log.
exec >>/config/corei-start.log 2>&1
set -u
export WINEPREFIX=/config/.wine WINEDEBUG=-all
MT5="$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"
INSTALLER_URL="${MT5_INSTALLER_URL:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe}"
PYTHON_URL="${PYTHON_URL:-https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe}"
PORT="${COREI_BRIDGE_PORT:-9443}"
say() { echo "[$(date -Is)] $*"; }

say "COREI MT5 container starting"
if [ ! -d "$WINEPREFIX/drive_c" ]; then
  say "1/5 Creating the Wine prefix (Windows 10 mode)"
  wineboot --init; wineserver -w
fi
wine reg add 'HKCU\Software\Wine' /v Version /t REG_SZ /d win10 /f >/dev/null 2>&1

if [ ! -f "$MT5" ]; then
  say "2/5 Installing MetaTrader 5 from $INSTALLER_URL"
  curl -fsSL -o /tmp/mt5setup.exe "$INSTALLER_URL" && wine /tmp/mt5setup.exe /auto; wineserver -w; rm -f /tmp/mt5setup.exe
  # A broker installer may install into its own folder name: use the terminal it installed.
  [ -f "$MT5" ] || MT5=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe | head -1)
fi
[ -f "$MT5" ] || MT5=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe | head -1)
[ -n "$MT5" ] && [ -f "$MT5" ] || { say "MT5 terminal not installed; check the installer URL. Retrying in 60 s"; sleep 60; exec /corei/start.sh; }

if ! wine python --version >/dev/null 2>&1; then
  say "3/5 Installing Python for Windows"
  curl -fsSL -o /tmp/python.exe "$PYTHON_URL" && wine /tmp/python.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0; wineserver -w; rm -f /tmp/python.exe
fi
say "4/5 MetaTrader5 package (latest)"
wine python -m pip install --quiet --disable-pip-version-check --upgrade MetaTrader5 || say "pip failed; using what is installed"

say "5/5 Starting the terminal and the COREI bridge on port $PORT"
wine "$MT5" >/dev/null 2>&1 &
sleep 20
WIN_MT5=$(winepath -w "$MT5")
while true; do
  wine python /corei/corei_mt5_bridge.py --terminal "$WIN_MT5" --bind 0.0.0.0 --port "$PORT" --state 'Z:\config\corei-state'
  say "bridge stopped (exit $?); restarting in 10 s"
  sleep 10
done
