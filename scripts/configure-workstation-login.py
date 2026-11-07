"""Set up the single operator locally; never print or accept a password in argv."""
from pathlib import Path
import getpass
import hashlib
import os
import re
import secrets
import sys

path = Path('/etc/corei-os-mini/workstation.env')
if os.geteuid() != 0:
    sys.exit('Run sudo python3 scripts/configure-workstation-login.py')
if not path.exists() or path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
    sys.exit('Run database setup first; its root-owned environment file must have mode 600.')
text = path.read_text()
if 'COREI_OPERATOR_USER=' in text or 'COREI_OPERATOR_PASSWORD_HASH=' in text:
    sys.exit('Operator configuration already exists; it has been preserved.')
username = input('New workstation username [coreeye]: ').strip() or 'coreeye'
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username):
    sys.exit('Use 1–64 letters, numbers, underscores, dots or hyphens.')
password = getpass.getpass('New workstation password (at least 12 characters): ')
if len(password) < 12 or len(password) > 256:
    sys.exit('Password must contain 12–256 characters. Nothing changed.')
if password != getpass.getpass('Confirm password: '):
    sys.exit('Passwords differ. Nothing changed.')
salt = secrets.token_hex(16)
digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
encoded = 'pbkdf2_sha256$600000$' + salt + '$' + digest
# Atomic replacement; retain the database configuration.
temp = path.with_name('workstation.env.' + secrets.token_hex(8))
try:
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(text.rstrip() + '\nCOREI_OPERATOR_USER=' + username + '\nCOREI_OPERATOR_PASSWORD_HASH=' + encoded + '\n')
    os.replace(temp, path)
finally:
    temp.unlink(missing_ok=True)
print('Operator login configured. Deploy/restart the workstation to load it.')
