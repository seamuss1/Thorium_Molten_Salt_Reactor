from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
from typing import Any

PCM_PER_DELTA_K = 100000.0
REQUIRED_OPENMC_XML = ("geometry.xml", "materials.xml", "settings.xml")
REQUIRED_BUNDLE_ARTIFACTS = (
    "summary.json",
    "validation.json",
    "benchmark_residuals.json",
    "report.md",
    "nuclear_data_provenance.json",
    "source_convergence_diagnostics.json",
    "cross_code_comparison.json",
    "benchmark_evidence.json",
)


def materialize_benchmark_evidence(
    bundle: Any,
    config: dict[str, Any],
    summary: dict[str, Any],
    benchmark: dict[str, Any] | None,
    *,
    provenance: dict[str, Any] | None = None,
    openmc_module: Any | None = None,
) -> dict[str, Any]:
    """Write benchmark evidence sidecars and return fail-closed gate status."""

    benchmark = benchmark or {}
    nuclear_data = _preserve_existing_complete_sidecar(
        bundle.root / "nuclear_data_provenance.json",
        build_nuclear_data_provenance(openmc_module=openmc_module),
    )
    bundle.write_json("nuclear_data_provenance.json", nuclear_data)

    convergence = _preserve_existing_complete_sidecar(
        bundle.root / "source_convergence_diagnostics.json",
        build_source_convergence_diagnostics(
            bundle.openmc_dir,
            config,
            summary,
            openmc_module=openmc_module,
        ),
    )
    bundle.write_json("source_convergence_diagnostics.json", convergence)

    cross_code = build_cross_code_comparison(summary, benchmark)
    bundle.write_json("cross_code_comparison.json", cross_code)

    evidence = build_benchmark_evidence(
        bundle.root,
        bundle.openmc_dir,
        config,
        summary,
        benchmark,
        provenance=provenance,
        nuclear_data=nuclear_data,
        convergence=convergence,
        cross_code=cross_code,
    )
    bundle.write_json("benchmark_evidence.json", evidence)
    return evidence


