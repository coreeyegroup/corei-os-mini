<#
COREI MT5 bridge - install and register a Windows machine (VPS, PC or server) in one step (DEC-75).

What it does, in order:
  1. Reads what COREI set for this machine (name, how COREI reaches it, port, pool size) with the one-time code
     from COREI: Setup > Bridge nodes > Connect a Windows machine.
  2. Downloads the bridge and its set-up scripts from COREI into C:\corei-setup (always the current versions).
  3. Runs setup-corei-bridge.ps1: Python 64-bit (installed if missing), the MetaTrader5 package, the bridge in
     C:\corei-bridge\<name>, a scheduled task that keeps it running, the clock sync, and either a firewall rule open
     to COREI only (Direct) or an outgoing SSH tunnel (Tunnel).
  4. Registers the machine with COREI: certificate fingerprint, token (sent once over HTTPS, never shown) and how to
     reach it. COREI then checks the connection. Nothing has to be copied by hand.

Run in PowerShell as Administrator (COREI shows these two lines with your code filled in):
  New-Item -ItemType Directory -Force C:\corei-setup | Out-Null; Invoke-WebRequest https://corei-os-mini.coreeye.in/api/bridge-enroll/files/install-corei-bridge.ps1 -OutFile C:\corei-setup\install-corei-bridge.ps1
  powershell -ExecutionPolicy Bypass -File C:\corei-setup\install-corei-bridge.ps1 -Corei https://corei-os-mini.coreeye.in -Code ABCDE-FGHJK-LMNPQ-RSTUV

Update an installed bridge to the current version (keeps its certificate and token):
  powershell -ExecutionPolicy Bypass -File C:\corei-setup\install-corei-bridge.ps1 -Corei https://corei-os-mini.coreeye.in -Update -Name <node name>

Requirements: the broker's MT5 terminal installed and logged in once by hand (any account). Windows 10/11 or
Windows Server 2016+.
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^https://[A-Za-z0-9.-]+(:\d+)?/?$')][string]$Corei,
    [string]$Code = '',
    [switch]$Update,
    [ValidatePattern('^$|^[a-z0-9][a-z0-9-]{1,40}$')][string]$Name = '',
    [string]$Terminal = '',
    [string]$Setup = 'C:\corei-setup',
    [string]$Root = 'C:\corei-bridge'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }
function Ok($text) { Write-Host $text -ForegroundColor Green }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail 'Run PowerShell as Administrator (Start > type PowerShell > right-click > Run as administrator).' }
if (-not $Code -and -not $Update) { Fail 'Give -Code <code from COREI> to install, or -Update -Name <node> to update.' }
if ($Update -and -not $Name) { Fail '-Update needs -Name (the node name shown in COREI).' }
$Corei = $Corei.TrimEnd('/')
New-Item -ItemType Directory -Force -Path $Setup | Out-Null

if ($Code) {
    Step '1/4 What COREI set for this machine'
    try { $info = Invoke-RestMethod -UseBasicParsing -Uri "$Corei/api/bridge-enroll/$Code" -TimeoutSec 30 }
    catch {
        $msg = $_.ErrorDetails.Message
        try { $msg = ($msg | ConvertFrom-Json).detail } catch {}
        Fail "COREI did not accept the code: $msg"
    }
    $Name = $info.name
    Write-Host "Node: $Name - mode $($info.mode) - port $($info.port) - up to $($info.max_terminals) terminals at once"
}

Step '2/4 Download the current bridge and scripts from COREI'
foreach ($f in @('corei_mt5_bridge.py', 'setup-corei-bridge.ps1', 'update-corei-bridge.ps1')) {
    $target = Join-Path $Setup $f
    Invoke-WebRequest -UseBasicParsing -Uri "$Corei/api/bridge-enroll/files/$f" -OutFile $target
    Write-Host "  $target"
}
$version = (Select-String -Path (Join-Path $Setup 'corei_mt5_bridge.py') -Pattern "BRIDGE_VERSION = '([0-9.]+)'").Matches[0].Groups[1].Value
Write-Host "Bridge version: $version"

