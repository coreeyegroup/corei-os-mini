from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response

from .config import get_settings
from .contracts import PaperRunRequest, PaperRunResult
from .persistence import (
    PersistenceNotConfigured,
    PersistenceUnavailable,
    check_database,
    get_run,
    save_run,
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
