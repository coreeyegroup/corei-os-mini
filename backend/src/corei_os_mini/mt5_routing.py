"""Which addresses (access points) an MT5 account connects through, and in which order.

Owns: the one rule every reader uses — readiness, the account screen and the MT5
adapter — so the answer can never differ between them. Pure functions; no database.

Modes (cfg_accounts.access_mode):
  SERVER  – the connection's ticked addresses in the connection's order (default).
  FASTEST – the connection's ticked addresses, fastest latest successful measurement
            first; unmeasured or unreachable ones follow in the connection's order.
  ACCOUNT – the account's own ordered list, which must come from the server's
            addresses. Addresses the server no longer lists are skipped and flagged;
            if none remain the account falls back to SERVER and is flagged.
An account must never be left with no route because of a broker-side address change,
and it must never use an address that is not one of its server's.
"""
from __future__ import annotations

from typing import Any, Iterable

Key = tuple[str, int]


def _key(point: dict[str, Any]) -> Key:
    return str(point['host']).lower(), int(point['port'])


def effective_access_points(mode: str, account_points: Iterable[dict], server_points: Iterable[dict],
                            latency: dict[Key, dict] | None = None) -> dict[str, Any]:
    """Return {'mode', 'addresses': [{host, port, latency_ms, reachable, measured_at}], 'warnings': [...]}."""
    latency = latency or {}
    server = [p for p in server_points]
    on_server = {_key(p) for p in server}
    ticked = [p for p in server if p.get('enabled', True)]
    warnings: list[str] = []
    used_mode = mode
    if mode == 'ACCOUNT':
        own = [p for p in account_points]
        chosen = [p for p in own if _key(p) in on_server]
        if len(chosen) < len(own):
            warnings.append('ACCOUNT_ADDRESSES_REMOVED_BY_SERVER')
        if not chosen:
            warnings.append('ACCOUNT_ADDRESSES_FALLBACK_TO_SERVER')
            used_mode, chosen = 'SERVER', ticked
    elif mode == 'FASTEST':
        measured = [p for p in ticked if (latency.get(_key(p)) or {}).get('reachable')]
        if not measured:
            warnings.append('NO_LATENCY_MEASURED')
        order = {k: i for i, k in enumerate(_key(p) for p in ticked)}
        chosen = sorted(ticked, key=lambda p: (0, float(latency[_key(p)]['latency_ms']), order[_key(p)])
                        if (latency.get(_key(p)) or {}).get('reachable') else (1, 0.0, order[_key(p)]))
    else:
        chosen = ticked
    if not chosen:
        warnings.append('NO_ADDRESS_AVAILABLE')
    out = []
    for p in chosen:
        sample = latency.get(_key(p)) or {}
        out.append({'host': _key(p)[0], 'port': _key(p)[1],
                    'latency_ms': float(sample['latency_ms']) if sample.get('latency_ms') is not None else None,
                    'reachable': sample.get('reachable'), 'measured_at': sample.get('measured_at')})
    return {'mode': mode, 'used_mode': used_mode, 'addresses': out, 'warnings': warnings}
