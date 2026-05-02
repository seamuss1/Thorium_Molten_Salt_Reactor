from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from thorium_reactor.web.jobs import JobManager, is_terminal
from thorium_reactor.web.repository import WebRepository
from thorium_reactor.web.schemas import (
    CaseDetail,
    CaseSummary,
    DocRecord,
    DocSummary,
    DraftValidationRequest,
    DraftValidationResponse,
    HealthResponse,
    RunRecord,
    SimulationDraft,
    model_to_dict,
)


def create_app(repo_root: Path | None = None) -> FastAPI:
    repository = WebRepository(repo_root)
    jobs = JobManager(repository)
    app = FastAPI(
        title="Thorium Reactor Lab",
        version="0.1.0",
        docs_url="/api/openapi",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.state.repository = repository
    app.state.jobs = jobs

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", repo_root=str(repository.repo_root))

    @app.get("/api/cases", response_model=list[CaseSummary])
    def list_cases() -> list[CaseSummary]:
        return repository.list_cases()

    @app.get("/api/cases/{case_name}", response_model=CaseDetail)
    def get_case(case_name: str) -> CaseDetail:
        try:
            return repository.get_case(case_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/cases/{case_name}/validate-draft", response_model=DraftValidationResponse)
    def validate_draft(case_name: str, request: DraftValidationRequest) -> DraftValidationResponse:
        return repository.validate_draft(case_name, draft_yaml=request.draft_yaml, patch=request.patch)

    @app.post("/api/runs", response_model=RunRecord, status_code=202)
    def create_run(request: SimulationDraft) -> RunRecord:
        try:
            return jobs.submit(request)
        except Exception as exc:  # noqa: BLE001 - converted into browser-safe feedback.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs", response_model=list[RunRecord])
    def list_runs() -> list[RunRecord]:
        return repository.list_runs()

    @app.get("/api/runs/{case_name}/{run_id}", response_model=RunRecord)
    def get_run(case_name: str, run_id: str) -> RunRecord:
        try:
            return repository.get_run(case_name, run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{case_name}/{run_id}/events")
    async def stream_events(case_name: str, run_id: str) -> StreamingResponse:
        async def event_stream():
            seen = 0
            while True:
                try:
                    events = repository.read_events(case_name, run_id)
                    record = repository.get_run(case_name, run_id)
                except FileNotFoundError:
                    yield "event: error\ndata: {\"message\":\"Run not found\"}\n\n"
                    return
                for event in events[seen:]:
                    yield f"event: run\ndata: {json.dumps(model_to_dict(event))}\n\n"
                seen = len(events)
                if is_terminal(record.status):
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/runs/{case_name}/{run_id}/artifacts/{artifact_path:path}")
    def get_artifact(case_name: str, run_id: str, artifact_path: str) -> FileResponse:
        try:
            path = repository.resolve_artifact_path(case_name, run_id, artifact_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(path)

    @app.get("/api/docs", response_model=list[DocSummary])
    def list_docs() -> list[DocSummary]:
        return repository.list_docs()

    @app.get("/api/docs/{slug}", response_model=DocRecord)
    def get_doc(slug: str) -> DocRecord:
        try:
            return repository.get_doc(slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    dist_dir = repository.repo_root / "web" / "ui" / "dist"
    if dist_dir.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:
            candidate = (dist_dir / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist_dir / "index.html")

    return app
