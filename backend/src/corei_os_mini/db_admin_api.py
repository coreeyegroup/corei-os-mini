"""Database editor routes, /api/admin/db. Administrator role; every change needs the password re-entered recently
(428 → the screen asks and retries) and a reason, and is written to the audit log."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from . import db_admin
from .operator_auth import Principal, require_reauth, require_role

router = APIRouter(prefix='/api/admin/db', tags=['database'])
_admin = require_role('ADMIN')
Reason = Field(min_length=5, max_length=500)


class _In(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RowIn(_In):
    values: dict[str, Any] = Field(default_factory=dict)
    reason: str = Reason


class RowChangeIn(_In):
    key: dict[str, Any]
    values: dict[str, Any]
    reason: str = Reason


class RowDeleteIn(_In):
    key: dict[str, Any]
    reason: str = Reason


class ColumnIn(_In):
    name: str = Field(min_length=1, max_length=63)
    type: str
    nullable: bool = True
    default: str | None = None
    reason: str = Reason


class ColumnChangeIn(_In):
    rename_to: str | None = None
    type: str | None = None
    nullable: bool | None = None
    default: str | None = None
    drop_default: bool = False
    reason: str = Reason


class DropIn(_In):
    confirm: str = Field(min_length=1, max_length=130)
    reason: str = Reason


class TableIn(_In):
    name: str = Field(min_length=1, max_length=63)
    reason: str = Reason


def _no_store(response: Response) -> None:
    response.headers['Cache-Control'] = 'no-store'


@router.get('/tables')
def list_tables(response: Response, _: Principal = Depends(_admin)):
    _no_store(response)
    return {'items': db_admin.tables(), 'types': list(db_admin.TYPES)}


@router.get('/tables/{table}/rows')
def list_rows(table: str, response: Response, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
              q: str = Query('', max_length=200), order: str | None = None, desc: bool = False, _: Principal = Depends(_admin)):
    _no_store(response)
    return db_admin.rows(table, limit, offset, q, order, desc)


@router.post('/tables/{table}/rows', status_code=201)
def insert_row(table: str, data: RowIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    return db_admin.insert_row(user.username, table, data.values, data.reason)


@router.patch('/tables/{table}/rows')
def update_row(table: str, data: RowChangeIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    return db_admin.update_row(user.username, table, data.key, data.values, data.reason)


@router.post('/tables/{table}/rows/delete', status_code=204)
def delete_row(table: str, data: RowDeleteIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    db_admin.delete_row(user.username, table, data.key, data.reason)


@router.post('/tables/{table}/columns')
def add_column(table: str, data: ColumnIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    return db_admin.add_column(user.username, table, data.name, data.type, data.nullable, data.default, data.reason)


@router.patch('/tables/{table}/columns/{column}')
def change_column(table: str, column: str, data: ColumnChangeIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    return db_admin.change_column(user.username, table, column, data.rename_to, data.type, data.nullable, data.default, data.drop_default, data.reason)


@router.post('/tables/{table}/columns/{column}/drop')
def drop_column(table: str, column: str, data: DropIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    return db_admin.drop_column(user.username, table, column, data.confirm, data.reason)


@router.post('/tables', status_code=201)
def create_table(data: TableIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    return db_admin.create_table(user.username, data.name, data.reason)


@router.post('/tables/{table}/drop', status_code=204)
def drop_table(table: str, data: DropIn, user: Principal = Depends(_admin), __: Principal = Depends(require_reauth)):
    db_admin.drop_table(user.username, table, data.confirm, data.reason)
