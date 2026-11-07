from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class PersistenceNotConfigured(RuntimeError):
    pass


class PersistenceUnavailable(RuntimeError):
    pass


def _connect(database_url: str | None) -> psycopg.Connection[Any]:
    if not database_url:
        raise PersistenceNotConfigured("COREI_DATABASE_URL is required for paper runs")
    return psycopg.connect(database_url, row_factory=dict_row, connect_timeout=3)


def check_database(database_url: str | None) -> bool:
    if not database_url:
        return False
    try:
        with _connect(database_url) as connection:
            connection.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


def check_schema(database_url: str | None) -> bool:
    """Verify required columns are readable, without returning stored records."""
    if not database_url:
        return False
    try:
        with _connect(database_url) as connection:
            connection.execute("SELECT run_id, instrument_id, strategy_id, execution_mode, request_json, result_json, created_at FROM corei_paper_runs LIMIT 0")
            connection.execute("SELECT assessment_id, instrument_id, evaluated_at, request_json, result_json, created_at FROM corei_market_data_assessments LIMIT 0")
        return True
    except psycopg.Error:
        return False


def check_registry_schema(database_url: str | None) -> bool:
    if not database_url:
        return False
    try:
        with _connect(database_url) as connection:
            for query in (
                "SELECT id,name,kind,enabled FROM corei_providers LIMIT 0",
                "SELECT id,provider_id,name,purpose,enabled FROM corei_connections LIMIT 0",
                "SELECT id,connection_id,name,external_reference,mode,enabled FROM corei_accounts LIMIT 0",
                "SELECT id,symbol,asset_class,description,enabled FROM corei_instruments LIMIT 0",
                "SELECT id,connection_id,instrument_id,native_symbol,enabled FROM corei_symbol_mappings LIMIT 0",
            ):
                connection.execute(query)
        return True
    except psycopg.Error:
        return False


def save_run(
    database_url: str | None,
    run_id: UUID,
    instrument_id: str,
    strategy_id: str,
    request_data: dict[str, Any],
    result_data: dict[str, Any],
) -> None:
    try:
        with _connect(database_url) as connection:
            connection.execute(
                """
                INSERT INTO corei_paper_runs
                    (run_id, instrument_id, strategy_id, execution_mode, request_json, result_json)
                VALUES (%s, %s, %s, 'PAPER', %s::jsonb, %s::jsonb)
                """,
                (
                    run_id,
                    instrument_id,
                    strategy_id,
                    json.dumps(request_data, separators=(",", ":")),
                    json.dumps(result_data, separators=(",", ":")),
                ),
            )
    except psycopg.Error as exc:
        raise PersistenceUnavailable("PostgreSQL write failed") from exc


def get_run(database_url: str | None, run_id: UUID) -> dict[str, Any] | None:
    try:
        with _connect(database_url) as connection:
            row = connection.execute(
                """
                SELECT run_id, instrument_id, strategy_id, execution_mode,
                       request_json, result_json, created_at
                FROM corei_paper_runs WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise PersistenceUnavailable("PostgreSQL read failed") from exc
    if row is None:
        return None
    return dict(row)


def save_market_data_assessment(
    database_url: str | None,
    assessment_id: UUID,
    instrument_id: str,
    evaluated_at: datetime,
    request_data: dict[str, Any],
    result_data: dict[str, Any],
) -> None:
    try:
        with _connect(database_url) as connection:
            connection.execute(
                """
                INSERT INTO corei_market_data_assessments
                    (assessment_id, instrument_id, evaluated_at, request_json, result_json)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    assessment_id,
                    instrument_id,
                    evaluated_at,
                    json.dumps(request_data, separators=(",", ":")),
                    json.dumps(result_data, separators=(",", ":")),
                ),
            )
    except psycopg.Error as exc:
        raise PersistenceUnavailable("PostgreSQL market-data assessment write failed") from exc


def get_market_data_assessment(
    database_url: str | None,
    assessment_id: UUID,
) -> dict[str, Any] | None:
    try:
        with _connect(database_url) as connection:
            row = connection.execute(
                """
                SELECT assessment_id, instrument_id, evaluated_at,
                       request_json, result_json, created_at
                FROM corei_market_data_assessments
                WHERE assessment_id = %s
                """,
                (assessment_id,),
            ).fetchone()
    except psycopg.Error as exc:
        raise PersistenceUnavailable("PostgreSQL market-data assessment read failed") from exc
    if row is None:
        return None
    return dict(row)


def check_connector_schema(database_url: str | None) -> bool:
    if not database_url:
        return False
    try:
        with _connect(database_url) as connection:
            connection.execute("SELECT id,connection_id,name,connector_type,adapter_key,environment,runtime,server_name,enabled,created_at FROM corei_connector_profiles LIMIT 0")
        return True
    except psycopg.Error:
        return False
