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


_KEY = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


def server_details(raw: object) -> dict:
    """Every scalar field the SDK exposes for a server, validated. Repeats of the
    server name are dropped; everything else is kept as the file states it."""
    if not isinstance(raw, dict) or len(raw) > 64:
        raise ValueError('Invalid server details')
    details = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError('Invalid server detail name')
        if key.lower() == 'servername':
            continue
        if isinstance(value, str):
            value = _CONTROL.sub(' ', value).strip()[:500]
            if not value:
                continue
        elif isinstance(value, bool) or (isinstance(value, int) and abs(value) < 2**63):
            pass
        elif isinstance(value, float) and value == value and abs(value) != float('inf'):
            pass
        else:
            raise ValueError('Invalid server detail value')
        details[key] = value
    return details


def company(details: dict) -> str | None:
    for key in ('CompanyName', 'Company', 'company_name', 'company'):
        if isinstance(details.get(key), str):
            return details[key][:200]
    return None


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
        servers = document['servers']
        if not isinstance(servers, list) or not 1 <= len(servers) <= 512:
            raise ValueError('Invalid server count')
        result, total = [], 0
        for server in servers:
            name = server['server_name']
            if not isinstance(name, str) or not 1 <= len(name) <= 120:
                raise ValueError('Invalid server name')
            points = []
            for access in server['access_points']:
                label = access['name']
                if not isinstance(label, str) or not 1 <= len(label) <= 200:
                    raise ValueError('Invalid access point label')
                host, port = endpoint(access['address'])
                points.append(dict(name=label, host=host, port=port))
            total += len(points)
            if not points or total > 8192:
                raise ValueError('Invalid access point count')
            details = server_details(server.get('details', {}))
            result.append(dict(server_name=name, company_name=company(details), details=details, access_points=points))
        return result
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise HTTPException(422, 'Cannot read this servers.dat format. No account was saved. Use a supported server file.') from None
    finally:
        parse_slot.release()
