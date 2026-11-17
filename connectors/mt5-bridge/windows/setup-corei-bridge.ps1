<#
COREI MT5 bridge on Windows - a Windows VPS or a Windows PC (DEC-69).

Puts the official MT5 terminal behind COREI's bridge (TLS, pinned certificate, token) and keeps it running:
  * Python 64-bit + the MetaTrader5 package (MetaQuotes' own) + numpy + cryptography
  * the bridge in C:\corei-bridge\<Name>, its certificate and token
  * a scheduled task that starts the bridge at log-on and restarts it if it stops
  * how COREI reaches it - choose one:
      -Mode Direct : COREI connects to this machine's port (a VPS with a public IP). A firewall rule allows
                     ONLY COREI's server IP (-CoreiIp).
      -Mode Tunnel : no inbound port at all (a PC at home/office behind a router, or a locked-down VPS). This
                     machine opens an SSH tunnel OUT to the COREI server; COREI connects to 127.0.0.1:<TunnelPort>
                     on its own side. Uses Windows' built-in OpenSSH client and a key made here.

Run in PowerShell as Administrator, in the folder holding this script and corei_mt5_bridge.py
(Setup > the account > Routes > "Windows setup files" downloads both):

  VPS:  powershell -ExecutionPolicy Bypass -File setup-corei-bridge.ps1 -Name metaquotes-demo -Mode Direct -CoreiIp 203.0.113.10
  PC:   powershell -ExecutionPolicy Bypass -File setup-corei-bridge.ps1 -Name metaquotes-demo -Mode Tunnel -SshTarget coreeye@corei-os-mini.coreeye.in -TunnelPort 9450

Every account of the firm can use this one bridge: for each login COREI sends, the bridge makes a portable copy of the
installed terminal (once, under C:\corei-bridge\<Name>\terminals\<login>) and runs it, so several accounts stream at
the same time through the same host and port. The installed terminal itself is only the template.
-MaxTerminals (default 20) caps how many run at once: an idle terminal (no stream, least recently used) is closed to
make room, so many accounts can share a machine for account reads; streaming accounts keep theirs.
At the end it prints the host, port, fingerprint and token to enter in COREI (Setup > the account > Routes > Add route).
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-z0-9][a-z0-9-]{1,40}$')][string]$Name,
    [ValidateSet('Direct', 'Tunnel')][string]$Mode = 'Direct',
    [string]$Terminal = '',
    [int]$Port = 9443,
    [string]$CoreiIp = '',
    [string]$SshTarget = '',
    [int]$TunnelPort = 9450,
    [int]$MaxTerminals = 20,
    [string]$Root = 'C:\corei-bridge',
    [switch]$Enrolling   # run by install-corei-bridge.ps1: COREI receives fingerprint and token by enrolment, so they are not printed
)
$ErrorActionPreference = 'Stop'
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail 'Run PowerShell as Administrator.' }
if ($Mode -eq 'Direct' -and -not $CoreiIp) { Fail 'Direct mode needs -CoreiIp (COREI server''s public IP, shown on Setup > Overview).' }
if ($Mode -eq 'Tunnel' -and -not $SshTarget) { Fail 'Tunnel mode needs -SshTarget user@corei-server.' }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeSrc = Join-Path $here 'corei_mt5_bridge.py'
if (-not (Test-Path $bridgeSrc)) { Fail "corei_mt5_bridge.py must be next to this script ($here)." }
$dir = Join-Path $Root $Name
New-Item -ItemType Directory -Force -Path $dir | Out-Null

Step '1/6 MT5 terminal'
if (-not $Terminal) {
    $Terminal = Get-ChildItem -Path "$env:ProgramFiles", "${env:ProgramFiles(x86)}" -Filter terminal64.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Terminal -or -not (Test-Path $Terminal)) {
    Fail 'No terminal64.exe found. Install the broker''s MT5 terminal (or MetaQuotes'' mt5setup.exe), log in once by hand, then run this again (or pass -Terminal "<path>\terminal64.exe").'
}
Write-Host "Terminal: $Terminal"

Step '2/6 Python 64-bit'
function Find-Python {
    $probe = "import sys,struct;print(sys.executable if struct.calcsize('P')==8 and sys.version_info>=(3,9) else '')"
    foreach ($v in @('-3.12', '-3.11', '-3')) {
        try { $out = & py $v -c $probe 2>$null; if ($out) { return $out.Trim() } } catch {}
    }
    foreach ($exe in @('python', "$env:ProgramFiles\Python312\python.exe", "$env:ProgramFiles\Python311\python.exe")) {
        try { $out = & $exe -c $probe 2>$null; if ($out) { return $out.Trim() } } catch {}
    }
    return $null
}
$pyExe = Find-Python
if (-not $pyExe) {
    Write-Host 'Installing Python 3.12 (64-bit) from python.org...'
    $inst = Join-Path $env:TEMP 'python-3.12-amd64.exe'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile $inst
    Start-Process -Wait -FilePath $inst -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0'
    $pyExe = Find-Python
    if (-not $pyExe) { Fail 'Python did not install; install Python 3.12 64-bit from python.org by hand and run this again.' }
}
Write-Host "Python: $pyExe"

Step '3/6 MetaTrader5 package, numpy, cryptography'
& $pyExe -m pip install --quiet --disable-pip-version-check --upgrade MetaTrader5 numpy cryptography
& $pyExe -c "import MetaTrader5, numpy; print('MetaTrader5', MetaTrader5.__version__, '- numpy', numpy.__version__)"

