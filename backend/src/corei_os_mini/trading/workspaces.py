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
        result['sources'] = db.execute("""SELECT a.id AS account_id,a.code,p.broker_symbol,min(p.stale_seconds) AS stale_seconds
            FROM md_collection_plans p JOIN cfg_accounts a ON a.id=p.account_id
            WHERE p.instrument_id=%s AND p.status <> 'ARCHIVED' GROUP BY a.id,a.code,p.broker_symbol ORDER BY a.code,p.broker_symbol""", (instrument,)).fetchall()
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
        r.last_run_at,r.last_error,r.timeframe,r.source_account,r.source_symbol,r.last_bar_time,r.last_evaluation
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
        s.side AS signal_side,s.strength,s.bar_time,s.explanation,s.state AS signal_state,s.id AS signal_id,i.symbol,
        x.deployment_id,x.handoff,x.expires_at,x.quantity,x.sizing_note,
        st.code AS strategy_code,sv.number AS strategy_version
        FROM tr_intents x JOIN cfg_deployments d ON d.id=x.deployment_id JOIN tr_signals s ON s.id=x.signal_id
        JOIN cfg_instruments i ON i.id=x.instrument_id LEFT JOIN cfg_strategies st ON st.id=s.strategy_id
        LEFT JOIN oms_orders o ON o.id=x.order_id LEFT JOIN ex_parent_orders p ON p.id=x.parent_id
        LEFT JOIN cfg_strategy_versions sv ON sv.id=s.strategy_version_id
        WHERE {event_where} ORDER BY x.created_at DESC,x.id DESC LIMIT 25""", event_args).fetchall()
    if deployment:
        evaluation = next(d for d in result['deployments'] if str(d['id']) == str(deployment))['last_evaluation']
        if evaluation and evaluation['signal_id']:
            result['signal'] = db.execute("""SELECT s.id,s.side,s.state,s.strength,s.bar_time,s.explanation,s.reasons,i.symbol
                FROM tr_signals s JOIN cfg_instruments i ON i.id=s.instrument_id
                WHERE s.id=%s AND s.deployment_id=%s AND s.account_id=%s""",
                (evaluation['signal_id'], deployment, account)).fetchone()
            result['evaluation_decision'] = db.execute("""SELECT id FROM tr_intents
                WHERE signal_id=%s AND deployment_id=%s AND account_id=%s ORDER BY created_at DESC LIMIT 1""",
                (evaluation['signal_id'], deployment, account)).fetchone()
    result['reconciliation'] = db.execute("""SELECT at,kind,status,detail FROM pt_recon_runs
        WHERE account_id=%s AND kind=%s ORDER BY at DESC,id DESC LIMIT 1""",
        (account, 'INTERNAL' if mode == 'PAPER' else 'BROKER')).fetchone()
    return result


def decision_orders(db, decision, account, mode):
    row = db.execute("""SELECT t.id,t.outcome,t.target,t.reasons,t.created_at,t.deployment_id,t.handoff,t.expires_at,t.quantity,t.sizing_note,
        s.id AS signal_id,s.side AS signal_side,s.state AS signal_state,s.strength,s.bar_time,s.timeframe,s.features->'_workspace_bar' AS evaluated_bar,s.explanation,
        i.symbol, st.code AS strategy_code, v.number AS strategy_version,
        t.order_id,t.parent_id,p.algo,p.state AS parent_state,p.filled_qty AS parent_filled_qty
        FROM tr_intents t JOIN cfg_deployments d ON d.id=t.deployment_id JOIN cfg_accounts a ON a.id=t.account_id
        JOIN cfg_connections c ON c.id=a.connection_id
        JOIN tr_signals s ON s.id=t.signal_id JOIN cfg_instruments i ON i.id=t.instrument_id
        LEFT JOIN cfg_strategies st ON st.id=s.strategy_id LEFT JOIN cfg_strategy_versions v ON v.id=s.strategy_version_id
        LEFT JOIN oms_orders o ON o.id=t.order_id
        LEFT JOIN ex_parent_orders p ON p.id=t.parent_id WHERE t.id=%s AND t.account_id=%s
        AND (CASE WHEN COALESCE(o.mode,p.mode,d.mode)='PAPER' THEN 'PAPER' ELSE c.environment END)=%s""",
        (decision, account, mode)).fetchone()
    if not row:
        raise HTTPException(404, 'Decision is unavailable for this account and mode.')
    orders = db.execute("""SELECT o.id,o.client_order_id,o.state,o.side,o.quantity,o.filled_qty,
        (SELECT row_to_json(pos) FROM (SELECT b.id,b.quantity::text AS quantity,b.updated_at FROM pf_positions b WHERE b.account_id=o.account_id AND b.instrument_id=o.instrument_id
            AND b.deployment_id IS NOT DISTINCT FROM o.deployment_id AND b.mode=o.mode) pos) AS position,
        (SELECT row_to_json(r) FROM (SELECT approved,approved_qty::text AS approved_qty,reasons FROM risk_decisions
            WHERE order_id=o.id ORDER BY at DESC LIMIT 1) r) AS risk,
        (SELECT json_agg(f ORDER BY f.at,f.id) FROM (
            SELECT f.id,f.at,f.quantity::text AS quantity,f.price::text AS price,f.broker_deal_id,
                CASE WHEN f.mode='PAPER' THEN 'INTERNAL_FILL'
                     WHEN f.broker_deal_id IS NULL THEN 'NO_BROKER_TICKET'
                     WHEN b.id IS NOT NULL THEN 'OPEN_MISSING_FILL_BREAK'
                     WHEN d.order_id=f.order_id THEN 'BROKER_DEAL_LINKED'
                     WHEN d.ticket IS NOT NULL THEN 'BROKER_DEAL_UNCONFIRMED'
                     ELSE 'BROKER_DEAL_UNAVAILABLE' END AS evidence_state
            FROM oms_fills f
            LEFT JOIN pt_broker_deals d ON d.account_id=f.account_id AND d.ticket=f.broker_deal_id
            LEFT JOIN pt_breaks b ON b.account_id=f.account_id AND b.key='deal:' || f.broker_deal_id
                AND b.kind='FILL_MISSING' AND b.state='OPEN'
            WHERE f.order_id=o.id ORDER BY f.at,f.id LIMIT 51) f) AS fills
        FROM oms_orders o WHERE o.account_id=%s AND o.mode=%s AND (o.id=%s OR o.parent_id=%s)
        ORDER BY o.created_at,o.id LIMIT 51""",
        (account, 'PAPER' if mode == 'PAPER' else 'BROKER', row['order_id'], row['parent_id'])).fetchall()
    checks = db.execute('''SELECT id,order_id,approved,requested_qty,approved_qty,reasons,at
        FROM risk_decisions WHERE intent_id=%s AND account_id=%s ORDER BY at DESC LIMIT 50''', (decision, account)).fetchall()
    return {'decision': row, 'checks': checks, 'items': orders[:50], 'more': len(orders) > 50,
            'parent': row if row['parent_id'] else None}
