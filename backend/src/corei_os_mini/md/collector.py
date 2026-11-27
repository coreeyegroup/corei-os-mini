"""The market-data collector: one process (system service corei-os-mini-collector),
one source worker per account that has active plans.

Each worker runs the MT5 reader in --stream mode (password on stdin), and for every
line it receives:
  1. keeps the raw line for the journal;
  2. updates the live price, the bar builder and the quality tracker in memory;
  3. queues the tick, closed bars, quality minutes and events for the database.
Once a second the sink writes the journal of every worker (fsync) and only then the
database, in one transaction — so the database never holds data the journal lacks.

The supervisor (every 5 s) re-reads plans and switches, starts/stops workers and
(un)subscribes symbols, checks staleness, compares sources of the same instrument,
switches feeds, guards the disk and hands backfill jobs to workers. Every minute it checks every active plan's
symbol against the source's symbol list; a symbol the list lacks is marked INVALID_SYMBOL, left out of the
subscription and of automatic backfill, and reported once with the closest matches.

Each plan gets one health state per supervisor cycle (md/health.py), written to md_plan_health: the screens and the
trading feed gate read it, not the collector's connection state.
Bars are built from the exchange (event) time when the source gives a plausible one, aligned to the market's
session, priced and counted per instrument kind (md/bars.py). A broker clock offset that the source does not state
is re-measured from the median of recent ticks every 15 minutes, not fixed by the first tick.

Run: python -m corei_os_mini.md.collector
Environment: COREI_DATABASE_URL, COREI_MD_ROOT (default /var/lib/corei-os-mini/market-data),
COREI_MT5_CATALOG_PARSER (the reader).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from .. import account_routes
from .. import config_store as cfg
from . import markets, store
from statistics import median

from .bars import BarBuilder, FRAMES, event_time, floor
from .health import WORDS, plan_state
from .bridge_client import BridgeError, BridgeTransport, bridge_of
from .explain import explain
from .journal import Journal
from .quality import Limits, SymbolQuality, consensus_deviation, server_offset

log = logging.getLogger('corei.md.collector')
MAX_BACKLOG = 500_000
RECONNECT_STEPS = (1, 2, 5, 10, 30, 60)


def md_root() -> Path:
    return Path(os.getenv('COREI_MD_ROOT', '/var/lib/corei-os-mini/market-data'))


def reader_path() -> str:
    return os.getenv('COREI_MT5_CATALOG_PARSER', '/opt/corei-os-mini/server-catalog/ServerCatalog')


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


CALENDAR: dict[str, frozenset] = {}  # exchange holidays by currency, refreshed on every sync


def market_of(plan: dict) -> str | None:
    """The market whose hours apply to a plan: its instrument's market, else inferred from the broker symbol."""
    return markets.market_for(plan.get('instrument_market'), plan.get('broker_symbol') or '', plan.get('platform') or '', plan.get('instrument_class') or '')


def plan_holidays(plan: dict, calendar: dict | None = None) -> frozenset:
    """The provider's holidays plus calendar HOLIDAY events in the market's currency or for its exchange."""
    days = set()
    for h in plan.get('holidays') or []:
        if isinstance(h, dict) and h.get('date'):
            try:
                days.add(date.fromisoformat(str(h['date'])[:10]))
            except ValueError:
                pass
    code = market_of(plan) or ''
    m = markets.MARKETS.get(code)
    if m and m.currency and calendar:
        days |= set(calendar.get(m.currency, ()))
        days |= set(calendar.get(code.split('_')[0], ()))   # the exchange's own holidays (NSE_FO closes with NSE)
    return frozenset(days)


def in_session(plan: dict, now: datetime, calendar: dict | None = None) -> bool:
    """Is the plan's market open (pre-open included)? Unknown market: always."""
    return markets.is_open(market_of(plan), now, plan_holidays(plan, calendar), pre_open=True)


def scheduled(plan: dict, now: datetime, calendar: dict | None = None) -> bool:
    """Is this plan meant to collect right now?"""
    schedule = plan['schedule']
    if schedule == 'ALWAYS':
        return True
    if schedule == 'WHILE_DEPLOYED':
        return bool(plan.get('deployed'))
    if schedule == 'SESSIONS':
        # The market's own hours (NSE 09:00–15:30 IST, MCX to 23:30/23:55, FX Sun–Fri…), minus holidays.
        if market_of(plan):
            return in_session(plan, now, calendar)
        holidays = {h.get('date') for h in (plan.get('holidays') or []) if isinstance(h, dict)}
        return now.isoweekday() <= 5 and now.strftime('%Y-%m-%d') not in holidays
    if now.isoweekday() not in (plan.get('window_days') or []):
        return False
    start, end, t = plan['window_start'], plan['window_end'], now.time().replace(tzinfo=None)
    return start <= t < end if start < end else (t >= start or t < end)


class Sink:
    """What workers produce, written once a second (journal first, then the database)."""

    def __init__(self):
        self.lock = threading.RLock()
        self.ticks: list[tuple] = []
        self.latest: dict[tuple, tuple] = {}
        self.bars: dict[tuple, dict] = {}
        self.quality: list[dict] = []
        self.events: list[dict] = []
        self.status: dict[str, dict] = {}
        self.health: dict[str, dict] = {}
        self.dropped = 0

    def take(self) -> dict:
        with self.lock:
            batch = {'ticks': self.ticks, 'latest': self.latest, 'bars': list(self.bars.values()), 'quality': self.quality,
                     'events': self.events, 'status': self.status, 'health': self.health}
            self.ticks, self.latest, self.bars, self.quality, self.events, self.status, self.health = [], {}, {}, [], [], {}, {}
            return batch

    def put_back(self, batch: dict) -> None:
        """A failed write is retried with the next batch (the journal already has it)."""
        with self.lock:
            self.ticks = batch['ticks'] + self.ticks
            if len(self.ticks) > MAX_BACKLOG:
                over = len(self.ticks) - MAX_BACKLOG
                self.ticks = self.ticks[over:]
                self.dropped += over
            for k, v in batch['latest'].items():
                self.latest.setdefault(k, v)
            for b in batch['bars']:
                self.bars.setdefault((b['account_id'], b['broker_symbol'], b['timeframe'], b['bar_time']), b)
            self.quality = batch['quality'] + self.quality
            self.events = batch['events'] + self.events
            for k, v in batch['status'].items():
                self.status.setdefault(k, v)
            for k, v in batch.get('health', {}).items():
                self.health.setdefault(k, v)

    def backlog(self) -> int:
        with self.lock:
            return len(self.ticks)


