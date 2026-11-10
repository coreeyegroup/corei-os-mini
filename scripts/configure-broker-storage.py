#!/usr/bin/env python3
"""Create the credential encryption key once. Never print or replace a saved key."""
import hashlib
import os
from pathlib import Path
import pwd
import stat
import sys

if os.geteuid() != 0 or len(sys.argv) != 2:
    sys.exit('Use bash scripts/setup-broker-accounts.sh as the deployment user.')
user = pwd.getpwnam(sys.argv[1])
if user.pw_uid == 0:
    sys.exit('Expected the normal workstation service user.')
folder = Path('/etc/corei-os-mini')
folder.mkdir(mode=0o755, parents=True, exist_ok=True)
path = folder / 'broker-credentials.key'
marker = folder / 'broker-credentials.key.sha256'
if path.is_symlink() or marker.is_symlink():
    sys.exit('Refusing symlink credential files.')
if not path.exists():
    if marker.exists():
        sys.exit('Credential key is missing. Restore its backup; refusing to replace it.')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as file:
        file.write(os.urandom(32))
        file.flush()
        os.fsync(file.fileno())
key = path.read_bytes()
if len(key) != 32 or not stat.S_ISREG(path.stat().st_mode):
    sys.exit('Invalid credential key; no change made.')
digest = hashlib.sha256(key).hexdigest()
if marker.exists() and marker.read_text().strip() != digest:
    sys.exit('Credential key fingerprint changed. Restore the original key.')
if not marker.exists():
    with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
        file.write(digest + '\n')
os.chown(path,user.pw_uid,user.pw_gid)
os.chmod(path,0o600)
print('Credential key ready. Back up this key securely with the database; losing it loses access to saved passwords.')
