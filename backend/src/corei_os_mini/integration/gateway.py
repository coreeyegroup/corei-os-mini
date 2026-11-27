"""Universal integration gateway, version 1: /api/v1/automation — a controlled facade over COREI's own services.

Why it exists: CORE-AI, n8n, agents, scripts and dashboards need one stable, documented, permissioned API to COREI's
financial intelligence and controlled execution, without reaching into its database or duplicating its logic.
Purpose: discovery (system, auth test, capabilities), reference data (accounts, instruments), market data (quotes,
candles, the analysis snapshot), market intelligence, strategies and durable scan jobs (wrapping Strategy Studio's
scan runs), candidate signals with provenance, risk (read, assess — always rolled back), portfolio, orders (read,
preview, paper and DEMO placement through the normal order management, risk checks, setup gates and switches;
cancel own orders), post-trade fills, usage, webhooks and the events catalogue, and a filtered OpenAPI document.
Must never: return credentials or secrets; place, approve or release a LIVE order; let an assessment or a signal create
an order; skip the per-endpoint permission or per-resource scope check; hold a transaction during an outbound call.
Dependencies: integration.auth, integration.catalog, integration.snapshot, integration.webhooks, config_store,
trading (oms, risk, portfolio, common), strategy_studio, strategy_scan_runs, intel.engine.
Consumers: external applications over HTTPS (Caddy route /api/v1/automation/*).
Architectural position: integration gateway, API surface.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from .. import config_store as store
from ..intel import engine
from . import catalog, snapshot as snap, webhooks
from .auth import Client, GatewayError, client, in_scope, need

API_VERSION = '1.0'
router = APIRouter(prefix='/api/v1/automation', tags=['integration gateway v1'])
Decimalish = Field(default=None, max_digits=38, decimal_places=12)


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 500)), max(0, offset)


def _release() -> str:
    try:
        from pathlib import Path
        return (Path(__file__).resolve().parents[4] / 'VERSION').read_text().strip()
    except OSError:
        return 'unknown'


# ---------- system and discovery ----------

@router.get('/system')
def system(c: Client = Depends(client)):
    need(c, 'system.read')
    from ..alerts import heartbeat as alert_hb
    with store.transaction('read') as db:
        schema = db.execute("SELECT max(file_name) AS f FROM corei_schema_migrations").fetchone() \
            if db.execute("SELECT to_regclass('corei_schema_migrations') AS t").fetchone()['t'] else None
        collectors = db.execute("SELECT state, count(*) AS n FROM md_plan_health GROUP BY state").fetchall()
        controls = db.execute('SELECT environment, trading_enabled FROM cfg_execution_controls').fetchall()
    return {'platform': 'COREI-OS-MINI', 'release': _release(), 'api_version': API_VERSION, 'schema': schema and schema['f'],
            'time': datetime.now(timezone.utc), 'database': 'OK',
            'services': {'market_data_feeds': {r['state']: r['n'] for r in collectors}, 'intelligence_engine': engine.STATUS,
                         'webhooks': {'last_cycle': webhooks.heartbeat['at']}, 'alerts': {'last_cycle': alert_hb['at']}},
            'order_sending': {r['environment']: r['trading_enabled'] for r in controls},
            'integrations_live_orders': False}


@router.get('/auth/test')
def auth_test(c: Client = Depends(client)):
    """Proves an external Bearer credential works end to end (no permission needed beyond a valid key)."""
    return {'authenticated': True, 'connection': c.code, 'name': c.name, 'client_type': c.client_type, 'environment': c.environment,
            'key_prefix': c.key_prefix, 'key_expires_at': c.key_expires_at, 'scopes': sorted(c.scopes),
            'access': c.access, 'limits': {'requests_per_minute': c.requests_per_minute, 'max_concurrent_jobs': c.max_concurrent_jobs}}


@router.get('/capabilities')
def capabilities(c: Client = Depends(client)):
    need(c, 'capabilities.read')
    with store.transaction('read') as db:
        tfs = [r['tf'] for r in db.execute('SELECT DISTINCT unnest(bar_timeframes) AS tf FROM md_collection_plans WHERE status=%s', ('ACTIVE',)).fetchall()]
        classes = [r['asset_class'] for r in db.execute("SELECT DISTINCT asset_class FROM cfg_instruments WHERE status='ACTIVE' ORDER BY 1").fetchall()]
        markets = [r['m'] for r in db.execute("SELECT DISTINCT coalesce(market, exchange) AS m FROM cfg_instruments WHERE status='ACTIVE' AND coalesce(market, exchange) IS NOT NULL ORDER BY 1").fetchall()]
        platforms = [dict(r) for r in db.execute("""SELECT pr.code AS provider, c.platform, c.environment, count(a.id) AS accounts
                                                   FROM cfg_connections c JOIN cfg_providers pr ON pr.id=c.provider_id
                                                   LEFT JOIN cfg_accounts a ON a.connection_id=c.id AND a.status='ACTIVE'
                                                   WHERE c.status='ACTIVE' GROUP BY 1,2,3 ORDER BY 1,2,3""").fetchall()]
    scopes = [{'scope': s, 'group': g, 'label': label, 'status': st, 'note': note,
               'granted': s in c.scopes and st != catalog.UNAVAILABLE} for s, (g, label, st, note) in catalog.SCOPES.items()]
    return {'api_version': API_VERSION, 'scopes': scopes,
            'market_data': {'timeframes_collected': sorted(tfs), 'snapshot_timeframes': list(snap.TIMEFRAMES), 'intelligence_timeframes': list(engine.TIMEFRAMES),
                            'datasets': {d: ('UNAVAILABLE' if d in snap.NOT_COLLECTED else 'AVAILABLE') for d in snap.DATASETS},
                            'unavailable_reasons': snap.NOT_COLLECTED, 'quote_kind': 'POLLED_OBSERVATION (not an exchange tick stream)',
                            'formats': ['RAW', 'NORMALIZED', 'COMPACT', 'AI_COMPACT', 'SUMMARY', 'FEATURE_VECTOR']},
            'markets': markets, 'asset_classes': classes, 'brokers': platforms,
            'scanning': {'modes': ['instrument', 'strategy'], 'async': True},
            'orders': {'environments': ['PAPER'] + (['DEMO'] if c.environment == 'DEMO' else []), 'live': 'UNAVAILABLE',
                       'idempotency': 'Idempotency-Key header (8–48 of A-Z a-z 0-9 _ -) required on POST /orders'},
            'events': catalog.EVENTS,
            'limits': {'requests_per_minute': c.requests_per_minute, 'max_concurrent_jobs': c.max_concurrent_jobs, 'max_page_size': 500,
                       'snapshot_max_instruments': 50}}


# ---------- accounts and instruments ----------

@router.get('/accounts')
def accounts(c: Client = Depends(client)):
    need(c, 'accounts.read')
    from ..trading import portfolio
    with store.transaction('read') as db:
        rows = db.execute("""SELECT a.id, a.code, a.name, a.status, a.trading_permission, a.base_currency, c.environment, c.platform, pr.code AS provider,
                                    EXISTS (SELECT 1 FROM pf_paper_accounts p WHERE p.account_id=a.id) AS paper
                             FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id JOIN cfg_providers pr ON pr.id=c.provider_id
                             WHERE a.status <> 'ARCHIVED' ORDER BY a.code""").fetchall()
        out = []
        for r in rows:
            if not c.allows('accounts', r['id']):
                continue
            eq = portfolio.equity(db, r['id'])
            out.append({**store.clean(dict(r)), 'equity': store.clean(eq)})
    return {'items': out, 'count': len(out)}


@router.get('/instruments')
def instruments(c: Client = Depends(client), q: str = Query('', max_length=40), asset_class: str | None = None, market: str | None = None,
                collected: bool | None = None, tradeable: bool | None = None, limit: int = 100, offset: int = 0):
    need(c, 'instruments.read')
    limit, offset = _page(limit, offset)
    ids = c.ids('instruments')
    with store.transaction('read') as db:
        rows = db.execute("""SELECT i.*, EXISTS (SELECT 1 FROM md_collection_plans p WHERE p.instrument_id=i.id AND p.status='ACTIVE') AS collected,
                                    EXISTS (SELECT 1 FROM cfg_symbol_mappings m WHERE m.instrument_id=i.id AND m.status='ACTIVE' AND m.usage IN ('TRADING','BOTH')) AS tradeable,
                                    (SELECT array_agg(p.code ORDER BY p.feed_role) FROM md_collection_plans p WHERE p.instrument_id=i.id AND p.status='ACTIVE') AS feeds
                             FROM cfg_instruments i
                             WHERE (%s = '' OR i.symbol ILIKE %s OR i.name ILIKE %s) AND (%s::text IS NULL OR i.asset_class=%s)
                               AND (%s::text IS NULL OR coalesce(i.market, i.exchange)=%s) AND (%s::uuid[] IS NULL OR i.id = ANY(%s::uuid[]))
                             ORDER BY i.symbol""",
                         (q, f'%{q}%', f'%{q}%', asset_class, asset_class, market, market, None if ids is None else list(ids),
                          None if ids is None else list(ids))).fetchall()
    rows = [r for r in rows if (collected is None or r['collected'] == collected) and (tradeable is None or r['tradeable'] == tradeable)]
    return {'items': [store.clean(dict(r)) for r in rows[offset:offset + limit]], 'total': len(rows), 'limit': limit, 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(rows) else None}


# ---------- market data ----------

@router.get('/market-data/quotes')
def quotes(instruments: str = Query(..., max_length=2000, description='Comma-separated symbols or ids'), c: Client = Depends(client)):
    need(c, 'market_data.read')
    req = snap.SnapshotIn(instruments=[x.strip() for x in instruments.split(',') if x.strip()][:50], include=['latest_quote', 'data_quality'],
                          timeframes=['M1'], format='NORMALIZED')
    with store.transaction('read') as db:
        out = snap.build(db, c, req)
    return {'items': [{'instrument': i['instrument'], 'source': i.get('source'), 'quote': i.get('latest_quote'), 'data_quality': i.get('data_quality')}
                      for i in out['items']], 'missing_instruments': out['missing_instruments'],
            'missing': [m for m in out['missing_datasets'] if m['dataset'] == 'latest_quote'], 'at': out['snapshot_at']}


@router.get('/market-data/candles')
def candles(instrument: str, timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = 'H1', limit: int = Query(200, ge=1, le=5000),
            start: datetime | None = None, end: datetime | None = None, feed: str = 'ACTIVE_REFERENCE', c: Client = Depends(client)):
    need(c, 'market_data.history')
    with store.transaction('read') as db:
        found, missing = snap.resolve_instruments(db, c, [instrument])
        if not found:
            raise GatewayError(404, 'INVALID_INSTRUMENT', missing[0]['message'])
        ins = found[0]
        src = snap.source_for(db, ins['id'], feed)
        if not src:
            raise GatewayError(404, 'DATA_NOT_COLLECTED', f"{ins['symbol']} has no market-data collection plan.")
        bars, source = engine.bars_for(db, src['account_id'], src['broker_symbol'], timeframe, ins.get('market'), cutoff=end,
                                       window=None if start else limit)
    if start:
        bars = [b for b in bars if b['bar_time'] >= start][-limit:]
    return {'instrument': ins['symbol'], 'timeframe': timeframe, 'source': {'plan': src['code'], 'account': src['account_code'], 'bars_from': source},
            'closed_only': True, 'count': len(bars),
            'columns': ['t', 'o', 'h', 'l', 'c', 'v', 'ticks'],
            'rows': [[b['bar_time'], snap._num(b['open']), snap._num(b['high']), snap._num(b['low']), snap._num(b['close']),
                      snap._num(b.get('volume')), b.get('ticks')] for b in bars]}


@router.post('/market-data/snapshot')
def market_snapshot(body: snap.SnapshotIn, c: Client = Depends(client)):
    need(c, 'market_data.snapshot')
    if {'indicators', 'market_regime', 'support_resistance', 'economic_events'} & set(body.include):
        need(c, 'market_intelligence.read')
    with store.transaction('write') as db:
        out = snap.build(db, c, body)
        db.execute('INSERT INTO int_snapshots(id, client_id, request_id, request, summary, output_chars) VALUES (%s,%s,%s,%s,%s,%s)',
                   (out['snapshot_id'], c.id, body.request_id, Jsonb(body.model_dump(mode='json')),
                    Jsonb({'instruments': [i['instrument']['symbol'] for i in out['items']], 'missing': len(out['missing_datasets']),
                           'coverage': out['coverage']}), out['output_chars']))
    return out


# ---------- market intelligence ----------

@router.get('/market-intelligence')
def intelligence(timeframe: Literal['M1', 'M5', 'M15', 'H1', 'H4', 'D1'] = 'H1', c: Client = Depends(client)):
    need(c, 'market_intelligence.read')
    ids = c.ids('instruments')
    with store.transaction('read') as db:
        rows = db.execute("""SELECT i.id AS instrument_id, i.symbol, p.code AS plan, s.bar_time, s.regime, s.volatility, s.confidence, s.reasons, s.session,
                                    s.features, s.computed_at
                             FROM intel_snapshot s JOIN md_collection_plans p ON p.account_id=s.account_id AND p.broker_symbol=s.broker_symbol AND p.status='ACTIVE'
                             JOIN cfg_instruments i ON i.id=p.instrument_id WHERE s.timeframe=%s ORDER BY i.symbol, p.feed_role""", (timeframe,)).fetchall()
    seen, items = set(), []
    for r in rows:
        if r['instrument_id'] in seen or (ids is not None and str(r['instrument_id']) not in ids):
            continue
        seen.add(r['instrument_id'])
        f = r['features'] or {}
        items.append({**store.clean({k: v for k, v in r.items() if k != 'features'}), 'indicators': {k: f.get(k) for k in snap.KEY_FEATURES if k in f}})
    return {'timeframe': timeframe, 'engine': engine.STATUS, 'items': items}


@router.get('/market-intelligence/screener')
def screener(timeframe: Literal['M5', 'M15', 'H1', 'H4', 'D1'] = 'H1', c: Client = Depends(client)):
    need(c, 'market_intelligence.screen')
    from ..intel_api import screener as ws_screener
    from fastapi import Response
    out = ws_screener(Response(), timeframe, c.actor)
    ids = c.ids('instruments')
    out['items'] = [i for i in out['items'] if ids is None or str(i.get('instrument_id')) in ids]
    return out


# ---------- strategies and jobs ----------

@router.get('/strategies')
def strategies(c: Client = Depends(client), status: Literal['ACTIVE', 'RETIRED', 'ALL'] = 'ACTIVE'):
    need(c, 'strategies.read')
    ids = c.ids('strategies')
    with store.transaction('read') as db:
        rows = db.execute("""SELECT s.id, s.code, s.name, s.description, s.status,
                                    (SELECT jsonb_agg(jsonb_build_object('id', v.id, 'number', v.number, 'algorithm', v.algorithm, 'parameters', v.parameters,
                                                                         'state', v.state, 'approved_at', v.approved_at) ORDER BY v.number DESC)
                                     FROM cfg_strategy_versions v WHERE v.strategy_id=s.id) AS versions,
                                    (SELECT count(*) FROM cfg_deployments d WHERE d.strategy_id=s.id AND d.status='ACTIVE') AS active_deployments
                             FROM cfg_strategies s WHERE (%s='ALL' OR s.status=%s) ORDER BY s.code""", (status, status)).fetchall()
    items = [store.clean(dict(r)) for r in rows if ids is None or str(r['id']) in ids]
    for it in items:
        it['approved_version'] = next((v for v in it.get('versions') or [] if v['state'] == 'APPROVED'), None)
    return {'items': items, 'count': len(items)}


class ScanIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str | None = Field(None, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    mode: Literal['instrument', 'strategy']
    instruments: list[str] | None = Field(None, min_length=1, max_length=500)
    strategies: list[str] | None = Field(None, min_length=1, max_length=500)
    entry_lookback: int = Field(1, ge=1, le=20)
    stop_atr: Decimal = Field(Decimal('1.5'), ge=Decimal('0.1'), le=Decimal('20'))
    reward_risk: Decimal = Field(Decimal('2'), ge=Decimal('0.1'), le=Decimal('20'))
    name: str | None = Field(None, max_length=80)


def _scoped_codes(db, c: Client, instruments: list[str] | None, strategies: list[str] | None) -> tuple[list[str] | None, list[str] | None]:
    ins_ids, strat_ids = c.ids('instruments'), c.ids('strategies')
    if instruments:
        found, missing = snap.resolve_instruments(db, c, instruments)
        if missing:
            raise GatewayError(404, 'INVALID_INSTRUMENT', '; '.join(m['instrument'] for m in missing) + ': not in the catalogue.')
        symbols = [i['symbol'] for i in found]
    elif ins_ids is not None:
        symbols = [r['symbol'] for r in db.execute("SELECT symbol FROM cfg_instruments WHERE id = ANY(%s::uuid[]) AND status='ACTIVE'", (list(ins_ids),)).fetchall()]
        if not symbols:
            raise GatewayError(403, 'OUT_OF_SCOPE', 'No instrument is in this connection\'s scope.')
    else:
        symbols = None
    if strategies:
        rows = db.execute('SELECT id, code FROM cfg_strategies WHERE code = ANY(%s)', (strategies,)).fetchall()
        if len(rows) != len(set(strategies)):
            raise GatewayError(404, 'INVALID_STRATEGY', 'One or more strategies are not in the catalogue.')
        for r in rows:
            in_scope(c, 'strategies', r['id'], f"Strategy {r['code']}")
        codes = [r['code'] for r in rows]
    elif strat_ids is not None:
        codes = [r['code'] for r in db.execute("SELECT code FROM cfg_strategies WHERE id = ANY(%s::uuid[]) AND status='ACTIVE'", (list(strat_ids),)).fetchall()]
        if not codes:
            raise GatewayError(403, 'OUT_OF_SCOPE', 'No strategy is in this connection\'s scope.')
    else:
        codes = None
    return symbols, codes


def _job(row: dict) -> dict:
    status = {'COMPLETED': 'SUCCEEDED'}.get(row['status'], row['status'])
    total = (len(row['instruments']) if isinstance(row.get('instruments'), list) else row.get('instruments')) or 0
    done = row.get('next_instrument') or 0
    return {'job_id': str(row['id']), 'type': f"strategy_scan.{row['mode']}", 'name': row.get('name'), 'status': status,
            'progress': {'done': min(done, total) if total else done, 'total': total, 'pct': round(100 * min(done, total) / total, 1) if total else None},
            'submitted_at': row.get('created_at'), 'updated_at': row.get('updated_at'), 'error': row.get('failure'),
            'result': f"/api/v1/automation/jobs/{row['id']}/results" if status == 'SUCCEEDED' else None}


def _enqueue_scan(c: Client, body: ScanIn) -> dict:
    from ..strategy_studio import SignalScanIn, resolve_scan_scope
    from ..strategy_scan_runs import _json
    with store.transaction('write') as db:
        if body.request_id:
            prev = db.execute("SELECT * FROM studio_scan_runs WHERE operator=%s AND name=%s ORDER BY created_at DESC LIMIT 1",
                              (c.actor, f'req:{body.request_id}')).fetchone()
            if prev:
                return {**_job(dict(prev)), 'duplicate': True}
        running = db.execute("SELECT count(*) AS n FROM studio_scan_runs WHERE operator=%s AND status IN ('QUEUED','RUNNING')", (c.actor,)).fetchone()['n']
        if running >= c.max_concurrent_jobs:
            raise GatewayError(429, 'TOO_MANY_JOBS', f'{running} job(s) already queued or running (limit {c.max_concurrent_jobs}).', {'Retry-After': '30'})
        symbols, codes = _scoped_codes(db, c, body.instruments, body.strategies)
        req = SignalScanIn(instrument_symbols=symbols, strategy_codes=codes, entry_lookback=body.entry_lookback, stop_atr=body.stop_atr,
                           reward_risk=body.reward_risk)
        ins, strats = resolve_scan_scope(db, req)
        if not ins or not strats:
            raise GatewayError(422, 'NOTHING_TO_SCAN', 'The scan needs active instruments and strategies in scope.')
        row = db.execute("""INSERT INTO studio_scan_runs (id, operator, mode, request, instruments, strategies, name)
                            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                         (uuid4(), c.actor, body.mode, _json(req.model_dump(mode='json')), _json(ins), _json(strats),
                          f'req:{body.request_id}' if body.request_id else (body.name or None))).fetchone()
        store.audit(db, c.actor, 'API_SCAN_JOB', 'scan_run', row['id'], None, {'mode': body.mode, 'instruments': len(ins), 'strategies': len(strats)},
                    'Integration gateway scan')
    return _job(dict(row))


