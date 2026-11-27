"""Calculation graphs in PostgreSQL: the library, versions and their life cycle, bindings into the stages, and the
insert-only record of every run.

Why it exists: a calculation used for decisions must be traceable — which version, drafted by whom, approved by whom,
bound where, run on which bars with what result. Every change and its audit row are written in the same transaction;
approved versions are frozen by the database itself (sql/038).
Purpose:
  Library      `ensure_builtins` (the built-in graphs from calc.builtin are kept in the tables as APPROVED versions
               owned by "platform"; a changed built-in becomes a new version, older ones stay for what is bound to them),
               `graphs`, `graph`, `version`.
  Life cycle   `create` (version 1 DRAFT, checked), `copy` (any version, built-ins included, as a new DRAFT graph),
               `save_draft` (edits the open DRAFT or opens the next version), `transition`: propose (DRAFT → PROPOSED, must
               check), approve (PROPOSED → APPROVED, Risk approver, never the person who proposed it — four-eyes), reject
               (PROPOSED → DRAFT), retire (APPROVED → RETIRED, refused while bound).
  Bindings     `bindings`, `bind` (an APPROVED version of the right purpose to a target; ends the previous binding),
               `unbind`, `usage`.
  Runs         `record_run`, `recent_runs`.
Must never: change an approved version (a new version instead); let a built-in be edited in place; let anyone approve
their own proposal; bind a version that is not APPROVED.
Dependencies: calc.builtin, calc.run, config_store (audit), psycopg. Consumers: calc_api, trading.signal_library.
Architectural position: persistence of Calculation Graphs (DEC-93).
"""
from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .. import config_store as cs
from .builtin import BUILTINS
from .run import GraphInvalid, compile_graph, definition_hash

BIND_PURPOSE = {'SIGNAL_DEFINITION': 'SIGNAL', 'DEPLOYMENT': 'STRATEGY_RULE', 'FEATURE': 'FEATURE', 'RISK_CHECK': 'RISK_CHECK',
                'SIZING': 'SIZING', 'MEASURE': 'MEASURE'}
LIVE_KINDS = {'SIGNAL_DEFINITION'}   # bindings the engine runs today; the others are recorded for the later phases


def _jsonable(d: dict) -> dict:
    return json.loads(json.dumps(d, default=str))


def _check(definition: dict) -> tuple[dict, str]:
    try:
        plan = compile_graph(definition)
    except GraphInvalid as exc:
        raise HTTPException(422, 'The graph has errors: ' + ' · '.join(e['message'] for e in exc.errors[:6])) from None
    return _jsonable(plan.graph.model_dump(by_alias=True)), plan.hash


def ensure_builtins(db) -> None:
    have = {r['code']: r for r in db.execute(
        'SELECT g.id, g.code, (SELECT v.hash FROM calc_graph_versions v WHERE v.graph_id = g.id ORDER BY v.version DESC LIMIT 1) AS hash, '
        '(SELECT max(v.version) FROM calc_graph_versions v WHERE v.graph_id = g.id) AS n FROM calc_graphs g WHERE g.builtin').fetchall()}
    for code, b in BUILTINS.items():
        h = definition_hash(b['definition'])
        row = have.get(code)
        if row and row['hash'] == h:
            continue
        if row is None:
            gid = db.execute('INSERT INTO calc_graphs(id, code, name, purpose, description, builtin, created_by) VALUES (%s,%s,%s,%s,%s,true,%s) '
                             'ON CONFLICT (code) DO UPDATE SET builtin = true RETURNING id',
                             (uuid4(), code, b['name'], b['purpose'], b['description'], 'platform')).fetchone()['id']
            n = 1
        else:
            gid, n = row['id'], (row['n'] or 0) + 1
        db.execute("INSERT INTO calc_graph_versions(id, graph_id, version, status, definition, hash, parity_ref, notes, created_by, approved_by, approved_at, reason) "
                   "VALUES (%s,%s,%s,'APPROVED',%s,%s,%s,%s,'platform','platform',now(),%s) ON CONFLICT (graph_id, version) DO NOTHING",
                   (uuid4(), gid, n, Jsonb(_jsonable(b['definition'])), h, b['parity_ref'], b['definition'].get('notes', ''),
                    'built-in graph, equal to its code (parity test)'))


