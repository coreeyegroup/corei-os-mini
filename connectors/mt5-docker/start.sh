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
WEBVIEW_URL="${WEBVIEW_URL:-https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/c1336fd6-a2eb-4669-9b03-949fc70ace0e/MicrosoftEdgeWebview2Setup.exe}"
PYTHON_URL="${PYTHON_URL:-https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe}"
PORT="${COREI_BRIDGE_PORT:-9443}"
say() { echo "[$(date -Is)] [$$] $*"; }

say "COREI MT5 container starting as $(id -un) · $(wine --version 2>/dev/null)"
# Working = Wine can actually run a Windows command in it (a half-written folder can still contain kernel32.dll).
prefix_ok() { [ -f "$WINEPREFIX/drive_c/windows/system32/kernel32.dll" ] && timeout 60 wine cmd /c exit 0 >/dev/null 2>&1; }
diagnose() {
  say "Diagnostics:"; uname -a; id; echo "WINEARCH=${WINEARCH:-} WINEPREFIX=$WINEPREFIX"
  dpkg -l 'wine*' 'winehq*' 2>/dev/null | awk '/^ii/{print "  "$2" "$3}'
  ls /opt/wine-stable/lib/wine 2>&1 | sed 's/^/  lib\/wine: /'
  ls /opt/wine-stable/lib/wine/x86_64-windows/kernel32.dll /opt/wine-stable/lib/wine/i386-windows/kernel32.dll 2>&1 | sed 's/^/  builtin: /'
  findmnt -no SOURCE,FSTYPE,OPTIONS -T /config 2>&1 | sed 's/^/  \/config mount: /'
  cat /proc/sys/vm/mmap_min_addr 2>/dev/null | sed 's/^/  vm.mmap_min_addr: /'
  grep -E '^Seccomp:' /proc/self/status | sed 's/^/  /'
  env | grep -E '^(LD_|WINE|PATH=)' | sed 's/^/  env: /'
  # A throw-away prefix outside /config: if this one works, the problem is the /config folder, not Wine.
  local d; d=$(mktemp -d)
  WINEPREFIX=$d/wine timeout 120 wine cmd /c ver 2>&1 | tail -3 | sed 's/^/  wine in a temp folder: /'
  WINEPREFIX=$d/wine wineserver -k 2>/dev/null; rm -rf "$d"
  WINEDEBUG=err+all,warn+module timeout 60 wine cmd /c exit 0 2>&1 | head -15 | sed 's/^/  wine in \/config: /'
}
WINE_NOW=$(wine --version 2>/dev/null)
# A Windows folder made by another Wine version (an older or newer image): start it again while MT5 is not installed.
if [ -d "$WINEPREFIX" ] && [ "$(cat "$WINEPREFIX/.corei-wine-version" 2>/dev/null)" != "$WINE_NOW" ] \
   && [ ! -f "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
  say "The Wine folder was made by another Wine version ($(cat "$WINEPREFIX/.corei-wine-version" 2>/dev/null || echo unknown)); creating it again for $WINE_NOW"
  wineserver -k 2>/dev/null; rm -rf "$WINEPREFIX"
fi
if [ -d "$WINEPREFIX" ] && ! prefix_ok && [ ! -f "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe" ]; then
  say "The Wine folder does not work (left half-written by an earlier set-up); creating it again"
  wineserver -k 2>/dev/null; rm -rf "$WINEPREFIX"
fi
if ! prefix_ok; then
  say "1/5 Creating the Wine prefix (Windows 10 mode)"
  timeout 300 wineboot --init; timeout 120 wineserver -w || wineserver -k
  prefix_ok || { say "Wine cannot run in its new Windows folder."; diagnose; \
                 say "Retrying in 60 s"; sleep 60; exec 9>&-; exec /corei/start.sh; }
  echo "$WINE_NOW" > "$WINEPREFIX/.corei-wine-version"
  say "Wine folder ready"
fi
wine reg add 'HKCU\Software\Wine' /v Version /t REG_SZ /d win10 /f >/dev/null 2>&1

if [ ! -f "$MT5" ]; then
  say "2/5 Installing MetaTrader 5 from $INSTALLER_URL"
  # The installer downloads the terminal and then starts it, so Wine never goes idle: waiting for Wine to finish
  # ("wineserver -w") hangs for ever. Instead: run it in the background, watch for terminal64.exe, then stop Wine.
  # As MetaQuotes' own Linux script (mt5debian.sh): the WebView2 runtime first; the MT5 installer needs it.
  if [ ! -d "$WINEPREFIX/drive_c/Program Files (x86)/Microsoft/EdgeWebView" ]; then
    say "   WebView2 runtime (needed by the MT5 installer)"
    if curl -fsSL -o /tmp/webview2.exe "$WEBVIEW_URL"; then
      timeout 900 wine /tmp/webview2.exe /silent /install >/dev/null 2>&1; say "   WebView2 installer finished (exit $?)"
      timeout 60 wineserver -w || wineserver -k; rm -f /tmp/webview2.exe
    else
      say "   Could not download WebView2 from $WEBVIEW_URL"
    fi
  fi
  if curl -fsSL -o /tmp/mt5setup.exe "$INSTALLER_URL"; then
    wine /tmp/mt5setup.exe /auto >/dev/null 2>&1 &
    for i in $(seq 1 60); do   # up to 10 minutes
      sleep 10
      found=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe 2>/dev/null | head -1)
      if [ -n "$found" ]; then say "Terminal installed: $found"; sleep 30; break; fi
      (( i % 6 == 0 )) && say "  still installing ($((i / 6)) min; files so far: $(du -sh "$WINEPREFIX/drive_c" 2>/dev/null | cut -f1); windows: $(wmctrl -l 2>/dev/null | cut -d' ' -f5- | paste -sd'|' -))"
    done
    wineserver -k 2>/dev/null; sleep 3
    rm -f /tmp/mt5setup.exe
  else
    say "Could not download the installer from $INSTALLER_URL"
  fi
  # A broker installer may install into its own folder name: use the terminal it installed.
  [ -f "$MT5" ] || MT5=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe | head -1)