def bar_builder(plan: dict) -> BarBuilder:
    """Bars for this plan: aligned to its market's session, priced by instrument kind; Upstox reports the day's
    running volume, so only its increase counts."""
    return BarBuilder(plan['bar_timeframes'] if 'BARS' in plan['data_types'] else [], market=market_of(plan),
                      cumulative_volume=plan.get('platform') == 'REST_API')


class PlanState:
    def __init__(self, plan: dict):
        self.plan = plan
        self.bars = bar_builder(plan)
        self.errors = 0                     # consecutive requests the source rejected for this symbol
        self.last_error: str | None = None
        self.invalid: list[str] | None = None   # suggestions when the source's symbol list lacks the symbol
        self.health: tuple | None = None    # (state, detail) last written
        self.quality = SymbolQuality(self.limits(plan), point=10 ** -int(plan.get('digits') or 5))
        self.last_stored: datetime | None = None
        self.mid: float | None = None
        self.mid_at: datetime | None = None
        self.unhealthy_since: datetime | None = None
        self.healthy_since: datetime | None = None

    @staticmethod
    def limits(plan: dict, deviation_bp: float = 10) -> Limits:
        return Limits(max_latency_ms=plan['max_latency_ms'], max_gap_seconds=plan['max_gap_seconds'],
                      max_spread_points=float(plan['max_spread_points']) if plan.get('max_spread_points') else None,
                      stale_seconds=plan['stale_seconds'], deviation_bp=deviation_bp)

    def refresh(self, plan: dict, deviation_bp: float) -> None:
        if plan['bar_timeframes'] != self.plan['bar_timeframes'] or plan['data_types'] != self.plan['data_types']:
            self.plan = plan
            self.bars = bar_builder(plan)
        self.plan = plan
        self.quality.limits = self.limits(plan, deviation_bp)


def fix_request(settings: dict, connection_id, used_by: str, purpose: str) -> dict:
    """The FIX session settings for the reader, with the session login when one is saved (logged as a credential use)."""
    request = {k: settings.get(k) for k in ('host', 'port', 'tls', 'tls_verify', 'begin_string', 'sender_comp_id', 'target_comp_id',
                                            'sender_sub_id', 'target_sub_id', 'heartbeat_seconds', 'reset_seq_on_logon', 'md_depth',
                                            'md_snapshots_only')}
    with cfg.transaction('read') as db:
        has_login = db.execute("SELECT 1 FROM cfg_credentials WHERE connection_id=%s AND kind='FIX_LOGIN'", (connection_id,)).fetchone()
    if has_login:
        secret = cfg.use_credential('connections', UUID(str(connection_id)), used_by, purpose)
        request['username'], request['password'] = secret['username'], secret['password']
        secret.clear()
    return request


