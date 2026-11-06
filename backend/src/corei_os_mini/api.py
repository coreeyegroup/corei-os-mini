from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response

from .config import get_settings
from .contracts import PaperRunRequest, PaperRunResult
from .persistence import (
    PersistenceNotConfigured,
    PersistenceUnavailable,
    check_database,
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
    yield


app = FastAPI(
    title="COREI-OS-MINI Paper Workflow API",
    version="0.1.0",
    description="Local synthetic-input and paper-execution slice. No broker connectivity or live order route exists.",
    lifespan=lifespan,
)


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
    """Identity and actual capability availability for the new workstation shell."""
    response.headers["Cache-Control"] = "no-store"
    return {
        "project_id": "corei-os-mini",
        "contract_version": 1,
        "release": "0.2.0-shell",
        "execution_mode": settings.execution_mode,
        "database_connected": check_database(settings.database_url),
        "demo_enabled": settings.enable_demo,
        "capabilities": [
            {"id": "assessment", "label": "Saved market-data assessments", "available": True,
             "reason": "Retrieval is implemented; requires configured PostgreSQL and applied schema."},
            {"id": "providers", "label": "Provider connectivity", "available": False,
             "reason": "Provider adapters and registry persistence are not implemented."},
            {"id": "assignments", "label": "Accounts, instruments and strategy assignments", "available": False,
             "reason": "Operational registries and eligibility management are not implemented."},
            {"id": "execution", "label": "Operational execution", "available": False,
             "reason": "Only synthetic demo paper runs exist; operational order routing is not implemented."},
            {"id": "portfolio", "label": "Portfolio and reconciliation", "available": False,
             "reason": "No authoritative portfolio ledger or provider reconciliation exists yet."},
        ],
    }