@router.post('/strategies/scan', status_code=202)
def scan(body: ScanIn, c: Client = Depends(client)):
    need(c, 'strategies.scan')
    return _enqueue_scan(c, body)


class EvaluateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str | None = Field(None, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    instrument: str
    strategy: str


@router.post('/strategies/evaluate', status_code=202)
def evaluate(body: EvaluateIn, c: Client = Depends(client)):
    need(c, 'strategies.evaluate')
    return _enqueue_scan(c, ScanIn(request_id=body.request_id, mode='instrument', instruments=[body.instrument], strategies=[body.strategy]))


def _own_run(db, c: Client, job_id: UUID) -> dict:
    row = db.execute('SELECT * FROM studio_scan_runs WHERE id=%s AND operator=%s', (job_id, c.actor)).fetchone()
    if not row:
        raise GatewayError(404, 'JOB_NOT_FOUND', 'No such job for this connection.')
    return dict(row)


@router.get('/jobs')
def jobs(c: Client = Depends(client), limit: int = 50, offset: int = 0):
    need(c, 'jobs.read')
    limit, offset = _page(limit, offset)
    with store.transaction('read') as db:
        rows = db.execute('SELECT * FROM studio_scan_runs WHERE operator=%s ORDER BY created_at DESC LIMIT %s OFFSET %s', (c.actor, limit, offset)).fetchall()
    return {'items': [_job(dict(r)) for r in rows], 'limit': limit, 'offset': offset}


@router.get('/jobs/{job_id}')
def job(job_id: UUID, c: Client = Depends(client)):
    need(c, 'jobs.read')
    with store.transaction('read') as db:
        return _job(_own_run(db, c, job_id))


@router.get('/jobs/{job_id}/results')
def job_results(job_id: UUID, c: Client = Depends(client), status: Literal['SIGNAL', 'WAIT', 'ERROR'] | None = None,
                limit: int = 100, offset: int = 0):
    need(c, 'jobs.read')
    limit, offset = _page(limit, offset)
    with store.transaction('read') as db:
        run = _own_run(db, c, job_id)
        rows = db.execute("""SELECT detail FROM studio_scan_results WHERE run_id=%s AND (%s::text IS NULL OR status=%s)
                             ORDER BY CASE status WHEN 'SIGNAL' THEN 0 WHEN 'WAIT' THEN 1 ELSE 2 END, strength DESC NULLS LAST, strategy, instrument
                             LIMIT %s OFFSET %s""", (job_id, status, status, limit + 1, offset)).fetchall()
    return {'job': _job(run), 'items': [r['detail'] for r in rows[:limit]], 'limit': limit, 'offset': offset,
            'next_offset': offset + limit if len(rows) > limit else None,
            'note': 'Scan results are candidate signals for research; they are not orders.'}


@router.post('/jobs/{job_id}/cancel')
def job_cancel(job_id: UUID, c: Client = Depends(client)):
    need(c, 'jobs.cancel')
    with store.transaction('write') as db:
        _own_run(db, c, job_id)
        row = db.execute("""UPDATE studio_scan_runs SET status='CANCELLED', updated_at=now() WHERE id=%s AND operator=%s AND status IN ('QUEUED','RUNNING')
                            RETURNING *""", (job_id, c.actor)).fetchone()
        return _job(dict(row) if row else _own_run(db, c, job_id))


# ---------- signals ----------

class SignalIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    instrument: str
    account_id: UUID | None = None
    strategy_ref: str | None = Field(None, max_length=120)
    direction: Literal['LONG', 'SHORT', 'FLAT']
    entry: Decimal | None = Decimalish
    entry_condition: str | None = Field(None, max_length=500)
    stop_loss: Decimal | None = Decimalish
    targets: list[Decimal] = Field(default_factory=list, max_length=10)
    strength: Decimal | None = Field(None, ge=0, le=1, max_digits=6, decimal_places=4)
    confidence_kind: Literal['SUBJECTIVE', 'CALIBRATED', 'NONE'] = 'SUBJECTIVE'
    valid_minutes: int = Field(60, ge=1, le=10080)
    invalidation: str | None = Field(None, max_length=500)
    evidence: list = Field(default_factory=list, max_length=50)
    opposing_evidence: list = Field(default_factory=list, max_length=50)
    provenance: dict = Field(description='application, agent, model, model_version, input_snapshot_id, ...')


def _signal(r: dict) -> dict:
    out = store.clean(r)
    out['note'] = 'A candidate signal is not an order and never authorizes one.'
    return out


@router.post('/signals', status_code=201)
def propose_signal(body: SignalIn, c: Client = Depends(client)):
    need(c, 'signals.propose')
    prov = dict(body.provenance or {})
    if not prov.get('model') and not prov.get('agent') and not prov.get('application'):
        raise GatewayError(422, 'MISSING_PROVENANCE', 'provenance must name at least the application, agent or model that produced the signal.')
    if len(json.dumps(prov, default=str)) > 4000 or len(json.dumps(body.evidence, default=str)) > 20000:
        raise GatewayError(413, 'REQUEST_SIZE_EXCEEDED', 'provenance (4 000 chars) or evidence (20 000 chars) is too large.')
    prov.update({'connection': c.code, 'key_prefix': c.key_prefix, 'received_at': datetime.now(timezone.utc).isoformat()})
    with store.transaction('write') as db:
        found, missing = snap.resolve_instruments(db, c, [body.instrument])
        if not found:
            raise GatewayError(404, 'INVALID_INSTRUMENT', missing[0]['message'])
        if body.account_id:
            in_scope(c, 'accounts', body.account_id, 'Account')
        if prov.get('input_snapshot_id'):
            try:
                ok = db.execute('SELECT 1 FROM int_snapshots WHERE id=%s AND client_id=%s', (str(UUID(str(prov['input_snapshot_id']))), c.id)).fetchone()
            except ValueError:
                ok = None
            if not ok:
                raise GatewayError(422, 'INVALID_SNAPSHOT', 'input_snapshot_id is not a snapshot taken by this connection.')
        prev = db.execute('SELECT * FROM int_signals WHERE client_id=%s AND request_id=%s', (c.id, body.request_id)).fetchone()
        if prev:
            return {**_signal(dict(prev)), 'duplicate': True}
        row = db.execute("""INSERT INTO int_signals(id, client_id, request_id, instrument_id, account_id, strategy_ref, direction, entry, entry_condition, stop_loss,
                                targets, strength, confidence_kind, valid_until, invalidation, evidence, opposing_evidence, provenance)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now() + make_interval(mins => %s),%s,%s,%s,%s) RETURNING *""",
                         (uuid4(), c.id, body.request_id, found[0]['id'], body.account_id, body.strategy_ref, body.direction, body.entry, body.entry_condition,
                          body.stop_loss, body.targets, body.strength, body.confidence_kind, body.valid_minutes, body.invalidation,
                          Jsonb(json.loads(json.dumps(body.evidence, default=str))), Jsonb(json.loads(json.dumps(body.opposing_evidence, default=str))),
                          Jsonb(prov))).fetchone()
        store.audit(db, c.actor, 'API_SIGNAL', 'int_signal', row['id'], None, {'instrument': found[0]['symbol'], 'direction': body.direction},
                    'Candidate signal from an integration')
        webhooks.emit(db, 'signal.created', {'signal_id': str(row['id']), 'instrument': found[0]['symbol'], 'direction': body.direction,
                                             'connection': c.code}, client_id=c.id)
    return _signal(dict(row))


@router.get('/signals')
def list_signals(c: Client = Depends(client), status: str | None = None, limit: int = 100, offset: int = 0):
    need(c, 'signals.read')
    limit, offset = _page(limit, offset)
    with store.transaction('read') as db:
        rows = db.execute("""SELECT s.*, i.symbol, CASE WHEN s.status='PROPOSED' AND s.valid_until < now() THEN 'EXPIRED' ELSE s.status END AS status
                             FROM int_signals s JOIN cfg_instruments i ON i.id=s.instrument_id
                             WHERE s.client_id=%s AND (%s::text IS NULL OR s.status=%s) ORDER BY s.created_at DESC LIMIT %s OFFSET %s""",
                         (c.id, status, status, limit, offset)).fetchall()
    return {'items': [_signal(dict(r)) for r in rows], 'limit': limit, 'offset': offset}


@router.get('/signals/{signal_id}')
def get_signal(signal_id: UUID, c: Client = Depends(client)):
    need(c, 'signals.read')
    with store.transaction('read') as db:
        row = db.execute('SELECT s.*, i.symbol FROM int_signals s JOIN cfg_instruments i ON i.id=s.instrument_id WHERE s.id=%s AND s.client_id=%s',
                         (signal_id, c.id)).fetchone()
    if not row:
        raise GatewayError(404, 'SIGNAL_NOT_FOUND', 'No such signal for this connection.')
    return _signal(dict(row))


# ---------- risk ----------

class AssessIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    signal_id: UUID | None = None
    account_id: UUID | None = None
    instrument: str | None = None
    side: Literal['BUY', 'SELL'] | None = None
    quantity: Decimal | None = Field(None, gt=0, max_digits=38, decimal_places=12)
    order_type: Literal['MARKET', 'LIMIT', 'STOP'] = 'MARKET'
    limit_price: Decimal | None = Decimalish
    stop_price: Decimal | None = Decimalish
    mode: Literal['PAPER', 'DEMO'] = 'PAPER'


def _assess(db, c: Client, body: AssessIn) -> dict:
    """The real pre-trade risk check, inside a transaction that is always rolled back: nothing is created or changed."""
    from ..trading import common, risk
    if body.signal_id:
        s = db.execute('SELECT * FROM int_signals WHERE id=%s AND client_id=%s', (body.signal_id, c.id)).fetchone()
        if not s:
            raise GatewayError(404, 'SIGNAL_NOT_FOUND', 'No such signal for this connection.')
        if s['direction'] == 'FLAT':
            raise GatewayError(422, 'NOT_ASSESSABLE', 'A FLAT signal has no order to assess.')
        instrument_id = s['instrument_id']
        account_id = body.account_id or s['account_id']
        side = body.side or ('BUY' if s['direction'] == 'LONG' else 'SELL')
    else:
        if not (body.instrument and body.side):
            raise GatewayError(422, 'INVALID_REQUEST', 'Give signal_id, or instrument, side, quantity and account_id.')
        found, missing = snap.resolve_instruments(db, c, [body.instrument])
        if not found:
            raise GatewayError(404, 'INVALID_INSTRUMENT', missing[0]['message'])
        instrument_id, account_id, side = found[0]['id'], body.account_id, body.side
    if not account_id or not body.quantity:
        raise GatewayError(422, 'INVALID_REQUEST', 'account_id and quantity are needed for a risk assessment.')
    in_scope(c, 'accounts', account_id, 'Account')
    in_scope(c, 'instruments', instrument_id, 'Instrument')
    acc = db.execute('SELECT a.id, a.code, c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s', (account_id,)).fetchone()
    if not acc:
        raise GatewayError(404, 'INVALID_ACCOUNT', 'No such account.')
    mode = 'PAPER' if body.mode == 'PAPER' else 'BROKER'
    if mode == 'BROKER' and acc['environment'] != 'DEMO':
        raise GatewayError(403, 'LIVE_NOT_ENABLED', 'Integrations assess DEMO broker orders only; LIVE is not offered to integrations.')
    now = datetime.now(timezone.utc)
    try:
        q = common.quote(db, account_id, instrument_id)
        probe = {'id': uuid4(), 'account_id': account_id, 'instrument_id': instrument_id, 'side': side, 'quantity': body.quantity, 'mode': mode,
                 'order_type': body.order_type, 'limit_price': body.limit_price, 'stop_price': body.stop_price, 'intent_id': None, 'broker_symbol': None}
        verdict = risk.check(db, probe, None, now, q, flatten=False)
    except GatewayError:
        raise
    except Exception as exc:  # noqa: BLE001 - an assessment that cannot be made is UNAVAILABLE, never approved
        db.rollback()
        return {'decision': 'UNAVAILABLE', 'reasons': [f'ASSESSMENT_FAILED: {str(exc)[:200]}'], 'assessed_at': now}
    db.rollback()
    reasons = list(verdict.get('reasons') or [])
    decision = 'APPROVED' if verdict.get('approved') else ('UNAVAILABLE' if reasons and all(r.startswith(('NO_PRICE', 'STALE')) for r in reasons) else 'REJECTED')
    return {'decision': decision, 'reasons': reasons, 'quantity_allowed': store.clean({'q': verdict.get('quantity')})['q'],
            'account': acc['code'], 'mode': body.mode, 'quote': store.clean(q) if q else None, 'assessed_at': now,
            'note': 'An assessment is not an order authorization. Orders are checked again when placed.'}


@router.post('/risk/assess')
def assess(body: AssessIn, c: Client = Depends(client)):
    need(c, 'risk.assess')
    if body.signal_id:
        need(c, 'signals.validate')
    with store.transaction('read') as db:
        out = _assess(db, c, body)
    if body.signal_id:
        with store.transaction('write') as db:
            status = {'APPROVED': 'VALIDATED', 'REJECTED': 'REJECTED'}.get(out['decision'], 'PROPOSED')
            db.execute("UPDATE int_signals SET risk_decision=%s, risk_reasons=%s, risk_checked_at=now(), status=CASE WHEN status='PROPOSED' THEN %s ELSE status END "
                       'WHERE id=%s AND client_id=%s', (out['decision'], Jsonb(out['reasons']), status, body.signal_id, c.id))
            if out['decision'] in ('APPROVED', 'REJECTED'):
                webhooks.emit(db, 'signal.validated' if out['decision'] == 'APPROVED' else 'signal.rejected',
                              {'signal_id': str(body.signal_id), 'decision': out['decision'], 'reasons': out['reasons']}, client_id=c.id)
    return out


@router.get('/risk')
def risk_state(c: Client = Depends(client)):
    need(c, 'risk.read')
    from ..trading import risk
    acc = c.ids('accounts')
    with store.transaction('read') as db:
        limits = [x for x in risk.limits(db) if x.get('scope') != 'ACCOUNT' or acc is None or str(x.get('target_id')) in acc]
        kills = [x for x in risk.kill_switches(db) if x.get('scope') != 'ACCOUNT' or acc is None or str(x.get('target_id')) in acc]
        decisions = db.execute("""SELECT o.created_at, o.state, o.reasons, a.code AS account, i.symbol, o.side, o.quantity FROM oms_orders o
                                  JOIN cfg_accounts a ON a.id=o.account_id JOIN cfg_instruments i ON i.id=o.instrument_id
                                  WHERE o.state='REJECTED' AND (%s::uuid[] IS NULL OR o.account_id = ANY(%s::uuid[]))
                                  ORDER BY o.created_at DESC LIMIT 50""", (None if acc is None else list(acc), None if acc is None else list(acc))).fetchall()
    return {'limits': store.clean({'x': limits})['x'], 'kill_switches': store.clean({'x': kills})['x'],
            'recent_rejections': [store.clean(dict(r)) for r in decisions]}


# ---------- portfolio, orders, post-trade ----------

@router.get('/portfolio')
def portfolio_view(c: Client = Depends(client)):
    need(c, 'portfolio.read')
    from ..trading import portfolio
    acc = c.ids('accounts')
    with store.transaction('read') as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM cfg_accounts WHERE status <> 'ARCHIVED'").fetchall() if acc is None or str(r['id']) in acc]
        accounts_out = [{**store.clean(portfolio.equity(db, a)), 'account_id': str(a), 'day_pnl': str(portfolio.day_pnl(db, a))} for a in ids]
        positions = [p for p in portfolio.positions(db, None, True) if str(p.get('account_id')) in {str(i) for i in ids}]
    return {'accounts': accounts_out, 'positions': store.clean({'x': positions})['x']}


@router.get('/orders')
def list_orders(c: Client = Depends(client), open_only: bool = False, limit: int = 100):
    need(c, 'orders.read')
    from ..trading import oms
    acc = c.ids('accounts')
    with store.transaction('read') as db:
        rows = oms.orders(db, None, None, min(max(limit, 1), 500), open_only)
    return {'items': [r for r in rows if acc is None or str(r['account_id']) in acc]}


class OrderIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: UUID
    instrument: str
    side: Literal['BUY', 'SELL']
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=12)
    order_type: Literal['MARKET', 'LIMIT', 'STOP'] = 'MARKET'
    limit_price: Decimal | None = Decimalish
    stop_price: Decimal | None = Decimalish
    stop_loss: Decimal | None = Decimalish
    take_profit: Decimal | None = Decimalish
    mode: Literal['PAPER', 'DEMO', 'LIVE'] = 'PAPER'
    signal_id: UUID | None = None
    reason: str = Field('', max_length=300)


