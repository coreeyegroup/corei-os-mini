"""Plan health: one state per collection plan, in words anyone can act on, and the feed gate trading uses.

Why it exists: "collector connected" was shown as the health of every plan, so a plan whose symbol the broker does
not know (live: NIFTY50 with "Nifty 50") showed green "collecting" for days while collecting nothing, and feed
quality never reached a trading decision.
Purpose:
  - plan_state(): the state of one plan from what the collector knows (pure).
  - WORDS: the plain-language meaning and next step of each state, shared by the API and the screens.
  - gate(): before a risk-increasing order or signal, is its price feed fit to trade on? (reads the database)
States: LIVE (prices arriving), CLOSED (market shut; none expected), WAITING (connected, no price yet), STALE (in
  session, nothing within the plan's stale limit), DEGRADED (prices arrive but breach a quality limit), ERROR (the
  source rejects this symbol's requests), INVALID_SYMBOL (the source's symbol list does not have it), OFFLINE (the
  source is not connected), PAUSED (stopped by a person or by schedule).
Must never: change a plan, place or block an order itself (risk and signals decide, using gate()), or treat "no
  health recorded" as unhealthy (an instrument without a plan is checked by price age only, as before).
Dependencies: config_store (policies), md_plan_health and md_quality tables. Consumers: md.collector, md.store,
  trading.risk, trading.signals, controls_api (overview), md_api.
Architectural position: Stage 1 · Market data; the gate is the Stage 1 → Stage 4/5 contract for price fitness.
"""
from __future__ import annotations

from datetime import datetime

STATES = ('LIVE', 'CLOSED', 'WAITING', 'STALE', 'DEGRADED', 'ERROR', 'INVALID_SYMBOL', 'OFFLINE', 'PAUSED')
PROBLEMS = {'STALE', 'ERROR', 'INVALID_SYMBOL'}   # need a person (OFFLINE is reported once, on its source)
FINE = {'LIVE', 'CLOSED'}                                    # working as intended

WORDS = {
    'LIVE': ('Live', 'Prices are arriving.', ''),
    'CLOSED': ('Market closed', 'The market is shut; no prices are expected.', ''),
    'WAITING': ('Waiting', 'Connected; no price for this symbol yet.', 'If this lasts in market hours, check the symbol.'),
    'STALE': ('Delayed', 'No price within the plan\'s stale limit during market hours.', 'Check the source, or the broker\'s session for this symbol.'),
    'DEGRADED': ('Unreliable', 'Prices arrive but breach a quality limit (latency, spread or deviation).', 'Compare with another source; adjust the limit if it is too tight.'),
    'ERROR': ('Error', 'The source rejects requests for this symbol.', 'Read the last error; fix the symbol or the account.'),
    'INVALID_SYMBOL': ('Wrong symbol', 'The source\'s symbol list does not contain this symbol, so it can never receive a price.',
                       'Archive this plan and add the instrument again from the source\'s list (a suggestion is shown).'),
    'OFFLINE': ('Source offline', 'The source is not connected.', 'See the source\'s problem above.'),
    'PAUSED': ('Paused', 'Collection is paused or outside its schedule.', ''),
}


def plan_state(*, source_state: str, invalid: bool, errors: int, in_session: bool, last_rx: datetime | None,
               now: datetime, stale_seconds: float, breaching: set | frozenset = frozenset()) -> str:
    """The plan's state, most serious first."""
    if invalid:
        return 'INVALID_SYMBOL'
    if errors:
        return 'ERROR'
    if source_state != 'CONNECTED':
        return 'OFFLINE'
    if not in_session:
        return 'CLOSED'
    if last_rx is None:
        return 'WAITING'
    if (now - last_rx).total_seconds() > stale_seconds:
        return 'STALE'
    if breaching & {'LATENCY', 'SPREAD', 'DEVIATION'}:
        return 'DEGRADED'
    return 'LIVE'


def gate(db, account_id, broker_symbol: str | None, now: datetime, policy) -> str | None:
    """A refusal reason when the feed of this account × symbol is not fit for a risk-increasing decision, else None.
    `policy(db, key, default)` reads cfg_policies (passed in to keep this module free of config imports).
    Rules (Setup › Policies): md_block_states (default INVALID_SYMBOL, ERROR, OFFLINE, STALE) and
    md_min_quality_score (default 40; 0 = off) on the last scored minute of the last 3 minutes, in session only."""
    if not account_id or not broker_symbol:
        return None
    row = db.execute('SELECT state, detail FROM md_plan_health WHERE account_id=%s AND broker_symbol=%s',
                     (str(account_id), broker_symbol)).fetchone()
    if row is None:
        return None
    block = set(policy(db, 'md_block_states', ['INVALID_SYMBOL', 'ERROR', 'OFFLINE', 'STALE']) or [])
    if row['state'] in block:
        return f"FEED_{row['state']}: the price feed for {broker_symbol} is {WORDS[row['state']][0].lower()} ({row['detail'] or WORDS[row['state']][1]})"
    floor = float(policy(db, 'md_min_quality_score', 40) or 0)
    if floor > 0 and row['state'] == 'LIVE':
        q = db.execute("""SELECT score FROM md_quality WHERE account_id=%s AND broker_symbol=%s AND ticks > 0
                          AND minute > %s - interval '3 minutes' ORDER BY minute DESC LIMIT 1""",
                       (str(account_id), broker_symbol, now)).fetchone()
        if q is not None and q['score'] is not None and float(q['score']) < floor:
            return f"FEED_QUALITY: the price feed for {broker_symbol} scored {float(q['score']):g} in the last minute (minimum {floor:g}; Setup › Policies md_min_quality_score)"
    return None
