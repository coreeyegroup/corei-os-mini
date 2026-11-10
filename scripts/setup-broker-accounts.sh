#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $(id -u) == 0 || $(uname -m) != x86_64 ]]; then
  echo 'Run as the normal workstation user on Ubuntu x86_64.' >&2; exit 1
fi
TASK_BUILD=$(mktemp -d)
trap 'rm -rf "$TASK_BUILD"' EXIT
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
curl --fail --location --retry 2 --connect-timeout 15 --max-time 600 \
  https://builds.dotnet.microsoft.com/dotnet/Sdk/8.0.427/dotnet-sdk-8.0.427-linux-x64.tar.gz \
  -o "$TASK_BUILD/dotnet.tar.gz"
curl --fail --location --retry 2 --connect-timeout 15 --max-time 120 \
  https://api.nuget.org/v3-flatcontainer/mtapi.be.mt5net/5.4850.0/mtapi.be.mt5net.5.4850.0.nupkg \
  -o "$TASK_BUILD/sdk.nupkg"
python3 - "$TASK_BUILD" <<'PY'
import hashlib, pathlib, sys, zipfile
p=pathlib.Path(sys.argv[1])
for name, expected in {
 'dotnet.tar.gz':'9f2a52ab827f1530b972b41f7c5d211c21a16ea8b3217b2846191f746f626149',
 'sdk.nupkg':'bcade45611435b501d6822e835f34fb8662d11de7bc8adb3a529ad6e88ab3f36',
}.items():
 if hashlib.sha256((p/name).read_bytes()).hexdigest()!=expected:
  sys.exit('Downloaded dependency checksum mismatch: '+name)
with zipfile.ZipFile(p/'sdk.nupkg') as z:
 (p/'mt5api.dll').write_bytes(z.read('lib/netstandard2.0/mt5api.dll'))
PY
mkdir "$TASK_BUILD/dotnet"
tar --no-same-owner -xzf "$TASK_BUILD/dotnet.tar.gz" -C "$TASK_BUILD/dotnet"
"$TASK_BUILD/dotnet/dotnet" publish connectors/mt5-direct-check/ServerCatalog.csproj \
  -c Release -r linux-x64 --self-contained true -p:Mt5SdkPath="$TASK_BUILD/mt5api.dll" \
  -o "$TASK_BUILD/publish"
# Versioned immutable installation, followed by one atomic symlink switch.
TASK_RELEASE="/opt/corei-os-mini/server-catalog-$(date +%Y%m%d%H%M%S)-$$"
sudo install -d -m 755 "$TASK_RELEASE"
sudo cp -a "$TASK_BUILD/publish/." "$TASK_RELEASE/"
sudo chown -R root:root "$TASK_RELEASE"
sudo chmod -R go-w "$TASK_RELEASE"
sudo ln -s "$TASK_RELEASE" "$TASK_RELEASE.link"
sudo mv -Tf "$TASK_RELEASE.link" /opt/corei-os-mini/server-catalog
sudo python3 scripts/configure-broker-storage.py "$(id -un)"
printf '%s\n' 'Server-file reader and credential key installed. Apply migrations, then deploy the workstation.'