class SourceWorker(threading.Thread):
    """One account: login, stream, reconnect, backfill."""

    def __init__(self, account_id: str, plans: dict[str, dict], sink: Sink, root: Path, clock=store.utcnow):
        self.offset_known = False
        self.client_build: int | None = None
        super().__init__(name=f'md-{account_id[:8]}', daemon=True)
        self.account_id, self.sink, self.clock = account_id, sink, clock
        self.plans: dict[str, PlanState] = {s: PlanState(p) for s, p in plans.items()}
        first = next(iter(plans.values()))
        self.account_code, self.connection_id, self.platform = first['account_code'], first['connection_id'], first['platform']
        self.journal = Journal(root, first['account_code'])
        self.journal_lock = threading.Lock()
        self.pending_journal: list[bytes] = []
        self.stopping = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.bridge = None  # BridgeTransport while a terminal-bridge session is open
        self.write_lock = threading.Lock()
        self.subscribed: set[str] = set()
        self.offset = timedelta(0)
        self.state, self.host, self.port, self.error = 'STARTING', None, None, None
        self.started_at = clock()
        self.ticks_total = 0
        self.last_rx: datetime | None = None
        self.outage_since: datetime | None = None
        self.job: dict | None = None
        self.job_sent_at = 0.0
        self.job_requests: deque = deque()
        self.reported_error: str | None = None
        self.failures = 0
        self.clock_samples: deque = deque(maxlen=20)
        self.offset_stated = False           # the source stated its clock at connect (no measuring from ticks)
        self.offset_samples: deque = deque(maxlen=60)
        self.offset_checked = 0.0
        self.clock_skew_ms: float | None = None
        self.clock_skew_reported = False
        # Routes (DEC-69): the account's ordered ways in; empty = the server's method.
        self.routes: list[dict] = []
        self.route_idx = 0
        self.route_failures = 0
        self.active_route: dict | None = None
        self.left_first_route_at: float | None = None

    # ----- status -----
    def status(self) -> dict:
        lag = (self.clock() - self.last_rx).total_seconds() * 1000 if self.last_rx and self.state == 'CONNECTED' else None
        return {'state': self.state, 'host': self.host, 'port': self.port, 'pid': self.proc.pid if self.proc else None,
                'subscribed': len(self.subscribed), 'ticks_total': self.ticks_total, 'lag_ms': lag, 'backlog': self.sink.backlog(),
                'server_offset_minutes': int(self.offset.total_seconds() // 60), 'started_at': self.started_at, 'last_error': self.error,
                'route': (self.active_route or {}).get('label'), 'route_method': (self.active_route or {}).get('method'),
                'routes': len(self.routes), 'clock_skew_ms': round(self.clock_skew_ms) if self.clock_skew_ms is not None else None}

    def _event(self, kind: str, severity: str = 'INFO', symbol: str | None = None, plan_id: Any = None, **detail) -> None:
        with self.sink.lock:
            self.sink.events.append({'kind': kind, 'severity': severity, 'detail': detail, 'account_id': self.account_id,
                                     'broker_symbol': symbol, 'plan_id': plan_id})

    def flush_journal(self) -> None:
        with self.journal_lock:
            lines, self.pending_journal = self.pending_journal, []
            if lines:
                self.journal.write(lines, self.clock())

    # ----- control -----
    def wanted(self) -> set[str]:
        """Symbols to subscribe: a symbol the source does not list is left out (it cannot receive a price, and on
        Upstox one bad key used to fail the quotes of every key polled with it)."""
        return {s for s, st in self.plans.items() if ('QUOTES' in st.plan['data_types'] or 'BARS' in st.plan['data_types'])
                and st.invalid is None}

    def set_invalid(self, invalid: dict[str, list[str]]) -> None:
        """Symbols the source's list lacks (with suggestions); others are cleared. Re-syncs subscriptions."""
        changed = False
        for symbol, st in self.plans.items():
            new = invalid.get(symbol)
            if (new is None) != (st.invalid is None):
                changed = True
                if new is not None:
                    self._event('SYMBOL_INVALID', 'CRITICAL', symbol=symbol, plan_id=st.plan['id'],
                                message=f"{symbol} is not in this source's symbol list, so it can never receive a price.",
                                suggestions=new, next_step=WORDS['INVALID_SYMBOL'][2])
            st.invalid = new
        if changed and self.state == 'CONNECTED':
            self._sync_subscriptions()

    def update(self, plans: dict[str, dict], deviation_bp: float) -> None:
        for s, p in plans.items():
            if s in self.plans:
                self.plans[s].refresh(p, deviation_bp)
            else:
                self.plans[s] = PlanState(p)
        for s in list(self.plans):
            if s not in plans:
                del self.plans[s]
        if self.state == 'CONNECTED':
            self._sync_subscriptions()

    def _send(self, message: dict) -> None:
        if getattr(self, 'push_mode', False):
            from . import push
            push.send(self.account_id, message)
            return
        with self.write_lock:
            if self.bridge is not None:
                try:
                    self.bridge.send(message)
                except OSError:
                    pass
                return
            if self.proc and self.proc.stdin and self.proc.poll() is None:
                try:
                    self.proc.stdin.write((json.dumps(message) + '\n').encode())
                    self.proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass

    def _sync_subscriptions(self) -> None:
        want = self.wanted()
        add, drop = sorted(want - self.subscribed), sorted(self.subscribed - want)
        if add:
            self._send({'cmd': 'subscribe', 'symbols': add})
        if drop:
            self._send({'cmd': 'unsubscribe', 'symbols': drop})

    def stop(self) -> None:
        self.stopping.set()
        self._send({'cmd': 'stop'})
        if self.bridge is not None:
            self.bridge.close()
        if self.proc:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _config_stamp(self) -> Any:
        """Changes when the account, its connection (method, bridge, addresses), either's saved login, or the account's
        daily broker session (Upstox login, 0.66.2) changes."""
        try:
            with cfg.transaction('read') as db:
                row = db.execute(
                    'SELECT a.version AS av, a.updated_at AS au, c.version AS cv, c.updated_at AS cu, c.adapter, '
                    '(SELECT max(updated_at) FROM cfg_credentials k WHERE k.account_id=a.id OR k.connection_id=c.id) AS ku, '
                    '(SELECT max(obtained_at) FROM cfg_session_tokens s WHERE s.account_id=a.id) AS su, '
                    '(SELECT max(updated_at)::text || count(*) FROM cfg_account_routes r WHERE r.account_id=a.id) AS ru '
                    'FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s', (self.account_id,)).fetchone()
            return tuple(row.values()) if row else None
        except Exception:  # noqa: BLE001 - database briefly unavailable: just keep waiting
            return None

    def _wait_or_config_change(self, delay: float) -> None:
        """Wait before the next try, but retry at once when someone fixes the setup (new method, bridge, password)."""
        before = self._config_stamp()
        deadline = time.monotonic() + delay
        while not self.stopping.is_set():
            left = deadline - time.monotonic()
            if left <= 0 or self.stopping.wait(min(left, 30)):
                return
            now = self._config_stamp()
            if before is not None and now is not None and now != before:
                self._event('CONFIG_CHANGED', 'INFO', message='Setup changed: retrying now.')
                return

    # ----- main loop -----
    def run(self) -> None:
        if self.platform not in cfg.DATA_ADAPTERS:
            self.state, self.error = 'UNSUPPORTED', f'The collector for {self.platform} accounts is not built yet; plans are kept.'
            return
        attempt = 0
        while not self.stopping.is_set():
            try:
                connected = self._session()
            except Exception as exc:  # noqa: BLE001 - a worker must never die silently
                log.exception('Worker %s failed', self.account_code)
                self.error, connected = f'{type(exc).__name__}: {exc}'[:300], False
                self._event('ERROR', 'WARNING', message=self.error)
            if self.stopping.is_set():
                break
            attempt = 0 if connected else attempt + 1
            self.outage_since = self.outage_since or self.clock()
            reason = explain(self.error)
            if self._next_route(connected, bool(reason and reason['permanent'])):
                attempt = 0
                self._wait_or_config_change(2)
                continue
            if reason and reason['permanent']:
                # Retrying every few seconds cannot fix a refused version or password; check every 5 minutes.
                # An SDK older than the server cannot fix itself either: each try is a real login at the broker,
                # so wait an hour (a deploy with a new mt5api.dll restarts the collector at once).
                self.state = 'FAILED'
                delay = 3600 if any(k in (self.error or '') for k in ('PROTOCOL_AFTER_LOGIN', 'SdkProtocolException')) else 300
            else:
                self.state = 'RECONNECTING'
                delay = RECONNECT_STEPS[min(attempt, len(RECONNECT_STEPS) - 1)]
            self._wait_or_config_change(delay)
        self.state = 'STOPPED'
        self.flush_journal()
        self.journal.close()

    def _next_route(self, connected: bool, permanent: bool) -> bool:
        """After a session on a route: record it, and move to the next route when this one keeps failing.
        True when the worker switched route (retry at once on the new one)."""
        route = self.active_route
        if not route:
            return False
        if connected:
            self.route_failures = 0
            account_routes.record(route['id'], True)
            # Back on a lower route long enough: the next reconnect tries the first route again.
            if self.route_idx and self.left_first_route_at and time.monotonic() - self.left_first_route_at >= account_routes.FAILBACK_SECONDS:
                self.route_idx = 0
            return False
        self.route_failures += 1
        account_routes.record(route['id'], False, self.error)
        if len(self.routes) < 2 or (self.route_failures < account_routes.SWITCH_AFTER_FAILURES and not permanent):
            return False
        self.route_idx = (self.route_idx + 1) % len(self.routes)
        self.route_failures = 0
        new = self.routes[self.route_idx]
        self.left_first_route_at = time.monotonic() if self.route_idx else None
        self.state = 'SWITCHING_ROUTE'
        self._event('FEED_SWITCH', 'WARNING', message=f"Route {route['label']} failed: switching to {new['label']}.",
                    from_route=route['label'], to_route=new['label'], error=(self.error or '')[:300])
        account_routes.fallback_alert(self.account_code, route, new, self.error)
        return True

    def _command(self, account: dict) -> tuple[list[str], dict] | None:
        """The reader to run and its first stdin line, per platform; None when it cannot start (error set)."""
        if self.platform == 'FIX':
            request = fix_request(account.get('connection_settings') or {}, account['connection_id'], 'collector', 'Stream market data (FIX session)')
            request.update({'symbols': sorted(self.wanted()), 'timeout_seconds': 15})
            return [sys.executable, '-m', 'corei_os_mini.md.fix_reader'], request
        if self.platform == 'REST_API':
            settings = account.get('connection_settings') or {}
            if 'upstox' not in (settings.get('api_base_url') or ''):
                self.state, self.error = 'FAILED', 'Only the Upstox API is supported for market data on API connections.'
                return None
            from .. import broker_login
            try:
                token = broker_login.session_token(UUID(self.account_id), 'collector', 'Stream market data (read-only)')
            except Exception as exc:  # noqa: BLE001 - HTTPException with the reason
                self.state, self.error = 'FAILED', 'BROKER_LOGIN_EXPIRED: ' + str(getattr(exc, 'detail', exc))
                return None
            return ([sys.executable, '-m', 'corei_os_mini.md.upstox_reader'],
                    {'token': token, 'api_base': settings.get('api_base_url'), 'symbols': sorted(self.wanted()),
                     'heartbeat_seconds': 5, 'poll_ms': 1000})
        if account.get('adapter') == 'TERMINAL_PUSH':
            # The COREI EA in the terminal sends; nothing to start or log in to here.
            return ['push'], {}
        bridge = bridge_of(account.get('connection_settings')) if account.get('adapter') == 'TERMINAL_BRIDGE' else None
        if account.get('adapter') == 'TERMINAL_BRIDGE' and bridge is None:
            self.state, self.error = 'FAILED', 'BRIDGE_NOT_CONFIGURED: the connection uses the MT5 terminal bridge but has no bridge host and fingerprint.'
            return None
        if bridge:
            try:
                token = account_routes.bridge_token(account, 'collector', 'Open the MT5 terminal bridge')
            except Exception as exc:  # noqa: BLE001 - HTTPException: no token saved
                where = f"route {account['route']['label']}" if account.get('route') else 'the connection'
                self.state, self.error = 'FAILED', f'BRIDGE_NOT_CONFIGURED: no bridge token saved on {where} (' + str(getattr(exc, 'detail', exc)) + ').'
                return None
            level = 'READ_ONLY' if account.get('investor_credential_kind') else 'FULL'
            secret = cfg.use_credential('accounts', UUID(self.account_id), 'collector', 'Stream market data through the terminal bridge', level)
            request = {'token': token['token'], 'mode': 'stream', 'login': int(account['external_account_id']), 'password': secret['password'],
                       'server': (account.get('connection_settings') or {}).get('server_name'),
                       'symbols': sorted(self.wanted()), 'heartbeat_seconds': 5}
            secret.clear()
            token.clear()
            return (['bridge', bridge['host'], str(bridge['port']), bridge['fingerprint']], request)
        addresses = ((account or {}).get('routing') or {}).get('addresses') or []
        if not addresses:
            self.state, self.error = 'FAILED', 'No address to connect to (check the account routing).'
            return None
        level = 'READ_ONLY' if account.get('investor_credential_kind') else 'FULL'
        secret = cfg.use_credential('accounts', UUID(self.account_id), 'collector', 'Stream market data (read-only)', level)
        request = {'login': int(account['external_account_id']), 'password': secret['password'],
                   'hosts': [{'host': a['host'], 'port': a['port']} for a in addresses[:12]],
                   'symbols': sorted(self.wanted()), 'timeout_seconds': 20, 'heartbeat_seconds': 5}
        if self.client_build:
            # The build this server accepted last time: one login instead of climbing the ladder again.
            request['client_build'] = self.client_build
        secret.clear()
        return [reader_path(), '--stream'], request

    def _session(self) -> bool:
        # FASTEST routing: measure the server's addresses again first when the last measurement is old.
        if self.platform == 'MT5' and getattr(self, 'adapter', None) != 'TERMINAL_PUSH':
            cfg.refresh_latency_if_stale(self.connection_id, 'collector')
        with cfg.transaction('read') as db:
            account = cfg.get_record(db, 'accounts', UUID(self.account_id))
            conn = db.execute('SELECT settings FROM cfg_connections WHERE id=%s', (self.connection_id,)).fetchone()
        if account:
            account['connection_settings'] = (conn or {}).get('settings') or {}
        if not account:
            self.state, self.error = 'FAILED', 'The account no longer exists.'
            return False
        self.routes = account_routes.enabled_routes(self.account_id) if self.platform == 'MT5' else []
        if self.routes:
            self.route_idx %= len(self.routes)
            route = self.routes[self.route_idx]
            if (self.active_route or {}).get('id') != route['id']:
                account_routes.mark_active(route['id'])
            self.active_route = route
            account = account_routes.apply(account, route)
            self.adapter = account['adapter']
        else:
            self.active_route = None
        started = self._command(account)
        if started is None:
            return False
        command, request = started
        if command[0] == 'push':
            return self._push_session()
        if command[0] == 'bridge':
            return self._bridge_session(command, request)
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env={**os.environ, 'DOTNET_GCHeapHardLimit': '0x40000000', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
        self._send(request)
        request.clear()
        connected = False
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            if self.stopping.is_set():
                break
            message = self._on_line(raw)
            if message == 'connected':
                connected = True
            elif message == 'end':
                break
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # The reader has answered; a process that lingers is killed rather than reported as an error.
            self.proc.kill()
            self.proc.wait(timeout=5)
        return connected

    PUSH_SILENCE = 30.0  # seconds without anything from the terminal before the source counts as disconnected
    PUSH_WAIT = 90.0     # with other routes: seconds to wait for a first push before trying the next route

    def _push_session(self) -> bool:
        """MT5 terminal push: the COREI EA's lines arrive through the database inbox (see md/push.py) and go through
        exactly the same handling as a reader's or bridge's lines. Commands go back through the outbox."""
        from . import push
        self.adapter, self.push_mode = 'TERMINAL_PUSH', True
        push.reset(self.account_id)
        self.subscribed = set()
        self.state, self.error = 'WAITING_FOR_TERMINAL', 'Waiting for the COREI EA in the MT5 terminal to send.'
        connected, last = False, time.monotonic()
        self._sync_subscriptions()
        try:
            while not self.stopping.is_set():
                lines = push.take(self.account_id)
                if lines:
                    last = time.monotonic()
                    for raw in lines:
                        if self._on_line(raw) == 'connected':
                            # The EA (re)started: it knows no subscriptions yet.
                            connected, self.subscribed = True, set()
                            self._sync_subscriptions()
                elif connected and time.monotonic() - last > self.PUSH_SILENCE:
                    self._on_line(json.dumps({'t': 'disconnected'}).encode())
                    connected = False
                    self.state, self.error = 'WAITING_FOR_TERMINAL', f'Nothing from the terminal for {int(self.PUSH_SILENCE)} s: is the PC or VPS on and the COREI EA running?'
                    if len(self.routes) > 1:
                        return False  # another route can take over
                elif not connected and len(self.routes) > 1 and time.monotonic() - last > self.PUSH_WAIT:
                    self.error = f'PUSH_SILENT: nothing from the COREI EA for {int(self.PUSH_WAIT)} s on route {(self.active_route or {}).get("label")}.'
                    return False
                self.stopping.wait(0.25)
        finally:
            self.push_mode = False
        return connected

    def _bridge_session(self, command: list[str], request: dict) -> bool:
        """The same loop over the terminal bridge's TLS stream instead of a child process."""
        _, host, port, fingerprint = command
        try:
            transport = BridgeTransport(host, int(port), fingerprint)
        except BridgeError as exc:
            request.clear()
            self._on_line(json.dumps({'t': 'failed', 'attempts': [{'host': host, 'port': int(port), 'ok': False, 'error': str(exc)}]}).encode())
            return False
        with self.write_lock:
            self.bridge = transport
        connected = False
        try:
            transport.send(request)
            request.clear()
            for raw in transport.lines():
                if self.stopping.is_set():
                    break
                if raw.startswith(b'{"t":"connected"'):
                    m = json.loads(raw)
                    m['host'], m['port'] = host, int(port)
                    raw = json.dumps(m).encode()
                message = self._on_line(raw)
                if message == 'connected':
                    connected = True
                elif message == 'end':
                    break
        except OSError as exc:
            self._on_line(json.dumps({'t': 'error', 'msg': f'Bridge connection lost: {exc}'[:200]}).encode())
        finally:
            with self.write_lock:
                self.bridge = None
            transport.close()
        return connected

    def _on_line(self, raw: bytes) -> str | None:
        try:
            m = json.loads(raw)
        except ValueError:
            return None
        kind = m.get('t')
        if kind in ('q', 'bars', 'connected', 'disconnected', 'failed'):
            with self.journal_lock:
                self.pending_journal.append(raw.rstrip(b'\n'))
        if kind == 'q':
            self._quote(m)
        elif kind == 'hb':
            self._clock_sample(m)
        elif kind == 'connected':
            self.client_build = m.get('client_build') or self.client_build
            self._clock_sample(m)
            self._connected(m)
            return 'connected'
        elif kind == 'subscribed':
            self.subscribed = set(m.get('symbols') or [])
        elif kind == 'bars':
            self._bars(m)
        elif kind == 'history_error':
            self._job_failed(m.get('msg') or 'History request failed')
        elif kind == 'failed':
            attempts = m.get('attempts') or []
            self.client_build = m.get('client_build_accepted') or self.client_build
            self.error = next((a.get('error') for a in reversed(attempts) if a.get('error')), 'Login failed')
            self.state = 'FAILED'
            self.failures += 1
            if self.error != self.reported_error:
                # One event (and alert) per distinct problem, not one per retry.
                self.reported_error = self.error
                reason = explain(self.error) or {}
                self._event('DISCONNECTED', 'CRITICAL', message=reason.get('title'), reason=self.error,
                            next_step=reason.get('detail'), addresses_tried=len(attempts))
                from .. import controls
                controls.set_connection_status(self.connection_id, 'FAILED', error=self.error)
            return 'end'
        elif kind in ('disconnected', 'stopped'):
            if kind == 'disconnected' and not self.stopping.is_set():
                self.outage_since = self.clock()
                self._event('DISCONNECTED', 'CRITICAL', host=self.host, port=self.port)
                from .. import controls
                controls.set_connection_status(self.connection_id, 'DEGRADED', error='Market-data stream disconnected')
            return 'end'
        elif kind == 'symbol_error':
            self._symbol_error(m.get('s'), m.get('msg') or 'The source rejected this symbol')
        elif kind == 'error':
            self._event('ERROR', 'WARNING', symbol=m.get('s'), message=m.get('msg'))
        return None

    def _symbol_error(self, symbol: str | None, message: str) -> None:
        st = self.plans.get(symbol or '')
        if st is None:
            return
        st.errors += 1
        first = st.last_error != message
        st.last_error = message
        if first:
            self._event('ERROR', 'WARNING', symbol=symbol, plan_id=st.plan['id'], message=message, next_step=WORDS['ERROR'][2])

    def _connected(self, m: dict) -> None:
        self.state, self.host, self.port, self.error = 'CONNECTED', m.get('host'), m.get('port'), None
        if self.active_route:
            account_routes.record(self.active_route['id'], True)
        self.reported_error, self.failures = None, 0
        self.offset = server_offset(m.get('server_time'), m.get('utc_now'))
        self.offset_stated = bool(m.get('server_time') and m.get('utc_now'))
        self.offset_samples.clear()
        # A source that cannot state its clock (the terminal bridge on a closed market) sends no server_time:
        # the offset is then measured from the first live tick instead (see _quote).
        self.offset_known = bool(m.get('server_time') and m.get('utc_now'))
        now = self.clock()
        outage = self.outage_since
        self._event('CONNECTED', 'INFO', host=self.host, port=self.port, client_build=m.get('client_build'), server_offset_minutes=int(self.offset.total_seconds() // 60),
                    outage_seconds=round((now - outage).total_seconds(), 1) if outage else None)
        from .. import controls
        controls.set_connection_status(self.connection_id, 'CONNECTED', host=self.host, port=self.port, logged_in=True, heartbeat=True,
                                       server_time_offset_ms=int(self.offset.total_seconds() * 1000))
        if outage:
            for st in self.plans.values():
                if 'BARS' in st.plan['data_types'] and st.invalid is None:
                    store.queue_gap_backfill(st.plan['id'], 'M1', floor(outage, 60) - timedelta(minutes=1), floor(now, 60) + timedelta(minutes=1))
        self.outage_since = None

    CLOCK_SKEW_WARN_MS = 500

    def _clock_sample(self, m: dict) -> None:
        """How far the reader's (bridge machine's) clock is from COREI's. Each heartbeat gives one sample
        (their clock minus ours at receipt, which is skew minus the network delay); the largest of the last 20 is the
        closest to the true skew. A clock that is off makes every latency figure wrong by that much."""
        if getattr(self, 'adapter', None) == 'TERMINAL_PUSH':
            return  # pushed quotes are stamped with COREI's own receipt time: no other clock is involved
        theirs = parse_time(m.get('utc_now'))
        if not theirs or '.' not in str(m.get('utc_now')):
            return  # whole-second stamps cannot measure sub-second skew
        self.clock_samples.append((theirs - self.clock()).total_seconds() * 1000)
        skew = max(self.clock_samples)
        self.clock_skew_ms = skew
        if abs(skew) > self.CLOCK_SKEW_WARN_MS and not self.clock_skew_reported:
            self.clock_skew_reported = True
            self._event('CLOCK_SKEW', 'WARNING', clock_skew_ms=round(skew), message=(
                f'The clock of the machine reading prices is {abs(round(skew))} ms {"ahead of" if skew > 0 else "behind"} COREI\'s. '
                'Latency figures are corrected by this amount; synchronise its clock (Windows: w32tm /resync).'))

    def _quote(self, m: dict) -> None:
        symbol = m.get('s')
        st = self.plans.get(symbol)
        rx = parse_time(m.get('rx')) or self.clock()
        if self.clock_skew_ms and abs(self.clock_skew_ms) > self.CLOCK_SKEW_WARN_MS:
            rx = rx - timedelta(milliseconds=self.clock_skew_ms)
        self.last_rx = rx
        if st is None:
            return
        source = parse_time(m.get('st'))
        if source and not self.offset_stated:
            self._remeasure_offset(source, rx, symbol)
        if source and not self.offset_known:
            # First live tick: broker time minus receipt time, rounded to 15 minutes, is the broker clock offset.
            self.offset = timedelta(minutes=round((source - rx).total_seconds() / 60 / 15) * 15)
            self.offset_known = True
            self._event('CLOCK_MEASURED', 'INFO', server_offset_minutes=int(self.offset.total_seconds() // 60), symbol=symbol)
            from .. import controls
            controls.set_connection_status(self.connection_id, 'CONNECTED', host=self.host, port=self.port, logged_in=True, heartbeat=True,
                                           server_time_offset_ms=int(self.offset.total_seconds() * 1000))
        source_utc = source - self.offset if source else None
        bid, ask, last, volume = m.get('b') or None, m.get('a') or None, m.get('l') or None, m.get('v')
        plan = st.plan
        self.ticks_total += 1
        st.errors, st.last_error = 0, None   # a price arrived: the symbol works
        at, basis = event_time(rx, source_utc)
        closed = st.bars.add(at, bid, ask, last, volume, basis, received=rx)
        rows, events = st.quality.on_tick(rx, source_utc, bid, ask)
        if bid and ask:
            st.mid, st.mid_at = (bid + ask) / 2, rx
        store_tick = 'QUOTES' in plan['data_types'] and (
            not plan['conflate_ms'] or st.last_stored is None or (rx - st.last_stored).total_seconds() * 1000 >= plan['conflate_ms'])
        key = (self.account_id, symbol)
        with self.sink.lock:
            if store_tick:
                st.last_stored = rx
                if len(self.sink.ticks) < MAX_BACKLOG:
                    self.sink.ticks.append((self.account_id, symbol, plan['id'], source_utc, rx, m.get('n') or 0, bid, ask, last, volume, False))
                else:
                    self.sink.dropped += 1
            self.sink.latest[key] = (self.account_id, symbol, bid, ask, last, source_utc, rx, m.get('n') or 0)
            for b in closed:
                self.sink.bars[(self.account_id, symbol, b['timeframe'], b['bar_time'])] = {'account_id': self.account_id, 'broker_symbol': symbol, **b}
            for r in rows:
                self.sink.quality.append({'account_id': self.account_id, 'broker_symbol': symbol, **r})
            for e in events:
                self.sink.events.append({**e, 'account_id': self.account_id, 'broker_symbol': symbol, 'plan_id': plan['id']})

    OFFSET_RECHECK = 900.0  # seconds between re-measurements of a broker clock offset the source does not state

    def _remeasure_offset(self, source: datetime, rx: datetime, symbol: str | None) -> None:
        """Broker time minus receipt, over recent ticks: the median rounded to 15 minutes. Re-checked every 15 minutes
        so a daylight-saving change or one old first tick does not shift every time for the rest of the session."""
        self.offset_samples.append((source - rx).total_seconds())
        if len(self.offset_samples) < 5 or time.monotonic() - self.offset_checked < self.OFFSET_RECHECK:
            return
        self.offset_checked = time.monotonic()
        measured = timedelta(minutes=round(median(self.offset_samples) / 60 / 15) * 15)
        if measured != self.offset:
            before = self.offset
            self.offset = measured
            self.offset_known = True
            self._event('CLOCK_MEASURED', 'WARNING' if before != timedelta(0) else 'INFO', symbol=symbol,
                        server_offset_minutes=int(measured.total_seconds() // 60), previous_minutes=int(before.total_seconds() // 60),
                        samples=len(self.offset_samples))

    def health_rows(self, now: datetime) -> dict[str, dict]:
        """One health row per plan (md_plan_health), written only when the state or detail changes, plus once a minute."""
        out = {}
        for symbol, st in self.plans.items():
            session = in_session(st.plan, now, CALENDAR)
            state = plan_state(source_state=self.state, invalid=st.invalid is not None, errors=st.errors, in_session=session,
                               last_rx=st.quality.last_rx, now=now, stale_seconds=st.plan['stale_seconds'], breaching=st.quality.breaching)
            if state == 'INVALID_SYMBOL':
                detail = f"Not in the source's symbol list." + (f" Did you mean: {', '.join(st.invalid[:3])}?" if st.invalid else '')
            elif state == 'ERROR':
                detail = (st.last_error or '')[:300]
            elif state == 'OFFLINE':
                detail = f'Source {self.state.lower().replace("_", " ")}' + (f': {self.error[:200]}' if self.error else '')
            elif state == 'DEGRADED':
                detail = 'Breaching: ' + ', '.join(sorted(st.quality.breaching & {'LATENCY', 'SPREAD', 'DEVIATION'})).lower()
            else:
                detail = WORDS[state][1]
            key = (state, detail, now.replace(second=0, microsecond=0))
            if key != st.health:
                st.health = key
                out[str(st.plan['id'])] = {'plan_id': st.plan['id'], 'account_id': self.account_id, 'broker_symbol': symbol, 'state': state,
                                           'detail': detail, 'suggestions': st.invalid or [], 'last_price_at': st.quality.last_rx,
                                           'consecutive_errors': st.errors, 'last_error': st.last_error}
        return out

    # ----- periodic (called by the supervisor) -----
    def tick(self, now: datetime) -> None:
        if self.state == 'UNSUPPORTED':
            with self.sink.lock:
                self.sink.status[self.account_id] = self.status()
                self.sink.health.update(self.health_rows(now))
            return
        with self.sink.lock:
            for symbol, st in self.plans.items():
                for b in st.bars.due(now):
                    self.sink.bars[(self.account_id, symbol, b['timeframe'], b['bar_time'])] = {'account_id': self.account_id, 'broker_symbol': symbol, **b}
                for b in st.bars.partial():
                    self.sink.bars[(self.account_id, symbol, b['timeframe'], b['bar_time'])] = {'account_id': self.account_id, 'broker_symbol': symbol, **b}
                for r in st.quality.roll(now):
                    self.sink.quality.append({'account_id': self.account_id, 'broker_symbol': symbol, **r})
                # No price is expected while the market is closed (NIFTY after 15:30 IST, weekends): no stale alarm.
                for e in (st.quality.stale(now) if in_session(st.plan, now, CALENDAR) else []):
                    self.sink.events.append({**e, 'account_id': self.account_id, 'broker_symbol': symbol, 'plan_id': st.plan['id']})
            self.sink.status[self.account_id] = self.status()
            self.sink.health.update(self.health_rows(now))
        self._next_job()

    # ----- backfill -----
    def _next_job(self) -> None:
        if self.state != 'CONNECTED':
            return
        if self.job:
            if time.monotonic() - self.job_sent_at > 120:
                self._job_failed('No answer from the broker within 120 s')
            return
        plan_ids = [st.plan['id'] for st in self.plans.values()]
        jobs = store.open_jobs(plan_ids)
        if not jobs:
            return
        job = jobs[0]
        plan = next(st.plan for st in self.plans.values() if st.plan['id'] == job['plan_id'])
        per_minute = plan['backfill_per_minute']
        while self.job_requests and time.monotonic() - self.job_requests[0] > 60:
            self.job_requests.popleft()
        if len(self.job_requests) >= per_minute:
            return
        start = job['done_until'] or job['range_from']
        if start >= job['range_to']:
            store.job_progress(job['id'], 'DONE')
            return
        end = min(job['range_to'], start + timedelta(seconds=FRAMES[job['timeframe']] * 1000))
        self.job = {**job, 'symbol': plan['broker_symbol'], 'chunk_end': end}
        self.job_sent_at = time.monotonic()
        self.job_requests.append(self.job_sent_at)
        # The broker's history is in server time.
        self._send({'cmd': 'history', 'id': str(job['id']), 'symbol': plan['broker_symbol'], 'timeframe': job['timeframe'],
                    'from': (start + self.offset).strftime('%Y-%m-%dT%H:%M:%S'), 'to': (end + self.offset).strftime('%Y-%m-%dT%H:%M:%S')})

    def _bars(self, m: dict) -> None:
        job = self.job
        if not job or m.get('id') != str(job['id']):
            return
        self.job = None
        if store.job_status(job['id']) == 'CANCELLED':
            return
        bars = []
        st = self.plans.get(job['symbol'])
        st_basis = st.bars.price_basis if st else None
        for b in m.get('bars') or []:
            t = parse_time(b.get('t'))
            if t is None or None in (b.get('o'), b.get('h'), b.get('l'), b.get('c')):
                continue
            t -= self.offset
            if not (job['range_from'] <= t < job['range_to']):
                continue
            bars.append({'account_id': self.account_id, 'broker_symbol': job['symbol'], 'timeframe': job['timeframe'], 'bar_time': t,
                         'open': b['o'], 'high': b['h'], 'low': b['l'], 'close': b['c'], 'ticks': 0,
                         'spread_avg': b.get('sp'), 'volume': b.get('v'), 'source': 'BROKER', 'complete': True,
                         'price_basis': st_basis, 'volume_kind': 'BROKER', 'anchor': 'BROKER', 'time_basis': 'BROKER', 'derivation': 'BACKFILLED'})
        done = job['chunk_end']
        store.job_progress(job['id'], 'DONE' if done >= job['range_to'] else 'RUNNING', done_until=done, bars=bars)

    def _job_failed(self, message: str) -> None:
        if self.job:
            store.job_progress(self.job['id'], 'FAILED', error=message[:300])
            self.job = None


class Collector:
    SWITCH_AFTER = 30.0     # seconds a primary must be unhealthy (with a healthy backup) before switching
    RESTORE_AFTER = 300.0   # seconds a primary must be healthy again before switching back

    def __init__(self, root: Path | None = None, clock=store.utcnow):
        self.root = root or md_root()
        self.clock = clock
        self.sink = Sink()
        self.workers: dict[str, SourceWorker] = {}
        self.stop_event = threading.Event()
        self.last_disk_warning = 0.0
        self.last_symbol_check = 0.0
        self.idle_written: dict[str, tuple] = {}
        self.invalid: dict[str, dict[str, list[str]]] = {}   # account -> {symbol: suggestions}
        self.writer = threading.Thread(target=self._write_loop, name='md-writer', daemon=True)

    # ----- writer -----
    def _write_loop(self) -> None:
        while not self.stop_event.is_set():
            self.flush()
            self.stop_event.wait(1.0)
        self.flush()

    def flush(self) -> None:
        # Take the batch first: every price in it was added to its worker's journal buffer
        # before it reached the sink, so syncing the journals now guarantees the journal
        # holds everything the database is about to receive.
        batch = self.sink.take()
        for w in list(self.workers.values()):
            try:
                w.flush_journal()
            except OSError as exc:
                log.error('Journal write failed: %s', exc)
                self.sink.put_back(batch)
                store.event('ERROR', 'CRITICAL', detail={'message': f'Journal write failed: {exc}; database writes held back'}, account_id=w.account_id)
                return
        if self.sink.dropped:
            batch['events'].append({'kind': 'BACKLOG', 'severity': 'CRITICAL',
                                    'detail': {'ticks_not_written_to_database': self.sink.dropped, 'note': 'They are in the journal.'}})
            self.sink.dropped = 0
        try:
            store.write(batch)
        except Exception as exc:  # noqa: BLE001 - keep the batch and retry
            log.warning('Database write failed; retrying with the next batch: %s', exc)
            self.sink.put_back(batch)

    # ----- supervisor -----
    def sync(self) -> None:
        now = self.clock()
        plans = store.active_plans()
        calendar = store.holidays()
        CALENDAR.clear(); CALENDAR.update(calendar)  # noqa: E702 - read by the workers' stale check
        paused = store.paused_scopes()
        deviation_bp = float(store.policy('md_deviation_bp', 10))
        by_account: dict[str, dict[str, dict]] = {}
        idle: dict[str, dict] = {}
        if time.monotonic() - self.last_symbol_check >= self.SYMBOL_CHECK:
            self.last_symbol_check = time.monotonic()
            try:
                self.invalid = store.unknown_symbols()
            except Exception:  # noqa: BLE001 - keep the last answer; checked again next minute
                log.warning('Symbol check failed', exc_info=True)
        for p in plans:
            account = str(p['account_id'])
            if 'ALL' in paused or f'account:{account}' in paused or p['account_status'] == 'ARCHIVED':
                idle[str(p['id'])] = {**self._idle(p, 'PAUSED'), 'detail': 'Collection is paused (Market data › Controls).'}
            elif not scheduled(p, now, calendar):
                idle[str(p['id'])] = self._idle(p, 'PAUSED' if p['schedule'] == 'WHILE_DEPLOYED' else 'CLOSED')
            else:
                by_account.setdefault(account, {})[p['broker_symbol']] = p
        for account, wanted in by_account.items():
            worker = self.workers.get(account)
            if worker is None or (not worker.is_alive() and worker.state != 'UNSUPPORTED'):
                worker = SourceWorker(account, wanted, self.sink, self.root, self.clock)
                worker.set_invalid(self.invalid.get(account, {}))   # before the first login: never subscribe a bad symbol
                self.workers[account] = worker
                worker.start()
            else:
                worker.update(wanted, deviation_bp)
                worker.set_invalid(self.invalid.get(account, {}))
        for account in list(self.workers):
            if account not in by_account:
                worker = self.workers.pop(account)
                worker.stop()
                with self.sink.lock:
                    self.sink.status[account] = {**worker.status(), 'state': 'PAUSED' if paused else 'STOPPED', 'pid': None}
        for worker in self.workers.values():
            worker.tick(now)
        minute = now.replace(second=0, microsecond=0)
        idle = {k: v for k, v in idle.items() if self.idle_written.get(k) != (v['state'], v['detail'], minute)}
        if idle:
            self.idle_written.update({k: (v['state'], v['detail'], minute) for k, v in idle.items()})
            with self.sink.lock:
                self.sink.health.update(idle)
        self._compare_sources(now)
        self._switch_feeds(now)
        self._guard_disk()

    SYMBOL_CHECK = 60.0  # seconds between checks of plan symbols against the sources' symbol lists

    def _idle(self, plan: dict, state: str) -> dict:
        bad = self.invalid.get(str(plan['account_id']), {}).get(plan['broker_symbol'])
        if bad is not None:
            state = 'INVALID_SYMBOL'
        return {'plan_id': plan['id'], 'account_id': str(plan['account_id']), 'broker_symbol': plan['broker_symbol'], 'state': state,
                'detail': WORDS[state][1] if bad is None else "Not in the source's symbol list." + (f" Did you mean: {', '.join(bad[:3])}?" if bad else ''),
                'suggestions': bad or [], 'last_price_at': None, 'consecutive_errors': 0, 'last_error': None}

    def _states_by_instrument(self) -> dict[str, list[tuple[SourceWorker, PlanState]]]:
        out: dict[str, list] = {}
        for w in self.workers.values():
            for st in w.plans.values():
                if st.plan.get('instrument_id'):
                    out.setdefault(str(st.plan['instrument_id']), []).append((w, st))
        return out

    def _compare_sources(self, now: datetime) -> None:
        for members in self._states_by_instrument().values():
            fresh = [(w, st) for w, st in members if st.mid and st.mid_at and (now - st.mid_at).total_seconds() <= st.plan['stale_seconds']]
            if len(fresh) < 2:
                continue
            for w, st in fresh:
                bp = consensus_deviation(st.mid, [o.mid for ow, o in fresh if o is not st])
                for e in st.quality.set_deviation(bp):
                    with self.sink.lock:
                        self.sink.events.append({**e, 'account_id': w.account_id, 'broker_symbol': st.plan['broker_symbol'], 'plan_id': st.plan['id']})

    def _switch_feeds(self, now: datetime) -> None:
        state = None
        for instrument, members in self._states_by_instrument().items():
            primary = next((st for _, st in members if st.plan['feed_role'] == 'PRIMARY' and st.plan['purpose'] == 'TRADING_FEED'), None)
            backup = next((st for _, st in members if st.plan['feed_role'] == 'BACKUP' and st.plan['purpose'] == 'TRADING_FEED'), None)
            if not primary or not backup or primary.plan['on_breach'] != 'SWITCH':
                continue
            if state is None:
                state = store.feed_state()
            active = str((state.get(instrument) or {}).get('active_plan_id') or primary.plan['id'])
            ok_primary, ok_backup = primary.quality.healthy(now), backup.quality.healthy(now)
            if ok_primary:
                primary.unhealthy_since, primary.healthy_since = None, primary.healthy_since or now
            else:
                primary.unhealthy_since, primary.healthy_since = primary.unhealthy_since or now, None
            if active == str(primary.plan['id']) and primary.unhealthy_since and ok_backup \
                    and (now - primary.unhealthy_since).total_seconds() >= self.SWITCH_AFTER:
                store.switch_feed(instrument, backup.plan['id'], 'FEED_SWITCH', f"Primary {primary.plan['code']} unhealthy for {self.SWITCH_AFTER:g} s",
                                  {'from': primary.plan['code'], 'to': backup.plan['code'], 'problems': sorted(primary.quality.breaching)})
            elif active == str(backup.plan['id']) and primary.healthy_since and (now - primary.healthy_since).total_seconds() >= self.RESTORE_AFTER:
                store.switch_feed(instrument, primary.plan['id'], 'FEED_RESTORED', f"Primary {primary.plan['code']} healthy for {self.RESTORE_AFTER:g} s",
                                  {'from': backup.plan['code'], 'to': primary.plan['code']})

    def _guard_disk(self) -> None:
        try:
            usage = shutil.disk_usage(self.root)
        except OSError:
            return
        used = usage.used / usage.total * 100 if usage.total else 0
        warn, pause = float(store.policy('md_disk_warn_pct', 85)), float(store.policy('md_disk_pause_pct', 92))
        if used >= pause:
            store.pause_for_disk(used, pause)
        elif used >= warn and time.monotonic() - self.last_disk_warning > 3600:
            self.last_disk_warning = time.monotonic()
            store.event('DISK', 'WARNING', detail={'used_pct': round(used, 1), 'warn_pct': warn, 'free_gb': round(usage.free / 1e9, 1)})

    def run(self, interval: float = 5.0) -> None:
        try:
            if not self.root.is_dir():
                self.root.mkdir(parents=True, mode=0o750)
            probe = self.root / '.write-check'
            probe.write_bytes(b'')
            probe.unlink()
        except OSError as exc:
            raise SystemExit(f'Market-data folder {self.root} is not writable by this user ({exc}). '
                             'Run bash scripts/deploy-workstation.sh, or: sudo chmod 755 ' + str(self.root.parent)) from None
        self.writer.start()
        while not self.stop_event.is_set():
            try:
                self.sync()
            except Exception:  # noqa: BLE001 - the supervisor must survive a database restart
                log.exception('Supervisor cycle failed')
            self.stop_event.wait(interval)

    def shutdown(self) -> None:
        for w in list(self.workers.values()):
            w.stop()
        self.stop_event.set()
        if self.writer.is_alive():
            self.writer.join(timeout=10)
        else:
            self.flush()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    collector = Collector()

    def stop(*_):
        collector.stop_event.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        collector.run()
    finally:
        collector.shutdown()


if __name__ == '__main__':
    main()
