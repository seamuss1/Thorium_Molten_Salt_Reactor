from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thorium_reactor.runtime_context import build_runtime_context

PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ARTIFACT_STATUS_SCHEMA_VERSION = 1
STAGE_MANIFEST_SCHEMA_VERSION = 1


@dataclass(slots=True)
class ResultBundle:
    case_name: str
    run_id: str
    root: Path
    openmc_dir: Path
    plots_dir: Path
    images_dir: Path
    geometry_dir: Path

    @property
    def geometry_exports_dir(self) -> Path:
        return self.geometry_dir / "exports"

    def write_text(self, name: str, contents: str) -> Path:
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def write_json(self, name: str, payload: object) -> Path:
        import json

        path = self.root / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def write_metrics(self, metrics: dict[str, object]) -> Path:
        path = self.root / "metrics.csv"
        lines = ["metric,value"]
        for key, value in metrics.items():
            lines.append(f"{key},{value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def snapshot_bundle_artifacts(bundle: ResultBundle) -> dict[str, str]:
    if not bundle.root.exists():
        return {}
    artifacts: dict[str, str] = {}
    for path in bundle.root.rglob("*"):
        if path.is_file():
            artifacts[_relative_artifact_path(bundle, path)] = _artifact_fingerprint(path)
    return artifacts


def changed_bundle_artifacts(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {path for path, fingerprint in after.items() if before.get(path) != fingerprint}


def append_stage_manifest(
    bundle: ResultBundle,
    *,
    stage: str,
    command: list[str],
    started_utc: str,
    ended_utc: str | None = None,
    status: str = "completed",
    inputs: dict[str, Any] | None = None,
    output_artifacts: list[str] | None = None,
    method_tier: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    manifest_path = bundle.root / "stage_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
            "generator": "thorium_reactor.paths.append_stage_manifest",
            "case_name": bundle.case_name,
            "run_id": bundle.run_id,
            "stages": [],
        }
    stages = manifest.setdefault("stages", [])
    context = runtime_context or build_runtime_context(command=command, cwd=repo_root)
    record = {
        "sequence": len(stages) + 1,
        "stage": stage,
        "command": command[:1],
        "args": list(command[1:]),
        "started_utc": started_utc,
        "ended_utc": ended_utc or _utc_now(),
        "status": status,
        "runtime_context": context,
        "backend": context.get("backend", {"service": context.get("service", "host")}),
        "device": context.get("backend", {}).get("device", "host-cpu")
        if isinstance(context.get("backend"), dict)
        else "host-cpu",
        "input_snapshot": (inputs or {}).get("input_snapshots")
        or {
            "case": (inputs or {}).get("case", {}),
            "benchmark": (inputs or {}).get("benchmark", {}),
        },
        "output_artifacts": sorted(output_artifacts or []),
        "method_tier": method_tier or "unspecified",
    }
    if message:
        record["message"] = message
    stages.append(record)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def refresh_bundle_artifact_statuses(bundle: ResultBundle, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = summary or {}
    status = {
        "schema_version": ARTIFACT_STATUS_SCHEMA_VERSION,
        "generator": "thorium_reactor.paths.refresh_bundle_artifact_statuses",
        "updated_utc": _utc_now(),
        "case_name": bundle.case_name,
        "run_id": bundle.run_id,
        "groups": {
            "openmc": _openmc_group_status(bundle, summary),
            "images": _directory_group_status(bundle, "images", bundle.images_dir),
            "geometry_exports": _directory_group_status(bundle, "geometry_exports", bundle.geometry_exports_dir),
        },
    }
    if _has_benchmark_evidence_contract(summary):
        status["groups"]["benchmark_evidence"] = _benchmark_evidence_group_status(bundle, summary)
    status["blockers"] = [blocker for group in status["groups"].values() for blocker in group.get("blockers", [])]
    status["warnings"] = [warning for group in status["groups"].values() for warning in group.get("warnings", [])]
    (bundle.root / "artifact_status.json").write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    _write_directory_status(bundle.openmc_dir, status["groups"]["openmc"])
    _write_directory_status(bundle.images_dir, status["groups"]["images"])
    _write_directory_status(bundle.geometry_exports_dir, status["groups"]["geometry_exports"])
    return status


def discover_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "configs" / "cases").exists():
            return candidate
    raise FileNotFoundError("Could not locate repository root from the current working directory.")


def case_config_path(repo_root: Path, case_name: str) -> Path:
    return repo_root / "configs" / "cases" / safe_path_segment(case_name, "case name") / "case.yaml"


def create_result_bundle(repo_root: Path, case_name: str, run_id: str | None = None) -> ResultBundle:
    resolved_case_name = safe_path_segment(case_name, "case name")
    explicit_run_id = run_id is not None
    results_root = (repo_root / "results").resolve()
    case_root = (results_root / resolved_case_name).resolve()

    if explicit_run_id:
        resolved_run_id = safe_path_segment(run_id, "run id")
        root = (case_root / resolved_run_id).resolve()
        if not root.is_relative_to(results_root):
            raise ValueError("Result bundle path must stay inside the results directory.")
        if root.exists():
            raise FileExistsError(f"Run '{resolved_run_id}' for case '{resolved_case_name}' already exists.")
        root.mkdir(parents=True, exist_ok=False)
    else:
        root = None
        resolved_run_id = ""
        for _ in range(8):
            resolved_run_id = default_run_id()
            candidate = (case_root / resolved_run_id).resolve()
            if not candidate.is_relative_to(results_root):
                raise ValueError("Result bundle path must stay inside the results directory.")
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            root = candidate
            break
        if root is None:
            raise FileExistsError(f"Could not allocate a unique run id for case '{resolved_case_name}'.")

    openmc_dir = root / "openmc"
    plots_dir = root / "plots"
    images_dir = root / "images"
    geometry_dir = root / "geometry"
    geometry_exports_dir = geometry_dir / "exports"
    for path in (openmc_dir, plots_dir, images_dir, geometry_dir, geometry_exports_dir):
        path.mkdir(parents=True, exist_ok=True)
    bundle = ResultBundle(
        case_name=resolved_case_name,
        run_id=resolved_run_id,
        root=root,
        openmc_dir=openmc_dir,
        plots_dir=plots_dir,
        images_dir=images_dir,
        geometry_dir=geometry_dir,
    )
    refresh_bundle_artifact_statuses(bundle)
    return bundle


def latest_result_bundle(repo_root: Path, case_name: str) -> ResultBundle:
    resolved_case_name = safe_path_segment(case_name, "case name")
    case_root = repo_root / "results" / resolved_case_name
    if not case_root.exists():
        raise FileNotFoundError(f"No results found for case '{resolved_case_name}'.")
    candidates = [path for path in case_root.iterdir() if path.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No runs found for case '{resolved_case_name}'.")
    # Newest by modification time so arbitrary run-id formats (web-*, CI ids)
    # never outrank a newer timestamped run; ties fall back to the name.
    latest = max(candidates, key=lambda path: (path.stat().st_mtime, path.name))
    return ResultBundle(
        case_name=resolved_case_name,
        run_id=latest.name,
        root=latest,
        openmc_dir=latest / "openmc",
        plots_dir=latest / "plots",
        images_dir=latest / "images",
        geometry_dir=latest / "geometry",
    )


def default_run_id() -> str:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _openmc_group_status(bundle: ResultBundle, summary: dict[str, Any]) -> dict[str, Any]:
    artifacts = _directory_artifacts(bundle.openmc_dir)
    statepoints = [path for path in artifacts if Path(path).name.startswith("statepoint.") and path.endswith(".h5")]
    neutronics = summary.get("neutronics", {}) if isinstance(summary.get("neutronics"), dict) else {}
    raw_status = str(neutronics.get("status", "")).lower()
    if raw_status == "completed" and statepoints:
        state = "completed"
    elif raw_status == "completed":
        state = "failed" if not artifacts else "skipped"
    elif raw_status in {"dry-run", "dry_run"}:
        state = "dry_run"
    elif raw_status.startswith("skipped"):
        state = "skipped"
    elif raw_status == "failed":
        state = "failed"
    elif artifacts:
        state = "dry_run"
    else:
        state = "not_generated"
    blockers: list[str] = []
    warnings: list[str] = []
    if state in {"failed", "skipped", "not_generated"} or not statepoints:
        blockers.append("No solver-backed OpenMC statepoint artifact is present for this bundle.")
    if not artifacts:
        warnings.append("OpenMC output folder is empty.")
    return {
        "kind": "solver",
        "path": "openmc",
        "state": state,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "blockers": blockers,
        "warnings": warnings,
        "neutronics_status": raw_status or None,
    }


def _has_benchmark_evidence_contract(summary: dict[str, Any]) -> bool:
    return bool(summary.get("benchmark_quality") or summary.get("benchmark_evidence"))


def _benchmark_evidence_group_status(bundle: ResultBundle, summary: dict[str, Any]) -> dict[str, Any]:
    required = [
        "summary.json",
        "validation.json",
        "benchmark_residuals.json",
        "report.md",
        "nuclear_data_provenance.json",
        "source_convergence_diagnostics.json",
        "cross_code_comparison.json",
        "uncertainty_budget.json",
        "benchmark_evidence.json",
    ]
    missing = [name for name in required if not (bundle.root / name).exists()]
    evidence = summary.get("benchmark_evidence", {}) if isinstance(summary.get("benchmark_evidence"), dict) else {}
    evidence_blockers = [str(item) for item in evidence.get("blockers", []) if str(item).strip()]
    blockers = [f"Required benchmark evidence artifact is missing: {name}." for name in missing]
    blockers.extend(evidence_blockers)
    return {
        "kind": "benchmark_evidence",
        "path": ".",
        "state": "completed" if not blockers else "blocked",
        "artifact_count": len(required) - len(missing),
        "artifacts": [name for name in required if (bundle.root / name).exists()],
        "blockers": blockers,
        "warnings": [],
    }


def _directory_group_status(bundle: ResultBundle, name: str, directory: Path) -> dict[str, Any]:
    artifacts = _directory_artifacts(directory)
    state = "completed" if artifacts else "not_generated"
    warnings = [] if artifacts else [f"{name} folder is empty."]
    return {
        "kind": "visual" if name == "images" else "geometry",
        "path": _relative_artifact_path(bundle, directory),
        "state": state,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "blockers": [],
        "warnings": warnings,
    }


def _directory_artifacts(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    artifacts = []
    for path in directory.rglob("*"):
        if path.is_file() and path.name != "status.json":
            artifacts.append(str(path.relative_to(directory)).replace("\\", "/"))
    return sorted(artifacts)


def _write_directory_status(directory: Path, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "status.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _relative_artifact_path(bundle: ResultBundle, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(bundle.root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _artifact_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def safe_path_segment(value: str | None, label: str = "path segment") -> str:
    if value is None:
        raise ValueError(f"{label.capitalize()} is required.")
    segment = str(value)
    if segment in {"", ".", ".."}:
        raise ValueError(f"{label.capitalize()} must contain at least one safe character.")
    if any(separator in segment for separator in ("/", "\\")):
        raise ValueError(f"{label.capitalize()} must not contain path separators.")
    if ":" in segment:
        raise ValueError(f"{label.capitalize()} must not contain a drive or URI separator.")
    if not PATH_SEGMENT_RE.fullmatch(segment):
        raise ValueError(f"{label.capitalize()} contains unsupported characters.")
    return segment


def existing_result_bundle(repo_root: Path, case_name: str, run_id: str) -> ResultBundle:
    resolved_case_name = safe_path_segment(case_name, "case name")
    resolved_run_id = safe_path_segment(run_id, "run id")
    root = (repo_root / "results" / resolved_case_name / resolved_run_id).resolve()
    results_root = (repo_root / "results").resolve()
    if not root.is_relative_to(results_root):
        raise ValueError("Result bundle path must stay inside the results directory.")
    if not root.exists():
        raise FileNotFoundError(f"Run '{resolved_run_id}' for case '{resolved_case_name}' does not exist.")
    return ResultBundle(
        case_name=resolved_case_name,
        run_id=resolved_run_id,
        root=root,
        openmc_dir=root / "openmc",
        plots_dir=root / "plots",
        images_dir=root / "images",
        geometry_dir=root / "geometry",
    )