def merge_benchmark_evidence_into_quality(
    quality: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach bundle evidence gates to benchmark quality without allowing overclaims."""

    if not isinstance(quality, dict) or not quality:
        return quality or {}
    if not isinstance(evidence, dict) or not evidence:
        return dict(quality)

    existing = [dict(gate) for gate in quality.get("gates", []) if not str(gate.get("id", "")).startswith("evidence::")]
    evidence_gates = [
        {
            "id": f"evidence::{gate.get('id', 'gate')}",
            "status": gate.get("status", "fail"),
            "message": gate.get("message", ""),
        }
        for gate in evidence.get("gates", [])
    ]
    gates = [*existing, *evidence_gates]
    passed_count = sum(1 for gate in gates if gate.get("status") == "pass")
    failed_gates = [gate for gate in gates if gate.get("status") != "pass"]
    merged = dict(quality)
    merged["gates"] = gates
    merged["passed_gate_count"] = passed_count
    merged["failed_gate_count"] = len(failed_gates)
    merged["quality_score"] = round(100.0 * passed_count / len(gates), 1) if gates else 0.0
    merged["benchmark_ready"] = not failed_gates
    merged["quality_stage"] = "benchmark_ready" if merged["benchmark_ready"] else "benchmark_blocked"
    merged["promotion_blockers"] = [str(gate.get("message", "")) for gate in failed_gates if gate.get("message")]
    return merged


def build_nuclear_data_provenance(*, openmc_module: Any | None = None) -> dict[str, Any]:
    env_paths = {
        "OPENMC_CROSS_SECTIONS": os.environ.get("OPENMC_CROSS_SECTIONS"),
        "OPENMC_MG_CROSS_SECTIONS": os.environ.get("OPENMC_MG_CROSS_SECTIONS"),
        "OPENMC_CHAIN_FILE": os.environ.get("OPENMC_CHAIN_FILE"),
    }
    path_records = {name: _path_record(value) for name, value in env_paths.items() if value}
    hashed = [record for record in path_records.values() if isinstance(record, dict) and record.get("sha256")]
    status = "complete" if hashed else "blocked_missing_cross_sections"
    return {
        "schema_version": 1,
        "status": status,
        "openmc_version": _openmc_version(openmc_module),
        "environment": env_paths,
        "paths": path_records,
        "blockers": (
            []
            if status == "complete"
            else [
                "No hashable OpenMC nuclear-data cross_sections XML or chain file was found in the runtime environment."
            ]
        ),
    }


def build_source_convergence_diagnostics(
    openmc_dir: Path,
    config: dict[str, Any],
    summary: dict[str, Any],
    *,
    openmc_module: Any | None = None,
) -> dict[str, Any]:
    simulation = summary.get("neutronics", {}).get("simulation") or _simulation_settings(config)
    statepoints = sorted(openmc_dir.glob("statepoint.*.h5"))
    diagnostics: dict[str, Any] = {
        "schema_version": 1,
        "status": "blocked_missing_statepoint",
        "simulation": simulation,
        "statepoint": str(statepoints[-1]) if statepoints else None,
        "statepoint_count": len(statepoints),
        "keff_history": {"available": False},
        "notes": [],
        "blockers": [],
    }
    if not statepoints:
        diagnostics["blockers"].append(
            "No OpenMC statepoint.*.h5 file is present, so source convergence cannot be audited."
        )
        return diagnostics
    if openmc_module is None:
        diagnostics["status"] = "blocked_openmc_python_unavailable"
        diagnostics["blockers"].append(
            "OpenMC Python bindings are unavailable, so statepoint convergence metadata was not extracted."
        )
        return diagnostics

    try:
        with openmc_module.StatePoint(str(statepoints[-1])) as statepoint:
            diagnostics["status"] = "complete"
            diagnostics["statepoint_metadata"] = {
                "current_batch": _json_safe(getattr(statepoint, "current_batch", None)),
                "n_batches": _json_safe(getattr(statepoint, "n_batches", None)),
                "n_inactive": _json_safe(getattr(statepoint, "n_inactive", None)),
                "n_particles": _json_safe(getattr(statepoint, "n_particles", None)),
                "generations_per_batch": _json_safe(getattr(statepoint, "generations_per_batch", None)),
            }
            keff = getattr(statepoint, "keff", None)
            if keff is not None:
                diagnostics["keff"] = {
                    "nominal": _json_safe(getattr(keff, "nominal_value", None)),
                    "std_dev": _json_safe(getattr(keff, "std_dev", None)),
                }
            k_generation = getattr(statepoint, "k_generation", None)
            if k_generation is not None:
                values = [_json_safe(value) for value in list(k_generation)]
                diagnostics["keff_history"] = {
                    "available": True,
                    "count": len(values),
                    "first": values[:10],
                    "last": values[-10:],
                }
            else:
                diagnostics["notes"].append("OpenMC statepoint did not expose per-generation keff history.")
    except Exception as exc:  # pragma: no cover - depends on external OpenMC/HDF5
        diagnostics["status"] = "blocked_statepoint_read_failed"
        diagnostics["blockers"].append(f"Failed to read OpenMC statepoint diagnostics: {exc}")
    return diagnostics


def build_cross_code_comparison(summary: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    references = _cross_code_references(benchmark)
    openmc_keff = _coerce_float(summary.get("metrics", {}).get("keff"))
    openmc_sigma = _coerce_float(summary.get("metrics", {}).get("keff_std_dev"))
    solver_status = str(summary.get("neutronics", {}).get("status", "")).lower()
    comparisons = []
    for reference in references:
        residual_pcm = None
        combined_uncertainty_pcm = None
        normalized_residual = None
        if openmc_keff is not None and solver_status == "completed":
            residual_pcm = round((openmc_keff - float(reference["keff"])) * PCM_PER_DELTA_K, 3)
            reference_uncertainty = _coerce_float(reference.get("uncertainty_pcm")) or 0.0
            openmc_uncertainty = (openmc_sigma or 0.0) * PCM_PER_DELTA_K
            combined_uncertainty_pcm = round((reference_uncertainty**2 + openmc_uncertainty**2) ** 0.5, 3)
            if combined_uncertainty_pcm > 0.0:
                normalized_residual = round(residual_pcm / combined_uncertainty_pcm, 4)
        comparisons.append(
            {
                **reference,
                "openmc_keff": openmc_keff,
                "openmc_keff_std_dev": openmc_sigma,
                "residual_pcm": residual_pcm,
                "combined_uncertainty_pcm": combined_uncertainty_pcm,
                "normalized_residual": normalized_residual,
                "status": "completed" if residual_pcm is not None else "blocked_missing_openmc_result",
            }
        )
    completed = [item for item in comparisons if item["status"] == "completed"]
    return {
        "schema_version": 1,
        "status": "completed" if references and len(completed) == len(references) else "blocked",
        "openmc_status": solver_status or "unknown",
        "references": references,
        "comparisons": comparisons,
        "blockers": []
        if references and len(completed) == len(references)
        else _cross_code_blockers(references, openmc_keff, solver_status),
    }


def build_benchmark_evidence(
    bundle_root: Path,
    openmc_dir: Path,
    config: dict[str, Any],
    summary: dict[str, Any],
    benchmark: dict[str, Any],
    *,
    provenance: dict[str, Any] | None,
    nuclear_data: dict[str, Any],
    convergence: dict[str, Any],
    cross_code: dict[str, Any],
) -> dict[str, Any]:
    gates = []
    tallies_required = bool(config.get("simulation", {}).get("tallies"))
    xml_files = [*REQUIRED_OPENMC_XML, *(("tallies.xml",) if tallies_required else ())]
    gates.append(
        _gate(
            "openmc_xml_inputs",
            all((openmc_dir / name).exists() for name in xml_files),
            f"Required OpenMC XML inputs present: {', '.join(xml_files)}.",
        )
    )
    statepoints = sorted(openmc_dir.glob("statepoint.*.h5"))
    gates.append(
        _gate("openmc_statepoint", bool(statepoints), "At least one OpenMC statepoint.*.h5 artifact is present.")
    )
    for artifact in REQUIRED_BUNDLE_ARTIFACTS:
        if artifact == "benchmark_evidence.json":
            continue
        gates.append(
            _gate(
                f"artifact::{artifact}",
                (bundle_root / artifact).exists(),
                f"Required evidence artifact {artifact} is present.",
            )
        )
    gates.append(
        _gate(
            "nuclear_data_provenance",
            nuclear_data.get("status") == "complete",
            "Nuclear data provenance includes a hashable runtime library record.",
        )
    )
    gates.append(
        _gate(
            "source_convergence_diagnostics",
            convergence.get("status") == "complete",
            "OpenMC source convergence diagnostics were extracted from a statepoint.",
        )
    )
    gates.append(
        _gate(
            "cross_code_comparison",
            cross_code.get("status") == "completed",
            "Declared cross-code comparison residuals are complete.",
        )
    )
    gates.append(
        _gate(
            "uncertainty_budget",
            _uncertainty_budget_complete(bundle_root),
            "Geometry/material uncertainty budget is complete and source-backed for benchmark-ready use.",
        )
    )
    gates.append(
        _gate(
            "runtime_context",
            _runtime_context_complete(summary, provenance),
            "Runtime context includes git commit and reproducibility metadata.",
        )
    )
    gates.append(
        _gate(
            "source_dossier_paths",
            _source_dossier_paths_exist(bundle_root, benchmark),
            "Source index, parameter table, and assumption log paths exist.",
        )
    )
    passed = [gate for gate in gates if gate["status"] == "pass"]
    failed = [gate for gate in gates if gate["status"] != "pass"]
    return {
        "schema_version": 1,
        "status": "pass" if not failed else "blocked",
        "benchmark_ready_evidence": not failed,
        "case": summary.get("case"),
        "run_id": provenance.get("run_id") if isinstance(provenance, dict) else None,
        "statepoints": [str(path) for path in statepoints],
        "gates": gates,
        "passed_gate_count": len(passed),
        "failed_gate_count": len(failed),
        "blockers": [gate["message"] for gate in failed],
        "artifacts": _artifact_index(bundle_root, openmc_dir),
    }


def _gate(gate_id: str, passed: bool, success_message: str) -> dict[str, str]:
    message = success_message
    if not passed:
        message = _failure_message(gate_id, success_message)
    return {"id": gate_id, "status": "pass" if passed else "fail", "message": message}


def _failure_message(gate_id: str, success_message: str) -> str:
    if gate_id == "openmc_statepoint":
        return "No OpenMC statepoint.*.h5 artifact is present."
    if gate_id == "nuclear_data_provenance":
        return "Nuclear data provenance is missing library name/version/path/hash evidence."
    if gate_id == "source_convergence_diagnostics":
        return "OpenMC source convergence diagnostics are missing or could not be extracted."
    if gate_id == "cross_code_comparison":
        return "OpenMC-vs-Serpent/SCALE comparison residuals are not complete."
    if gate_id == "uncertainty_budget":
        return "Geometry/material uncertainty budget is missing, incomplete, or not source-backed."
    if gate_id == "runtime_context":
        return "Runtime context is missing git commit or reproducibility metadata."
    if gate_id == "source_dossier_paths":
        return "Source dossier paths are missing or unresolved."
    if gate_id.startswith("artifact::"):
        return f"Required evidence artifact {gate_id.split('::', 1)[1]} is missing."
    if gate_id == "openmc_xml_inputs":
        return "One or more required OpenMC XML input files are missing."
    return success_message


def _simulation_settings(config: dict[str, Any]) -> dict[str, Any]:
    simulation = config.get("simulation", {}) if isinstance(config.get("simulation"), dict) else {}
    return {
        "mode": simulation.get("mode"),
        "particles": simulation.get("particles"),
        "batches": simulation.get("batches"),
        "inactive": simulation.get("inactive", 0),
        "active_batches": (
            int(simulation.get("batches", 0)) - int(simulation.get("inactive", 0))
            if simulation.get("batches") is not None
            else None
        ),
        "source": simulation.get("source"),
        "tallies": simulation.get("tallies", []),
    }


def _cross_code_references(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    references = []
    for dataset in benchmark.get("datasets", []):
        if not isinstance(dataset, dict):
            continue
        for observable in dataset.get("observables", []):
            if not isinstance(observable, dict):
                continue
            observable_id = str(observable.get("id", ""))
            if (
                "serpent" not in observable_id.lower()
                and "scale" not in observable_id.lower()
                and "shift" not in observable_id.lower()
            ):
                continue
            references.append(
                {
                    "id": observable_id,
                    "code": _reference_code(observable_id),
                    "library": observable.get("library") or "ENDF/B-VII.1"
                    if "serpent" in observable_id.lower()
                    else observable.get("library"),
                    "keff": observable.get("value"),
                    "uncertainty_pcm": observable.get("uncertainty_pcm"),
                    "source": dataset.get("source"),
                    "evidence_refs": observable.get("evidence_refs", []),
                }
            )
    return [item for item in references if _coerce_float(item.get("keff")) is not None]


def _reference_code(reference_id: str) -> str:
    lowered = reference_id.lower()
    if "serpent" in lowered:
        return "Serpent"
    if "scale" in lowered:
        return "SCALE"
    if "shift" in lowered:
        return "Shift"
    return "reference"


def _cross_code_blockers(references: list[dict[str, Any]], openmc_keff: float | None, solver_status: str) -> list[str]:
    blockers = []
    if not references:
        blockers.append("No Serpent/SCALE/Shift reference values are declared in benchmark metadata.")
    if openmc_keff is None or solver_status != "completed":
        blockers.append("No solver-backed OpenMC keff is available for cross-code residuals.")
    return blockers


def _uncertainty_budget_complete(bundle_root: Path) -> bool:
    path = bundle_root / "uncertainty_budget.json"
    if not path.exists():
        return False
    try:
        budget = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    coverage = budget.get("coverage", {}) if isinstance(budget.get("coverage"), dict) else {}
    return (
        budget.get("status") == "completed"
        and coverage.get("status") == "quantified"
        and coverage.get("all_required_source_backed") is True
    )


def _runtime_context_complete(summary: dict[str, Any], provenance: dict[str, Any] | None) -> bool:
    runtime = summary.get("runtime_context", {})
    if not isinstance(runtime, dict) or not runtime.get("git_commit"):
        runtime = provenance.get("runtime", {}) if isinstance(provenance, dict) else {}
    git = provenance.get("git", {}) if isinstance(provenance, dict) else {}
    return bool(runtime.get("git_commit") and (runtime.get("dependency_hash") or git.get("available") is not None))


def _source_dossier_paths_exist(bundle_root: Path, benchmark: dict[str, Any]) -> bool:
    source_dossier = benchmark.get("source_dossier", {}) if isinstance(benchmark.get("source_dossier"), dict) else {}
    required = ("source_index", "parameter_table", "assumption_log")
    repo_root = _repo_root_from_bundle(bundle_root)
    for key in required:
        raw_path = source_dossier.get(key)
        if not raw_path:
            return False
        path = Path(str(raw_path))
        if not path.is_absolute() and repo_root is not None:
            path = repo_root / path
        if not path.exists():
            return False
    return True


def _repo_root_from_bundle(bundle_root: Path) -> Path | None:
    try:
        return bundle_root.parents[2]
    except IndexError:
        return None


def _artifact_index(bundle_root: Path, openmc_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for name in REQUIRED_BUNDLE_ARTIFACTS:
        artifacts[name] = _path_record(str(bundle_root / name))
    artifacts["openmc"] = {
        name: _path_record(str(openmc_dir / name)) for name in (*REQUIRED_OPENMC_XML, "tallies.xml", "summary.h5")
    }
    artifacts["openmc"]["statepoints"] = [
        _path_record(str(path)) for path in sorted(openmc_dir.glob("statepoint.*.h5"))
    ]
    return artifacts


def _path_record(path_text: str | None) -> dict[str, Any]:
    if not path_text:
        return {"path": None, "exists": False}
    path = Path(path_text)
    record: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return record
    if path.is_file():
        record["size_bytes"] = path.stat().st_size
        record["sha256"] = _file_sha256(path)
    elif path.is_dir():
        record["type"] = "directory"
    return record


def _preserve_existing_complete_sidecar(path: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    if _sidecar_is_complete(candidate):
        return candidate
    existing = _load_sidecar(path)
    if existing is not None and _sidecar_is_complete(existing):
        return existing
    return candidate


def _load_sidecar(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _sidecar_is_complete(sidecar: dict[str, Any]) -> bool:
    return str(sidecar.get("status", "")).lower() in {"complete", "completed", "pass"}


def _file_sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _openmc_version(openmc_module: Any | None) -> str | None:
    if openmc_module is not None and getattr(openmc_module, "__version__", None):
        return str(openmc_module.__version__)
    try:
        return importlib.metadata.version("openmc")
    except importlib.metadata.PackageNotFoundError:
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
