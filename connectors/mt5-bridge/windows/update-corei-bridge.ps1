<#
COREI MT5 bridge - update on Windows (VPS or PC).

Standard layout:
  C:\corei-setup\                      the files you download from COREI (always these exact names; replace old ones)
      setup-corei-bridge.ps1           first-time set-up
      update-corei-bridge.ps1          this script
      corei_mt5_bridge.py              the bridge (its version: python corei_mt5_bridge.py --version)
  C:\corei-bridge\<Name>\              the installed bridge: certificate, token, logs, terminal copies (never edit by hand)

Run in PowerShell as Administrator:
  cd C:\corei-setup
  powershell -ExecutionPolicy Bypass -File .\update-corei-bridge.ps1 -Name metaquotes-demo
  (optional: -MaxTerminals 40 on a server with more memory; about 150-300 MB per terminal)

It shows the old and new version, stops the bridge, keeps a backup of the old file, installs the new one, turns on
"every account" mode if it is not on, starts the bridge, checks the port and prints the last lines of its log.
The certificate and token do not change, so nothing changes in COREI.
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z0-9][a-z0-9-]{1,40}$')][string]$Name,
    [string]$Root = 'C:\corei-bridge',
    [ValidateRange(1, 500)][int]$MaxTerminals = 20
)
$ErrorActionPreference = 'Stop'
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail 'Run PowerShell as Administrator.' }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here 'corei_mt5_bridge.py'
$dir = Join-Path $Root $Name
$dst = Join-Path $dir 'corei_mt5_bridge.py'
$cmd = Join-Path $dir 'run-bridge.cmd'
$task = "COREI MT5 bridge $Name"
if (-not (Test-Path $src)) { Fail "corei_mt5_bridge.py is not in $here. Download it from COREI into this folder (replace the old one; the name must stay exactly corei_mt5_bridge.py)." }
if (-not (Test-Path $cmd)) { Fail "No bridge named '$Name' is installed ($cmd not found). Use setup-corei-bridge.ps1 for a first installation." }

# The Python and port the installed bridge uses (from its start file).
$start = Get-Content $cmd -Raw
$pyExe = ([regex]'"([^"]*python[^"]*\.exe)"').Match($start).Groups[1].Value
$port = ([regex]'--port (\d+)').Match($start).Groups[1].Value
if (-not $pyExe -or -not (Test-Path $pyExe)) { Fail "Python not found in $cmd." }

# One bridge per port (0.43.2). Another COREI bridge on this machine set to the same port (an older set-up) would take
# the port whenever this one restarts, and COREI would see the wrong certificate. Find it, stop it and turn off its
# start-up task (reversible), so exactly this bridge listens.
function Resolve-PortConflict([int]$Port, [string]$Dir, [string]$Root) {
    $others = @()
    Get-ChildItem -Path $Root -Directory -ErrorAction SilentlyContinue | Where-Object { $_.FullName -ne $Dir } | ForEach-Object {
        $rc = Join-Path $_.FullName 'run-bridge.cmd'
        if ((Test-Path $rc) -and ((Get-Content $rc -Raw) -match "--port $Port(\s|$)")) { $others += $_.Name }
    }
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)"
        $cl = "$($p.CommandLine)"
        if ($cl -like "*$Dir*") { return }
        if ($cl -match 'corei_mt5_bridge') {
            $m = [regex]::Match($cl, 'corei-bridge\\([A-Za-z0-9-]+)\\')
            if ($m.Success -and $others -notcontains $m.Groups[1].Value) { $others += $m.Groups[1].Value }
        } elseif ($p) {
            Fail "Port $Port is used by another program: $($p.Name) (pid $($p.ProcessId)). Stop it or give this bridge another port."
        }
    }
    foreach ($o in ($others | Select-Object -Unique)) {
        Write-Host "Another COREI bridge on this machine, '$o', uses port $Port too. Only one can listen: stopping '$o' and turning its start-up task off." -ForegroundColor Yellow
        Stop-ScheduledTask -TaskName "COREI MT5 bridge $o" -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName "COREI MT5 bridge $o" -ErrorAction SilentlyContinue | Out-Null
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*corei-bridge\$o\*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "  stopped pid $($_.ProcessId)" }
        Write-Host "  Undo (if ever needed): Enable-ScheduledTask -TaskName 'COREI MT5 bridge $o'"
        Write-Host "  In COREI: routes that used '$o' (its own certificate) must now use this node; Setup > the account > Routes." -ForegroundColor Yellow
    }
    Start-Sleep 2
}