_LIST = ('SELECT g.*, coalesce((SELECT json_agg(json_build_object(\'version\', v.version, \'status\', v.status, \'hash\', v.hash, '
         '\'parity_ref\', v.parity_ref, \'created_by\', v.created_by, \'created_at\', v.created_at, \'proposed_by\', v.proposed_by, '
         '\'approved_by\', v.approved_by, \'approved_at\', v.approved_at, \'retired_by\', v.retired_by, \'notes\', v.notes, \'reason\', v.reason, '
         '\'nodes\', jsonb_array_length(v.definition->\'nodes\')) ORDER BY v.version) FROM calc_graph_versions v WHERE v.graph_id = g.id), \'[]\') AS versions, '
         "(SELECT count(*) FROM calc_bindings b JOIN calc_graph_versions v ON v.id = b.graph_version_id WHERE v.graph_id = g.id AND b.active) AS bound "
         'FROM calc_graphs g')


def _family(code: str) -> str | None:
    return BUILTINS.get(code, {}).get('family')


def graphs(db) -> list[dict]:
    ensure_builtins(db)
    rows = db.execute(_LIST + ' ORDER BY g.builtin DESC, g.purpose, g.code').fetchall()
    return [{**cs.clean(r), 'family': _family(r['code']), 'kind': BUILTINS.get(r['code'], {}).get('kind')} for r in rows]


def graph(db, code: str) -> dict | None:
    ensure_builtins(db)
    r = db.execute(_LIST + ' WHERE g.code = %s', (code,)).fetchone()
    return None if r is None else {**cs.clean(r), 'family': _family(code), 'kind': BUILTINS.get(code, {}).get('kind')}


def version(db, code: str, n: int | None = None) -> dict | None:
    ensure_builtins(db)
    q = ('SELECT v.*, g.code, g.name, g.purpose, g.builtin, g.description FROM calc_graph_versions v JOIN calc_graphs g ON g.id = v.graph_id '
         'WHERE g.code = %s ' + ('AND v.version = %s' if n else 'ORDER BY v.version DESC LIMIT 1'))
    row = db.execute(q, (code, n) if n else (code,)).fetchone()
    return dict(row) if row else None


def _graph_row(db, code: str, lock: bool = False) -> dict:
    ensure_builtins(db)
    row = db.execute('SELECT * FROM calc_graphs WHERE code = %s' + (' FOR UPDATE' if lock else ''), (code,)).fetchone()
    if row is None:
        raise HTTPException(404, f'There is no calculation graph {code}.')
    return dict(row)


def create(db, actor: str, code: str, name: str, purpose: str, description: str, definition: dict, notes: str, reason: str) -> dict:
    ensure_builtins(db)
    if definition.get('purpose') != purpose:
        raise HTTPException(422, f"The definition's purpose ({definition.get('purpose')}) must be the graph's purpose ({purpose}).")
    d, h = _check(definition)
    if db.execute('SELECT 1 FROM calc_graphs WHERE code = %s', (code,)).fetchone():
        raise HTTPException(409, f'A calculation graph called {code} already exists.')
    gid = db.execute('INSERT INTO calc_graphs(id, code, name, purpose, description, created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
                     (uuid4(), code, name, purpose, description, actor)).fetchone()['id']
    v = db.execute("INSERT INTO calc_graph_versions(id, graph_id, version, status, definition, hash, notes, created_by, reason) "
                   "VALUES (%s,%s,1,'DRAFT',%s,%s,%s,%s,%s) RETURNING *", (uuid4(), gid, Jsonb(d), h, notes, actor, reason)).fetchone()
    cs.audit(db, actor, 'CREATE_CALC_GRAPH', 'calc_graph', gid, None, {'code': code, 'purpose': purpose, 'version': 1, 'hash': h}, reason)
    return dict(v)


def copy(db, actor: str, src: str, n: int | None, code: str, name: str, reason: str) -> dict:
    v = version(db, src, n)
    if v is None:
        raise HTTPException(404, f'Calculation graph {src} has no version {n}.')
    note = f'copy of {src} v{v["version"]}'
    return create(db, actor, code, name, v['purpose'], f"{v.get('description') or ''} ({note})".strip(), v['definition'], note, reason)


def save_draft(db, actor: str, code: str, definition: dict, notes: str, reason: str) -> dict:
    g = _graph_row(db, code, lock=True)
    if g['builtin']:
        raise HTTPException(409, 'A built-in graph is read-only: copy it to change it.')
    if definition.get('purpose') != g['purpose']:
        raise HTTPException(422, f"This graph's purpose is {g['purpose']}.")
    d, h = _check(definition)
    last = db.execute('SELECT * FROM calc_graph_versions WHERE graph_id = %s ORDER BY version DESC LIMIT 1 FOR UPDATE', (g['id'],)).fetchone()
    if last and last['status'] == 'DRAFT':
        v = db.execute('UPDATE calc_graph_versions SET definition = %s, hash = %s, notes = %s, reason = %s WHERE id = %s RETURNING *',
                       (Jsonb(d), h, notes, reason, last['id'])).fetchone()
        action = 'UPDATE_CALC_DRAFT'
    else:
        if last and last['status'] == 'PROPOSED':
            raise HTTPException(409, f"Version {last['version']} is waiting for approval; approve or reject it before drafting the next one.")
        n = (last['version'] if last else 0) + 1
        v = db.execute("INSERT INTO calc_graph_versions(id, graph_id, version, status, definition, hash, notes, created_by, reason) "
                       "VALUES (%s,%s,%s,'DRAFT',%s,%s,%s,%s,%s) RETURNING *", (uuid4(), g['id'], n, Jsonb(d), h, notes, actor, reason)).fetchone()
        action = 'CREATE_CALC_VERSION'
    db.execute('UPDATE calc_graphs SET updated_at = now() WHERE id = %s', (g['id'],))
    cs.audit(db, actor, action, 'calc_graph_version', v['id'], None if not last else {'version': last['version'], 'hash': last['hash']},
             {'code': code, 'version': v['version'], 'hash': h}, reason)
    return dict(v)


