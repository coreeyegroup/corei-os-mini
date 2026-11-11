"""Latency test for MT5 server addresses: TCP connect time from the COREI host.

Owns: measuring, nothing else. It opens a TCP connection and closes it straight away:
no MT5 handshake, no login, no data sent. Three attempts per address; the best
(minimum) and median times are reported.

Safety: only public internet addresses are tested. Hosts that resolve to private,
loopback, link-local, multicast or reserved addresses are refused, so this cannot be
used to probe the COREI server's own network. One test runs at a time per process.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import statistics
import time
from typing import Any

ATTEMPTS = 3
TIMEOUT = 3.0
PARALLEL = 16
_running = asyncio.Lock()


def _public(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return address.is_global and not address.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), TIMEOUT)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def _connect_ms(ip: str, port: int) -> float:
    started = time.perf_counter()
    _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), TIMEOUT)
    elapsed = (time.perf_counter() - started) * 1000
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1.0)
    except (OSError, asyncio.TimeoutError):
        pass
    return elapsed


async def _one(host: str, port: int, gate: asyncio.Semaphore) -> dict[str, Any]:
    result: dict[str, Any] = {'host': host, 'port': port, 'reachable': False, 'latency_ms': None,
                              'median_ms': None, 'resolved': None, 'error': None}
    async with gate:
        try:
            ips = await _resolve(host, port)
        except (OSError, asyncio.TimeoutError, UnicodeError):
            result['error'] = 'Name does not resolve'
            return result
        public = [ip for ip in ips if _public(ip)]
        if not public:
            result['error'] = 'Private or reserved address; not tested'
            return result
        ip = public[0]
        result['resolved'] = ip
        samples, last_error = [], None
        for _ in range(ATTEMPTS):
            try:
                samples.append(await _connect_ms(ip, port))
            except asyncio.TimeoutError:
                last_error = f'No answer within {TIMEOUT:.0f} s'
            except OSError as exc:
                last_error = (exc.strerror or 'Connection refused')[:120]
        if samples:
            result.update(reachable=True, latency_ms=round(min(samples), 2), median_ms=round(statistics.median(samples), 2))
        else:
            result['error'] = last_error
        return result


async def measure(addresses: list[tuple[str, int]]) -> dict[str, Any]:
    if _running.locked():
        raise RuntimeError('busy')
    async with _running:
        gate = asyncio.Semaphore(PARALLEL)
        results = await asyncio.gather(*(_one(h, p, gate) for h, p in addresses))
    return {'measured_from': socket.gethostname()[:253], 'attempts': ATTEMPTS, 'results': list(results)}
