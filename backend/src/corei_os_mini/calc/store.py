"""Calculation graphs in PostgreSQL: the library (built-ins from code plus the desk's graphs), their versions, and the
insert-only record of every run.

Why it exists: a calculation used for decisions must be traceable — which version, approved by whom, run on which bars
with what result. Versions and their audit rows are written in the same transaction; approved versions are frozen by
the database itself (sql/038).
Purpose: `graphs(db)`, `graph(db, code)`, `version(db, code, n)` (built-ins are version 1, APPROVED, read-only),
`record_run(db, …)` (insert-only calc_runs row; the API writes it in the transaction that serves the preview).
Must never: update or delete an approved version or a run; let a built-in be changed in place.
Dependencies: calc.builtin, calc.run (hash), psycopg. Consumers: calc_api.
Architectural position: persistence of Calculation Graphs (DEC-93); write paths for drafts, proposals and approvals
arrive with the editor (phase 3).
"""
from __future__ import annotations

import json
from datetime import datetime

from psycopg.types.json import Jsonb

from .builtin import BUILTINS
from .run import definition_hash


def _builtin_row(code: str, b: dict) -> dict:
    return {'code': code, 'name': b['name'], 'purpose': b['purpose'], 'description': b['description'], 'builtin': True,
            'versions': [{'version': 1, 'status': 'APPROVED', 'hash': definition_hash(b['definition']), 'parity_ref': b['parity_ref'],
                          'approved_by': 'platform', 'notes': b['definition'].get('notes', '')}]}


def graphs(db) -> list[dict]:
    out = [_builtin_row(code, b) for code, b in BUILTINS.items()]
    rows = db.execute('SELECT g.code, g.name, g.purpose, g.description, g.builtin, g.updated_at, '
                      "coalesce(json_agg(json_build_object('version', v.version, 'status', v.status, 'hash', v.hash, 'parity_ref', v.parity_ref, "
                      "'approved_by', v.approved_by, 'notes', v.notes) ORDER BY v.version) FILTER (WHERE v.id IS NOT NULL), '[]') AS versions "
                      'FROM calc_graphs g LEFT JOIN calc_graph_versions v ON v.graph_id = g.id GROUP BY g.id ORDER BY g.purpose, g.code').fetchall()
    out += [dict(r) for r in rows if r['code'] not in BUILTINS]
    return out


def graph(db, code: str) -> dict | None:
    return next((g for g in graphs(db) if g['code'] == code), None)


def version(db, code: str, n: int) -> dict | None:
    if code in BUILTINS:
        if n != 1:
            return None
        b = BUILTINS[code]
        return {**_builtin_row(code, b)['versions'][0], 'code': code, 'name': b['name'], 'purpose': b['purpose'], 'builtin': True,
                'definition': b['definition'], 'id': None}
    row = db.execute('SELECT v.*, g.code, g.name, g.purpose, g.builtin FROM calc_graph_versions v JOIN calc_graphs g ON g.id = v.graph_id '
                     'WHERE g.code = %s AND v.version = %s', (code, n)).fetchone()
    return dict(row) if row else None


def record_run(db, *, kind: str, actor: str, plan, result, code: str | None = None, version_id=None, account_id=None,
               symbol: str | None = None, timeframe: str | None = None, last_bar_time: datetime | None = None, parameters: dict | None = None) -> int:
    row = db.execute('INSERT INTO calc_runs(kind, graph_code, graph_version_id, graph_hash, purpose, actor, account_id, broker_symbol, timeframe, '
                     'bars, last_bar_time, parameters, ok, errors, ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                     (kind, code, version_id, plan.hash, plan.graph.purpose, actor, account_id, symbol, timeframe, result.bars, last_bar_time,
                      Jsonb(json.loads(json.dumps(parameters or {}, default=str))), result.ok, Jsonb(result.errors), result.ms)).fetchone()
    return row['id']


def recent_runs(db, limit: int = 50) -> list[dict]:
    return [dict(r) for r in db.execute('SELECT * FROM calc_runs ORDER BY id DESC LIMIT %s', (limit,)).fetchall()]