def _order_checks(db, c: Client, body: OrderIn) -> tuple[dict, dict]:
    if body.mode == 'LIVE':
        raise GatewayError(403, 'LIVE_NOT_ENABLED', 'LIVE orders through integrations are not enabled. Place LIVE orders from the workstation.')
    need(c, 'orders.paper' if body.mode == 'PAPER' else 'orders.demo')
    if body.mode == 'DEMO' and c.environment != 'DEMO':
        raise GatewayError(403, 'ENVIRONMENT_NOT_ALLOWED', 'This connection is limited to the PAPER environment.')
    in_scope(c, 'accounts', body.account_id, 'Account')
    found, missing = snap.resolve_instruments(db, c, [body.instrument])
    if not found:
        raise GatewayError(404, 'INVALID_INSTRUMENT', missing[0]['message'])
    acc = db.execute('SELECT a.id, a.code, c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id WHERE a.id=%s',
                     (body.account_id,)).fetchone()
    if not acc:
        raise GatewayError(404, 'INVALID_ACCOUNT', 'No such account.')
    if body.mode == 'DEMO' and acc['environment'] != 'DEMO':
        raise GatewayError(403, 'LIVE_NOT_ENABLED' if acc['environment'] == 'LIVE' else 'ENVIRONMENT_NOT_ALLOWED',
                           f"{acc['code']} is a {acc['environment']} account; integrations send broker orders to DEMO accounts only.")
    return found[0], dict(acc)


