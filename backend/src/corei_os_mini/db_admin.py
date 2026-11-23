"""Database editor for administrators: browse every table of COREI's database and add, edit or delete rows; add,
rename, retype, change and drop columns; create and drop tables.

Guard rails:
- One write policy with System › Tables (db_policy.py): the trading ledger (orders, fills, positions, risk,
  reconciliation, strategy runtime), append-only logs and secrets are never written here; configuration tables with
  their own controlled screens follow the policy infra_edit_controlled_tables. A delete is refused when it — or the
  rows it would remove through foreign keys — touches a protected table. COREI's own tables keep their structure.
- Only tables and columns that exist are named; identifiers are always quoted, values always passed as parameters.
- Secrets stay secret: encrypted columns (bytea), password hashes and session token hashes are shown masked and
  cannot be written here (use the credential and user screens). Everything else is editable.
- Every change is one audit-log entry (who, what, before, after, reason).
- Structure changes made here are not in the migration files: the next migration may need to know about them.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import re
from typing import Any, Iterator

from fastapi import HTTPException
import psycopg
from psycopg import sql

from . import config_store as store
from . import db_policy
from .config import get_settings


@contextmanager
def _tx() -> Iterator[psycopg.Connection]:
    """One transaction; the database's own message is shown (an administrator needs the exact reason)."""
    try:
        with store._connect(get_settings().database_url) as db:
            yield db
    except HTTPException:
        raise
    except psycopg.errors.InsufficientPrivilege as exc:
        raise HTTPException(403, f'The database refused: {exc.diag.message_primary}. COREI\'s database login does not own this object.') from None
    except psycopg.OperationalError:
        raise HTTPException(503, 'Database unreachable.') from None
    except psycopg.Error as exc:
        detail = ' '.join(x for x in (exc.diag.message_primary, exc.diag.message_detail, exc.diag.message_hint) if x)
        raise HTTPException(422, f'The database refused: {detail or type(exc).__name__}') from None

SECRET_NAMES = re.compile(r'(password_hash|token_hash|secret|ciphertext|totp|private_key)', re.I)
TYPES = {  # what can be chosen for a new or changed column
    'text': 'text', 'integer': 'integer', 'bigint': 'bigint', 'numeric': 'numeric', 'double precision': 'double precision',
    'boolean': 'boolean', 'date': 'date', 'time': 'time', 'timestamptz': 'timestamp with time zone', 'uuid': 'uuid',
    'jsonb': 'jsonb', 'text[]': 'text[]', 'integer[]': 'integer[]',
}
IDENT = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')


def _schema(db) -> str:
    return db.execute('SELECT current_schema() AS s').fetchone()['s']


def _columns(db, table: str) -> list[dict]:
    rows = db.execute(
        """SELECT c.column_name AS name, c.data_type, c.udt_name, c.is_nullable = 'YES' AS nullable, c.column_default AS "default",
                  c.ordinal_position AS position
           FROM information_schema.columns c WHERE c.table_schema = current_schema() AND c.table_name = %s ORDER BY c.ordinal_position""",
        (table,)).fetchall()
    if not rows:
        raise HTTPException(404, f'No table {table}.')
    pk = {r['column_name'] for r in db.execute(
        """SELECT k.column_name FROM information_schema.table_constraints t
           JOIN information_schema.key_column_usage k ON k.constraint_name = t.constraint_name AND k.table_schema = t.table_schema
           WHERE t.table_schema = current_schema() AND t.table_name = %s AND t.constraint_type = 'PRIMARY KEY'""", (table,)).fetchall()}
    for r in rows:
        r['primary_key'] = r['name'] in pk
        r['secret'] = r['udt_name'] == 'bytea' or bool(SECRET_NAMES.search(r['name']))
        r['cast'] = _cast(r)
    return rows


def _cast(col: dict) -> str:
    """The SQL type to cast a text value to when writing this column."""
    udt = col['udt_name']
    if udt.startswith('_'):
        return f'{udt[1:]}[]'
    return {'timestamptz': 'timestamptz', 'timestamp': 'timestamp', 'int4': 'integer', 'int8': 'bigint', 'int2': 'smallint',
            'float8': 'double precision', 'float4': 'real', 'bool': 'boolean'}.get(udt, udt)


def tables() -> list[dict]:
    with _tx() as db:
        return db.execute(
            """SELECT t.table_name AS name, COALESCE(s.n_live_tup, 0)::bigint AS rows,
                      (SELECT count(*) FROM information_schema.columns c WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name) AS columns,
                      pg_total_relation_size(format('%I.%I', t.table_schema, t.table_name)) AS bytes
               FROM information_schema.tables t
               LEFT JOIN pg_stat_user_tables s ON s.schemaname = t.table_schema AND s.relname = t.table_name
               WHERE t.table_schema = current_schema() AND t.table_type = 'BASE TABLE'
                 AND NOT EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid WHERE c.relname = t.table_name)
               ORDER BY t.table_name""").fetchall()


