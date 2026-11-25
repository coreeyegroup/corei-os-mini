"""Journal replay check: rebuild one account's bars for one day from the journal and compare them with the database.

Why it exists: the collector writes every source message to the journal before the database ("journal first") so a
day can be rebuilt, but nothing ever rebuilt one, so the claim was untested (Stage 1 audit MD-C10).
Purpose: prove, or disprove, that the stored bars of a day are what the journal says, and list every difference.
Responsibilities: read <root>/journal/<day>/<ACCOUNT>.jsonl.gz; replay connect lines (broker clock offset) and quote
  lines through the same bar builder the collector uses, with each plan's current settings; compare complete bars
  with the stored BUILT bars of the same symbols and timeframes; report matched, different, missing and extra.
Must never: write to the database or the journal (read-only check); it does not repair. Bars stored before 0.54.0
  were built by receipt time and UTC alignment, so a day from before the upgrade is expected to differ for session
  markets; run it on days after the upgrade.
Known limits (reported, not hidden): the collector's clock-skew correction and offset re-measurement are replayed
  from the journal's own connect lines only, so a day with a skewed reader clock may show small time differences;
  lines without a receipt time (some terminal-push builds) are skipped and their bars reported as different.
Dependencies: md.bars, md.collector (plan market), md.journal, md.quality (server offset), config_store (read).
Consumers: operators (System › shell), tests.
Run: python -m corei_os_mini.md.replay --account VTM_A --day 2026-11-24 [--root /var/lib/corei-os-mini/market-data]
Architectural position: Stage 1 · Market data, an offline verification tool beside the collector.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .. import config_store as store
from .bars import event_time
from .collector import bar_builder, md_root, parse_time
from .journal import read
from .quality import server_offset

PRICE_TOLERANCE = 1e-9


def rebuild(lines: list[bytes], plans: dict[str, dict]) -> dict[tuple, dict]:
    """Complete bars from journal lines: {(symbol, timeframe, bar_time): bar}."""
    builders = {s: bar_builder(p) for s, p in plans.items()}
    offset, stated, out = timedelta(0), False, {}
    for raw in lines:
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        if m.get('t') == 'connected':
            offset, stated = server_offset(m.get('server_time'), m.get('utc_now')), bool(m.get('server_time') and m.get('utc_now'))
            continue
        if m.get('t') != 'q' or m.get('s') not in builders:
            continue
        rx = parse_time(m.get('rx'))
        if rx is None:
            continue
        source = parse_time(m.get('st'))
        if source and not stated:
            offset, stated = timedelta(minutes=round((source - rx).total_seconds() / 60 / 15) * 15), True
        at, basis = event_time(rx, source - offset if source else None)
        for bar in builders[m['s']].add(at, m.get('b') or None, m.get('a') or None, m.get('l') or None, m.get('v'), basis, received=rx):
            out[(m['s'], bar['timeframe'], bar['bar_time'])] = bar
    return out


def compare(rebuilt: dict[tuple, dict], stored: dict[tuple, dict]) -> dict:
    """Matched, different (with the fields that differ), missing in the database, extra in the database."""
    different, missing = [], []
    matched = 0
    for key, bar in sorted(rebuilt.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        row = stored.get(key)
        if row is None:
            missing.append(key)
            continue
        fields = [f for f in ('open', 'high', 'low', 'close') if abs(float(row[f]) - float(bar[f])) > PRICE_TOLERANCE]
        if row.get('volume') is not None and bar.get('volume') is not None and abs(float(row['volume']) - float(bar['volume'])) > PRICE_TOLERANCE:
            fields.append('volume')
        if int(row.get('ticks') or 0) != int(bar['ticks']):
            fields.append('ticks')
        if fields:
            different.append({'symbol': key[0], 'timeframe': key[1], 'bar_time': key[2].isoformat(), 'fields': fields})
        else:
            matched += 1
    extra = [k for k in stored if k not in rebuilt]
    return {'rebuilt': len(rebuilt), 'matched': matched, 'different': different, 'missing_in_database': [
        {'symbol': k[0], 'timeframe': k[1], 'bar_time': k[2].isoformat()} for k in missing], 'extra_in_database': len(extra),
        'exact': not different and not missing}


def check(account_code: str, day: str, root: Path | None = None) -> dict:
    path = (root or md_root()) / 'journal' / day / f'{account_code}.jsonl.gz'
    if not path.exists():
        return {'error': f'No journal for {account_code} on {day} ({path}).'}
    with store.transaction('read') as db:
        plans = [dict(r) for r in db.execute("""SELECT p.*, c.platform, i.market AS instrument_market, i.asset_class AS instrument_class
            FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id JOIN cfg_connections c ON c.id=a.connection_id
            LEFT JOIN cfg_instruments i ON i.id=p.instrument_id WHERE a.code=%s AND p.status <> 'ARCHIVED' AND 'BARS' = ANY(p.data_types)""",
                                              (account_code,)).fetchall()]
        if not plans:
            return {'error': f'{account_code} has no plan that keeps bars.'}
        rebuilt = rebuild(read(path), {p['broker_symbol']: p for p in plans})
        if not rebuilt:
            return {'rebuilt': 0, 'matched': 0, 'different': [], 'missing_in_database': [], 'extra_in_database': 0, 'exact': True}
        first, last = min(k[2] for k in rebuilt), max(k[2] for k in rebuilt)
        rows = db.execute("""SELECT b.* FROM md_bars b JOIN cfg_accounts a ON a.id=b.account_id WHERE a.code=%s AND b.source='BUILT'
                             AND b.complete AND b.broker_symbol = ANY(%s) AND b.bar_time BETWEEN %s AND %s""",
                          (account_code, [p['broker_symbol'] for p in plans], first, last)).fetchall()
    stored = {(r['broker_symbol'], r['timeframe'], r['bar_time']): dict(r) for r in rows
              if r['timeframe'] in {b['timeframe'] for b in rebuilt.values()}}
    return {'account': account_code, 'day': day, 'journal': str(path), **compare(rebuilt, stored)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Rebuild a day of bars from the journal and compare with the database (read-only).')
    ap.add_argument('--account', required=True, help='Account code, e.g. VTM_A')
    ap.add_argument('--day', required=True, help='Journal day, YYYY-MM-DD (UTC)')
    ap.add_argument('--root', default=None, help='Market-data folder (default COREI_MD_ROOT)')
    args = ap.parse_args(argv)
    datetime.strptime(args.day, '%Y-%m-%d')
    result = check(args.account, args.day, Path(args.root) if args.root else None)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get('exact') else 1


if __name__ == '__main__':
    sys.exit(main())
