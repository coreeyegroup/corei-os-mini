"""Strategy registry and deployment matrix.

Owns: strategies, immutable strategy versions with an approval lifecycle, the
algorithm catalogue (which rules exist and what parameters they take), and
deployments: explicit strategy-version × instrument × account rows that name their
market-data source. Readiness explains every reason a deployment cannot trade.

Does not own: signal generation (strategy.py), risk limits, or order submission.

Permissions: Trading operations creates and edits; Risk approver approves versions
and activates deployments. On LIVE accounts the activating approver must be a
different person from the one who proposed the deployment (Blueprint §15).
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from . import config_store as store
from .operator_auth import Principal, current_user, require_role

router = APIRouter(prefix='/api/config', tags=['strategies'])
_ops = require_role('TRADING_OPS')
_approver = require_role('RISK_APPROVER')
_ops_or_approver = require_role('TRADING_OPS', 'RISK_APPROVER')


# ---------- algorithm catalogue ----------

class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MaCrossoverParameters(_Strict):
    """Buy when the fast simple moving average is above the slow one, sell when below."""
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] = Field(description='Bar period the averages are computed on')
    fast_window: int = Field(ge=1, le=500, description='Bars in the fast moving average')
    slow_window: int = Field(ge=2, le=1000, description='Bars in the slow moving average')

    @model_validator(mode='after')
    def fast_below_slow(self) -> 'MaCrossoverParameters':
        if self.fast_window >= self.slow_window:
            raise ValueError('fast_window must be smaller than slow_window')
        return self


# New rule sets are added here once their logic is implemented and tested in strategy.py.
ALGORITHMS: dict[str, dict] = {
    'MA_CROSSOVER': {'label': 'Moving-average crossover', 'model': MaCrossoverParameters,
                     'description': MaCrossoverParameters.__doc__},
}


def validate_parameters(algorithm: str, parameters: object) -> dict:
    spec = ALGORITHMS.get(algorithm)
    if not spec:
        raise HTTPException(422, f'Unknown algorithm {algorithm}. Choose one of: {", ".join(ALGORITHMS)}.')
    try:
        return spec['model'].model_validate(parameters).model_dump(mode='json')
    except ValidationError as exc:
        raise HTTPException(422, [{'field': 'parameters.' + '.'.join(str(p) for p in e['loc']) if e['loc'] else 'parameters',
                                   'message': e['msg'].removeprefix('Value error, ')} for e in exc.errors()]) from None


# ---------- inputs ----------

Code = Annotated[str, BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
                 StringConstraints(pattern=r'^[A-Z][A-Z0-9_]{1,31}$')]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class StrategyIn(_Strict):
    code: Code
    name: Name
    description: Text = ''
    status: Literal['ACTIVE', 'RETIRED'] = 'ACTIVE'


class VersionIn(_Strict):
    algorithm: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=64)]
    parameters: dict
    notes: Text = ''


class DeploymentIn(_Strict):
    strategy_id: UUID
    strategy_version_id: UUID
    instrument_id: UUID
    account_id: UUID
    data_mapping_id: UUID
    allocation_weight_bps: int = Field(10_000, ge=1, le=10_000)
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ''


class Versioned(_Strict):
    version: int = Field(ge=1)


def _parse(model: type[BaseModel], payload: object) -> BaseModel:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, [{'field': '.'.join(str(p) for p in e['loc']) or 'record',
                                   'message': e['msg'].removeprefix('Value error, ')} for e in exc.errors()]) from None


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


def _locked(db, table: str, record_id: UUID, version: int, label: str) -> dict:
    row = db.execute(f'SELECT * FROM {table} WHERE id=%s FOR UPDATE', (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, f'{label.capitalize()} not found.')
    if row['version'] != version:
        raise HTTPException(409, f'This {label} was changed by someone else. Reload it before saving.')
    return row


# ---------- strategies ----------

STRATEGY_SELECT = '''SELECT s.*,
    (SELECT count(*) FROM cfg_strategy_versions v WHERE v.strategy_id=s.id) AS version_count,
    (SELECT max(number) FROM cfg_strategy_versions v WHERE v.strategy_id=s.id AND v.state='APPROVED') AS latest_approved,
    (SELECT count(*) FROM cfg_deployments d WHERE d.strategy_id=s.id AND d.status='ACTIVE') AS active_deployments
    FROM cfg_strategies s'''


@router.get('/algorithms')
def algorithms(response: Response, _: Principal = Depends(current_user)):
    _no_store(response)
    return {'items': [{'id': key, 'label': spec['label'], 'description': spec['description'],
                       'schema': spec['model'].model_json_schema()} for key, spec in ALGORITHMS.items()]}


@router.get('/strategies')
def list_strategies(response: Response, q: str = Query('', max_length=120), _: Principal = Depends(current_user)):
    _no_store(response)
    with store.transaction('read') as db:
        pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        rows = db.execute(STRATEGY_SELECT + ' WHERE s.code ILIKE %s OR s.name ILIKE %s ORDER BY s.code', (pattern, pattern)).fetchall()
    return {'items': [store.clean(r) for r in rows]}


@router.post('/strategies', status_code=201)
def create_strategy(payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    data = _parse(StrategyIn, payload)
    with store.transaction('create') as db:
        if db.execute('SELECT 1 FROM cfg_strategies WHERE code=%s', (data.code,)).fetchone():
            raise HTTPException(409, 'A strategy with this code already exists.')
        row = db.execute('INSERT INTO cfg_strategies(id, code, name, description, status) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(), data.code, data.name, data.description, data.status)).fetchone()
        store.audit(db, user.username, 'CREATE', 'strategy', row['id'], None, row)
        return store.clean(db.execute(STRATEGY_SELECT + ' WHERE s.id=%s', (row['id'],)).fetchone())


@router.put('/strategies/{strategy_id}')
def update_strategy(strategy_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    version = _parse(Versioned, {'version': payload.pop('version', None)}).version
    data = _parse(StrategyIn, payload)
    with store.transaction('update') as db:
        before = _locked(db, 'cfg_strategies', strategy_id, version, 'strategy')
        if data.code != before['code']:
            raise HTTPException(422, 'code cannot be changed. Create a new strategy instead.')
        if data.status == 'RETIRED' and db.execute("SELECT 1 FROM cfg_deployments WHERE strategy_id=%s AND status='ACTIVE'", (strategy_id,)).fetchone():
            raise HTTPException(422, 'This strategy has active deployments. Pause or end them before retiring it.')
        after = db.execute('UPDATE cfg_strategies SET name=%s, description=%s, status=%s, version=version+1, updated_at=now() '
                           'WHERE id=%s RETURNING *', (data.name, data.description, data.status, strategy_id)).fetchone()
        store.audit(db, user.username, 'UPDATE', 'strategy', strategy_id, before, after)
        return store.clean(db.execute(STRATEGY_SELECT + ' WHERE s.id=%s', (strategy_id,)).fetchone())


# ---------- versions ----------

@router.get('/strategies/{strategy_id}/versions')
def list_versions(strategy_id: UUID, response: Response, _: Principal = Depends(current_user)):
    _no_store(response)
    with store.transaction('read') as db:
        rows = db.execute('SELECT v.*, (SELECT count(*) FROM cfg_deployments d WHERE d.strategy_version_id=v.id) AS deployment_count '
                          'FROM cfg_strategy_versions v WHERE strategy_id=%s ORDER BY number DESC', (strategy_id,)).fetchall()
    return {'items': [store.clean(r) for r in rows]}


@router.post('/strategies/{strategy_id}/versions', status_code=201)
def create_version(strategy_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    data = _parse(VersionIn, payload)
    parameters = validate_parameters(data.algorithm, data.parameters)
    with store.transaction('create') as db:
        strategy = db.execute('SELECT status FROM cfg_strategies WHERE id=%s FOR UPDATE', (strategy_id,)).fetchone()
        if not strategy:
            raise HTTPException(404, 'Strategy not found.')
        if strategy['status'] != 'ACTIVE':
            raise HTTPException(422, 'This strategy is retired. Reactivate it before adding versions.')
        number = db.execute('SELECT coalesce(max(number), 0) + 1 AS n FROM cfg_strategy_versions WHERE strategy_id=%s',
                            (strategy_id,)).fetchone()['n']
        row = db.execute('INSERT INTO cfg_strategy_versions(id, strategy_id, number, algorithm, parameters, notes, created_by) '
                         'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(), strategy_id, number, data.algorithm, Jsonb(parameters), data.notes, user.username)).fetchone()
        store.audit(db, user.username, 'CREATE', 'strategy version', row['id'], None, row)
        return store.clean(row)


@router.put('/strategy-versions/{version_id}')
def update_version(version_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    version = _parse(Versioned, {'version': payload.pop('version', None)}).version
    data = _parse(VersionIn, payload)
    parameters = validate_parameters(data.algorithm, data.parameters)
    with store.transaction('update') as db:
        before = _locked(db, 'cfg_strategy_versions', version_id, version, 'strategy version')
        if before['state'] != 'DRAFT':
            raise HTTPException(422, 'Only draft versions can be edited. Create a new version to change rules or parameters.')
        after = db.execute('UPDATE cfg_strategy_versions SET algorithm=%s, parameters=%s, notes=%s, version=version+1, updated_at=now() '
                           'WHERE id=%s RETURNING *', (data.algorithm, Jsonb(parameters), data.notes, version_id)).fetchone()
        store.audit(db, user.username, 'UPDATE', 'strategy version', version_id, before, after)
        return store.clean(after)


@router.post('/strategy-versions/{version_id}/approve')
def approve_version(version_id: UUID, payload: dict, response: Response, user: Principal = Depends(_approver)):
    _no_store(response)
    version = _parse(Versioned, payload).version
    with store.transaction('update') as db:
        before = _locked(db, 'cfg_strategy_versions', version_id, version, 'strategy version')
        if before['state'] != 'DRAFT':
            raise HTTPException(422, 'Only draft versions can be approved.')
        validate_parameters(before['algorithm'], before['parameters'])
        after = db.execute("UPDATE cfg_strategy_versions SET state='APPROVED', approved_by=%s, approved_at=now(), version=version+1, "
                           'updated_at=now() WHERE id=%s RETURNING *', (user.username, version_id)).fetchone()
        store.audit(db, user.username, 'APPROVE', 'strategy version', version_id, before, after)
        return store.clean(after)


@router.post('/strategy-versions/{version_id}/retire')
def retire_version(version_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops_or_approver)):
    _no_store(response)
    version = _parse(Versioned, payload).version
    with store.transaction('update') as db:
        before = _locked(db, 'cfg_strategy_versions', version_id, version, 'strategy version')
        if before['state'] == 'RETIRED':
            raise HTTPException(422, 'This version is already retired.')
        if db.execute("SELECT 1 FROM cfg_deployments WHERE strategy_version_id=%s AND status IN ('ACTIVE','PAUSED','PROPOSED')",
                      (version_id,)).fetchone():
            raise HTTPException(422, 'Deployments still use this version. Move them to another version or end them first.')
        after = db.execute("UPDATE cfg_strategy_versions SET state='RETIRED', version=version+1, updated_at=now() WHERE id=%s RETURNING *",
                           (version_id,)).fetchone()
        store.audit(db, user.username, 'RETIRE', 'strategy version', version_id, before, after)
        return store.clean(after)


# ---------- deployments ----------

DEPLOYMENT_SELECT = '''SELECT d.*, s.code AS strategy_code, s.name AS strategy_name, s.status AS strategy_status,
    v.number AS version_number, v.state AS version_state, v.algorithm,
    i.symbol AS instrument_symbol, i.status AS instrument_status,
    a.code AS account_code, a.external_account_id, a.holder_name, a.connection_id AS account_connection_id,
    c.code AS connection_code, c.environment, c.provider_id, p.code AS provider_code,
    dm.broker_symbol AS data_symbol, dm.status AS data_mapping_status, dm.usage AS data_usage, dm.instrument_id AS data_instrument_id,
    dc.code AS data_connection_code, dc.status AS data_connection_status, dc.purpose AS data_connection_purpose,
    dp.code AS data_provider_code,
    tm.broker_symbol AS trading_symbol, tm.status AS trading_mapping_status
    FROM cfg_deployments d
    JOIN cfg_strategies s ON s.id=d.strategy_id
    JOIN cfg_strategy_versions v ON v.id=d.strategy_version_id
    JOIN cfg_instruments i ON i.id=d.instrument_id
    JOIN cfg_accounts a ON a.id=d.account_id
    JOIN cfg_connections c ON c.id=a.connection_id
    JOIN cfg_providers p ON p.id=c.provider_id
    JOIN cfg_symbol_mappings dm ON dm.id=d.data_mapping_id
    JOIN cfg_connections dc ON dc.id=dm.connection_id
    JOIN cfg_providers dp ON dp.id=dc.provider_id
    LEFT JOIN cfg_symbol_mappings tm ON tm.connection_id=a.connection_id AND tm.instrument_id=d.instrument_id
        AND tm.usage IN ('TRADING','BOTH')'''

BLOCKERS = {
    'DEPLOYMENT_NOT_ACTIVE', 'STRATEGY_RETIRED', 'VERSION_NOT_APPROVED', 'INSTRUMENT_INACTIVE',
    'NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION', 'TRADING_SYMBOL_INACTIVE', 'DATA_SOURCE_INACTIVE',
}


def readiness(row: dict, account: dict, controls: dict) -> list[str]:
    """Every reason this deployment cannot send orders now, in order of the trading flow."""
    blockers: list[str] = []
    if row['status'] != 'ACTIVE':
        blockers.append('DEPLOYMENT_NOT_ACTIVE')
    if row['strategy_status'] != 'ACTIVE':
        blockers.append('STRATEGY_RETIRED')
    if row['version_state'] != 'APPROVED':
        blockers.append('VERSION_NOT_APPROVED')
    if row['instrument_status'] != 'ACTIVE':
        blockers.append('INSTRUMENT_INACTIVE')
    if row['data_mapping_status'] != 'ACTIVE' or row['data_connection_status'] != 'ACTIVE':
        blockers.append('DATA_SOURCE_INACTIVE')
    if not row['trading_symbol']:
        blockers.append('NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION')
    elif row['trading_mapping_status'] != 'ACTIVE':
        blockers.append('TRADING_SYMBOL_INACTIVE')
    blockers.extend(b for b in store.account_readiness(account, controls)['trading_blockers'] if b not in blockers)
    return blockers


def _with_readiness(db, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    controls = store.execution_controls(db)
    accounts = {r['id']: r for r in db.execute(store.SELECTS['accounts'] + ' WHERE a.id = ANY(%s)',
                                                ([r['account_id'] for r in rows],)).fetchall()}
    return [{**store.clean(r), 'blockers': readiness(r, accounts[r['account_id']], controls)} for r in rows]


@router.get('/deployments')
def list_deployments(response: Response, strategy_id: UUID | None = None, instrument_id: UUID | None = None,
                     account_id: UUID | None = None, provider_id: UUID | None = None, connection_id: UUID | None = None,
                     status: Literal['PROPOSED', 'ACTIVE', 'PAUSED', 'ENDED'] | None = None,
                     _: Principal = Depends(current_user)):
    """The same matrix from any starting point: provider, account, instrument or strategy."""
    _no_store(response)
    filters = {'d.strategy_id': strategy_id, 'd.instrument_id': instrument_id, 'd.account_id': account_id,
               'c.provider_id': provider_id, 'a.connection_id': connection_id, 'd.status': status}
    clauses = [f'{column}=%s' for column, value in filters.items() if value is not None]
    params = [value for value in filters.values() if value is not None]
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    with store.transaction('read') as db:
        rows = db.execute(DEPLOYMENT_SELECT + where + ' ORDER BY s.code, i.symbol, p.code, a.code LIMIT 2000', params).fetchall()
        return {'items': _with_readiness(db, rows)}


def _deployment(db, deployment_id: UUID) -> dict:
    row = db.execute(DEPLOYMENT_SELECT + ' WHERE d.id=%s', (deployment_id,)).fetchone()
    return _with_readiness(db, [row])[0]


def _check_links(db, data: DeploymentIn) -> None:
    version = db.execute('SELECT strategy_id, state FROM cfg_strategy_versions WHERE id=%s', (data.strategy_version_id,)).fetchone()
    if not version or version['strategy_id'] != data.strategy_id:
        raise HTTPException(422, 'The strategy version does not belong to this strategy.')
    if version['state'] == 'RETIRED':
        raise HTTPException(422, 'This strategy version is retired. Choose a draft or approved version.')
    if not db.execute("SELECT 1 FROM cfg_strategies WHERE id=%s AND status='ACTIVE'", (data.strategy_id,)).fetchone():
        raise HTTPException(422, 'This strategy is retired.')
    account = db.execute('SELECT a.trading_permission, c.purpose FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id '
                         'WHERE a.id=%s', (data.account_id,)).fetchone()
    if not account:
        raise HTTPException(422, 'Account does not exist.')
    if account['purpose'] == 'MARKET_DATA':
        raise HTTPException(422, 'This account is on a market-data-only connection and cannot receive orders.')
    mapping = db.execute('SELECT instrument_id, usage FROM cfg_symbol_mappings WHERE id=%s', (data.data_mapping_id,)).fetchone()
    if not mapping or mapping['instrument_id'] != data.instrument_id:
        raise HTTPException(422, 'The market-data source must be a symbol mapping of the same instrument.')
    if mapping['usage'] == 'TRADING':
        raise HTTPException(422, 'The chosen symbol mapping is for trading only. Choose one used for market data.')


@router.post('/deployments', status_code=201)
def create_deployment(payload: dict, response: Response, user: Principal = Depends(_ops)):
    _no_store(response)
    data = _parse(DeploymentIn, payload)
    with store.transaction('create') as db:
        _check_links(db, data)
        if db.execute('SELECT 1 FROM cfg_deployments WHERE strategy_id=%s AND instrument_id=%s AND account_id=%s',
                      (data.strategy_id, data.instrument_id, data.account_id)).fetchone():
            raise HTTPException(409, 'This strategy is already deployed on this instrument and account.')
        row = db.execute('INSERT INTO cfg_deployments(id, strategy_id, strategy_version_id, instrument_id, account_id, data_mapping_id, '
                         'allocation_weight_bps, note, proposed_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                         (uuid4(), data.strategy_id, data.strategy_version_id, data.instrument_id, data.account_id,
                          data.data_mapping_id, data.allocation_weight_bps, data.note, user.username)).fetchone()
        store.audit(db, user.username, 'PROPOSE', 'deployment', row['id'], None, row)
        return _deployment(db, row['id'])


@router.put('/deployments/{deployment_id}')
def update_deployment(deployment_id: UUID, payload: dict, response: Response, user: Principal = Depends(_ops)):
    """Any change returns the deployment to PROPOSED so it needs activating again."""
    _no_store(response)
    version = _parse(Versioned, {'version': payload.pop('version', None)}).version
    data = _parse(DeploymentIn, payload)
    with store.transaction('update') as db:
        before = _locked(db, 'cfg_deployments', deployment_id, version, 'deployment')
        for field in ('strategy_id', 'instrument_id', 'account_id'):
            if getattr(data, field) != before[field]:
                raise HTTPException(422, f'{field} cannot be changed. End this deployment and create a new one.')
        if before['status'] == 'ENDED':
            raise HTTPException(422, 'This deployment has ended. Create a new one instead.')
        _check_links(db, data)
        after = db.execute("UPDATE cfg_deployments SET strategy_version_id=%s, data_mapping_id=%s, allocation_weight_bps=%s, note=%s, "
                           "status='PROPOSED', proposed_by=%s, activated_by=NULL, activated_at=NULL, version=version+1, updated_at=now() "
                           'WHERE id=%s RETURNING *', (data.strategy_version_id, data.data_mapping_id, data.allocation_weight_bps,
                                                       data.note, user.username, deployment_id)).fetchone()
        store.audit(db, user.username, 'UPDATE', 'deployment', deployment_id, before, after,
                    'Returned to PROPOSED; needs activation' if before['status'] == 'ACTIVE' else '')
        return _deployment(db, deployment_id)


class Transition(_Strict):
    version: int = Field(ge=1)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


@router.post('/deployments/{deployment_id}/{action}')
def transition(deployment_id: UUID, action: Literal['activate', 'pause', 'end'], payload: dict, response: Response,
               user: Principal = Depends(current_user)):
    _no_store(response)
    data = _parse(Transition, payload)
    if action == 'activate' and not user.has('RISK_APPROVER'):
        raise HTTPException(403, 'Activating a deployment needs the Risk approver role.')
    if action != 'activate' and not user.has('RISK_APPROVER', 'TRADING_OPS'):
        raise HTTPException(403, 'This action needs the Trading operations or Risk approver role.')
    allowed = {'activate': ('PROPOSED', 'PAUSED'), 'pause': ('ACTIVE',), 'end': ('PROPOSED', 'ACTIVE', 'PAUSED')}[action]
    target = {'activate': 'ACTIVE', 'pause': 'PAUSED', 'end': 'ENDED'}[action]
    with store.transaction('update') as db:
        before = _locked(db, 'cfg_deployments', deployment_id, data.version, 'deployment')
        if before['status'] not in allowed:
            raise HTTPException(422, f'A {before["status"].lower()} deployment cannot be {target.lower()}.')
        if action == 'activate':
            current = _deployment(db, deployment_id)
            structural = [b for b in current['blockers'] if b in ('STRATEGY_RETIRED', 'VERSION_NOT_APPROVED', 'INSTRUMENT_INACTIVE',
                                                                    'NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION', 'DATA_SOURCE_INACTIVE')]
            if structural:
                raise HTTPException(422, 'Resolve these before activating: ' + ', '.join(b.replace('_', ' ').lower() for b in structural) + '.')
            if current['environment'] == 'LIVE' and before['proposed_by'] == user.username:
                raise HTTPException(422, 'On LIVE accounts a different person must activate the deployment than the one who proposed it.')
        after = db.execute('UPDATE cfg_deployments SET status=%s, version=version+1, updated_at=now()'
                           + (', activated_by=%s, activated_at=now()' if action == 'activate' else '') + ' WHERE id=%s RETURNING *',
                           (target, user.username, deployment_id) if action == 'activate' else (target, deployment_id)).fetchone()
        store.audit(db, user.username, action.upper(), 'deployment', deployment_id, before, after, data.reason)
        return _deployment(db, deployment_id)


@router.delete('/deployments/{deployment_id}', status_code=204)
def delete_deployment(deployment_id: UUID, version: int = Query(ge=1), user: Principal = Depends(_ops)):
    with store.transaction('delete') as db:
        before = _locked(db, 'cfg_deployments', deployment_id, version, 'deployment')
        if before['status'] not in ('PROPOSED', 'ENDED'):
            raise HTTPException(422, 'Pause and end a deployment before deleting it.')
        db.execute('DELETE FROM cfg_deployments WHERE id=%s', (deployment_id,))
        store.audit(db, user.username, 'DELETE', 'deployment', deployment_id, before, None)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})