def _show(value: Any, col: dict) -> Any:
    if col['secret'] and value is not None:
        return f'‹hidden {len(value)} bytes›' if isinstance(value, (bytes, memoryview)) else '‹hidden›'
    if isinstance(value, (bytes, memoryview)):
        return f'‹{len(value)} bytes›'
    return value


def rows(table: str, limit: int, offset: int, search: str, order: str | None, desc: bool) -> dict:
    with _tx() as db:
        cols = _columns(db, table)
        names = [c['name'] for c in cols]
        where, params = sql.SQL(''), []
        if search:
            text_cols = [c['name'] for c in cols if not c['secret']]
            where = sql.SQL(' WHERE ') + sql.SQL(' OR ').join(sql.SQL('{}::text ILIKE %s').format(sql.Identifier(n)) for n in text_cols)
            params = [f'%{search}%'] * len(text_cols)
        order_by = sql.SQL('')
        if order:
            if order not in names:
                raise HTTPException(422, f'No column {order}.')
            order_by = sql.SQL(' ORDER BY {} {} NULLS LAST').format(sql.Identifier(order), sql.SQL('DESC' if desc else 'ASC'))
        total = db.execute(sql.SQL('SELECT count(*) AS n FROM {}{}').format(sql.Identifier(table), where), params).fetchone()['n']
        found = db.execute(sql.SQL('SELECT * FROM {}{}{} LIMIT %s OFFSET %s').format(sql.Identifier(table), where, order_by),
                           [*params, limit, offset]).fetchall()
        bycol = {c['name']: c for c in cols}
        return {'table': table, 'columns': cols, 'total': total,
                'rows': [{k: _show(v, bycol[k]) for k, v in r.items()} for r in found]}


def _guard_rows(db, table: str, cols: list[dict]) -> None:
    db_policy.check_rows(table, any(c.get('primary_key') for c in cols), bool(store.policy(db, 'infra_edit_controlled_tables', False)))


def _writable(cols: list[dict], values: dict) -> dict:
    bycol = {c['name']: c for c in cols}
    for name in values:
        if name not in bycol:
            raise HTTPException(422, f'No column {name}.')
        if bycol[name]['secret']:
            raise HTTPException(422, f'{name} holds a secret: change it from its own screen (credentials, users).')
    return bycol


def _value(v: Any, col: dict) -> sql.Composable:
    if v is None:
        return sql.SQL('NULL')
    return sql.SQL('{}::{}').format(sql.Literal(v if isinstance(v, str) else json.dumps(v) if col['udt_name'] == 'jsonb' else str(v)),
                                   sql.SQL(col['cast']))


def _key_where(cols: list[dict], key: dict) -> sql.Composable:
    pk = [c for c in cols if c['primary_key']]
    if not pk:
        raise HTTPException(422, 'This table has no primary key: rows cannot be addressed one by one.')
    if set(key) != {c['name'] for c in pk}:
        raise HTTPException(422, f"Give the primary key: {', '.join(c['name'] for c in pk)}.")
    return sql.SQL(' AND ').join(sql.SQL('{} = {}').format(sql.Identifier(c['name']), _value(key[c['name']], c)) for c in pk)


def insert_row(actor: str, table: str, values: dict, reason: str) -> dict:
    with _tx() as db:
        cols = _columns(db, table)
        _guard_rows(db, table, cols)
        bycol = _writable(cols, values)
        if not values:
            row = db.execute(sql.SQL('INSERT INTO {} DEFAULT VALUES RETURNING *').format(sql.Identifier(table))).fetchone()
        else:
            row = db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING *').format(
                sql.Identifier(table), sql.SQL(', ').join(sql.Identifier(k) for k in values),
                sql.SQL(', ').join(_value(v, bycol[k]) for k, v in values.items()))).fetchone()
        store.audit(db, actor, 'DB_INSERT', f'db:{table}', table, None, _plain(row, cols), reason)
        return {k: _show(v, bycol.get(k) or {'secret': False}) for k, v in row.items()}


def update_row(actor: str, table: str, key: dict, values: dict, reason: str) -> dict:
    with _tx() as db:
        cols = _columns(db, table)
        _guard_rows(db, table, cols)
        bycol = _writable(cols, values)
        if not values:
            raise HTTPException(422, 'Nothing to change.')
        where = _key_where(cols, key)
        before = db.execute(sql.SQL('SELECT * FROM {} WHERE {} FOR UPDATE').format(sql.Identifier(table), where)).fetchone()
        if not before:
            raise HTTPException(404, 'That row no longer exists.')
        row = db.execute(sql.SQL('UPDATE {} SET {} WHERE {} RETURNING *').format(
            sql.Identifier(table), sql.SQL(', ').join(sql.SQL('{} = {}').format(sql.Identifier(k), _value(v, bycol[k])) for k, v in values.items()),
            where)).fetchone()
        store.audit(db, actor, 'DB_UPDATE', f'db:{table}', str(key)[:200], _plain(before, cols), _plain(row, cols), reason)
        return {k: _show(v, bycol[k]) for k, v in row.items()}


