"""Local servers.dat parsing; never opens a broker connection."""
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException

MAX_FILE = 1024 * 1024
parse_slot = threading.BoundedSemaphore(1)


def endpoint(value: str) -> tuple[str, int]:
    if value.startswith('['):
        match = re.fullmatch(r'\[([^\]]+)\]:([0-9]{1,5})', value)
        if not match:
            raise ValueError('Invalid IPv6 endpoint')
        host = str(ipaddress.IPv6Address(match[1]))
        port = int(match[2])
    else:
        host, raw_port = value.rsplit(':', 1)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
            raise ValueError('Invalid host')
        port = int(raw_port)
        host = host.lower()
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    return host, port


def parse_catalog(data: bytes) -> list[dict]:
    if not 32 <= len(data) <= MAX_FILE:
        raise HTTPException(422, 'Select a servers.dat file between 32 bytes and 1 MiB.')
    parser = Path(os.getenv('COREI_MT5_CATALOG_PARSER', '/opt/corei-os-mini/server-catalog/ServerCatalog'))
    if not parser.is_file():
        raise HTTPException(503, 'Server-file reader is not installed. Run the broker-account setup command.')
    if not parse_slot.acquire(blocking=False):
        raise HTTPException(429, 'Another server file is being analysed. Try again shortly.')
    try:
        with tempfile.TemporaryDirectory(prefix='corei-catalog-') as folder:
            path = Path(folder) / 'servers.dat'
            path.write_bytes(data)
            with tempfile.TemporaryFile() as output:
                result = subprocess.run([str(parser), str(path)], stdout=output,
                    stderr=subprocess.DEVNULL, timeout=15, check=False,
                    env={**os.environ, 'DOTNET_GCHeapHardLimit': '0x10000000',
                         'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
                if result.returncode or output.tell() > 4 * MAX_FILE:
                    raise ValueError('Unsupported file')
                output.seek(0)
                document = json.load(output)
        rows = []
        servers = document['servers']
        if not isinstance(servers, list) or not 1 <= len(servers) <= 512:
            raise ValueError('Invalid server count')
        for server in servers:
            name = server['server_name']
            if not isinstance(name, str) or not 1 <= len(name) <= 120:
                raise ValueError('Invalid server name')
            for access in server['access_points']:
                label = access['name']
                if not isinstance(label, str) or not 1 <= len(label) <= 200:
                    raise ValueError('Invalid access point label')
                host, port = endpoint(access['address'])
                rows.append(dict(server_name=name, name=label, host=host, port=port))
                if len(rows) > 8192:
                    raise ValueError('Too many access points')
        if not rows:
            raise ValueError('No access points')
        return rows
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise HTTPException(422, 'Cannot read this servers.dat format. No account was saved. Use a supported server file.') from None
    finally:
        parse_slot.release()
