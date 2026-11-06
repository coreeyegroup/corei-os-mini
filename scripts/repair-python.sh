#!/usr/bin/env bash
# Build a verified Python source release into the project's isolated toolchain.
set -euo pipefail
TASK_PREFIX=/opt/corei-os-mini/toolchain/python-3.14.10
TASK_PYTHON="$TASK_PREFIX/bin/python3.14"
if [[ -x "$TASK_PYTHON" ]] && "$TASK_PYTHON" -I -c 'import ssl, hashlib, zlib, ctypes, sqlite3, bz2, lzma, ensurepip; ssl.create_default_context()'; then
  echo "Project Python already has the required modules: $TASK_PYTHON"
  exit 0
fi
if [[ "$EUID" == 0 ]]; then
  echo 'Run this script as coreeye; it requests sudo only for packages and installation.' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libssl-dev zlib1g-dev libffi-dev libbz2-dev liblzma-dev libsqlite3-dev libreadline-dev libncursesw5-dev libgdbm-dev uuid-dev curl ca-certificates xz-utils
TASK_BUILD=$(mktemp -d /tmp/corei-os-mini-python.XXXXXXXX)
printf 'Build directory: %s\n' "$TASK_BUILD"
cd "$TASK_BUILD"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 -o Python-3.14.10.tar.xz https://www.python.org/ftp/python/3.14.10/Python-3.14.10.tar.xz
printf '%s\n' 'de414a57119abef52784d8993e341d53aed635fc2b05c2607d9f98a2c5b72032  Python-3.14.10.tar.xz' | sha256sum --check -
tar -xJf Python-3.14.10.tar.xz
cd Python-3.14.10
./configure --prefix="$TASK_PREFIX" --with-openssl=/usr --with-ensurepip=install
make -j2
./python -I -c 'import ssl, hashlib, zlib, ctypes, sqlite3, bz2, lzma, ensurepip; print(ssl.OPENSSL_VERSION)'
sudo make altinstall
"$TASK_PYTHON" -I -c 'import ssl, urllib.request; print(ssl.OPENSSL_VERSION); r=urllib.request.urlopen("https://pypi.org/simple/setuptools/", timeout=30); print("Verified HTTPS to PyPI:", r.status); r.close()'
printf 'Project Python ready: %s\nBuild files retained at: %s\n' "$TASK_PYTHON" "$TASK_BUILD"