# The program answering on the port must be this bridge (not another one that took the port).
function Test-OwnListener([int]$Port, [string]$Dir) {
    $l = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $l) { return $false }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($l.OwningProcess)"
    return ("$($p.CommandLine)" -like "*$Dir*")
}

Step '1/5 Versions'
function Get-BridgeVersion($file) {
    $ErrorActionPreference = 'Continue'
    $out = (& $pyExe $file --version 2>&1 | ForEach-Object { "$_" }) -join "`n"
    if ($out -match 'corei_mt5_bridge (\d[\d.]*)') { return $Matches[1] } else { return $null }
}
$new = Get-BridgeVersion $src
$old = if (Test-Path $dst) { Get-BridgeVersion $dst } else { 'none' }
if (-not $old) { $old = 'before 0.38.2 (no version number)' }
if (-not $new) { Fail "The corei_mt5_bridge.py in $here is not a valid bridge (or is an old one). Download it again from COREI." }
Write-Host "Installed: $old"
Write-Host "New:       $new"

Step '2/5 Stop the bridge (and any other bridge set to the same port)'
Resolve-PortConflict -Port ([int]$port) -Dir $dir -Root $Root
Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*$dir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped pid $($_.ProcessId)" }
Start-Sleep 2

Step '3/5 Install the new file (the old one is kept as a backup)'
if (Test-Path $dst) {
    $script:backup = Join-Path $dir ("corei_mt5_bridge.py.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Copy-Item -Force $dst $backup
    Write-Host "Backup: $backup"
}
Copy-Item -Force $src $dst
Write-Host "Installed: $dst"

Step '4/5 Every-account mode'
if ($start -notmatch 'terminals-root') {
    $start = $start -replace ' --bind ', " --terminals-root `"$dir\terminals`" --bind "
    Set-Content -Path $cmd -Value $start -Encoding ASCII
    Write-Host 'Turned on: one terminal copy per account under' "$dir\terminals"
} else { Write-Host 'Already on.' }
if ($start -notmatch 'max-terminals') {
    $start = $start -replace ' --bind ', " --max-terminals $MaxTerminals --bind "
    Set-Content -Path $cmd -Value $start -Encoding ASCII
    Write-Host "Terminal pool: at most $MaxTerminals terminals open at once (least-recently-used idle one closed first)"
} else { Write-Host 'Terminal pool already set:' ([regex]::Match($start, '--max-terminals \d+').Value) }

Step '5/5 Start and check'
Enable-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue | Out-Null
Start-ScheduledTask -TaskName $task
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep 2
    $ok = Test-OwnListener -Port ([int]$port) -Dir $dir   # answering, AND it is this bridge (not another one on the port)
}
$fp = (& $pyExe $dst --fingerprint --state $dir 2>$null | Select-Object -Last 1)
Write-Host "This bridge's certificate fingerprint: $fp"
Write-Host "  (COREI pins this; it must match Setup > Bridge nodes > $Name)"
$log = Join-Path $dir 'bridge.log'
if (Test-Path $log) {
    # Read without locking the file the bridge is writing.
    $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite'); $sr = New-Object IO.StreamReader($fs)
    $lines = $sr.ReadToEnd() -split "`r?`n"; $sr.Close(); $fs.Close()
    Write-Host "Last lines of $log :"
    $lines | Select-Object -Last 8 | ForEach-Object { Write-Host "  $_" }
}
if ($ok) {
    Write-Host "`nOK: bridge $new ('$Name') is the one answering on port $port. COREI checks nodes every 5 minutes (or Setup > Bridge nodes > Check now); the first connection per account takes about a minute more (terminal copy)." -ForegroundColor Green
} else {
    Write-Host "`nThis bridge is not the one answering on port $port. Send the log lines above (look for PORT_IN_USE). To go back: Copy-Item -Force `"$backup`" `"$dst`"; Start-ScheduledTask -TaskName `"$task`"" -ForegroundColor Red
    exit 1
}