@router.post('/orders/preview')
def preview(body: OrderIn, c: Client = Depends(client)):
    need(c, 'orders.preview')
    with store.transaction('read') as db:
        ins, acc = _order_checks(db, c, body)
        out = _assess(db, c, AssessIn(account_id=body.account_id, instrument=ins['symbol'], side=body.side, quantity=body.quantity,
                                      order_type=body.order_type, limit_price=body.limit_price, stop_price=body.stop_price,
                                      mode='PAPER' if body.mode == 'PAPER' else 'DEMO'))
    return {**out, 'would_place': out['decision'] == 'APPROVED', 'dry_run': True}


@router.post('/orders', status_code=201)
def place_order(body: OrderIn, c: Client = Depends(client), idempotency_key: str = Header(..., alias='Idempotency-Key')):
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,48}', idempotency_key or ''):
        raise GatewayError(422, 'INVALID_IDEMPOTENCY_KEY', 'Idempotency-Key must be 8–48 characters of A-Z a-z 0-9 _ -.')
    from ..trading import oms
    with store.transaction('read') as db:
        ins, acc = _order_checks(db, c, body)
        if body.signal_id and not db.execute('SELECT 1 FROM int_signals WHERE id=%s AND client_id=%s', (body.signal_id, c.id)).fetchone():
            raise GatewayError(404, 'SIGNAL_NOT_FOUND', 'No such signal for this connection.')
    ticket_key = 'api-' + hashlib.sha256(f'{c.id}:{idempotency_key}'.encode()).hexdigest()[:40]
    reason = (body.reason or f'Integration {c.code}') + (f' (signal {body.signal_id})' if body.signal_id else '')
    out = oms.ticket(c.actor, oms.TicketIn(account_id=body.account_id, instrument_id=ins['id'], side=body.side, quantity=body.quantity,
                                           order_type=body.order_type, limit_price=body.limit_price, stop_price=body.stop_price,
                                           stop_loss=body.stop_loss, take_profit=body.take_profit, mode='PAPER' if body.mode == 'PAPER' else 'BROKER',
                                           ticket_key=ticket_key, reason=reason[:500]))
    orders = out.get('orders') if isinstance(out, dict) and 'orders' in out else [out]
    first = orders[0] if orders else {}
    with store.transaction('write') as db:
        webhooks.emit(db, 'order.rejected' if first.get('state') == 'REJECTED' else 'order.submitted',
                      {'order_id': str(first.get('id')), 'state': first.get('state'), 'account': acc['code'], 'instrument': ins['symbol'],
                       'side': body.side, 'quantity': str(body.quantity), 'mode': body.mode}, client_id=c.id)
    return {'orders': orders, 'idempotency_key': idempotency_key, 'duplicate': any(o.get('duplicate') for o in orders)}


