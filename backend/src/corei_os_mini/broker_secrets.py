"""Authenticated encryption. Business records hold secret IDs, never passwords."""
import os
import stat
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def credential_key() -> bytes:
    path = Path(os.getenv('COREI_BROKER_KEY_FILE', '/etc/corei-os-mini/broker-credentials.key'))
    with path.open('rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Credential key permissions must be 0600')
        key = file.read(33)
    if len(key) != 32:
        raise ValueError('Invalid credential key')
    return key


def encrypt_password(secret_id: UUID, password: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(credential_key()).encrypt(
        nonce, password.encode('utf-8'), ('corei-broker-v1:' + str(secret_id)).encode())


def decrypt_password(secret_id: UUID, ciphertext: bytes) -> str:
    # Internal adapter use only; there is deliberately no HTTP read-secret endpoint.
    return AESGCM(credential_key()).decrypt(ciphertext[:12], ciphertext[12:],
        ('corei-broker-v1:' + str(secret_id)).encode()).decode('utf-8')