def _references(db, table: str) -> list[dict]:
    """Foreign keys in other tables (or this one) that point at `table`: child table, its columns, the parent columns."""
    return db.execute(
        """SELECT c.conname AS name, cl.relname AS child, c.confdeltype AS on_delete,
                  array_agg(a.attname::text ORDER BY k.n) AS child_cols, array_agg(pa.attname::text ORDER BY k.n) AS parent_cols
           FROM pg_constraint c
           JOIN pg_class cl ON cl.oid = c.conrelid JOIN pg_namespace ns ON ns.oid = cl.relnamespace
           JOIN pg_class p ON p.oid = c.confrelid
           CROSS JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, n)
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.ck
           JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
           WHERE c.contype = 'f' AND c.conparentid = 0 AND p.relname = %s AND ns.nspname = current_schema()
           GROUP BY c.conname, cl.relname, c.confdeltype ORDER BY cl.relname""", (table,)).fetchall()


def _linked(db, table: str, where: sql.Composable, depth: int = 0, path: tuple = ()) -> list[dict]:
    """Every row in other tables that points (directly or through others) at the rows `table WHERE where`, deepest
    first — the order they must be deleted in. Foreign keys that set the value empty on delete are listed as such."""
    if depth > 8:
        return []
    steps: list[dict] = []
    for fk in _references(db, table):
        if (fk['child'], fk['name']) in path:
            continue
        cond = sql.SQL('({}) IN (SELECT {} FROM {} WHERE {})').format(
            sql.SQL(', ').join(sql.Identifier(c) for c in fk['child_cols']), sql.SQL(', ').join(sql.Identifier(c) for c in fk['parent_cols']),
            sql.Identifier(table), where)
        n = db.execute(sql.SQL('SELECT count(*) AS n FROM {} WHERE {}').format(sql.Identifier(fk['child']), cond)).fetchone()['n']
        if not n:
            continue
        action = 'set empty' if fk['on_delete'] in ('n', 'd') else 'delete'
        if action == 'delete':
            steps += _linked(db, fk['child'], cond, depth + 1, path + ((fk['child'], fk['name']),))
        steps.append({'table': fk['child'], 'rows': n, 'action': action, 'via': fk['name'], 'cond': cond})
    return steps


def _summary(steps: list[dict]) -> list[dict]:
    out: dict[tuple, int] = {}
    for st in steps:
        out[(st['table'], st['action'])] = out.get((st['table'], st['action']), 0) + st['rows']
    return [{'table': t, 'action': a, 'rows': n} for (t, a), n in out.items()]


def linked_rows(table: str, key: dict) -> dict:
    """What deleting this row would also remove (or set empty) in other tables."""
    with _tx() as db:
        cols = _columns(db, table)
        return {'linked': _summary(_linked(db, table, _key_where(cols, key)))}


def delete_row(actor: str, table: str, key: dict, reason: str, with_linked: bool = False) -> dict:
    """Delete one row. with_linked: first delete every row in other tables that points at it (deepest first)."""
    with _tx() as db:
        cols = _columns(db, table)
        _guard_rows(db, table, cols)
        where = _key_where(cols, key)
        removed: list[dict] = []
        touched = [st for st in _linked(db, table, where) if st['rows']]
        blocked = sorted({st['table'] for st in touched if db_policy.is_ledger(st['table']) or st['table'] in db_policy.READ_ONLY_TABLES})
        if blocked:
            raise HTTPException(403, f"Deleting this row would also change protected records in {', '.join(blocked)}; "
                                     'remove it through its own screen instead.')
        if with_linked:
            steps = touched
            for st in steps:
                if st['action'] == 'delete':
                    n = db.execute(sql.SQL('DELETE FROM {} WHERE {}').format(sql.Identifier(st['table']), st['cond'])).rowcount
                    if n:
                        removed.append({'table': st['table'], 'action': 'delete', 'rows': n})
            removed = _summary([{**r, 'cond': None} for r in removed])
        row = db.execute(sql.SQL('DELETE FROM {} WHERE {} RETURNING *').format(sql.Identifier(table), where)).fetchone()
        if not row:
            raise HTTPException(404, 'That row no longer exists.')
        store.audit(db, actor, 'DB_DELETE', f'db:{table}', str(key)[:200], {**(_plain(row, cols) or {}), 'linked_removed': removed}, None, reason)
        return {'removed': removed}