@router.post('/orders/{order_id}/cancel')
def cancel_order(order_id: UUID, c: Client = Depends(client)):
    need(c, 'orders.cancel')
    from ..trading import oms
    with store.transaction('read') as db:
        o = db.execute('SELECT created_by, account_id FROM oms_orders WHERE id=%s', (order_id,)).fetchone()
    if not o or o['created_by'] != c.actor:
        raise GatewayError(404, 'ORDER_NOT_FOUND', 'No order placed by this connection has that id.')
    return oms.cancel(c.actor, order_id, f'Cancelled by integration {c.code}')


@router.get('/post-trade/fills')
def fills(c: Client = Depends(client), days: int = Query(30, ge=1, le=366), limit: int = 200):
    need(c, 'post_trade.read')
    acc = c.ids('accounts')
    with store.transaction('read') as db:
        rows = db.execute("""SELECT f.*, a.code AS account_code, i.symbol FROM oms_fills f JOIN cfg_accounts a ON a.id=f.account_id
                             JOIN cfg_instruments i ON i.id=f.instrument_id WHERE f.at > now() - make_interval(days => %s)
                               AND (%s::uuid[] IS NULL OR f.account_id = ANY(%s::uuid[])) ORDER BY f.at DESC LIMIT %s""",
                         (days, None if acc is None else list(acc), None if acc is None else list(acc), min(max(limit, 1), 1000))).fetchall()
    return {'items': [store.clean(dict(r)) for r in rows]}