fi
[ -f "$MT5" ] || MT5=$(find "$WINEPREFIX/drive_c/Program Files" -maxdepth 2 -iname terminal64.exe | head -1)
[ -n "$MT5" ] && [ -f "$MT5" ] || { say "MT5 terminal not installed yet (see the windows listed above, or open the terminal view). Retrying in 60 s"; sleep 60; exec 9>&-; exec /corei/start.sh; }

if ! wine python --version >/dev/null 2>&1; then
  say "3/5 Installing Python for Windows"
  curl -fsSL -o /tmp/python.exe "$PYTHON_URL" && wine /tmp/python.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0; timeout 600 wineserver -w || wineserver -k; rm -f /tmp/python.exe
fi
say "4/5 MetaTrader5 package (latest) with numpy 1.26"
# numpy 2.x calls ucrtbase.dll.crealf, which Wine 10 does not have ("Unimplemented function ucrtbase.dll.crealf"):
# keep numpy 1.26.4 (MetaTrader5 needs numpy>=1.7).
wine python -m pip install --quiet --disable-pip-version-check --upgrade MetaTrader5 "numpy==1.26.4" || say "pip failed; using what is installed"
if wine python -c "import numpy, MetaTrader5; print('numpy', numpy.__version__, '· MetaTrader5', MetaTrader5.__version__)" 2>/dev/null; then :
else say "Python cannot load numpy/MetaTrader5 (see above)"; fi

say "5/5 Starting the terminal and the COREI bridge on port $PORT"
wine "$MT5" >/dev/null 2>&1 &
sleep 20
WIN_MT5=$(winepath -w "$MT5")
while true; do
  wine python /corei/corei_mt5_bridge.py --terminal "$WIN_MT5" --bind 0.0.0.0 --port "$PORT" --state 'Z:\config\corei-state'
  say "bridge stopped (exit $?); restarting in 10 s"
  sleep 10
done
