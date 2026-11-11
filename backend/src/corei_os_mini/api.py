from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response

from .operator_auth import admin_router, router as operator_router
from .config_api import router as config_router
from .strategy_registry import router as strategy_router
from .controls_api import router as controls_router
from .broker_login import oauth_router, router as broker_login_router
from . import config_store
from .credentials import storage_ready as credential_storage_ready
from .config import get_settings
from .contracts import PaperRunRequest, PaperRunResult
from .persistence import (
    PersistenceNotConfigured,
    PersistenceUnavailable,
    check_database,
    check_schema,
    get_market_data_assessment,
    get_run,
    save_market_data_assessment,
    save_run,
)
from .market_data import (
    MarketDataAssessmentRequest,
    MarketDataAssessmentResult,
    assess_market_data,
)
from .workflow import run_paper_workflow


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio
    import os
    from . import alerts
    task = None
    if os.getenv('COREI_ALERTS', 'on') != 'off' and settings.database_url:
        task = asyncio.create_task(alerts.run_forever(config_store.transaction))
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(
    title="COREI-OS-MINI API",
    version="0.11.0",
    description="Configuration, market-data assessment and trading-core services. No provider adapter is connected yet; order sending is governed by firm-wide execution controls.",
    lifespan=lifespan,
)


app.include_router(operator_router)
app.include_router(admin_router)
# Strategy routes first: config_router's /api/config/{entity} would otherwise claim them.
app.include_router(strategy_router)
app.include_router(controls_router)
app.include_router(broker_login_router)
app.include_router(oauth_router)
app.include_router(config_router)

@app.get("/health")
def health(response: Response) -> dict[str, str | bool]:
    database_ok = check_database(settings.database_url)
    healthy = database_ok
    if not healthy:
        response.status_code = 503
    return {
        "status": "ok" if healthy else "degraded",
        "database": database_ok,
        "execution_mode": settings.execution_mode,
        "demo_enabled": settings.enable_demo,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/demo/paper-runs", response_model=PaperRunResult, status_code=201)
def create_paper_run(payload: PaperRunRequest) -> PaperRunResult:
    if not settings.enable_demo:
        raise HTTPException(status_code=404, detail="Demo workflow is disabled")
    try:
        output = run_paper_workflow(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        save_run(
            settings.database_url,
            UUID(output.run_id),
            payload.instrument_id,
            payload.strategy.strategy_id,
            payload.model_dump(mode="json"),
            output.model_dump(mode="json"),
        )
    except (PersistenceNotConfigured, PersistenceUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return output


@app.post(
    "/api/demo/market-data/assess",
    response_model=MarketDataAssessmentResult,
)
def assess_demo_market_data(
    payload: MarketDataAssessmentRequest,
) -> MarketDataAssessmentResult:
    if not settings.enable_demo:
        raise HTTPException(status_code=404, detail="Demo workflow is disabled")
    assessment_id = uuid4()
    output = assess_market_data(payload, str(assessment_id))
    try:
        save_market_data_assessment(
            settings.database_url,
            assessment_id,
            payload.instrument_id,
            payload.evaluated_at,
            payload.model_dump(mode="json"),
            output.model_dump(mode="json"),
        )
    except (PersistenceNotConfigured, PersistenceUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return output


@app.get("/api/market-data/assessments/{assessment_id}")
def read_market_data_assessment(assessment_id: UUID) -> dict[str, object]:
    try:
        row = get_market_data_assessment(settings.database_url, assessment_id)
    except (PersistenceNotConfigured, PersistenceUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Market-data assessment not found")
    row["evaluated_at"] = row["evaluated_at"].isoformat()
    row["created_at"] = row["created_at"].isoformat()
    return row


@app.get("/api/paper-runs/{run_id}")
def read_paper_run(run_id: UUID) -> dict[str, object]:
    try:
        row = get_run(settings.database_url, run_id)
    except (PersistenceNotConfigured, PersistenceUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Paper run not found")
    row["created_at"] = row["created_at"].isoformat()
    return row


@app.get("/api/workstation")
def workstation(response: Response) -> dict[str, object]:
    """Identity and actual capability availability for the workstation shell."""
    config_ready = config_store.schema_ready()
    response.headers["Cache-Control"] = "no-store"
    return {
        "project_id": "corei-os-mini",
        "contract_version": 2,
        "release": "0.11.0-institutional-controls",
        "database_connected": check_database(settings.database_url),
        "schema_ready": check_schema(settings.database_url),
        "configuration_ready": config_ready,
        "demo_enabled": settings.enable_demo,
        "capabilities": [
            {"id": "configuration", "label": "Configuration", "available": config_ready,
             "reason": "Providers, connections, accounts, instruments, symbol mappings, credentials, order-sending controls and audit. Requires migration 009."},
            {"id": "users", "label": "Users and roles", "available": config_ready,
             "reason": "Administrator, trading operations, risk approver and observer roles. Requires migration 010."},
            {"id": "strategies", "label": "Strategies and deployments", "available": config_ready,
             "reason": "Strategy versions with approval and the deployment matrix. Requires migration 011."},
            {"id": "mt5_routing", "label": "MT5 servers and routing", "available": config_ready,
             "reason": "Broker directory search, servers.dat analysis, latency test and per-account address choice. Requires migration 013."},
            {"id": "credential_storage", "label": "Credential encryption", "available": credential_storage_ready(),
             "reason": "Requires the server key file created by scripts/setup-broker-accounts.sh."},
            {"id": "providers", "label": "Provider connectivity", "available": bool(config_store.AVAILABLE_ADAPTERS),
             "reason": "No provider adapter (MT5 or REST API) is implemented yet."},
            {"id": "assessment", "label": "Market-data assessment", "available": True,
             "reason": "Validation and feed selection on supplied observations; no live feed yet."},
            {"id": "execution", "label": "Order execution", "available": False,
             "reason": "Order lifecycle and provider submission are not implemented yet."},
            {"id": "portfolio", "label": "Portfolio and reconciliation", "available": False,
             "reason": "No position ledger or provider reconciliation exists yet."},
        ],
    }


# Never reflect submitted values (which may include credentials) in validation errors.
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def safe_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(('/api/config', '/api/operator', '/api/admin')):
        return JSONResponse(status_code=422, content={'detail': 'Invalid request body. Send a JSON object with the required fields.'})
    return await request_validation_exception_handler(request, exc)
