from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from thorium_reactor.web.jobs import JobManager, is_terminal
from thorium_reactor.web.permissions import AccessController, AccessUser
from thorium_reactor.web.repository import WebRepository
from thorium_reactor.web.schemas import (
    AuthSession,
    CaseDetail,
    CaseSummary,
    DocRecord,
    DocSummary,
    DraftValidationRequest,
    DraftValidationResponse,
    HealthResponse,
    RateLimitRecord,
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
    app.state.access = AccessController(repository.repo_root)

    def access() -> AccessController:
        return app.state.access

    def current_user(request: Request, controller: AccessController = Depends(access)) -> AccessUser:
        return controller.user_from_request(request)

    def current_admin(
        user: AccessUser = Depends(current_user),
        controller: AccessController = Depends(access),
    ) -> AccessUser:
        return controller.require_admin(user)

    # Every /api route requires a verified identity. The controller resolves
    # to the local dev identity when THORIUM_REACTOR_ACCESS_REQUIRED is off,
    # so this is transparent for local use and fails closed in a deployment.
    # Adding a route to this router opts it into auth by default; anything
    # deliberately public has to be registered on `app` below, in the open.
    api = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

    # Public: liveness must answer before an identity exists.
    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", repo_root=str(repository.repo_root))

    @api.get("/me", response_model=AuthSession)
    def get_me(
        user: AccessUser = Depends(current_user),
        controller: AccessController = Depends(access),
    ) -> AuthSession:
        return controller.session_for(user)

    @api.get("/cases", response_model=list[CaseSummary])
    def list_cases() -> list[CaseSummary]:
        return repository.list_cases()

    @api.get("/cases/{case_name}", response_model=CaseDetail)
    def get_case(case_name: str) -> CaseDetail:
        try:
            return repository.get_case(case_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/cases/{case_name}/validate-draft", response_model=DraftValidationResponse)
    def validate_draft(case_name: str, request: DraftValidationRequest) -> DraftValidationResponse:
        return repository.validate_draft(case_name, draft_yaml=request.draft_yaml, patch=request.patch)

    @api.post("/runs", response_model=RunRecord, status_code=202)
    def create_run(
        draft: SimulationDraft,
        user: AccessUser = Depends(current_user),
        controller: AccessController = Depends(access),
    ) -> RunRecord:
        claimed = controller.claim_run_start(user)
        try:
            return jobs.submit(draft)
        except Exception as exc:
            if claimed is not None:
                controller.release_run_start(user)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/runs", response_model=list[RunRecord])
    def list_runs() -> list[RunRecord]:
        return repository.list_runs()

    @api.get("/runs/{case_name}/{run_id}", response_model=RunRecord)
    def get_run(case_name: str, run_id: str) -> RunRecord:
        try:
            return repository.get_run(case_name, run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/runs/{case_name}/{run_id}/events")
    async def stream_events(case_name: str, run_id: str) -> StreamingResponse:
        async def event_stream():
            offset = 0
            while True:
                try:
                    events, offset = repository.read_events_from(case_name, run_id, offset)
                    status = repository.run_status(case_name, run_id)
                except FileNotFoundError:
                    yield 'event: error\ndata: {"message":"Run not found"}\n\n'
                    return
                for event in events:
                    yield f"event: run\ndata: {json.dumps(model_to_dict(event))}\n\n"
                if is_terminal(status):
                    return
                await asyncio.sleep(1.0)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @api.get("/runs/{case_name}/{run_id}/artifacts/{artifact_path:path}")
    def get_artifact(case_name: str, run_id: str, artifact_path: str) -> FileResponse:
        try:
            path = repository.resolve_artifact_path(case_name, run_id, artifact_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(path)

    @api.get("/docs", response_model=list[DocSummary])
    def list_docs() -> list[DocSummary]:
        return repository.list_docs()

    @api.get("/docs/{slug}", response_model=DocRecord)
    def get_doc(slug: str) -> DocRecord:
        try:
            return repository.get_doc(slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/admin/rate-limits", response_model=list[RateLimitRecord])
    def list_rate_limits(
        _admin: AccessUser = Depends(current_admin),
        controller: AccessController = Depends(access),
    ) -> list[RateLimitRecord]:
        return controller.store.list_records()

    @api.post("/admin/rate-limits/{email:path}/reset", response_model=RateLimitRecord)
    def reset_rate_limit(
        email: str,
        admin: AccessUser = Depends(current_admin),
        controller: AccessController = Depends(access),
    ) -> RateLimitRecord:
        return controller.store.reset(email, reset_by=admin.email)

    # Registered before the SPA catch-all below so /api/* always wins.
    app.include_router(api)

    dist_dir = repository.repo_root / "web" / "ui" / "dist"
    if dist_dir.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found.")
            candidate = (dist_dir / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist_dir / "index.html")

    return app
