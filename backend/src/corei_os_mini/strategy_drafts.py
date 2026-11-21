"""Versioned Studio workflows and handoff to the governed calculation graph editor."""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import config_store as store
from .calc import store as calc_store
from .calc.run import GraphInvalid, compile_graph
from .strategy_workflow import Workflow, compile_workflow


NODE_TYPES = {
    'input.bars': 'source.bars',
    'indicator.ema': 'ind.ema',
    'indicator.rsi': 'ind.rsi',
    'indicator.atr': 'ind.atr',
    'logic.and': 'logic.and',
    'logic.or': 'logic.or',
    'signal.direction': 'logic.direction',
}
INPUT_PORTS = {
    'indicator.ema': {'source': 'x'},
    'indicator.rsi': {'source': 'close'},
    'condition.compare': {'left': 'a', 'right': 'b'},
}
OUTPUT_PORTS = {
    'indicator.ema': {'value': 'out'},
    'indicator.rsi': {'value': 'out'},
    'indicator.atr': {'value': 'out'},
    'condition.compare': {'result': 'out'},
    'logic.and': {'result': 'out'},
    'logic.or': {'result': 'out'},
}


def calculation_definition(workflow: Workflow) -> dict:
    plan = compile_workflow(workflow)
    signals = [node.id for node in workflow.nodes if node.type == 'signal.direction']
    if len(signals) != 1:
        raise ValueError('Promotion requires exactly one signal.direction node.')
    by_id = {node.id: node for node in workflow.nodes}
    ids = {node_id: f'node{index}' for index, node_id in enumerate(plan['execution_order'], start=1)}
    upstream: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in workflow.edges:
        upstream[edge.target].append(edge.source)
    columns: dict[str, int] = {}
    rows: dict[int, int] = {}
    nodes = []
    for node_id, graph_id in ids.items():
        node = by_id[node_id]
        kind = NODE_TYPES.get(node.type)
        params = {}
        if node.type == 'condition.compare':
            operator = node.params['operator']
            if operator in ('CROSSES_ABOVE', 'CROSSES_BELOW'):
                kind, params = 'logic.crosses', {'direction': operator.removeprefix('CROSSES_').lower()}
            else:
                kind, params = 'logic.compare', {'op': operator}
        elif node.type.startswith('indicator.'):
            params = {'n': node.params['period']}
        if kind is None:
            raise ValueError(f'{node.id}: {node.type} requires the broker-aware planner and cannot be promoted as a strategy rule.')
        column = 1 + max((columns[parent] for parent in upstream[node_id]), default=-1)
        row = rows.get(column, 0)
        columns[node_id] = column
        rows[column] = row + 1
        nodes.append({'id': graph_id, 'type': kind, 'params': params, 'label': node.id,
                      'position': node.position or [column * 245, row * 150]})
    edges = []
    for edge in workflow.edges:
        source_port = OUTPUT_PORTS.get(by_id[edge.source].type, {}).get(edge.source_port, edge.source_port)
        target_port = INPUT_PORTS.get(by_id[edge.target].type, {}).get(edge.target_port, edge.target_port)
        edges.append({'from': f'{ids[edge.source]}.{source_port}', 'to': f'{ids[edge.target]}.{target_port}'})
    signal = signals[0]
    signal_position = by_id[signal].position or (columns[signal] * 245, 0)
    nodes.append({'id': 'rule', 'type': 'output.rule', 'label': 'Strategy rule',
                  'params': {'reason': workflow.name}, 'position': [signal_position[0] + 245, signal_position[1]]})
    edges.append({'from': f'{ids[signal]}.target', 'to': 'rule.target'})
    definition = {'purpose': 'STRATEGY_RULE', 'nodes': nodes, 'edges': edges}
    try:
        compile_graph(definition)
    except GraphInvalid as exc:
        raise ValueError(f'Calculation graph validation failed: {exc}') from exc
    return definition


def drafts(db) -> list[dict]:
    return db.execute("""
        SELECT d.id, v.version, v.workflow->>'name' AS name, v.created_at, v.created_by
        FROM studio_strategy_drafts d
        JOIN LATERAL (
            SELECT version, workflow, created_at, created_by FROM studio_strategy_draft_versions
            WHERE draft_id = d.id ORDER BY version DESC LIMIT 1
        ) v ON true
        ORDER BY v.created_at DESC, d.id
    """).fetchall()


