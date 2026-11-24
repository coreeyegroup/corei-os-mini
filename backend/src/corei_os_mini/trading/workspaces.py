"""Saved operator layouts and a scoped read model for the trading lifecycle."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator


PREF_KEY = 'trading-workspaces.v1'
Mode = Literal['PAPER', 'DEMO', 'LIVE']
Timeframe = Literal['S1', 'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1']
PanelId = Literal['market', 'intelligence', 'signals', 'strategy', 'risk', 'execution', 'portfolio', 'posttrade']


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Panel(WorkspaceModel):
    id: PanelId
    visible: bool = True
    wide: bool = False


class Workspace(WorkspaceModel):
    number: int = Field(ge=1)
    name: str = Field(max_length=80)
    mode: Mode
    timeframe: Timeframe
    account: UUID | None = None
    instrument: UUID | None = None
    deployment: UUID | None = None
    source: UUID | None = None
    symbol: str = Field(default='', max_length=200)
    panels: Annotated[list[Panel], Field(min_length=8, max_length=8)]


class Layouts(WorkspaceModel):
    revision: int = Field(ge=0)
    next_number: int = Field(ge=1)
    active: int = Field(ge=1)
    items: Annotated[list[Workspace], Field(min_length=1)]

    @model_validator(mode='after')
    def valid_layouts(self):
        numbers = {w.number for w in self.items}
        if self.active not in numbers or max(numbers) >= self.next_number or len(numbers) != len(self.items):
            raise ValueError('Invalid workspace numbering.')
        if any(len({p.id for p in w.panels}) != 8 for w in self.items):
            raise ValueError('Each stage must appear once in the panel settings.')
        return self


def layouts(db, username):
    row = db.execute('SELECT value FROM cfg_user_prefs WHERE username=%s AND key=%s', (username, PREF_KEY)).fetchone()
    return row['value'] if row else None


def save_layouts(db, username, data: Layouts):
    value = {**data.model_dump(mode='json'), 'revision': data.revision + 1}
    row = db.execute("""INSERT INTO cfg_user_prefs(username,key,value)
                        SELECT %s,%s,%s WHERE %s=0 OR EXISTS (
                            SELECT 1 FROM cfg_user_prefs WHERE username=%s AND key=%s)
                        ON CONFLICT(username,key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
                        WHERE (cfg_user_prefs.value->>'revision')::bigint=%s RETURNING value""",
                     (username, PREF_KEY, Jsonb(value), data.revision, username, PREF_KEY, data.revision)).fetchone()
    if not row:
        raise HTTPException(409, 'This workspace was updated elsewhere. Reload your saved layouts before editing again.')
    return row['value']


def catalogue(db):
    return {
        'accounts': db.execute("""SELECT a.id,a.code,c.environment FROM cfg_accounts a
                                   JOIN cfg_connections c ON c.id=a.connection_id ORDER BY a.code""").fetchall(),
        'instruments': db.execute('SELECT id,symbol,name FROM cfg_instruments ORDER BY symbol').fetchall(),
    }


def snapshot(db, mode, timeframe, account=None, instrument=None, deployment=None, source=None, symbol=''):
    result = {'sources': [], 'deployments': [], 'quote': None, 'bar': None, 'intelligence': None}
    if instrument:
        result['sources'] = db.execute("""SELECT DISTINCT a.id AS account_id,a.code,p.broker_symbol
            FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id
            WHERE p.instrument_id=%s AND p.status <> 'ARCHIVED' ORDER BY a.code,p.broker_symbol""", (instrument,)).fetchall()
    params = (source, symbol, timeframe)
    if any(str(s['account_id']) == str(source) and s['broker_symbol'] == symbol for s in result['sources']):
        result['quote'] = db.execute(
            'SELECT bid, ask, received_at FROM md_latest WHERE account_id=%s AND broker_symbol=%s', params[:2]).fetchone()
        result['bar'] = db.execute("""SELECT bar_time,open,high,low,close FROM md_bars
            WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s ORDER BY bar_time DESC LIMIT 1""", params).fetchone()
        result['intelligence'] = db.execute("""SELECT regime,volatility,bar_time,confidence,reasons FROM intel_snapshot
            WHERE account_id=%s AND broker_symbol=%s AND timeframe=%s""", params).fetchone()
    if not account:
        return result
    book = db.execute("""SELECT a.code,c.environment FROM cfg_accounts a JOIN cfg_connections c ON c.id=a.connection_id
                         WHERE a.id=%s""", (account,)).fetchone()
    if not book or (mode != 'PAPER' and book['environment'] != mode):
        return {**result, 'scope_error': 'Choose an execution account for this mode.'}

    trading_mode = 'PAPER' if mode == 'PAPER' else 'BROKER'
    position_clauses = ['x.account_id=%s', 'x.mode=%s']
    args = [account, trading_mode]
    if instrument:
        position_clauses.append('x.instrument_id=%s')
        args.append(instrument)
    where = ' AND '.join(position_clauses)
    result['deployments'] = db.execute(f"""SELECT x.id,x.status,s.code,v.number AS version,x.instrument_id,i.symbol,
        r.last_run_at,r.last_error,r.timeframe,r.source_account,r.source_symbol
        FROM cfg_deployments x JOIN cfg_strategies s ON s.id=x.strategy_id
        JOIN cfg_strategy_versions v ON v.id=x.strategy_version_id JOIN cfg_instruments i ON i.id=x.instrument_id
        LEFT JOIN tr_runtime_state r ON r.deployment_id=x.id
        WHERE {where} ORDER BY s.code,i.symbol""", args).fetchall()
    if deployment and not any(str(d['id']) == str(deployment) for d in result['deployments']):
        return {**result, 'scope_error': 'The saved deployment is outside this scope. Choose a deployment again.'}
    if deployment:
        where += ' AND x.deployment_id=%s'
        args.append(deployment)
    result['positions'] = db.execute(f"""SELECT x.id,i.symbol,x.quantity,x.avg_price,x.realised_pnl,x.currency
        FROM pf_positions x JOIN cfg_instruments i ON i.id=x.instrument_id
        WHERE {where} AND x.quantity<>0 ORDER BY i.symbol,x.id LIMIT 51""", args).fetchall()
    result['orders'] = db.execute(f"""SELECT x.id,i.symbol,x.state,x.side,x.quantity,x.filled_qty,x.created_at
        FROM oms_orders x JOIN cfg_instruments i ON i.id=x.instrument_id
        WHERE {where} ORDER BY x.created_at DESC,x.id DESC LIMIT 26""", args).fetchall()

    event_clauses = ['x.account_id=%s', 'COALESCE(o.mode,p.mode,d.mode)=%s', 's.timeframe=%s']
    event_args = [account, trading_mode, timeframe]
    if instrument:
        event_clauses.append('x.instrument_id=%s')
        event_args.append(instrument)
    if deployment:
        event_clauses.append('x.deployment_id=%s')
        event_args.append(deployment)
    event_where = ' AND '.join(event_clauses)
    result['decisions'] = db.execute(f"""SELECT x.id,x.outcome,x.target,x.reasons,x.created_at,x.order_id,x.parent_id,
        s.side AS signal_side,s.strength,s.bar_time,s.explanation,s.id AS signal_id,i.symbol,
        st.code AS strategy_code,sv.number AS strategy_version
        FROM tr_intents x JOIN cfg_deployments d ON d.id=x.deployment_id JOIN tr_signals s ON s.id=x.signal_id
        JOIN cfg_instruments i ON i.id=x.instrument_id LEFT JOIN cfg_strategies st ON st.id=s.strategy_id
        LEFT JOIN oms_orders o ON o.id=x.order_id LEFT JOIN ex_parent_orders p ON p.id=x.parent_id
        LEFT JOIN cfg_strategy_versions sv ON sv.id=s.strategy_version_id
        WHERE {event_where} ORDER BY x.created_at DESC,x.id DESC LIMIT 25""", event_args).fetchall()
    result['signal'] = db.execute(f"""SELECT s.id,s.side,s.state,s.strength,s.bar_time,s.explanation,i.symbol
        FROM tr_signals s JOIN cfg_deployments d ON d.id=s.deployment_id JOIN cfg_instruments i ON i.id=s.instrument_id
        WHERE {event_where.replace('x.', 's.').replace('COALESCE(o.mode,p.mode,d.mode)', 'd.mode')}
        ORDER BY s.created_at DESC,s.id DESC LIMIT 1""", event_args).fetchone()
    result['reconciliation'] = db.execute("""SELECT at,kind,status,detail FROM pt_recon_runs
        WHERE account_id=%s AND kind=%s ORDER BY at DESC,id DESC LIMIT 1""",
        (account, 'INTERNAL' if mode == 'PAPER' else 'BROKER')).fetchone()
    return result


def decision_orders(db, decision, account, mode):
    row = db.execute("""SELECT t.order_id,t.parent_id,p.algo,p.state AS parent_state,p.filled_qty AS parent_filled_qty
        FROM tr_intents t JOIN cfg_deployments d ON d.id=t.deployment_id JOIN cfg_accounts a ON a.id=t.account_id
        JOIN cfg_connections c ON c.id=a.connection_id LEFT JOIN oms_orders o ON o.id=t.order_id
        LEFT JOIN ex_parent_orders p ON p.id=t.parent_id WHERE t.id=%s AND t.account_id=%s
        AND (CASE WHEN COALESCE(o.mode,p.mode,d.mode)='PAPER' THEN 'PAPER' ELSE c.environment END)=%s""",
        (decision, account, mode)).fetchone()
    if not row:
        raise HTTPException(404, 'Decision is unavailable for this account and mode.')
    orders = db.execute("""SELECT o.id,o.client_order_id,o.state,o.side,o.quantity,o.filled_qty,
        (SELECT row_to_json(r) FROM (SELECT approved,approved_qty::text AS approved_qty,reasons FROM risk_decisions
            WHERE order_id=o.id ORDER BY at DESC LIMIT 1) r) AS risk,
        (SELECT json_agg(f ORDER BY f.at,f.id) FROM (SELECT id,at,quantity::text AS quantity,price::text AS price,broker_deal_id FROM oms_fills
            WHERE order_id=o.id ORDER BY at,id LIMIT 51) f) AS fills
        FROM oms_orders o WHERE o.account_id=%s AND o.mode=%s AND (o.id=%s OR o.parent_id=%s)
        ORDER BY o.created_at,o.id LIMIT 51""",
        (account, 'PAPER' if mode == 'PAPER' else 'BROKER', row['order_id'], row['parent_id'])).fetchall()
    return {'items': orders[:50], 'more': len(orders) > 50, 'parent': row if row['parent_id'] else None}