def _plain(row: dict | None, cols: list[dict]) -> dict | None:
    if row is None:
        return None
    secret = {c['name'] for c in cols if c['secret']}
    return {k: ('‹hidden›' if k in secret and v is not None else v if not isinstance(v, (bytes, memoryview)) else f'‹{len(v)} bytes›') for k, v in row.items()}


def _type(name: str) -> sql.Composable:
    if name not in TYPES:
        raise HTTPException(422, f"Type must be one of: {', '.join(TYPES)}.")
    return sql.SQL(TYPES[name])


def _ident(name: str) -> str:
    if not IDENT.match(name):
        raise HTTPException(422, 'Names: lower-case letters, digits and _, starting with a letter or _ (max 63).')
    return name


def add_column(actor: str, table: str, name: str, type_: str, nullable: bool, default: str | None, reason: str) -> dict:
    with _tx() as db:
        db_policy.check_structure(table)
        _columns(db, table)
        q = sql.SQL('ALTER TABLE {} ADD COLUMN {} {}').format(sql.Identifier(table), sql.Identifier(_ident(name)), _type(type_))
        if default not in (None, ''):
            q += sql.SQL(' DEFAULT {}::{}').format(sql.Literal(default), _type(type_))
        if not nullable:
            q += sql.SQL(' NOT NULL')
        db.execute(q)
        store.audit(db, actor, 'DB_ADD_COLUMN', f'db:{table}', table, None, {'column': name, 'type': type_, 'nullable': nullable, 'default': default}, reason)
        return {'columns': _columns(db, table)}


def change_column(actor: str, table: str, column: str, rename_to: str | None, type_: str | None, nullable: bool | None,
                  default: str | None, drop_default: bool, reason: str) -> dict:
    with _tx() as db:
        db_policy.check_structure(table)
        cols = {c['name']: c for c in _columns(db, table)}
        if column not in cols:
            raise HTTPException(404, f'No column {column}.')
        before = dict(cols[column])
        t, c = sql.Identifier(table), sql.Identifier(column)
        if type_:
            db.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} TYPE {} USING {}::{}').format(t, c, _type(type_), c, _type(type_)))
        if nullable is not None:
            db.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} {} NOT NULL').format(t, c, sql.SQL('DROP' if nullable else 'SET')))
        if drop_default:
            db.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} DROP DEFAULT').format(t, c))
        elif default not in (None, ''):
            db.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET DEFAULT {}').format(t, c, sql.Literal(default)))
        if rename_to and rename_to != column:
            db.execute(sql.SQL('ALTER TABLE {} RENAME COLUMN {} TO {}').format(t, c, sql.Identifier(_ident(rename_to))))
        store.audit(db, actor, 'DB_CHANGE_COLUMN', f'db:{table}', f'{table}.{column}', before,
                    {'rename_to': rename_to, 'type': type_, 'nullable': nullable, 'default': default, 'drop_default': drop_default}, reason)
        return {'columns': _columns(db, table)}


def drop_column(actor: str, table: str, column: str, confirm: str, reason: str) -> dict:
    with _tx() as db:
        db_policy.check_structure(table)
        cols = {c['name'] for c in _columns(db, table)}
        if column not in cols:
            raise HTTPException(404, f'No column {column}.')
        if confirm != f'{table}.{column}':
            raise HTTPException(422, f'Type {table}.{column} exactly to drop the column.')
        db.execute(sql.SQL('ALTER TABLE {} DROP COLUMN {} CASCADE').format(sql.Identifier(table), sql.Identifier(column)))
        store.audit(db, actor, 'DB_DROP_COLUMN', f'db:{table}', f'{table}.{column}', {'column': column}, None, reason)
        return {'columns': _columns(db, table)}


def create_table(actor: str, name: str, reason: str) -> dict:
    with _tx() as db:
        if db_policy.is_ledger(name) or name in db_policy.READ_ONLY_TABLES:
            raise HTTPException(403, f'{name} is a name reserved for COREI\'s own record.')
        db.execute(sql.SQL('CREATE TABLE {} (id bigserial PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now())').format(sql.Identifier(_ident(name))))
        store.audit(db, actor, 'DB_CREATE_TABLE', f'db:{name}', name, None, {'table': name}, reason)
        return {'columns': _columns(db, name)}


def drop_table(actor: str, table: str, confirm: str, reason: str) -> None:
    with _tx() as db:
        db_policy.check_structure(table)
        _columns(db, table)
        if confirm != table:
            raise HTTPException(422, f'Type {table} exactly to drop the table.')
        n = db.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']
        db.execute(sql.SQL('DROP TABLE {} CASCADE').format(sql.Identifier(table)))
        store.audit(db, actor, 'DB_DROP_TABLE', f'db:{table}', table, {'rows': n}, None, reason)
