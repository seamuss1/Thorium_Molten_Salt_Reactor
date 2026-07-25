"""Lightweight fail-closed schema validation for machine-readable sidecars.

Bundles carry provenance, artifact status, stage manifests, readiness, claims,
and figure metadata as JSON sidecars. These validators catch corrupted or
structurally invalid payloads before reports or QA make decisions from them.
Errors always identify the artifact path and the offending field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUPPORTED_ARTIFACT_STATUS_VERSIONS = {1}
SUPPORTED_STAGE_MANIFEST_VERSIONS = {1}
SUPPORTED_PROVENANCE_VERSIONS = {1}
SUPPORTED_FIGURE_CATALOG_VERSIONS = {1, 2}


class SidecarValidationError(ValueError):
    def __init__(self, artifact: str, field: str, message: str) -> None:
        self.artifact = artifact
        self.field = field
        super().__init__(f"{artifact}: field '{field}' {message}")


def validate_artifact_status(payload: Any, *, artifact: str = "artifact_status.json") -> dict[str, Any]:
    data = _require_mapping(payload, artifact, "<root>")
    _require_version(data, artifact, SUPPORTED_ARTIFACT_STATUS_VERSIONS)
    for field in ("case_name", "run_id", "updated_utc"):
        _require_str(data, artifact, field)
    groups = _require_mapping(data.get("groups"), artifact, "groups")
    for name, group in groups.items():
        group_map = _require_mapping(group, artifact, f"groups.{name}")
        _require_str(group_map, artifact, f"groups.{name}.state", value=group_map.get("state"))
        if not isinstance(group_map.get("artifacts"), list):
            raise SidecarValidationError(artifact, f"groups.{name}.artifacts", "must be a list")
        for field in ("blockers", "warnings"):
            if not isinstance(group_map.get(field, []), list):
                raise SidecarValidationError(artifact, f"groups.{name}.{field}", "must be a list")
    for field in ("blockers", "warnings"):
        if field in data and not isinstance(data[field], list):
            raise SidecarValidationError(artifact, field, "must be a list")
    return data


def validate_stage_manifest(payload: Any, *, artifact: str = "stage_manifest.json") -> dict[str, Any]:
    data = _require_mapping(payload, artifact, "<root>")
    _require_version(data, artifact, SUPPORTED_STAGE_MANIFEST_VERSIONS)
    for field in ("case_name", "run_id"):
        _require_str(data, artifact, field)
    stages = data.get("stages")
    if not isinstance(stages, list):
        raise SidecarValidationError(artifact, "stages", "must be a list")
    for index, stage in enumerate(stages):
        stage_map = _require_mapping(stage, artifact, f"stages[{index}]")
        for field in ("stage", "status", "started_utc", "ended_utc"):
            _require_str(stage_map, artifact, f"stages[{index}].{field}", value=stage_map.get(field))
        if not isinstance(stage_map.get("sequence"), int):
            raise SidecarValidationError(artifact, f"stages[{index}].sequence", "must be an integer")
        if not isinstance(stage_map.get("command"), list):
            raise SidecarValidationError(artifact, f"stages[{index}].command", "must be a list")
        if not isinstance(stage_map.get("output_artifacts", []), list):
            raise SidecarValidationError(artifact, f"stages[{index}].output_artifacts", "must be a list")
    return data


def validate_provenance(payload: Any, *, artifact: str = "provenance.json") -> dict[str, Any]:
    data = _require_mapping(payload, artifact, "<root>")
    _require_version(data, artifact, SUPPORTED_PROVENANCE_VERSIONS)
    for field in ("case_name", "run_id", "created_utc"):
        _require_str(data, artifact, field)
    # input_snapshots / git / runtime are optional for legacy bundles but must
    # be mappings when present.
    for field in ("input_snapshots", "git", "runtime"):
        if field in data and data[field] is not None and not isinstance(data[field], dict):
            raise SidecarValidationError(artifact, field, "must be a mapping when present")
    return data


def validate_design_readiness(payload: Any, *, artifact: str = "design_readiness.json") -> dict[str, Any]:
    data = _require_mapping(payload, artifact, "<root>")
    _require_str(data, artifact, "status")
    if not isinstance(data.get("severe_finding_count"), int):
        raise SidecarValidationError(artifact, "severe_finding_count", "must be an integer")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise SidecarValidationError(artifact, "findings", "must be a list")
    for index, finding in enumerate(findings):
        finding_map = _require_mapping(finding, artifact, f"findings[{index}]")
        for field in ("metric", "severity", "basis", "evidence_artifact"):
            _require_str(finding_map, artifact, f"findings[{index}].{field}", value=finding_map.get(field))
    if not isinstance(data.get("commercial_or_build_candidate_language_allowed"), bool):
        raise SidecarValidationError(artifact, "commercial_or_build_candidate_language_allowed", "must be a boolean")
    return data


def validate_result_claims(payload: Any, *, artifact: str = "result_claims.json") -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise SidecarValidationError(artifact, "<root>", "must be a list of claims")
    for index, claim in enumerate(payload):
        claim_map = _require_mapping(claim, artifact, f"[{index}]")
        for field in ("claim", "status", "evidence_artifact", "evidence_tier"):
            _require_str(claim_map, artifact, f"[{index}].{field}", value=claim_map.get(field))
    return payload


def validate_figure_catalog(payload: Any, *, artifact: str = "plots_manifest.json") -> dict[str, Any]:
    data = _require_mapping(payload, artifact, "<root>")
    version = data.get("schema_version")
    figures = data.get("figures")
    if version is None and figures is None:
        # Legacy flat {plot_id: path} manifests remain readable.
        for plot_id, value in data.items():
            if not isinstance(value, str):
                raise SidecarValidationError(artifact, str(plot_id), "legacy manifest entries must be path strings")
        return data
    if version not in SUPPORTED_FIGURE_CATALOG_VERSIONS:
        raise SidecarValidationError(artifact, "schema_version", f"unsupported version {version!r}")
    figures_map = _require_mapping(figures, artifact, "figures")
    for plot_id, entry in figures_map.items():
        entry_map = _require_mapping(entry, artifact, f"figures.{plot_id}")
        for field in ("path", "title", "caption", "quality_status", "status"):
            _require_str(entry_map, artifact, f"figures.{plot_id}.{field}", value=entry_map.get(field))
    return data


_SIDECAR_VALIDATORS = {
    "artifact_status.json": validate_artifact_status,
    "stage_manifest.json": validate_stage_manifest,
    "provenance.json": validate_provenance,
    "design_readiness.json": validate_design_readiness,
    "result_claims.json": validate_result_claims,
    "plots_manifest.json": validate_figure_catalog,
}


def validate_bundle_sidecars(bundle_dir: Path) -> list[str]:
    """Validate every recognized sidecar present in a bundle.

    Returns a list of human-readable errors; an empty list means every present
    sidecar parsed and validated. Missing sidecars are not errors here - the
    evidence gate and QA decide what absence means.
    """
    errors: list[str] = []
    for name, validator in _SIDECAR_VALIDATORS.items():
        path = Path(bundle_dir) / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: not readable as JSON ({exc}).")
            continue
        try:
            validator(payload, artifact=name)
        except SidecarValidationError as exc:
            errors.append(str(exc))
    return errors


def _require_mapping(value: Any, artifact: str, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SidecarValidationError(artifact, field, "must be a mapping")
    return value


def _require_str(data: dict[str, Any], artifact: str, field: str, *, value: Any = None) -> str:
    resolved = value if value is not None else data.get(field.split(".")[-1])
    if not isinstance(resolved, str) or not resolved:
        raise SidecarValidationError(artifact, field, "must be a non-empty string")
    return resolved


def _require_version(data: dict[str, Any], artifact: str, supported: set[int]) -> int:
    version = data.get("schema_version")
    if version not in supported:
        raise SidecarValidationError(artifact, "schema_version", f"unsupported version {version!r}")
    return version
