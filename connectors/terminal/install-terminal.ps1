param(
    [Parameter(Mandatory=$true)][ValidateSet('MT4','MT5')][string]$Platform,
    [Parameter(Mandatory=$true)][string]$DataFolder
)
$ErrorActionPreference = 'Stop'
if ($Platform -eq 'MT5') { $language = 'MQL5'; $extension = 'mq5' }
else { $language = 'MQL4'; $extension = 'mq4' }
$source = Join-Path $PSScriptRoot "COREIOSMiniTerminalCheck.$extension"
$experts = Join-Path $DataFolder "$language\Experts"
if (!(Test-Path -LiteralPath $source -PathType Leaf) -or !(Test-Path -LiteralPath $experts -PathType Container)) {
    throw 'Source or Experts folder missing. Use File > Open Data Folder in the correct terminal.'
}
$destination = Join-Path $experts 'COREIOSMini'
if (Test-Path -LiteralPath $destination) {
    if (((Get-Item -LiteralPath $destination).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Installation directory must not be a link or junction.'
    }
} else { New-Item -ItemType Directory -Path $destination | Out-Null }
$target = Join-Path $destination "COREIOSMiniTerminalCheck.$extension"
if (Test-Path -LiteralPath $target) {
    $existing = Get-Item -LiteralPath $target
    if ($existing.PSIsContainer -or (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw 'Existing target must be a regular file.'
    }
    Copy-Item -LiteralPath $target -Destination "$target.backup.$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffff'))"
}
Copy-Item -LiteralPath $source -Destination $target
Write-Output "Installed source: $target"
Write-Output 'Next: open source in MetaEditor and compile (F7). Require 0 errors.'
Write-Output 'Attach COREIOSMiniTerminalCheck to a chart. VPS connection remains unavailable.'
