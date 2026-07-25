"""Shared compiled evidence gate for claim-tier decisions.

Reporting, design readiness, reactor classification, and presentation QA all
answer the same questions: is the bundle solver-backed, can it use
benchmark-ready language, and can it use commercial/build-candidate language?
This module compiles a single evidence object from the canonical bundle
sidecars so those decisions stay consistent and fail closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EVIDENCE_STATUS_SCHEMA_VERSION = 1

_SOLVER_BACKED_STATES = {"completed"}


def load_canonical_artifact_status(bundle_dir: Path | None, summary: dict[str, Any]) -> dict[str, Any]:
    """Return artifact status, preferring the canonical sidecar over summary.

    ``artifact_status.json`` is the source of truth: later commands refresh the
    sidecar without necessarily rewriting ``summary.json``, so an embedded
    ``summary["artifact_status"]`` may be stale.
    """
    if bundle_dir is not None:
        sidecar = Path(bundle_dir) / "artifact_status.json"
        if sidecar.exists():
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                return payload
    embedded = summary.get("artifact_status")
    return embedded if isinstance(embedded, dict) else {}


def build_evidence_status(bundle_dir: Path | None, summary: dict[str, Any]) -> dict[str, Any]:
    """Compile the fail-closed evidence object for claim-tier decisions."""
    summary = summary if isinstance(summary, dict) else {}
    artifact_status = load_canonical_artifact_status(bundle_dir, summary)
    groups = artifact_status.get("groups", {}) if isinstance(artifact_status.get("groups"), dict) else {}
    openmc_group = groups.get("openmc", {}) if isinstance(groups.get("openmc"), dict) else {}
    openmc_state = str(openmc_group.get("state", "")).lower() or "unknown"

    statepoints = _statepoint_artifacts(bundle_dir, openmc_group)
    neutronics = summary.get("neutronics", {}) if isinstance(summary.get("neutronics"), dict) else {}
    neutronics_status = str(neutronics.get("status", "")).lower() or "unknown"

    blockers: list[str] = []
    if neutronics_status != "completed":
        blockers.append(f"Neutronics status is '{neutronics_status}', not a completed solver run.")
    if openmc_state not in _SOLVER_BACKED_STATES:
        blockers.append(f"OpenMC artifact state is '{openmc_state}', not 'completed'.")
    if not statepoints:
        blockers.append("No solver-backed OpenMC statepoint artifact is present.")

    can_use_solver_backed_language = not blockers

    benchmark_quality = (
        summary.get("benchmark_quality", {}) if isinstance(summary.get("benchmark_quality"), dict) else {}
    )
    benchmark_evidence = _load_benchmark_evidence(bundle_dir, summary)
    benchmark_blockers: list[str] = []
    if benchmark_quality.get("benchmark_ready") is not True:
        benchmark_blockers.append("Benchmark quality gates are not ready.")
    if benchmark_evidence:
        if int(benchmark_evidence.get("failed_gate_count", 0) or 0) > 0:
            benchmark_blockers.append("Benchmark evidence contract has failed gates.")
        if benchmark_evidence.get("benchmark_ready_evidence") is False:
            benchmark_blockers.append("Benchmark evidence contract is not benchmark-ready.")
    can_use_benchmark_ready_language = can_use_solver_backed_language and not benchmark_blockers

    failed_stages = _failed_stages(bundle_dir)
    design_readiness = summary.get("design_readiness", {}) if isinstance(summary.get("design_readiness"), dict) else {}
    severe_finding_count = int(design_readiness.get("severe_finding_count", 0) or 0)
    build_blockers: list[str] = []
    if severe_finding_count > 0:
        build_blockers.append(f"{severe_finding_count} severe design-screening finding(s) remain.")
    if failed_stages:
        build_blockers.append(f"Stage manifest records failed stage(s): {', '.join(sorted(failed_stages))}.")
    can_use_build_candidate_language = can_use_benchmark_ready_language and not build_blockers

    all_blockers = blockers + benchmark_blockers + build_blockers
    return {
        "schema_version": EVIDENCE_STATUS_SCHEMA_VERSION,
        "openmc_artifact_state": openmc_state,
        "neutronics_status": neutronics_status,
        "statepoint_artifacts": sorted(statepoints),
        "has_solver_backed_statepoint": bool(statepoints) and openmc_state in _SOLVER_BACKED_STATES,
        "claim_tier": "solver_backed" if can_use_solver_backed_language else "reduced_order_or_proxy",
        "can_use_solver_backed_language": can_use_solver_backed_language,
        "can_use_benchmark_ready_language": can_use_benchmark_ready_language,
        "can_use_build_candidate_language": can_use_build_candidate_language,
        "severe_finding_count": severe_finding_count,
        "failed_stages": sorted(failed_stages),
        "blockers": all_blockers,
    }


def _statepoint_artifacts(bundle_dir: Path | None, openmc_group: dict[str, Any]) -> list[str]:
    if bundle_dir is not None:
        openmc_dir = Path(bundle_dir) / "openmc"
        if openmc_dir.exists():
            return [
                str(path.relative_to(openmc_dir)).replace("\\", "/")
                for path in openmc_dir.rglob("statepoint.*")
                if path.is_file() and path.suffix == ".h5"
            ]
        return []
    artifacts = openmc_group.get("artifacts", [])
    if not isinstance(artifacts, list):
        return []
    return [
        str(name) for name in artifacts if Path(str(name)).name.startswith("statepoint.") and str(name).endswith(".h5")
    ]


def _load_benchmark_evidence(bundle_dir: Path | None, summary: dict[str, Any]) -> dict[str, Any]:
    evidence = summary.get("benchmark_evidence")
    if isinstance(evidence, dict) and evidence:
        return evidence
    if bundle_dir is None:
        return {}
    path = Path(bundle_dir) / "benchmark_evidence.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _failed_stages(bundle_dir: Path | None) -> list[str]:
    if bundle_dir is None:
        return []
    path = Path(bundle_dir) / "stage_manifest.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    stages = payload.get("stages", []) if isinstance(payload, dict) else []
    failed = []
    for stage in stages:
        if isinstance(stage, dict) and str(stage.get("status", "")).lower() == "failed":
            failed.append(str(stage.get("stage", "unknown")))
    return failed
