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
switches feeds, guards the disk and hands backfill jobs to workers.

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
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from .. import config_store as cfg
from . import store
from .bars import BarBuilder, FRAMES, floor
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


def scheduled(plan: dict, now: datetime) -> bool:
    """Is this plan meant to collect right now?"""
    schedule = plan['schedule']
    if schedule == 'ALWAYS':
        return True
    if schedule == 'WHILE_DEPLOYED':
        return bool(plan.get('deployed'))
    if schedule == 'SESSIONS':
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
        self.dropped = 0

    def take(self) -> dict:
        with self.lock:
            batch = {'ticks': self.ticks, 'latest': self.latest, 'bars': list(self.bars.values()), 'quality': self.quality,
                     'events': self.events, 'status': self.status}
            self.ticks, self.latest, self.bars, self.quality, self.events, self.status = [], {}, {}, [], [], {}
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

    def backlog(self) -> int:
        with self.lock:
            return len(self.ticks)


class PlanState:
    def __init__(self, plan: dict):
        self.plan = plan
        self.bars = BarBuilder(plan['bar_timeframes'] if 'BARS' in plan['data_types'] else [])
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
            self.bars = BarBuilder(plan['bar_timeframes'] if 'BARS' in plan['data_types'] else [])
        self.plan = plan
        self.quality.limits = self.limits(plan, deviation_bp)


