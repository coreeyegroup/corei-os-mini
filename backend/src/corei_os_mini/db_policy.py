"""One write policy for every direct table editor (System › Tables and Setup › Database).

Why it exists: COREI has two administrator editors. Before release 0.53 they protected different tables, and neither
protected the trading ledger, so an administrator could change an order's state, a position, a fill, a risk limit or
a kill switch without the order engine, risk or four-eyes — and could drop a ledger table outright. The trading
record must change only through the domain code that keeps its invariants and audits each step.
Purpose: decide, for a table name, whether a direct editor may write rows and whether it may change structure.
Responsibilities: the lists of ledger, append-only and screen-controlled tables; `row_mode` and `check_structure`.
Must never: allow a direct write to a ledger table, whatever the policy switches say; be bypassed by a linked
(cascading) delete — editors check every table a cascade would touch.
Dependencies: none (pure). Consumers: infra.py (System › Tables), db_admin.py (Setup › Database).
Architectural position: shared guard below both editors' API routes.
"""
from __future__ import annotations

from fastapi import HTTPException

# The trading record: written only by the order engine, portfolio, risk, post-trade and strategy runtime.
LEDGER_PREFIXES = ('oms_', 'pf_', 'pt_', 'risk_', 'tr_')
LEDGER_TABLES = {'calc_runs', 'calc_graph_versions', 'cfg_strategy_versions', 'cfg_policy_exceptions'}

READ_ONLY_TABLES = {
    'cfg_audit_log': 'Audit history is append-only.',
    'cfg_credential_use': 'The password-use log is append-only.',
    'infra_data_edits': 'The table-edit log is append-only.',
    'corei_schema_migrations': 'Migration history is maintained by the installer.',
    'cfg_credentials': 'Passwords are changed on the account screen (write-only, encrypted).',
    'cfg_sessions': 'Sign-in sessions are managed by the sign-in service.',
    'cfg_session_tokens': 'Broker tokens are managed by the broker login.',
    'cfg_oauth_states': 'Broker login state is managed by the broker login.',
    'cfg_change_requests': 'Change requests are decided on the Approvals screen.',
    'cfg_broker_reads': 'Broker reads are a record of what the broker said.',
    'cfg_latency_checks': 'Latency history is measured, not typed.',
    'cfg_alert_outbox': 'The alert outbox is managed by the alert service.',
    'cfg_users': 'Users are managed on the Users screen (roles, password rules).',
    'cfg_policies': 'Policies are changed on the Controls screen with a reason.',
    'cfg_execution_controls': 'Order sending is switched on the Controls screen with a reason.',
    'md_ticks': 'Prices as received are never edited.',
    'md_bars': 'Bars are built from prices or read from the broker.',
    'md_latest': 'Latest prices are written by the collector.',
    'md_quality': 'Quality is measured, not typed.',
    'md_events': 'Market-data events are a record of what happened.',
    'md_collector_status': 'Collector status is written by the collector.',
    'md_files': 'File fingerprints are written by the archive and checked nightly.',
    'md_backups': 'Backup records are written by the backup job.',
    'md_jobs': 'Backfill jobs are managed on the Market data screen.',
}
CONTROLLED_TABLES = {'cfg_providers', 'cfg_connections', 'cfg_accounts', 'cfg_symbol_mappings', 'cfg_instruments',
                     'cfg_strategies', 'cfg_strategy_versions', 'cfg_deployments', 'cfg_account_groups',
                     'cfg_account_group_members', 'cfg_alert_channels', 'cfg_connection_status', 'cfg_symbol_specs',
                     'md_collection_plans', 'md_controls', 'md_feed_state'}
LEDGER_WHY = ('Part of the trading record (orders, fills, positions, risk, reconciliation, strategy runtime): it changes only '
              'through the order engine, risk and post-trade screens, which keep its rules and audit every step.')


def is_ledger(table: str) -> bool:
    return table.startswith(LEDGER_PREFIXES) or table in LEDGER_TABLES


def row_mode(table: str, has_key: bool, allow_controlled: bool) -> tuple[str, str]:
    """('EDIT' | 'READ_ONLY', why) for row writes through a direct editor."""
    if is_ledger(table):
        return 'READ_ONLY', LEDGER_WHY
    if table in READ_ONLY_TABLES:
        return 'READ_ONLY', READ_ONLY_TABLES[table]
    if not has_key:
        return 'READ_ONLY', 'The table has no primary key, so a row cannot be addressed safely.'
    if table in CONTROLLED_TABLES and not allow_controlled:
        return 'READ_ONLY', ('Edited on its own screen with four-eyes and readiness checks. An administrator can allow direct '
                             'edits with the policy infra_edit_controlled_tables.')
    return 'EDIT', 'Administrators can edit with a reason; every change is logged.'


def check_rows(table: str, has_key: bool, allow_controlled: bool) -> None:
    mode, why = row_mode(table, has_key, allow_controlled)
    if mode != 'EDIT':
        raise HTTPException(403, f'{table} cannot be changed here: {why}')


def check_structure(table: str) -> None:
    """Columns and tables of COREI's own record are changed by migrations, never from an editor."""
    if is_ledger(table) or table in READ_ONLY_TABLES or table in CONTROLLED_TABLES:
        raise HTTPException(403, f'The structure of {table} is part of COREI and changes only through a migration.')
