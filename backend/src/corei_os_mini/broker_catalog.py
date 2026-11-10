"""MT5 servers.dat analysis. Never opens a broker connection.

Owns: running the local reader on an uploaded servers.dat, validating its output,
and keeping the parsed catalog briefly in memory so the operator can search every
access point of every broker in the file (like Westernpips' "Analyze servers.dat"
with broker filter). Nothing here is written to the database; the operator saves
only the server and access points they pick, on a connection.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
import time

from fastapi import HTTPException

# A terminal's servers.dat can list every broker it has searched for.
MAX_FILE = 32 * 1024 * 1024
MAX_SERVERS = 100_000
MAX_ACCESS_POINTS = 1_000_000
CATALOG_TTL = 30 * 60
MAX_CATALOGS = 4
parse_slot = threading.BoundedSemaphore(1)

_HOST = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}')


def endpoint(value: str) -> tuple[str, int]:
    """'host:port', '[ipv6]:port' or unbracketed 'ipv6:port' (last group is the port)."""
    value = value.strip()
    if value.startswith('['):
        match = re.fullmatch(r'\[([^\]]+)\]:([0-9]{1,5})', value)
        if not match:
            raise ValueError('Invalid IPv6 endpoint')
        host, port = str(ipaddress.IPv6Address(match[1])), int(match[2])
    else:
        raw_host, raw_port = value.rsplit(':', 1)
        if not raw_port.isdigit():
            raise ValueError('Invalid port')
        port = int(raw_port)
        try:
            host = str(ipaddress.ip_address(raw_host))
        except ValueError:
            if not _HOST.fullmatch(raw_host):
                raise ValueError('Invalid host') from None
            host = raw_host.lower()
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


def _clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = _CONTROL.sub(' ', value).strip()[:limit]
    return value or None


def parse_catalog(data: bytes) -> dict:
    """Run the reader and validate its output. Individual bad entries are skipped and
    counted rather than failing the whole file."""
    if not 32 <= len(data) <= MAX_FILE:
        raise HTTPException(422, 'Select a servers.dat file between 32 bytes and 32 MiB.')
    parser = Path(os.getenv('COREI_MT5_CATALOG_PARSER', '/opt/corei-os-mini/server-catalog/ServerCatalog'))
    if not parser.is_file():
        raise HTTPException(503, 'Server-file reader is not installed. Run scripts/setup-broker-accounts.sh.')
    if not parse_slot.acquire(blocking=False):
        raise HTTPException(429, 'Another server file is being analysed. Try again shortly.')
    try:
        with tempfile.TemporaryDirectory(prefix='corei-catalog-') as folder:
            path = Path(folder) / 'servers.dat'
            path.write_bytes(data)
            with tempfile.TemporaryFile() as output:
                result = subprocess.run([str(parser), str(path)], stdout=output,
                    stderr=subprocess.DEVNULL, timeout=120, check=False,
                    env={**os.environ, 'DOTNET_GCHeapHardLimit': '0x40000000',
                         'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
                if result.returncode or output.tell() > 512 * 1024 * 1024:
                    raise ValueError('Unsupported file')
                output.seek(0)
                document = json.load(output)
        servers = document['servers']
        if not isinstance(servers, list) or not 1 <= len(servers) <= MAX_SERVERS:
            raise ValueError('Invalid server count')
        parsed, total = [], 0
        stats = {'skipped_servers': int(document.get('skipped_servers', 0) or 0), 'invalid_addresses': 0,
                 'servers_without_access_points': 0,
                 'unreadable_extended_records': int(document.get('unreadable_extended_records', 0) or 0)}
        for server in servers:
            name = _clean_text(server.get('server_name') if isinstance(server, dict) else None, 120)
            if not name:
                stats['skipped_servers'] += 1
                continue
            try:
                details = server_details(server.get('details', {}))
            except ValueError:
                details = {}
            points, seen = [], set()
            for access in server.get('access_points') or []:
                try:
                    host, port = endpoint(access['address'])
                except (ValueError, KeyError, TypeError, AttributeError):
                    stats['invalid_addresses'] += 1
                    continue
                if (host, port) in seen:
                    continue
                seen.add((host, port))
                points.append(dict(name=_clean_text(access.get('name'), 200) or 'Access point', host=host, port=port))
            if not points:
                stats['servers_without_access_points'] += 1
            total += len(points)
            if total > MAX_ACCESS_POINTS:
                raise ValueError('Too many access points')
            parsed.append(dict(server_name=name, company_name=company(details), details=details, access_points=points))
        if not parsed:
            raise ValueError('No servers')
        parsed.sort(key=lambda s: s['server_name'].lower())
        return {'servers': parsed, 'server_count': len(parsed), 'access_point_count': total,
                'source_sha256': str(document.get('source_sha256', ''))[:64], **stats}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        raise HTTPException(422, 'Cannot read this servers.dat. Use the file from the MT5 terminal: File › Open Data Folder › Config › servers.dat.') from None
    finally:
        parse_slot.release()


# ---------- parsed catalogs kept briefly for searching ----------

@dataclass
class Catalog:
    owner: str
    expires: float
    data: dict
    # One row per access point: (server index, access point index, broker key, address key)
    rows: list[tuple[int, int, str, str]] = field(default_factory=list)


_catalogs: OrderedDict[str, Catalog] = OrderedDict()
_catalog_lock = threading.Lock()


def remember(owner: str, data: dict) -> tuple[str, float]:
    rows = []
    for si, server in enumerate(data['servers']):
        broker = f"{server['server_name']} {server.get('company_name') or ''}".lower()
        for ai, point in enumerate(server['access_points']):
            rows.append((si, ai, broker, f"{point['host']}:{point['port']} [{point['host']}]:{point['port']} {point['name']}".lower()))
        if not server['access_points']:
            # Listed so a gap is visible instead of the broker silently missing.
            rows.append((si, -1, broker, 'no readable address'))
    catalog_id = secrets.token_urlsafe(16)
    expires = time.time() + CATALOG_TTL
    with _catalog_lock:
        now = time.time()
        for key in [k for k, c in _catalogs.items() if c.expires <= now]:
            del _catalogs[key]
        while len(_catalogs) >= MAX_CATALOGS:
            _catalogs.popitem(last=False)
        _catalogs[catalog_id] = Catalog(owner, expires, data, rows)
    return catalog_id, expires


def recall(catalog_id: str, owner: str) -> Catalog:
    with _catalog_lock:
        catalog = _catalogs.get(catalog_id)
        if not catalog or catalog.expires <= time.time() or catalog.owner != owner:
            raise HTTPException(404, 'This server-file analysis has expired. Choose the servers.dat file again.')
        return catalog


def search(catalog: Catalog, broker: str, query: str, offset: int, limit: int) -> dict:
    """Flat access-point rows across every server, filtered like Westernpips: the
    broker filter matches server name or company, the second filter matches address
    or access-point name. Every term must match (space separates terms)."""
    broker_terms, query_terms = broker.lower().split(), query.lower().split()
    matches = [r for r in catalog.rows
               if all(t in r[2] for t in broker_terms) and all(t in r[3] for t in query_terms)]
    servers = catalog.data['servers']
    items = []
    for si, ai, _, _ in matches[offset:offset + limit]:
        server = servers[si]
        point = server['access_points'][ai] if ai >= 0 else {'name': 'No readable address in this file', 'host': None, 'port': None}
        items.append({'server_name': server['server_name'], 'company_name': server.get('company_name'), **point})
    return {'total': len(matches), 'server_matches': len({r[0] for r in matches}), 'offset': offset, 'items': items}


def server(catalog: Catalog, name: str) -> dict:
    for entry in catalog.data['servers']:
        if entry['server_name'] == name:
            return entry
    raise HTTPException(404, 'Server not found in this file.')