_VERSION_FIELDS = """
    v.draft_id, v.version, v.created_at, v.created_by, v.workflow->>'name' AS name,
    g.code AS promoted_graph_code, gv.version AS promoted_graph_version
"""
_VERSION_JOIN = """
    FROM studio_strategy_draft_versions v
    LEFT JOIN calc_graph_versions gv ON gv.id = v.promotion_version_id
    LEFT JOIN calc_graphs g ON g.id = gv.graph_id
"""


def versions(db, draft_id: UUID) -> list[dict]:
    found = db.execute(f'SELECT {_VERSION_FIELDS} {_VERSION_JOIN} WHERE v.draft_id = %s ORDER BY v.version DESC', (draft_id,)).fetchall()
    if not found:
        raise HTTPException(404, 'Strategy draft not found.')
    return found


def version(db, draft_id: UUID, number: int) -> dict:
    found = db.execute(f'SELECT {_VERSION_FIELDS}, v.workflow {_VERSION_JOIN} WHERE v.draft_id = %s AND v.version = %s',
                       (draft_id, number)).fetchone()
    if not found:
        raise HTTPException(404, 'Strategy draft version not found.')
    return found


def save(db, draft_id: UUID, expected_version: int, workflow: Workflow, actor: str) -> dict:
    if expected_version == 0:
        db.execute('INSERT INTO studio_strategy_drafts(id, created_by) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING',
                   (draft_id, actor))
    parent = db.execute('SELECT created_by FROM studio_strategy_drafts WHERE id = %s FOR UPDATE', (draft_id,)).fetchone()
    if not parent:
        raise HTTPException(404, 'Strategy draft not found.')
    last = db.execute('SELECT version, workflow FROM studio_strategy_draft_versions WHERE draft_id = %s '
                      'ORDER BY version DESC LIMIT 1', (draft_id,)).fetchone()
    current = last['version'] if last else 0
    definition = workflow.model_dump(mode='json', exclude_none=True)
    if expected_version == 0 and parent['created_by'] != actor:
        raise HTTPException(409, 'This draft ID already exists.')
    if last and last['workflow'] == definition and expected_version in (current, current - 1):
        return version(db, draft_id, current)
    if expected_version != current:
        raise HTTPException(409, f'The latest draft is version {current}. Open it or save a copy as a new draft.')
    number = current + 1
    db.execute('INSERT INTO studio_strategy_draft_versions(draft_id, version, workflow, created_by) VALUES (%s,%s,%s,%s)',
               (draft_id, number, Jsonb(definition), actor))
    store.audit(db, actor, 'SAVE_STUDIO_DRAFT', 'studio_strategy_draft', draft_id,
                {'version': current} if current else None, {'version': number, 'name': workflow.name})
    return version(db, draft_id, number)


def promote(db, draft_id: UUID, number: int, code: str, reason: str, actor: str) -> dict:
    saved = db.execute('SELECT workflow, promotion_version_id FROM studio_strategy_draft_versions '
                       'WHERE draft_id = %s AND version = %s FOR UPDATE', (draft_id, number)).fetchone()
    if not saved:
        raise HTTPException(404, 'Strategy draft version not found.')
    if saved['promotion_version_id']:
        promotion = version(db, draft_id, number)
        if promotion['promoted_graph_code'] == code:
            return promotion
        raise HTTPException(409, f"This version was already handed off as {promotion['promoted_graph_code']}.")
    workflow = Workflow.model_validate(saved['workflow'])
    try:
        definition = calculation_definition(workflow)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    origin = f'Strategy Studio draft {draft_id} v{number}'
    graph = calc_store.create(db, actor, code, workflow.name, 'STRATEGY_RULE', origin, definition, '', reason)
    db.execute('UPDATE studio_strategy_draft_versions SET promotion_version_id = %s WHERE draft_id = %s AND version = %s',
               (graph['id'], draft_id, number))
    store.audit(db, actor, 'HANDOFF_STUDIO_DRAFT', 'studio_strategy_draft', draft_id,
                {'version': number}, {'version': number, 'graph_code': code, 'graph_version': graph['version']}, reason)
    return version(db, draft_id, number)
