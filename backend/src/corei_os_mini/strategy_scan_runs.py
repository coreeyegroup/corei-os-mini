"""Persistent, read-only strategy evaluations with one committed unit per instrument."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import config_store as store
from .operator_auth import require_operator
from .strategy_studio import SignalScanIn, evaluate_scan_pairs, resolve_scan_scope

router = APIRouter(prefix='/api/trading/studio/scan-runs')
log = logging.getLogger(__name__)


class ScanRunIn(BaseModel):
    mode: Literal['instrument', 'strategy']
    request: SignalScanIn
    name: str | None = Field(None, max_length=80)


class ScanProfileIn(ScanRunIn):
    name: str = Field(min_length=1, max_length=80)
    all_instruments: bool = False
    all_strategies: bool = False


@router.get('/profiles')
def profiles(operator: str = Depends(require_operator)):
    with store.transaction('read') as db:
        return {'items': db.execute('SELECT name, mode, request, all_instruments, all_strategies '
                                    'FROM studio_scan_profiles WHERE operator=%s ORDER BY name', (operator,)).fetchall()}


@router.put('/profiles')
def save_profile(payload: ScanProfileIn, operator: str = Depends(require_operator)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, 'Enter a profile name.')
    with store.transaction() as db:
        db.execute("""
            INSERT INTO studio_scan_profiles (operator, name, mode, request, all_instruments, all_strategies)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (operator, name) DO UPDATE SET mode=EXCLUDED.mode, request=EXCLUDED.request,
                all_instruments=EXCLUDED.all_instruments, all_strategies=EXCLUDED.all_strategies
        """, (operator, name, payload.mode, _json(payload.request.model_dump(mode='json')),
              payload.all_instruments, payload.all_strategies))
    return {'name': name}


def _json(value):
    return Jsonb(value, dumps=lambda value: json.dumps(value, default=str))


def _run(db, run_id, operator):
    row = db.execute('SELECT * FROM studio_scan_runs WHERE id=%s AND operator=%s', (run_id, operator)).fetchone()
    if row is None:
        raise HTTPException(404, 'Scan run not found.')
    return row


def _public(row):
    return {
        **{key: row[key] for key in ('id', 'mode', 'status', 'created_at', 'updated_at', 'failure', 'name')},
        'total': len(row['instruments']) * len(row['strategies']),
        'instruments': [i['symbol'] for i in row['instruments']],
        'strategies': [s['code'] for s in row['strategies']],
        'request': row['request'],
        'retrying': row['retry_errors'],
        'processed_instruments': row['next_instrument'],
    }


@router.post('', status_code=202)
def enqueue(payload: ScanRunIn, operator: str = Depends(require_operator)):
    with store.transaction() as db:
        instruments, strategies = resolve_scan_scope(db, payload.request)
        if not instruments or not strategies:
            raise HTTPException(422, 'The scan requires active instruments and strategies.')
        row = db.execute("""
            INSERT INTO studio_scan_runs (id, operator, mode, request, instruments, strategies, name)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """, (uuid4(), operator, payload.mode, _json(payload.request.model_dump(mode='json')),
              _json(instruments), _json(strategies), (payload.name or '').strip() or None)).fetchone()
    return _public(row)


@router.get('')
def history(offset: int = Query(0, ge=0), operator: str = Depends(require_operator)):
    with store.transaction('read') as db:
        rows = db.execute("""
            SELECT id, name, mode, status, created_at, jsonb_array_length(instruments) AS instruments,
                   jsonb_array_length(strategies) AS strategies
            FROM studio_scan_runs WHERE operator=%s ORDER BY created_at DESC, id LIMIT 21 OFFSET %s
        """, (operator, offset)).fetchall()
    return {'items': rows[:20], 'has_more': len(rows) > 20}


@router.get('/{run_id}')
def read_run(run_id: UUID, operator: str = Depends(require_operator)):
    with store.transaction('read') as db:
        row = _run(db, run_id, operator)
        column = 'instrument' if row['mode'] == 'instrument' else 'strategy'
        best = "max(strength) FILTER (WHERE status='SIGNAL') OVER (PARTITION BY strategy)" if column == 'strategy' else 'NULL::numeric'
        summary = db.execute(f"""
            WITH ranked AS (
                SELECT {column} AS scope, instrument, strategy, status, strength,
                       {best} AS best
                FROM studio_scan_results WHERE run_id=%s
            )
            SELECT scope, count(*) AS checked, count(*) FILTER (WHERE status='SIGNAL') AS signals,
                   count(*) FILTER (WHERE status='ERROR') AS errors, max(best) AS strength,
                   array_agg(instrument ORDER BY instrument) FILTER (WHERE status='SIGNAL' AND strength=best) AS strongest,
                   array_agg(strategy ORDER BY strategy) FILTER (WHERE status='SIGNAL' AND %s) AS signal_strategies
            FROM ranked GROUP BY scope ORDER BY scope
        """, (run_id, column == 'instrument')).fetchall()
    return {**_public(row), 'summary': summary}


@router.get('/{run_id}/results')
def results(run_id: UUID, group: str = '', status: Literal['SIGNAL', 'WAIT', 'ERROR'] | None = None,
            offset: int = Query(0, ge=0), operator: str = Depends(require_operator)):
    with store.transaction('read') as db:
        row = _run(db, run_id, operator)
        column = 'instrument' if row['mode'] == 'instrument' else 'strategy'
        items = db.execute(f"""
            SELECT detail FROM studio_scan_results WHERE run_id=%s
                AND (%s='' OR {column}=%s) AND (%s::text IS NULL OR status=%s)
            ORDER BY CASE status WHEN 'SIGNAL' THEN 0 WHEN 'WAIT' THEN 1 ELSE 2 END,
                     strategy, strength DESC NULLS LAST, instrument LIMIT 51 OFFSET %s
        """, (run_id, group, group, status, status, offset)).fetchall()
    return {'items': [item['detail'] for item in items[:50]], 'has_more': len(items) > 50}


@router.post('/{run_id}/cancel')
def cancel(run_id: UUID, operator: str = Depends(require_operator)):
    with store.transaction() as db:
        result = db.execute("""
            UPDATE studio_scan_runs SET status='CANCELLED', updated_at=now()
            WHERE id=%s AND operator=%s AND status IN ('QUEUED','RUNNING') RETURNING *
        """, (run_id, operator)).fetchone()
        return _public(result or _run(db, run_id, operator))


@router.post('/{run_id}/retry')
def retry(run_id: UUID, operator: str = Depends(require_operator)):
    with store.transaction() as db:
        row = db.execute("""
            UPDATE studio_scan_runs r SET status='QUEUED', updated_at=now(), failure=NULL,
                next_instrument=CASE WHEN r.status='COMPLETED' THEN 0 ELSE r.next_instrument END,
                retry_errors=CASE WHEN r.status='COMPLETED' THEN true ELSE r.retry_errors END
            WHERE r.id=%s AND r.operator=%s AND (r.status IN ('FAILED','CANCELLED')
                OR (r.status='COMPLETED' AND EXISTS (
                    SELECT 1 FROM studio_scan_results WHERE run_id=r.id AND status='ERROR')))
            RETURNING *
        """, (run_id, operator)).fetchone()
        if row is None:
            _run(db, run_id, operator)
            raise HTTPException(409, 'This scan has no unfinished or failed checks to retry.')
        return _public(row)


def step():
    """The row lock protects cursor and results together, including across worker restarts."""
    claimed = None
    try:
        with store.transaction() as db:
            row = db.execute("""
                SELECT * FROM studio_scan_runs WHERE status IN ('QUEUED','RUNNING')
                ORDER BY updated_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
            """).fetchone()
            if row is None:
                return False
            claimed = (row['id'], row['updated_at'])
            index = row['next_instrument']
            instrument = row['instruments'][index]
            strategies = row['strategies']
            if row['retry_errors']:
                failed = db.execute("""
                    SELECT strategy FROM studio_scan_results
                    WHERE run_id=%s AND instrument=%s AND status='ERROR'
                """, (row['id'], instrument['symbol'])).fetchall()
                codes = {item['strategy'] for item in failed}
                strategies = [strategy for strategy in strategies if strategy['code'] in codes]
            if strategies:
                payload = SignalScanIn.model_validate(row['request'])
                items = evaluate_scan_pairs(db, [instrument], strategies, payload, row['created_at'], freeze_bars=True)
                with db.cursor() as cursor:
                    cursor.executemany("""
                        INSERT INTO studio_scan_results (run_id, instrument, strategy, status, strength, detail)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (run_id, instrument, strategy) DO UPDATE
                        SET status=EXCLUDED.status, strength=EXCLUDED.strength, detail=EXCLUDED.detail
                    """, [(row['id'], item['instrument_symbol'], item['strategy_code'], item['status'],
                           item.get('strength'), _json(item)) for item in items])
            done = index + 1 == len(row['instruments'])
            db.execute("""
                UPDATE studio_scan_runs SET next_instrument=%s, status=%s, updated_at=now() WHERE id=%s
            """, (index + 1, 'COMPLETED' if done else 'RUNNING', row['id']))
        return True
    except Exception:
        if claimed is None:
            raise
        log.exception('Scan run failed for %s', claimed[0])
        with store.transaction() as db:
            db.execute("""
                UPDATE studio_scan_runs SET status='FAILED', failure=%s, updated_at=now()
                WHERE id=%s AND updated_at=%s AND status IN ('QUEUED','RUNNING')
            """, ('Evaluation stopped. Retry to continue from the last saved instrument.', *claimed))
        return True


async def run_forever():
    while True:
        try:
            worked = await asyncio.to_thread(step)
        except Exception:
            log.exception('Could not poll the Studio scan queue')
            await asyncio.sleep(15)
        else:
            await asyncio.sleep(0.05 if worked else 2)