# ---------- usage, webhooks, events ----------

@router.get('/usage')
def usage(c: Client = Depends(client), hours: int = Query(24, ge=1, le=720)):
    need(c, 'system.read')
    with store.transaction('read') as db:
        agg = db.execute("""SELECT count(*) AS requests, count(*) FILTER (WHERE status >= 400) AS errors, avg(duration_ms)::int AS avg_ms,
                                   percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::int AS p95_ms, sum(response_bytes) AS bytes
                            FROM int_usage WHERE client_id=%s AND at > now() - make_interval(hours => %s)""", (c.id, hours)).fetchone()
        by_path = db.execute("""SELECT method, path, count(*) AS n, count(*) FILTER (WHERE status >= 400) AS errors FROM int_usage
                                WHERE client_id=%s AND at > now() - make_interval(hours => %s) GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30""", (c.id, hours)).fetchall()
    return {'hours': hours, 'summary': store.clean(dict(agg)), 'by_endpoint': [dict(r) for r in by_path]}


@router.get('/events')
def events_catalog(c: Client = Depends(client)):
    need(c, 'capabilities.read')
    return {'events': [{'event': k, 'description': v} for k, v in catalog.EVENTS.items()],
            'signature': 'X-COREI-Signature: sha256=HMAC_SHA256(secret, "<X-COREI-Timestamp>.<raw body>"); de-duplicate on X-COREI-Event-Id',
            'delivery': 'at least once; retried with exponential backoff'}


class WebhookIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(max_length=500)
    events: list[str] = Field(min_length=1, max_length=30)


def create_webhook(db, client_id, actor: str, url: str, events: list[str]) -> dict:
    """Shared by the gateway and the admin screen. Returns the webhook with its signing secret (shown once)."""
    import secrets as _secrets
    from .. import credentials
    bad = [e for e in events if e not in catalog.EVENTS]
    if bad:
        raise GatewayError(422, 'INVALID_EVENT', f"Unknown event(s): {', '.join(bad)}.")
    try:
        webhooks.validate_destination(url)
    except ValueError as exc:
        raise GatewayError(422, 'UNSAFE_DESTINATION', str(exc)) from None
    wid, secret = uuid4(), 'whsec_' + _secrets.token_urlsafe(32)
    db.execute('INSERT INTO int_webhooks(id, client_id, url, events, secret, created_by) VALUES (%s,%s,%s,%s,%s,%s)',
               (wid, client_id, url, events, credentials.encrypt(wid, {'secret': secret}), actor))
    store.audit(db, actor, 'CREATE_WEBHOOK', 'int_webhook', wid, None, {'url': url, 'events': events}, 'Integration webhook')
    return {'id': str(wid), 'url': url, 'events': events, 'status': 'ACTIVE', 'signing_secret': secret,
            'note': 'Store the signing secret now; it is not shown again.'}


def webhook_list(db, client_id) -> list[dict]:
    return [store.clean(dict(r)) for r in db.execute(
        """SELECT w.id, w.url, w.events, w.status, w.timeout_seconds, w.max_attempts, w.created_at,
                  (SELECT count(*) FROM int_webhook_deliveries d WHERE d.webhook_id=w.id AND d.status='SENT') AS sent,
                  (SELECT count(*) FROM int_webhook_deliveries d WHERE d.webhook_id=w.id AND d.status='FAILED') AS failed,
                  (SELECT count(*) FROM int_webhook_deliveries d WHERE d.webhook_id=w.id AND d.status IN ('PENDING','SENDING')) AS pending,
                  (SELECT max(sent_at) FROM int_webhook_deliveries d WHERE d.webhook_id=w.id) AS last_sent_at
           FROM int_webhooks w WHERE w.client_id=%s ORDER BY w.created_at""", (client_id,)).fetchall()]


@router.get('/webhooks')
def list_webhooks(c: Client = Depends(client)):
    need(c, 'webhooks.manage')
    with store.transaction('read') as db:
        return {'items': webhook_list(db, c.id)}


@router.post('/webhooks', status_code=201)
def add_webhook(body: WebhookIn, c: Client = Depends(client)):
    need(c, 'webhooks.manage', 'events.subscribe')
    with store.transaction('write') as db:
        if db.execute('SELECT count(*) AS n FROM int_webhooks WHERE client_id=%s', (c.id,)).fetchone()['n'] >= 10:
            raise GatewayError(409, 'TOO_MANY_WEBHOOKS', 'A connection can have up to 10 webhooks.')
        return create_webhook(db, c.id, c.actor, body.url, body.events)


@router.delete('/webhooks/{webhook_id}')
def delete_webhook(webhook_id: UUID, c: Client = Depends(client)):
    need(c, 'webhooks.manage')
    with store.transaction('write') as db:
        row = db.execute('DELETE FROM int_webhooks WHERE id=%s AND client_id=%s RETURNING id, url', (webhook_id, c.id)).fetchone()
        if not row:
            raise GatewayError(404, 'WEBHOOK_NOT_FOUND', 'No such webhook for this connection.')
        store.audit(db, c.actor, 'DELETE_WEBHOOK', 'int_webhook', webhook_id, {'url': row['url']}, None, 'Integration webhook')
    return {'deleted': True}


