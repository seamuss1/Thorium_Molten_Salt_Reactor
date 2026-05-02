from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    label: str
    kind: str
    mime_type: str
    size: int
    path: str
    url: str


class RunEvent(BaseModel):
    sequence: int
    timestamp: str
    level: str = "info"
    phase: str | None = None
    message: str
    progress: float | None = None
    artifact: ArtifactRef | None = None


class RunRecord(BaseModel):
    case_name: str
    run_id: str
    status: str
    phase: str | None = None
    command_plan: list[str] = Field(default_factory=list)
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    reactor: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    latest_event: RunEvent | None = None


class EditableParameter(BaseModel):
    path: str
    label: str
    group: str
    kind: str
    value: Any = None
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: list[str] | None = None


class CaseSummary(BaseModel):
    name: str
    reactor: dict[str, Any]
    capabilities: list[str]
    editable_parameters: list[EditableParameter]
    latest_run: RunRecord | None = None
    docs: list[dict[str, str]] = Field(default_factory=list)


class CaseDetail(CaseSummary):
    config: dict[str, Any]
    validation_targets: dict[str, Any] = Field(default_factory=dict)
    benchmark_path: str | None = None


class SimulationDraft(BaseModel):
    case_name: str
    run_id: str | None = None
    draft_yaml: str | None = None
    patch: dict[str, Any] = Field(default_factory=dict)
    phases: list[str] = Field(default_factory=lambda: ["run", "validate", "report"])
    scenario: str | None = None
    sweep_samples: int = 512
    sweep_seed: int = 42
    prefer_gpu: bool = False


class DraftValidationRequest(BaseModel):
    draft_yaml: str | None = None
    patch: dict[str, Any] = Field(default_factory=dict)


class DraftValidationResponse(BaseModel):
    valid: bool
    message: str
    normalized_yaml: str | None = None
    editable_parameters: list[EditableParameter] = Field(default_factory=list)


class DocSummary(BaseModel):
    slug: str
    title: str
    path: str
    headings: list[str] = Field(default_factory=list)


class DocRecord(DocSummary):
    content: str


class HealthResponse(BaseModel):
    status: str
    repo_root: str


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    return model.dict()


def copy_model(model: BaseModel, *, update: dict[str, Any]) -> BaseModel:
    copier = getattr(model, "model_copy", None)
    if callable(copier):
        return copier(update=update)
    return model.copy(update=update)