def transition(db, actor: str, can_approve: bool, code: str, n: int, action: str, reason: str) -> dict:
    g = _graph_row(db, code)
    v = db.execute('SELECT * FROM calc_graph_versions WHERE graph_id = %s AND version = %s FOR UPDATE', (g['id'], n)).fetchone()
    if v is None:
        raise HTTPException(404, f'Calculation graph {code} has no version {n}.')
    if g['builtin']:
        raise HTTPException(409, 'Built-in graphs are approved by the platform and cannot be changed; copy one to make your own.')
    before = {'status': v['status'], 'version': n, 'hash': v['hash']}
    if action == 'propose':
        if v['status'] != 'DRAFT':
            raise HTTPException(409, f"Only a draft can be proposed; version {n} is {v['status']}.")
        _check(v['definition'])
        row = db.execute("UPDATE calc_graph_versions SET status = 'PROPOSED', proposed_by = %s, proposed_at = now(), reason = %s WHERE id = %s RETURNING *",
                         (actor, reason, v['id'])).fetchone()
    elif action in ('approve', 'reject'):
        if not can_approve:
            raise HTTPException(403, 'Approving or rejecting a calculation needs the Risk approver role.')
        if v['status'] != 'PROPOSED':
            raise HTTPException(409, f"Only a proposed version can be {action}d; version {n} is {v['status']}.")
        from .. import platform_mode
        if not platform_mode.second_person_ok(db, v['proposed_by'], actor):
            raise HTTPException(409, 'Four-eyes: someone other than the person who proposed it must approve or reject it.')
        if action == 'approve':
            _check(v['definition'])
            row = db.execute("UPDATE calc_graph_versions SET status = 'APPROVED', approved_by = %s, approved_at = now(), reason = %s WHERE id = %s RETURNING *",
                             (actor, reason, v['id'])).fetchone()
        else:
            row = db.execute("UPDATE calc_graph_versions SET status = 'DRAFT', proposed_by = NULL, proposed_at = NULL, reason = %s WHERE id = %s RETURNING *",
                             (reason, v['id'])).fetchone()
    elif action == 'retire':
        if not can_approve:
            raise HTTPException(403, 'Retiring a calculation needs the Risk approver role.')
        if v['status'] != 'APPROVED':
            raise HTTPException(409, f"Only an approved version can be retired; version {n} is {v['status']}.")
        used = db.execute('SELECT target, kind FROM calc_bindings WHERE graph_version_id = %s AND active', (v['id'],)).fetchall()
        if used:
            raise HTTPException(409, 'This version is in use: ' + ', '.join(f"{u['kind'].lower().replace('_', ' ')} {u['target']}" for u in used)
                                + '. Bind those to another version first.')
        row = db.execute("UPDATE calc_graph_versions SET status = 'RETIRED', retired_by = %s, retired_at = now(), reason = %s WHERE id = %s RETURNING *",
                         (actor, reason, v['id'])).fetchone()
    else:
        raise HTTPException(422, f'Unknown action {action}.')
    cs.audit(db, actor, f'{action.upper()}_CALC_VERSION', 'calc_graph_version', v['id'], before, {'status': row['status'], 'version': n, 'code': code}, reason)
    return dict(row)


def clean_version(v: dict) -> dict:
    return {k: (str(x) if k in ('id', 'graph_id') and x is not None else x) for k, x in v.items()}


def bindings(db, active_only: bool = True) -> list[dict]:
    rows = db.execute('SELECT b.*, g.code AS graph_code, g.name AS graph_name, v.version, v.status AS version_status, v.hash FROM calc_bindings b '
                      'JOIN calc_graph_versions v ON v.id = b.graph_version_id JOIN calc_graphs g ON g.id = v.graph_id'
                      + (' WHERE b.active' if active_only else '') + ' ORDER BY b.created_at DESC').fetchall()
    return [cs.clean(r) for r in rows]