class SourceWorker(threading.Thread):
    """One account: login, stream, reconnect, backfill."""

    def __init__(self, account_id: str, plans: dict[str, dict], sink: Sink, root: Path, clock=store.utcnow):
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

    # ----- status -----
    def status(self) -> dict:
        lag = (self.clock() - self.last_rx).total_seconds() * 1000 if self.last_rx and self.state == 'CONNECTED' else None
        return {'state': self.state, 'host': self.host, 'port': self.port, 'pid': self.proc.pid if self.proc else None,
                'subscribed': len(self.subscribed), 'ticks_total': self.ticks_total, 'lag_ms': lag, 'backlog': self.sink.backlog(),
                'server_offset_minutes': int(self.offset.total_seconds() // 60), 'started_at': self.started_at, 'last_error': self.error}

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
        return {s for s, st in self.plans.items() if 'QUOTES' in st.plan['data_types'] or 'BARS' in st.plan['data_types']}

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
        with self.write_lock:
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
        if self.proc:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ----- main loop -----
    def run(self) -> None:
        if self.platform != 'MT5':
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
            if reason and reason['permanent']:
                # Retrying every few seconds cannot fix a refused version or password; check every 5 minutes.
                self.state = 'FAILED'
                delay = 300
            else:
                self.state = 'RECONNECTING'
                delay = RECONNECT_STEPS[min(attempt, len(RECONNECT_STEPS) - 1)]
            self.stopping.wait(delay)
        self.state = 'STOPPED'
        self.flush_journal()
        self.journal.close()

    def _session(self) -> bool:
        with cfg.transaction('read') as db:
            account = cfg.get_record(db, 'accounts', UUID(self.account_id))
        addresses = ((account or {}).get('routing') or {}).get('addresses') or []
        if not account or not addresses:
            self.state, self.error = 'FAILED', 'No address to connect to (check the account routing).'
            return False
        level = 'READ_ONLY' if account.get('investor_credential_kind') else 'FULL'
        secret = cfg.use_credential('accounts', UUID(self.account_id), 'collector', 'Stream market data (read-only)', level)
        request = {'login': int(account['external_account_id']), 'password': secret['password'],
                   'hosts': [{'host': a['host'], 'port': a['port']} for a in addresses[:12]],
                   'symbols': sorted(self.wanted()), 'timeout_seconds': 20, 'heartbeat_seconds': 5}
        self.proc = subprocess.Popen([reader_path(), '--stream'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env={**os.environ, 'DOTNET_GCHeapHardLimit': '0x40000000', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
        self._send(request)
        secret.clear()
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
            pass
        elif kind == 'connected':
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
        elif kind == 'error':
            self._event('ERROR', 'WARNING', message=m.get('msg'))
        return None

    def _connected(self, m: dict) -> None:
        self.state, self.host, self.port, self.error = 'CONNECTED', m.get('host'), m.get('port'), None
        self.reported_error, self.failures = None, 0
        self.offset = server_offset(m.get('server_time'), m.get('utc_now'))
        now = self.clock()
        outage = self.outage_since
        self._event('CONNECTED', 'INFO', host=self.host, port=self.port, client_build=m.get('client_build'), server_offset_minutes=int(self.offset.total_seconds() // 60),
                    outage_seconds=round((now - outage).total_seconds(), 1) if outage else None)
        from .. import controls
        controls.set_connection_status(self.connection_id, 'CONNECTED', host=self.host, port=self.port, logged_in=True, heartbeat=True,
                                       server_time_offset_ms=int(self.offset.total_seconds() * 1000))
        if outage:
            for st in self.plans.values():
                if 'BARS' in st.plan['data_types']:
                    store.queue_gap_backfill(st.plan['id'], 'M1', floor(outage, 60) - timedelta(minutes=1), floor(now, 60) + timedelta(minutes=1))
        self.outage_since = None

    def _quote(self, m: dict) -> None:
        symbol = m.get('s')
        st = self.plans.get(symbol)
        rx = parse_time(m.get('rx')) or self.clock()
        self.last_rx = rx
        if st is None:
            return
        source = parse_time(m.get('st'))
        source_utc = source - self.offset if source else None
        bid, ask, last, volume = m.get('b') or None, m.get('a') or None, m.get('l') or None, m.get('v')
        plan = st.plan
        self.ticks_total += 1
        closed = st.bars.add(rx, bid, ask, last, volume)
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

    # ----- periodic (called by the supervisor) -----
    def tick(self, now: datetime) -> None:
        if self.state == 'UNSUPPORTED':
            with self.sink.lock:
                self.sink.status[self.account_id] = self.status()
            return
        with self.sink.lock:
            for symbol, st in self.plans.items():
                for b in st.bars.due(now):
                    self.sink.bars[(self.account_id, symbol, b['timeframe'], b['bar_time'])] = {'account_id': self.account_id, 'broker_symbol': symbol, **b}
                for b in st.bars.partial():
                    self.sink.bars[(self.account_id, symbol, b['timeframe'], b['bar_time'])] = {'account_id': self.account_id, 'broker_symbol': symbol, **b}
                for r in st.quality.roll(now):
                    self.sink.quality.append({'account_id': self.account_id, 'broker_symbol': symbol, **r})
                for e in st.quality.stale(now):
                    self.sink.events.append({**e, 'account_id': self.account_id, 'broker_symbol': symbol, 'plan_id': st.plan['id']})
            self.sink.status[self.account_id] = self.status()
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
        for b in m.get('bars') or []:
            t = parse_time(b.get('t'))
            if t is None or None in (b.get('o'), b.get('h'), b.get('l'), b.get('c')):
                continue
            t -= self.offset
            if not (job['range_from'] <= t < job['range_to']):
                continue
            bars.append({'account_id': self.account_id, 'broker_symbol': job['symbol'], 'timeframe': job['timeframe'], 'bar_time': t,
                         'open': b['o'], 'high': b['h'], 'low': b['l'], 'close': b['c'], 'ticks': 0,
                         'spread_avg': b.get('sp'), 'volume': b.get('v'), 'source': 'BROKER', 'complete': True})
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
        paused = store.paused_scopes()
        deviation_bp = float(store.policy('md_deviation_bp', 10))
        by_account: dict[str, dict[str, dict]] = {}
        if 'ALL' not in paused:
            for p in plans:
                account = str(p['account_id'])
                if f'account:{account}' in paused or p['account_status'] == 'ARCHIVED' or not scheduled(p, now):
                    continue
                by_account.setdefault(account, {})[p['broker_symbol']] = p
        for account, wanted in by_account.items():
            worker = self.workers.get(account)
            if worker is None or (not worker.is_alive() and worker.state != 'UNSUPPORTED'):
                worker = SourceWorker(account, wanted, self.sink, self.root, self.clock)
                self.workers[account] = worker
                worker.start()
            else:
                worker.update(wanted, deviation_bp)
        for account in list(self.workers):
            if account not in by_account:
                worker = self.workers.pop(account)
                worker.stop()
                with self.sink.lock:
                    self.sink.status[account] = {**worker.status(), 'state': 'PAUSED' if paused else 'STOPPED', 'pid': None}
        for worker in self.workers.values():
            worker.tick(now)
        self._compare_sources(now)
        self._switch_feeds(now)
        self._guard_disk()

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
