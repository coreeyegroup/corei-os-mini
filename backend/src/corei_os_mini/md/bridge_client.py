"""Client for the COREI MT5 terminal bridge (connectors/mt5-bridge): TLS with the bridge's certificate
pinned by SHA-256 fingerprint, a shared token, then the same newline-JSON protocol as the direct reader.
The password travels once, over TLS, in the first line; nothing here stores it."""
from __future__ import annotations

import hashlib
import json
import socket
import ssl
from collections.abc import Iterator


class BridgeError(Exception):
    """The bridge could not be reached, or its certificate is not the pinned one."""


def _normalise(fingerprint: str) -> str:
    return fingerprint.replace(':', '').replace(' ', '').upper()


class BridgeTransport:
    """send(dict) writes one line; lines() yields the bridge's lines until it closes; close() ends it."""

    def __init__(self, host: str, port: int, fingerprint: str, timeout: float = 20.0):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # the pin below is the check; the bridge has no CA
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            raw = socket.create_connection((host, port), timeout=timeout)
            self.sock = context.wrap_socket(raw, server_hostname=host)
        except (OSError, ssl.SSLError) as exc:
            raise BridgeError(f'BRIDGE_UNREACHABLE: cannot reach the MT5 terminal bridge at {host}:{port} ({type(exc).__name__}: {str(exc)[:120]}).') from None
        der = self.sock.getpeercert(binary_form=True) or b''
        seen = hashlib.sha256(der).hexdigest().upper()
        if seen != _normalise(fingerprint):
            self.sock.close()
            raise BridgeError(f'BRIDGE_FINGERPRINT_MISMATCH: the bridge at {host}:{port} presented certificate {seen[:16]}…, not the pinned one. '
                              'Update the fingerprint on the connection only if you reinstalled the bridge.')
        self.sock.settimeout(None)
        self.reader = self.sock.makefile('rb')
        self.writer = self.sock.makefile('wb')

    def send(self, message: dict) -> None:
        self.writer.write((json.dumps(message, separators=(',', ':')) + '\n').encode())
        self.writer.flush()

    def lines(self) -> Iterator[bytes]:
        for raw in self.reader:
            yield raw

    def close(self) -> None:
        for part in (self.writer, self.reader, self.sock):
            try:
                part.close()
            except OSError:
                pass


def bridge_of(settings: dict | None) -> dict | None:
    """The bridge block of an MT5 connection's settings, or None when the connection does not use one."""
    bridge = (settings or {}).get('bridge')
    return bridge if isinstance(bridge, dict) and bridge.get('host') and bridge.get('fingerprint') else None
