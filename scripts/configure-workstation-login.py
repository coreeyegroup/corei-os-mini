"""Set up the single operator locally; never print or accept a password in argv."""
import argparse
from pathlib import Path
import getpass
import hashlib
import os
import re
import secrets
import sys

parser = argparse.ArgumentParser(description='Configure the workstation operator without exposing passwords.')
parser.add_argument('--reset-password', action='store_true', help='Replace the existing operator password; retain username and database settings.')
args = parser.parse_args()
path = Path('/etc/corei-os-mini/workstation.env')
if os.geteuid() != 0:
    sys.exit('Run sudo python3 scripts/configure-workstation-login.py')
if not path.exists() or path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
    sys.exit('Run database setup first; its root-owned environment file must have mode 600.')
text = path.read_text()
users = [line.split('=', 1)[1] for line in text.splitlines() if line.startswith('COREI_OPERATOR_USER=')]
hashes = [line.split('=', 1)[1] for line in text.splitlines() if line.startswith('COREI_OPERATOR_PASSWORD_HASH=')]
if users or hashes:
    if len(users) != 1 or len(hashes) != 1:
        sys.exit('Operator configuration is incomplete or duplicated. Nothing changed.')
    if not args.reset_password:
        print('Operator configuration already exists; it has been preserved.')
        sys.exit(0)
    username = users[0]
else:
    if args.reset_password:
        sys.exit('No operator exists. Run without --reset-password to create one.')
    username = input('New workstation username [coreeye]: ').strip() or 'coreeye'
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username):
    sys.exit('Use 1–64 letters, numbers, underscores, dots or hyphens.')
password = getpass.getpass('New workstation password (at least 6 characters): ')
if len(password) < 6 or len(password) > 256:
    sys.exit('Password must contain 6–256 characters. Nothing changed.')
if password != getpass.getpass('Confirm password: '):
    sys.exit('Passwords differ. Nothing changed.')
salt = secrets.token_hex(16)
digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
encoded = 'pbkdf2_sha256$600000$' + salt + '$' + digest
# Atomic replacement; retain database settings and replace only operator keys.
text = '\n'.join(line for line in text.splitlines() if not line.startswith(('COREI_OPERATOR_USER=', 'COREI_OPERATOR_PASSWORD_HASH=')))
temp = path.with_name('workstation.env.' + secrets.token_hex(8))
try:
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(text.rstrip() + '\nCOREI_OPERATOR_USER=' + username + '\nCOREI_OPERATOR_PASSWORD_HASH=' + encoded + '\n')
    os.replace(temp, path)
finally:
    temp.unlink(missing_ok=True)
print('Operator login configured. Deploy/restart the workstation to load it. Password changes invalidate previous sessions after restart.')