Step "4/6 Bridge in $dir"
Copy-Item -Force $bridgeSrc (Join-Path $dir 'corei_mt5_bridge.py')
$init = & $pyExe (Join-Path $dir 'corei_mt5_bridge.py') --init --state $dir | ConvertFrom-Json
$bind = if ($Mode -eq 'Direct') { '0.0.0.0' } else { '127.0.0.1' }
$runBridge = Join-Path $dir 'run-bridge.cmd'
@"
@echo off
rem COREI MT5 bridge ($Name): restarts itself if it stops.
:loop
"$pyExe" "$dir\corei_mt5_bridge.py" --terminal "$Terminal" --terminals-root "$dir\terminals" --max-terminals $MaxTerminals --bind $bind --port $Port --state "$dir" >> "$dir\bridge.log" 2>&1
timeout /t 10 /nobreak >nul
goto loop
"@ | Set-Content -Encoding ASCII $runBridge

Step '4b Clock: Windows time service synchronised (latency figures depend on this clock)'
try {
    & w32tm /config /manualpeerlist:"time.windows.com,0x9 pool.ntp.org,0x9" /syncfromflags:manual /update | Out-Null
    Set-Service w32time -StartupType Automatic; Restart-Service w32time
    & w32tm /resync /nowait | Out-Null
    Write-Host (& w32tm /query /status | Select-String 'Source|Last Successful' | ForEach-Object { $_.Line.Trim() }) -Separator ' - '
} catch { Write-Host "Time sync could not be configured: $_" }

Step '5/6 Keep it running (scheduled task at log-on; the terminal needs a desktop session)'
$user = "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$trigger = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $user),
    # Every 5 minutes: starts it again if it stopped (a running copy is left alone).
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650))
)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -Force -TaskName "COREI MT5 bridge $Name" -Action (New-ScheduledTaskAction -Execute $runBridge) -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName "COREI MT5 bridge $Name"
Write-Host "Task 'COREI MT5 bridge $Name' registered and started; it is started again every 5 minutes if it stops."
Write-Host 'Leave Remote Desktop by closing its window (do not Sign out), and set this user to sign in automatically after a reboot (Sysinternals Autologon).'

Step "6/6 How COREI reaches this machine: $Mode"
if ($Mode -eq 'Direct') {
    Get-NetFirewallRule -DisplayName "COREI MT5 bridge $Name" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "COREI MT5 bridge $Name" -Direction Inbound -Protocol TCP -LocalPort $Port -RemoteAddress $CoreiIp -Action Allow | Out-Null
    Write-Host "Firewall: TCP $Port open to $CoreiIp only. If the VPS provider has its own firewall/security group, allow the same there."
    $hostForCorei = $null
    try { $hostForCorei = (Invoke-RestMethod -UseBasicParsing -Uri 'https://api.ipify.org' -TimeoutSec 10) } catch {}
    if (-not $hostForCorei) { $hostForCorei = '<this VPS''s public IP>' }
    $portForCorei = $Port
} else {
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
        Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0 | Out-Null
    }
    $key = Join-Path $dir 'tunnel_ed25519'
    if (-not (Test-Path $key)) { & cmd /c "ssh-keygen.exe -q -t ed25519 -N """" -C corei-tunnel-$Name -f ""$key""" }
    # OpenSSH refuses a private key others can read: only this user may.
    & icacls.exe $key /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
    $runTunnel = Join-Path $dir 'run-tunnel.cmd'
    @"
@echo off
rem SSH tunnel OUT to the COREI server: COREI reaches this bridge at 127.0.0.1:$TunnelPort on its own side.
:loop
ssh.exe -N -i "$key" -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -R 127.0.0.1:${TunnelPort}:127.0.0.1:$Port $SshTarget >> "$dir\tunnel.log" 2>&1
timeout /t 10 /nobreak >nul
goto loop
"@ | Set-Content -Encoding ASCII $runTunnel
    Register-ScheduledTask -Force -TaskName "COREI MT5 tunnel $Name" -Action (New-ScheduledTaskAction -Execute $runTunnel) -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    $pub = (Get-Content "$key.pub").Trim()
    if (-not $Enrolling) {
        Write-Host ''
        Write-Host 'ON THE COREI SERVER, allow this key to open only this tunnel (one line, then start the task below):' -ForegroundColor Yellow
        Write-Host "  echo 'restrict,port-forwarding,permitlisten=""127.0.0.1:$TunnelPort"",command=""/bin/false"" $pub' >> ~/.ssh/authorized_keys"
        Write-Host "Then here: Start-ScheduledTask -TaskName 'COREI MT5 tunnel $Name'"
    }
    $hostForCorei = '127.0.0.1'
    $portForCorei = $TunnelPort
}

if ($Enrolling) { exit 0 }
Write-Host ''
Write-Host '==== Enter in COREI: Setup > the account > Routes > Add route > Terminal bridge ====' -ForegroundColor Green
Write-Host ("  Location     : " + $(if ($Mode -eq 'Direct') { 'Windows VPS' } else { 'Windows PC (or VPS) through SSH tunnel' }))
Write-Host "  Host         : $hostForCorei"
Write-Host "  Port         : $portForCorei"
Write-Host "  Fingerprint  : $($init.fingerprint)"
Write-Host "  Token        : $($init.token)"
Write-Host '  (the token is also in ' $init.token_file '; keep it secret)'
Write-Host ''
Write-Host "Bridge log: $dir\bridge.log. Log the terminal in to the account once by hand if it has never been."
