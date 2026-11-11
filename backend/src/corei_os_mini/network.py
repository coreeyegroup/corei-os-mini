"""This server's view of the network and its clock.

Owns: the outbound (egress) IP that brokers see — needed for IP-whitelisted API apps
under the SEBI retail algo framework — and whether the system clock is synchronised.
COREI_EGRESS_IP overrides discovery (set it when the server sits behind a fixed NAT);
otherwise a public echo service is asked, at most every 10 minutes.
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import threading
import time
import urllib.request

ECHO_URLS = ('https://api.ipify.org', 'https://checkip.amazonaws.com')
TTL = 600
_cache: dict = {'ip': None, 'source': None, 'at': 0.0}
_lock = threading.Lock()


def egress_ip(cached_only: bool = False) -> str | None:
    configured = os.getenv('COREI_EGRESS_IP', '').strip()
    if configured:
        try:
            return str(ipaddress.ip_address(configured))
        except ValueError:
            return None
    with _lock:
        if _cache['ip'] and time.time() - _cache['at'] < TTL or cached_only:
            return _cache['ip']
    for url in ECHO_URLS:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - fixed https URLs
                ip = str(ipaddress.ip_address(response.read(64).decode().strip()))
        except (OSError, ValueError):
            continue
        with _lock:
            _cache.update(ip=ip, source=url, at=time.time())
        return ip
    return None


def clock_synchronised() -> bool | None:
    """True/False from systemd-timedated; None when it cannot be determined."""
    if not shutil.which('timedatectl'):
        return None
    try:
        out = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'], capture_output=True,
                             text=True, timeout=3, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {'yes': True, 'no': False}.get(out)


def status() -> dict:
    ip = egress_ip()
    return {'egress_ip': ip, 'egress_source': 'COREI_EGRESS_IP' if os.getenv('COREI_EGRESS_IP') else (_cache['source'] or None),
            'clock_synchronised': clock_synchronised(), 'server_time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