@router.post('/webhooks/{webhook_id}/test', status_code=202)
def test_webhook(webhook_id: UUID, c: Client = Depends(client)):
    need(c, 'webhooks.manage')
    with store.transaction('write') as db:
        if not db.execute('SELECT 1 FROM int_webhooks WHERE id=%s AND client_id=%s', (webhook_id, c.id)).fetchone():
            raise GatewayError(404, 'WEBHOOK_NOT_FOUND', 'No such webhook for this connection.')
        queue_test(db, webhook_id, c.code)
    return {'queued': True, 'note': 'Delivered within about 5 seconds; see GET /webhooks/deliveries.'}


def queue_test(db, webhook_id, who: str) -> None:
    eid = uuid4()
    payload = {'event_id': str(eid), 'event': 'webhook.test', 'occurred_at': datetime.now(timezone.utc).isoformat(), 'source': 'corei-os-mini',
               'data': {'message': f'Test delivery requested by {who}.'}}
    db.execute('INSERT INTO int_webhook_deliveries(webhook_id, event_id, event, payload) VALUES (%s,%s,%s,%s)',
               (webhook_id, eid, 'webhook.test', json.dumps(payload)))


@router.get('/webhooks/deliveries')
def deliveries(c: Client = Depends(client), limit: int = 100):
    need(c, 'webhooks.manage')
    with store.transaction('read') as db:
        rows = db.execute("""SELECT d.id, d.webhook_id, d.event_id, d.event, d.status, d.attempts, d.last_status, d.last_error, d.created_at, d.sent_at, d.next_at
                             FROM int_webhook_deliveries d JOIN int_webhooks w ON w.id=d.webhook_id WHERE w.client_id=%s
                             ORDER BY d.id DESC LIMIT %s""", (c.id, min(max(limit, 1), 500))).fetchall()
    return {'items': [store.clean(dict(r)) for r in rows]}


@router.post('/webhooks/deliveries/{delivery_id}/replay', status_code=202)
def replay(delivery_id: int, c: Client = Depends(client)):
    need(c, 'webhooks.manage')
    with store.transaction('write') as db:
        row = db.execute("""UPDATE int_webhook_deliveries d SET status='PENDING', next_at=now(), attempts=0 FROM int_webhooks w
                            WHERE d.id=%s AND w.id=d.webhook_id AND w.client_id=%s AND d.status IN ('FAILED','SENT') RETURNING d.id""",
                         (delivery_id, c.id)).fetchone()
    if not row:
        raise GatewayError(404, 'DELIVERY_NOT_FOUND', 'No finished delivery with that id for this connection.')
    return {'queued': True, 'note': 'The same event_id is sent again; receivers de-duplicate on it.'}


P = '/api/v1/automation'
ENDPOINT_SCOPES: dict[tuple[str, str], list[str]] = {
    ('get', P + '/system'): ['system.read'], ('get', P + '/auth/test'): [], ('get', P + '/capabilities'): ['capabilities.read'],
    ('get', P + '/accounts'): ['accounts.read'], ('get', P + '/instruments'): ['instruments.read'],
    ('get', P + '/market-data/quotes'): ['market_data.read'], ('get', P + '/market-data/candles'): ['market_data.history'],
    ('post', P + '/market-data/snapshot'): ['market_data.snapshot', 'market_intelligence.read (for indicators, regime, levels, events)'],
    ('get', P + '/market-intelligence'): ['market_intelligence.read'], ('get', P + '/market-intelligence/screener'): ['market_intelligence.screen'],
    ('get', P + '/strategies'): ['strategies.read'], ('post', P + '/strategies/scan'): ['strategies.scan'],
    ('post', P + '/strategies/evaluate'): ['strategies.evaluate'], ('get', P + '/jobs'): ['jobs.read'], ('get', P + '/jobs/{job_id}'): ['jobs.read'],
    ('get', P + '/jobs/{job_id}/results'): ['jobs.read'], ('post', P + '/jobs/{job_id}/cancel'): ['jobs.cancel'],
    ('post', P + '/signals'): ['signals.propose'], ('get', P + '/signals'): ['signals.read'], ('get', P + '/signals/{signal_id}'): ['signals.read'],
    ('post', P + '/risk/assess'): ['risk.assess', 'signals.validate (with signal_id)'], ('get', P + '/risk'): ['risk.read'],
    ('get', P + '/portfolio'): ['portfolio.read'], ('get', P + '/orders'): ['orders.read'], ('post', P + '/orders/preview'): ['orders.preview'],
    ('post', P + '/orders'): ['orders.paper (mode PAPER)', 'orders.demo (mode DEMO)'], ('post', P + '/orders/{order_id}/cancel'): ['orders.cancel'],
    ('get', P + '/post-trade/fills'): ['post_trade.read'], ('get', P + '/usage'): ['system.read'], ('get', P + '/events'): ['capabilities.read'],
    ('get', P + '/webhooks'): ['webhooks.manage'], ('post', P + '/webhooks'): ['webhooks.manage', 'events.subscribe'],
    ('delete', P + '/webhooks/{webhook_id}'): ['webhooks.manage'], ('post', P + '/webhooks/{webhook_id}/test'): ['webhooks.manage'],
    ('get', P + '/webhooks/deliveries'): ['webhooks.manage'], ('post', P + '/webhooks/deliveries/{delivery_id}/replay'): ['webhooks.manage'],
}


@router.get('/openapi.json', include_in_schema=False)
def openapi_v1(request: Request):
    """OpenAPI for the gateway only: the app's document filtered to /api/v1/automation and the schemas those paths use.
    Public: it documents shapes, not data."""
    import copy
    full = request.app.openapi()
    paths = {p: v for p, v in full.get('paths', {}).items() if p.startswith('/api/v1/automation')}
    schemas = full.get('components', {}).get('schemas', {})
    keep: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get('$ref')
            if isinstance(ref, str) and ref.startswith('#/components/schemas/'):
                name = ref.rsplit('/', 1)[1]
                if name not in keep and name in schemas:
                    keep.add(name)
                    walk(schemas[name])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(paths)
    return {'openapi': full.get('openapi', '3.1.0'),
            'info': {'title': 'COREI-OS-MINI Integration Gateway', 'version': API_VERSION,
                     'description': 'Bearer-authenticated API for CORE-AI, n8n and other applications. See docs/INTEGRATION_GATEWAY.md.'},
            'paths': {p: {m: {**op, 'x-required-scopes': ENDPOINT_SCOPES.get((m, p), [])} if isinstance(op, dict) else op
                          for m, op in methods.items()} for p, methods in copy.deepcopy(paths).items()},
            'components': {'schemas': {k: schemas[k] for k in sorted(keep)}, 'securitySchemes': {'bearer': {'type': 'http', 'scheme': 'bearer'}}},
            'security': [{'bearer': []}]}
