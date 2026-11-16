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

It shows the old and new version, stops the bridge, keeps a backup of the old file, installs the new one, turns on
"every account" mode if it is not on, starts the bridge, checks the port and prints the last lines of its log.
The certificate and token do not change, so nothing changes in COREI.
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z0-9][a-z0-9-]{1,40}$')][string]$Name,
    [string]$Root = 'C:\corei-bridge'
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

Step '2/5 Stop the bridge'
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

Step '5/5 Start and check'
Start-ScheduledTask -TaskName $task
$ok = $false
for ($i = 0; $i -lt 15 -and -not $ok; $i++) {
    Start-Sleep 2
    $ok = (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue).TcpTestSucceeded
}
$log = Join-Path $dir 'bridge.log'
if (Test-Path $log) {
    # Read without locking the file the bridge is writing.
    $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite'); $sr = New-Object IO.StreamReader($fs)
    $lines = $sr.ReadToEnd() -split "`r?`n"; $sr.Close(); $fs.Close()
    Write-Host "Last lines of $log :"
    $lines | Select-Object -Last 8 | ForEach-Object { Write-Host "  $_" }
}
if ($ok) {
    Write-Host "`nOK: bridge $new is answering on port $port. COREI reconnects within a minute; the first connection per account takes about a minute more (terminal copy)." -ForegroundColor Green
} else {
    Write-Host "`nThe bridge is not answering on port $port. Send the log lines above. To go back: Copy-Item -Force `"$backup`" `"$dst`"; Start-ScheduledTask -TaskName `"$task`"" -ForegroundColor Red
    exit 1
}
