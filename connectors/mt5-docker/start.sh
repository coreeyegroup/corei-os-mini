#!/bin/bash
# Runs in the container's desktop session (user abc). First start: Wine prefix, MT5 terminal, Windows Python and the
# MetaTrader5 package (about 5-10 minutes). Every start: the terminal, then COREI's bridge, restarted if it stops.
# Progress: docker logs <container>, and /config/corei-start.log.
exec >>/config/corei-start.log 2>&1
set -u
export WINEPREFIX=/config/.wine WINEDEBUG=-all
# One copy only: the desktop session can launch its autostart more than once, and two Wine set-ups writing the same
# folder at the same time break it ("could not load kernel32.dll").
exec 9>/config/.corei-start.lock
if ! flock -n 9; then echo "[$(date -Is)] another start is running; this one exits"; exit 0; fi
MT5="$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"
INSTALLER_URL="${MT5_INSTALLER_URL:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe}"
PYTHON_URL="${PYTHON_URL:-https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe}"
PORT="${COREI_BRIDGE_PORT:-9443}"
say() { echo "[$(date -Is)] $*"; }

say "COREI MT5 container starting as $(id -un) · $(wine --version 2>/dev/null)"
# Working = Wine can actually run a Windows command in it (a half-written folder can still contain kernel32.dll).
prefix_ok() { [ -f "$WINEPREFIX/drive_c/windows/system32/kernel32.dll" ] && timeout 60 wine cmd /c exit 0 >/dev/null 2>&1; }
diagnose() {
  say "Diagnostics:"; uname -a; id; echo "WINEARCH=${WINEARCH:-} WINEPREFIX=$WINEPREFIX"
  dpkg -l 'wine*' 'winehq*' 2>/dev/null | awk '/^ii/{print "  "$2" "$3}'
  ls /opt/wine-stable/lib/wine 2>&1 | sed 's/^/  lib\/wine: /'
  findmnt -no SOURCE,FSTYPE,OPTIONS -T /config 2>&1 | sed 's/^/  \/config mount: /'
  cat /proc/sys/vm/mmap_min_addr 2>/dev/null | sed 's/^/  vm.mmap_min_addr: /'
  timeout 60 wine cmd /c ver 2>&1 | head -5 | sed 's/^/  wine cmd: /'
}
if [ -d "$WINEPREFIX" ] && ! prefix_ok && [ ! -f "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
  say "The Wine folder does not work (left half-written by an earlier set-up); creating it again"
  wineserver -k 2>/dev/null; rm -rf "$WINEPREFIX"
fi
if ! prefix_ok; then
  say "1/5 Creating the Wine prefix (Windows 10 mode)"
  wineboot --init; wineserver -w
  prefix_ok || { say "Wine cannot run in its new Windows folder."; diagnose; \
                 say "Retrying in 60 s"; sleep 60; exec 9>&-; exec /corei/start.sh; }
  say "Wine folder ready"
fi
wine reg add 'HKCU\Software\Wine' /v Version /t REG_SZ /d win10 /f >/dev/null 2>&1

if [ ! -f "$MT5" ]; then
  say "2/5 Installing MetaTrader 5 from $INSTALLER_URL"
  curl -fsSL -o /tmp/mt5setup.exe "$INSTALLER_URL" && wine /tmp/mt5setup.exe /auto; wineserver -w; rm -f /tmp/mt5setup.exe
  # A broker installer may install into its own folder name: use the terminal it installed.
  [ -f "$MT5" ] || MT5=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe | head -1)
fi
[ -f "$MT5" ] || MT5=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe | head -1)
[ -n "$MT5" ] && [ -f "$MT5" ] || { say "MT5 terminal not installed; check the installer URL. Retrying in 60 s"; sleep 60; exec 9>&-; exec /corei/start.sh; }

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