def bind(db, actor: str, kind: str, target: str, code: str, n: int | None, parameters: dict, reason: str) -> dict:
    v = version(db, code, n)
    if v is None:
        raise HTTPException(404, f'Calculation graph {code} has no version {n}.')
    if v['status'] != 'APPROVED':
        raise HTTPException(409, f"Only an approved version can be put to use; {code} v{v['version']} is {v['status']}.")
    want = BIND_PURPOSE[kind]
    if v['purpose'] != want:
        raise HTTPException(422, f"A {kind.lower().replace('_', ' ')} takes a {want} graph; {code} is {v['purpose']}.")
    if kind == 'SIGNAL_DEFINITION':
        d = db.execute('SELECT code, kind, parameters FROM sig_definitions WHERE code = %s', (target,)).fetchone()
        if d is None:
            raise HTTPException(404, f'There is no signal definition {target}.')
        names = {p['name'] for p in v['definition'].get('parameters', [])}
        params = {**{k: val for k, val in (d['parameters'] or {}).items() if k in names}, **parameters}
    else:
        params = parameters
    try:
        compile_graph(v['definition'], {k: val for k, val in params.items() if k in {p['name'] for p in v['definition'].get('parameters', [])}})
    except GraphInvalid as exc:
        raise HTTPException(422, 'The graph does not accept these parameters: ' + ' · '.join(e['message'] for e in exc.errors[:6])) from None
    before = db.execute('SELECT * FROM calc_bindings WHERE kind = %s AND target = %s AND active FOR UPDATE', (kind, target)).fetchone()
    if before:
        db.execute('UPDATE calc_bindings SET active = false, ended_by = %s, ended_at = now() WHERE id = %s', (actor, before['id']))
    row = db.execute('INSERT INTO calc_bindings(id, kind, target, graph_version_id, parameters, created_by, reason) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                     (uuid4(), kind, target, v['id'], Jsonb(_jsonable(parameters)), actor, reason)).fetchone()
    if kind == 'SIGNAL_DEFINITION':
        db.execute('DELETE FROM sig_live WHERE definition_id = (SELECT id FROM sig_definitions WHERE code = %s)', (target,))
    cs.audit(db, actor, 'BIND_CALC_GRAPH', 'calc_binding', row['id'], None if not before else cs.clean(before),
             {'kind': kind, 'target': target, 'graph': code, 'version': v['version'], 'hash': v['hash']}, reason)
    return cs.clean(row)


def unbind(db, actor: str, binding_id, reason: str) -> dict:
    row = db.execute('SELECT * FROM calc_bindings WHERE id = %s AND active FOR UPDATE', (str(binding_id),)).fetchone()
    if row is None:
        raise HTTPException(404, 'No active binding with that id.')
    db.execute('UPDATE calc_bindings SET active = false, ended_by = %s, ended_at = now() WHERE id = %s', (actor, row['id']))
    if row['kind'] == 'SIGNAL_DEFINITION':
        db.execute('DELETE FROM sig_live WHERE definition_id = (SELECT id FROM sig_definitions WHERE code = %s)', (row['target'],))
    cs.audit(db, actor, 'UNBIND_CALC_GRAPH', 'calc_binding', row['id'], cs.clean(row), None, reason)
    return cs.clean(row)


def usage(db, code: str) -> list[dict]:
    return [cs.clean(r) for r in db.execute(
        'SELECT b.*, v.version FROM calc_bindings b JOIN calc_graph_versions v ON v.id = b.graph_version_id JOIN calc_graphs g ON g.id = v.graph_id '
        'WHERE g.code = %s ORDER BY b.created_at DESC', (code,)).fetchall()]


def record_run(db, *, kind: str, actor: str, plan, result, code: str | None = None, version_id=None, account_id=None,
               symbol: str | None = None, timeframe: str | None = None, last_bar_time: datetime | None = None, parameters: dict | None = None) -> int:
    row = db.execute('INSERT INTO calc_runs(kind, graph_code, graph_version_id, graph_hash, purpose, actor, account_id, broker_symbol, timeframe, '
                     'bars, last_bar_time, parameters, ok, errors, ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                     (kind, code, version_id, plan.hash, plan.graph.purpose, actor, account_id, symbol, timeframe, result.bars, last_bar_time,
                      Jsonb(_jsonable(parameters or {})), result.ok, Jsonb(result.errors), result.ms)).fetchone()
    return row['id']


def recent_runs(db, limit: int = 50) -> list[dict]:
    return [dict(r) for r in db.execute('SELECT * FROM calc_runs ORDER BY id DESC LIMIT %s', (limit,)).fetchall()]
