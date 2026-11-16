"""Trading sessions (Stage 2 · Market intelligence).

Why it exists: liquidity, spreads and volatility depend on which centres are open; a strategy, a screen and a risk
limit need the same answer to "which session is this bar in".
Purpose: the FX/CFD session map for a given instant: Sydney, Tokyo, London, New York (local hours, so daylight-saving
changes are handled by the time zones), their overlaps, the daily rollover window and the weekend close.
Responsibilities: sessions_at(t), next_changes(t), the week's opening hours.
Must never: read the clock itself (callers pass the instant: bar time for features, now only for the live display),
or claim exchange holidays or economic events (the calendar is a later component).
Dependencies: zoneinfo. Consumers: intel.engine, intel_api, tests.
Architectural position: Stage 2, beside the indicators.
Convention: the FX week runs from Sunday 17:00 to Friday 17:00 New York time; rollover is 17:00 New York, with a
thin-liquidity window from 16:55 to 17:15.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SESSIONS = (  # (name, zone, open, close) in local time
    ('Sydney', 'Australia/Sydney', time(7), time(16)),
    ('Tokyo', 'Asia/Tokyo', time(9), time(18)),
    ('London', 'Europe/London', time(8), time(17)),
    ('New York', 'America/New_York', time(8), time(17)),
)
NY = ZoneInfo('America/New_York')


def _open(name: str, zone: str, start: time, end: time, t: datetime) -> bool:
    local = t.astimezone(ZoneInfo(zone))
    return local.weekday() < 5 and start <= local.time() < end


def market_open(t: datetime) -> bool:
    """FX week: Sunday 17:00 to Friday 17:00 New York."""
    ny = t.astimezone(NY)
    wd, hm = ny.weekday(), ny.time()
    if wd == 5:
        return False
    if wd == 6:
        return hm >= time(17)
    if wd == 4:
        return hm < time(17)
    return True


def rollover(t: datetime) -> bool:
    ny = t.astimezone(NY)
    return time(16, 55) <= ny.time() < time(17, 15)


def sessions_at(t: datetime) -> dict:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    is_open = market_open(t)
    names = [s[0] for s in SESSIONS if is_open and _open(*s, t)]
    overlap = ' + '.join(names) if len(names) > 1 else None
    if not is_open:
        label = 'Closed (weekend)'
    elif rollover(t):
        label = 'Rollover'
    elif names:
        label = overlap or names[0]
    else:
        label = 'Between sessions'
    return {'at': t.astimezone(timezone.utc).isoformat(), 'market_open': is_open, 'sessions': names, 'overlap': overlap,
            'rollover': is_open and rollover(t), 'label': label}


def next_changes(t: datetime, hours: int = 36) -> list[dict]:
    """When the session picture changes next (to the minute), for the live display."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.replace(second=0, microsecond=0)
    out, last = [], sessions_at(t)['label']
    for m in range(1, hours * 60 + 1):
        at = t + timedelta(minutes=m)
        label = sessions_at(at)['label']
        if label != last:
            out.append({'at': at.astimezone(timezone.utc).isoformat(), 'label': label})
            last = label
            if len(out) >= 8:
                break
    return out


def week_table() -> list[dict]:
    """Each session's hours in its own time zone and today's hours in UTC."""
    today = datetime.now(timezone.utc).date()  # display only
    rows = []
    for name, zone, start, end in SESSIONS:
        z = ZoneInfo(zone)
        o = datetime.combine(today, start, z).astimezone(timezone.utc)
        c = datetime.combine(today, end, z).astimezone(timezone.utc)
        rows.append({'name': name, 'zone': zone, 'local': f'{start:%H:%M}–{end:%H:%M}', 'utc_today': f'{o:%H:%M}–{c:%H:%M}'})
    return rows
