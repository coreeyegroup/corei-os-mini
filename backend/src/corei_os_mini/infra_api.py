"""HTTP routes for the Infrastructure console (/api/infra). Health for every operator;
the database view, SQL console and table editor for administrators. Every change needs
a re-entered password and a reason (see infra.py for the table rules)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from . import infra
from .operator_auth import Principal, require_operator, require_reauth, require_role

router = APIRouter(prefix='/api/infra', tags=['infrastructure'])
_admin = require_role('ADMIN')
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
TableName = Annotated[str, StringConstraints(pattern=r'^[a-z_][a-z0-9_]{0,62}$')]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RowUpdate(_Strict):
    key: dict[str, str | int | None]
    changes: dict[str, str | None] = Field(min_length=1, max_length=200)
    original: dict[str, str | None] = {}
    reason: Reason


class RowInsert(_Strict):
    values: dict[str, str | None] = Field(max_length=200)
    reason: Reason


class RowDelete(_Strict):
    key: dict[str, str | int | None]
    reason: Reason


class QueryIn(_Strict):
    sql: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    limit: int = Field(default=1000, ge=1, le=5000)


class CancelIn(_Strict):
    reason: Reason


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


def _parse(model, payload):
    from .config_api import parse
    return parse(model, payload)


@router.get('/health')
async def health(response: Response, _: str = Depends(require_operator)):
    _no_store(response)
    return await run_in_threadpool(infra.health)


@router.get('/database')
def database(response: Response, _: Principal = Depends(_admin)):
    _no_store(response)
    return infra.database()


@router.get('/tables/{table}')
def read_table(table: TableName, request: Request, response: Response, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
               sort: str | None = None, desc: bool = False, q: str = Query('', max_length=200), _: Principal = Depends(_admin)):
    """Filters are passed as f.<column>=<text> (contains, case-insensitive; '(empty)' for null)."""
    _no_store(response)
    filters = {k[2:]: v for k, v in request.query_params.items() if k.startswith('f.')}
    return infra.read_table(table, offset, limit, sort, desc, filters, q)


@router.patch('/tables/{table}/rows')
def update_row(table: TableName, payload: dict, response: Response, user: Principal = Depends(_admin), _: Principal = Depends(require_reauth)):
    _no_store(response)
    data = _parse(RowUpdate, payload)
    return infra.update_row(user.username, table, data.key, data.changes, data.original, data.reason)


@router.post('/tables/{table}/rows', status_code=201)
def insert_row(table: TableName, payload: dict, response: Response, user: Principal = Depends(_admin), _: Principal = Depends(require_reauth)):
    _no_store(response)
    data = _parse(RowInsert, payload)
    return infra.insert_row(user.username, table, data.values, data.reason)


@router.post('/tables/{table}/rows/delete', status_code=204)
def delete_row(table: TableName, payload: dict, user: Principal = Depends(_admin), _: Principal = Depends(require_reauth)):
    data = _parse(RowDelete, payload)
    infra.delete_row(user.username, table, data.key, data.reason)
    return Response(status_code=204)


@router.get('/edits')
def edits(response: Response, table: str | None = Query(None, max_length=63), _: Principal = Depends(_admin)):
    _no_store(response)
    return {'items': infra.edits(table)}


@router.post('/query')
async def query(payload: dict, response: Response, _: Principal = Depends(_admin)):
    _no_store(response)
    data = _parse(QueryIn, payload)
    return await run_in_threadpool(infra.query, data.sql, data.limit)


@router.post('/database/sessions/{pid}/cancel')
def cancel(pid: int, payload: dict, user: Principal = Depends(_admin), _: Principal = Depends(require_reauth)):
    data = _parse(CancelIn, payload)
    return infra.cancel_session(user.username, pid, data.reason)


class HistoryQueryIn(_Strict):
    sql: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    limit: int = Field(default=1000, ge=1, le=5000)


@router.get('/history/files')
def history_files(response: Response, _: Principal = Depends(_admin)):
    _no_store(response)
    return {'items': infra.history_files()}


@router.post('/history/query')
async def history_query(payload: dict, response: Response, _: Principal = Depends(_admin)):
    _no_store(response)
    data = _parse(HistoryQueryIn, payload)
    return await run_in_threadpool(infra.history_query, data.sql, data.limit)