if ($Update) {
    Step '3/4 Update the installed bridge (certificate and token stay the same)'
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Setup 'update-corei-bridge.ps1') -Name $Name -Root $Root
    if ($LASTEXITCODE -ne 0) { Fail 'The update stopped; see the lines above.' }
    Ok "Updated to $version. COREI shows the new version at its next check (Setup > Bridge nodes > Check now)."
    exit 0
}

Step '3/4 Install the bridge'
$psArgs = @('-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Setup 'setup-corei-bridge.ps1'), '-Name', $Name, '-Port', $info.port,
          '-MaxTerminals', $info.max_terminals, '-Root', $Root, '-Enrolling')
if ($Terminal) { $psArgs += @('-Terminal', $Terminal) }
if ($info.mode -eq 'DIRECT') {
    if (-not $info.corei_ip) { Fail 'COREI did not report its public IP (set COREI_EGRESS_IP on the server).' }
    $psArgs += @('-Mode', 'Direct', '-CoreiIp', $info.corei_ip)
} else {
    $psArgs += @('-Mode', 'Tunnel', '-SshTarget', $info.ssh_target, '-TunnelPort', $info.tunnel_port)
}
& powershell @psArgs
if ($LASTEXITCODE -ne 0) { Fail 'The installation stopped; see the lines above. Fix it and run this same command again (the code stays valid for 60 minutes).' }

Step '4/4 Register with COREI'
$dir = Join-Path $Root $Name
$runCmd = Get-Content (Join-Path $dir 'run-bridge.cmd') -Raw
$pyExe = ([regex]'"([^"]*python[^"]*\.exe)"').Match($runCmd).Groups[1].Value
$enrolArgs = @((Join-Path $dir 'corei_mt5_bridge.py'), '--enroll', $Corei, '--code', $Code, '--state', $dir, '--port', $info.port, '--max-terminals', $info.max_terminals)
if ($info.mode -eq 'TUNNEL') { $enrolArgs += @('--ssh-public-key', (Get-Content (Join-Path $dir 'tunnel_ed25519.pub')).Trim()) }
$out = & $pyExe @enrolArgs | Select-Object -Last 1
try { $res = $out | ConvertFrom-Json } catch { Fail "Unexpected answer: $out" }
if (-not $res.ok) { Fail "COREI did not register the machine: $($res.error)" }

if ($info.mode -eq 'TUNNEL') {
    if ($res.tunnel.automatic) {
        Start-ScheduledTask -TaskName "COREI MT5 tunnel $Name"
        Ok 'Tunnel key accepted by COREI; tunnel started.'
    } else {
        Write-Host 'The COREI server does not accept tunnel keys automatically yet. An administrator runs once on the COREI server:' -ForegroundColor Yellow
        Write-Host '  sudo bash scripts/enable-bridge-tunnels.sh'
        Write-Host "then in COREI: Setup > Bridge nodes > $Name > Check now. After that, start the tunnel here:"
        Write-Host "  Start-ScheduledTask -TaskName 'COREI MT5 tunnel $Name'"
    }
} elseif ($res.check.ok) {
    Ok ("COREI reached this bridge: round trip $($res.check.round_trip_ms) ms, clock difference $($res.check.clock_skew_ms) ms, bridge $($res.check.version).")
} else {
    Write-Host "Registered, but COREI could not reach the bridge yet: $($res.check.error)" -ForegroundColor Yellow
    Write-Host "If the VPS provider has its own firewall (security group), allow TCP $($info.port) from $($info.corei_ip) there, then: COREI > Setup > Bridge nodes > $Name > Check now."
}
Write-Host ''
Ok "Done. In COREI: Setup > Bridge nodes > $Name > Use for accounts (or tick 'Default for new MT5 accounts')."
Write-Host "Bridge log: $dir\bridge.log"
