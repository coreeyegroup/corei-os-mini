"""Credential vault: authenticated encryption of connection and account secrets.

Owns: encrypting/decrypting secret field sets with AES-256-GCM, bound to the
credential's own ID so ciphertext cannot be swapped between records.
Does not own: which record a credential belongs to (config_store) or any HTTP
route that returns secrets. There is deliberately no API that reads a secret back;
decrypt() exists only for provider adapters running inside this process.

Key file: COREI_BROKER_KEY_FILE (default /etc/corei-os-mini/broker-credentials.key),
32 random bytes, mode 0600, created by scripts/configure-broker-storage.py.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_CONTEXT = b'corei-credential-v2:'


class CredentialStorageUnavailable(RuntimeError):
    pass


def _key() -> bytes:
    path = Path(os.getenv('COREI_BROKER_KEY_FILE', '/etc/corei-os-mini/broker-credentials.key'))
    try:
        with path.open('rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise CredentialStorageUnavailable('Credential key file must be a regular file with mode 0600')
            key = file.read(33)
    except OSError:
        raise CredentialStorageUnavailable('Credential key file is missing or unreadable') from None
    if len(key) != 32:
        raise CredentialStorageUnavailable('Credential key file must contain exactly 32 bytes')
    return key


def key_id(key: bytes | None = None) -> str:
    """Short fingerprint of the key (not the key): which key encrypted a record, so keys
    can be rotated and old records found."""
    import hashlib
    return hashlib.sha256(b'corei-key-id:' + (key or _key())).hexdigest()[:16]


def storage_ready() -> bool:
    try:
        _key()
        return True
    except CredentialStorageUnavailable:
        return False


def encrypt(credential_id: UUID, fields: dict[str, str], key: bytes | None = None) -> bytes:
    nonce = os.urandom(12)
    plaintext = json.dumps(fields, separators=(',', ':'), sort_keys=True).encode()
    return nonce + AESGCM(key or _key()).encrypt(nonce, plaintext, _CONTEXT + str(credential_id).encode())


def decrypt(credential_id: UUID, ciphertext: bytes, key: bytes | None = None) -> dict[str, str]:
    """For in-process use only, through config_store.use_credential (which logs each use)
    or an approved change request. Never expose through an HTTP route."""
    plaintext = AESGCM(key or _key()).decrypt(ciphertext[:12], ciphertext[12:], _CONTEXT + str(credential_id).encode())
    return json.loads(plaintext)
