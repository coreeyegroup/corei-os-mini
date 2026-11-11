#!/usr/bin/env python3
"""Rotate the credential encryption key (audit: key separation and rotation).

Re-encrypts every stored secret — account and connection credentials, broker session
tokens, pending change-request secrets and alert-channel secrets — with a new random
key in ONE database transaction, then installs the new key and its fingerprint marker.
If anything fails, the database is unchanged and the old key stays in place.

Run as root on the server, with the workstation stopped:
  sudo systemctl stop corei-os-mini-workstation
  sudo /home/coreeye/corei-os-mini/.venv-workstation/bin/python scripts/rotate-credential-key.py
  sudo systemctl start corei-os-mini-workstation
The old key is kept as broker-credentials.key.retired-<timestamp> (mode 0600) until you
have confirmed the new key works and stored its backup; then delete it.

Options: --key-file PATH (default /etc/corei-os-mini/broker-credentials.key),
         --env-file PATH (default /etc/corei-os-mini/workstation.env), --dry-run.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'src'))
from corei_os_mini import credentials  # noqa: E402

TABLES = (  # (table, id column used as AEAD context, ciphertext column)
    ('cfg_credentials', 'id', 'ciphertext'),
    ('cfg_session_tokens', 'account_id', 'ciphertext'),
    ('cfg_change_requests', 'id', 'secret'),
    ('cfg_alert_channels', 'id', 'secret'),
)


def database_url(env_file: Path) -> str:
    if os.getenv('COREI_DATABASE_URL'):
        return os.environ['COREI_DATABASE_URL']
    for line in env_file.read_text().splitlines():
        if line.startswith('COREI_DATABASE_URL='):
            return line.split('=', 1)[1].strip()
    sys.exit(f'COREI_DATABASE_URL not found in {env_file}.')


def rotate(url: str, old: bytes, new: bytes, dry_run: bool) -> int:
    import psycopg
    count = 0
    new_id = credentials.key_id(new)
    with psycopg.connect(url) as db:
        for table, id_column, column in TABLES:
            exists = db.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0]
            if not exists:
                continue
            rows = db.execute(f'SELECT {id_column}, {column} FROM {table} WHERE {column} IS NOT NULL FOR UPDATE').fetchall()
            for record_id, ciphertext in rows:
                fields = credentials.decrypt(record_id, bytes(ciphertext), key=old)
                updated = credentials.encrypt(record_id, fields, key=new)
                has_key_id = table in ('cfg_credentials', 'cfg_session_tokens')
                db.execute(f'UPDATE {table} SET {column}=%s' + (', key_id=%s' if has_key_id else '') + f' WHERE {id_column}=%s',
                           (updated, new_id, record_id) if has_key_id else (updated, record_id))
                count += 1
        if dry_run:
            db.rollback()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file', default='/etc/corei-os-mini/broker-credentials.key')
    parser.add_argument('--env-file', default='/etc/corei-os-mini/workstation.env')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    key_path = Path(args.key_file)
    if key_path.is_symlink():
        sys.exit('Refusing a symlinked key file.')
    old = key_path.read_bytes()
    if len(old) != 32:
        sys.exit('Current key is not 32 bytes; nothing changed.')
    new = os.urandom(32)
    count = rotate(database_url(Path(args.env_file)), old, new, args.dry_run)
    if args.dry_run:
        print(f'Dry run: {count} secrets can be re-encrypted. Nothing changed.')
        return
    stat = key_path.stat()
    retired = key_path.with_name(f'{key_path.name}.retired-{time.strftime("%Y%m%d%H%M%S")}')
    os.replace(key_path, retired)
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as file:
        file.write(new)
        file.flush()
        os.fsync(file.fileno())
    os.chown(key_path, stat.st_uid, stat.st_gid)
    marker = key_path.with_name(key_path.name + '.sha256')
    if marker.exists() and not marker.is_symlink():
        marker.write_text(hashlib.sha256(new).hexdigest() + '\n')
    print(f'Rotated: {count} secrets re-encrypted. New key id {credentials.key_id(new)}. '
          f'Old key kept at {retired}; back up the new key, confirm sign-in works, then delete the old one.')


if __name__ == '__main__':
    main()
