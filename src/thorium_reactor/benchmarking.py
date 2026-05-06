from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from thorium_reactor.config import CaseConfig


CONFIDENCE_LEVELS = ("high", "medium", "low")
DATASET_STATUSES = {"planned", "numerical_validation", "context_only", "literature_backed"}
REACTOR_TARGET_LINKS = (
    ("design_power_mwth", "nominal_thermal_power_mwth", "design thermal power"),
    ("hot_leg_temp_c", "nominal_hot_leg_temp_c", "hot-leg temperature"),
    ("cold_leg_temp_c", "nominal_cold_leg_temp_c", "cold-leg temperature"),
)
OPERATING_POINT_STATUSES = {"literature-backed", "cross-code-backed", "surrogate"}
UNCERTAINTY_COVERAGE_STATUSES = {"quantified", "partial", "qualitative", "missing"}
QUALITY_GATE_PASS_STATUSES = {"completed", "complete", "pass", "passed"}
PCM_PER_DELTA_K = 100000.0


def assess_benchmark_traceability(
    config: CaseConfig | dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> dict[str, Any]:
    benchmark = benchmark or {}
    reactor, validation_targets = _extract_config_parts(config)

    evidence = [_normalize_evidence(item, index) for index, item in enumerate(benchmark.get("evidence", []), start=1)]
    assumptions = [_normalize_assumption(item, index) for index, item in enumerate(benchmark.get("assumptions", []), start=1)]
    targets = _collect_benchmark_targets(benchmark)
    datasets = [_normalize_dataset(item, index) for index, item in enumerate(benchmark.get("datasets", []), start=1)]

    evidence_complete_count = sum(1 for item in evidence if item["complete"])
    assumption_structured_count = sum(1 for item in assumptions if item["structured"])
    assumption_linked_count = sum(1 for item in assumptions if item["evidence_refs"])
    assumption_confidence_count = sum(1 for item in assumptions if item["confidence"] is not None)
    target_structured_count = sum(1 for item in targets if item["structured"])
    target_linked_count = sum(1 for item in targets if item["evidence_refs"])
    target_confidence_count = sum(1 for item in targets if item["confidence"] is not None)
    surrogate_target_count = sum(1 for item in targets if item["status"] == "surrogate")
    literature_target_count = sum(1 for item in targets if item["status"] == "literature-backed")

    reactor_parameter_links = []
    for case_field, target_id, label in REACTOR_TARGET_LINKS:
        present = reactor.get(case_field) is not None
        linked = any(item["id"] == target_id for item in targets) if present else False
        reactor_parameter_links.append(
            {
                "case_field": case_field,
                "target_id": target_id,
                "label": label,
                "present": present,
                "linked": linked,
            }
        )

    physics_validation_links = []
    for validation_name, target in validation_targets.items():
        target_ids = _infer_benchmark_targets_for_validation(target)
        if not target_ids:
            continue
        linked_ids = [target_id for target_id in target_ids if any(item["id"] == target_id for item in targets)]
        physics_validation_links.append(
            {
                "validation_target": validation_name,
                "linked_target_ids": linked_ids,
                "expected_target_ids": target_ids,
                "linked": len(linked_ids) == len(target_ids),
            }
        )

    confidence_summary = {level: 0 for level in CONFIDENCE_LEVELS}
    confidence_summary["unspecified"] = 0
    for item in [*evidence, *assumptions, *targets]:
        confidence = item.get("confidence")
        key = confidence if confidence in CONFIDENCE_LEVELS else "unspecified"
        confidence_summary[key] += 1

    score_components = {
        "evidence_completeness": _ratio(evidence_complete_count, len(evidence)),
        "assumption_structure": _ratio(assumption_structured_count, len(assumptions)),
        "assumption_linkage": _ratio(assumption_linked_count, len(assumptions)),
        "target_structure": _ratio(target_structured_count, len(targets)),
        "target_linkage": _ratio(target_linked_count, len(targets)),
        "reactor_parameter_linkage": _ratio(
            sum(1 for item in reactor_parameter_links if item["linked"]),
            sum(1 for item in reactor_parameter_links if item["present"]),
        ),
        "physics_validation_linkage": _ratio(
            sum(1 for item in physics_validation_links if item["linked"]),
            len(physics_validation_links),
        ),
    }
    traceability_score = round(
        100.0
        * (
            0.20 * score_components["evidence_completeness"]
            + 0.12 * score_components["assumption_structure"]
            + 0.13 * score_components["assumption_linkage"]
            + 0.15 * score_components["target_structure"]
            + 0.15 * score_components["target_linkage"]
            + 0.15 * score_components["reactor_parameter_linkage"]
            + 0.10 * score_components["physics_validation_linkage"]
        ),
        1,
    )

    maturity_stage = "untracked"
    if benchmark:
        maturity_stage = "surrogate_scaffold"
        if traceability_score >= 75.0:
            maturity_stage = "traceable_surrogate"
        if literature_target_count == len(targets) and targets:
            maturity_stage = "literature_tracked"

    gaps: list[str] = []
    if evidence and evidence_complete_count < len(evidence):
        gaps.append("Some evidence records are missing topic/source/claim/relevance fields.")
    if assumptions and assumption_linked_count < len(assumptions):
        gaps.append("Some benchmark assumptions are not linked to evidence records.")
    if targets and target_linked_count < len(targets):
        gaps.append("Some benchmark targets are not linked to evidence records.")
    if surrogate_target_count:
        gaps.append(f"{surrogate_target_count} benchmark target(s) are still marked surrogate.")
    missing_reactor_links = [item["label"] for item in reactor_parameter_links if item["present"] and not item["linked"]]
    if missing_reactor_links:
        gaps.append(
            "Some reactor operating parameters are not mapped into benchmark targets: "
            + ", ".join(missing_reactor_links)
            + "."
        )
    missing_validation_links = [
        item["validation_target"] for item in physics_validation_links if not item["linked"]
    ]
    if missing_validation_links:
        gaps.append(
            "Some physics-facing validation targets are not benchmark-linked: "
            + ", ".join(missing_validation_links)
            + "."
        )
    validation_maturity = _assess_validation_maturity(benchmark, targets)
    benchmark_quality = _assess_benchmark_quality(config, benchmark, targets, validation_maturity)

    return {
        "traceability_score": traceability_score,
        "maturity_stage": maturity_stage,
        "coverage": {
            "evidence_records_complete": {
                "linked": evidence_complete_count,
                "total": len(evidence),
            },
            "assumptions_structured": {
                "linked": assumption_structured_count,
                "total": len(assumptions),
            },
            "assumptions_with_evidence": {
                "linked": assumption_linked_count,
                "total": len(assumptions),
            },
            "targets_structured": {
                "linked": target_structured_count,
                "total": len(targets),
            },
            "targets_with_evidence": {
                "linked": target_linked_count,
                "total": len(targets),
            },
            "reactor_parameters_linked": {
                "linked": sum(1 for item in reactor_parameter_links if item["linked"]),
                "total": sum(1 for item in reactor_parameter_links if item["present"]),
            },
            "physics_validation_targets_linked": {
                "linked": sum(1 for item in physics_validation_links if item["linked"]),
                "total": len(physics_validation_links),
            },
        },
        "confidence_summary": confidence_summary,
        "status_summary": {
            "surrogate_targets": surrogate_target_count,
            "literature_backed_targets": literature_target_count,
        },
        "reactor_parameter_links": reactor_parameter_links,
        "physics_validation_links": physics_validation_links,
        "gaps": gaps,
        "validation_maturity": validation_maturity,
        "benchmark_quality": benchmark_quality,
        "assumptions": assumptions,
        "targets": targets,
        "evidence": evidence,
        "datasets": datasets,
    }


def run_solver_backed_benchmark(
    repo_root: Path,
    case_name: str,
    run_id: str,
) -> dict[str, Any]:
    docker_status = get_docker_runtime_status()
    if not docker_status["cli_available"]:
        raise RuntimeError(
            "Solver-backed benchmark runs require Docker on this host. "
            "Install Docker Desktop or use a supported OpenMC runtime."
        )
    if not docker_status["daemon_available"]:
        raise RuntimeError(str(docker_status["message"]))

    command = build_docker_openmc_command(case_name, run_id)
    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Docker-backed OpenMC benchmark run failed.\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return {
        "runtime": "docker-openmc",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def get_docker_runtime_status() -> dict[str, Any]:
    docker_executable = shutil.which("docker")
    if docker_executable is None:
        return {
            "cli_available": False,
            "daemon_available": False,
            "message": "Docker CLI is not installed on this host.",
        }

    completed = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        detail = completed.stderr.strip() or completed.stdout.strip()
        message = "Docker is installed but the Docker daemon is not reachable. Start Docker Desktop and retry."
        if detail:
            message = f"{message} Details: {detail}"
        return {
            "cli_available": True,
            "daemon_available": False,
            "message": message,
        }

    return {
        "cli_available": True,
        "daemon_available": True,
        "server_version": completed.stdout.strip(),
        "message": None,
    }


def build_docker_openmc_command(case_name: str, run_id: str) -> list[str]:
    return [
        "docker",
        "compose",
        "run",
        "--build",
        "--rm",
        "openmc",
        "python",
        "-m",
        "thorium_reactor.cli",
        "run",
        case_name,
        "--run-id",
        run_id,
    ]


def _extract_config_parts(config: CaseConfig | dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(config, CaseConfig):
        return config.reactor, config.validation_targets
    return config.get("reactor", {}), config.get("validation_targets", {})


def _extract_config_data(config: CaseConfig | dict[str, Any]) -> dict[str, Any]:
    if isinstance(config, CaseConfig):
        return config.data
    return config


def _normalize_evidence(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": f"evidence_{index}",
            "topic": None,
            "source": None,
            "claim": str(item),
            "relevance": None,
            "confidence": None,
            "complete": False,
        }
    return {
        "id": str(item.get("id", f"evidence_{index}")),
        "topic": item.get("topic"),
        "source": item.get("source"),
        "claim": item.get("claim"),
        "relevance": item.get("relevance"),
        "confidence": _normalize_confidence(item.get("confidence")),
        "complete": all(item.get(key) for key in ("topic", "source", "claim", "relevance")),
    }


def _normalize_assumption(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": f"assumption_{index}",
            "text": str(item),
            "basis": None,
            "confidence": None,
            "evidence_refs": [],
            "structured": False,
        }
    return {
        "id": str(item.get("id", f"assumption_{index}")),
        "text": item.get("text") or item.get("summary") or item.get("assumption"),
        "basis": item.get("basis"),
        "confidence": _normalize_confidence(item.get("confidence")),
        "evidence_refs": [str(value) for value in item.get("evidence_refs", [])],
        "structured": bool(item.get("text") or item.get("summary") or item.get("assumption")),
    }


def _normalize_target(name: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {
            "id": name,
            "kind": "scalar",
            "value": spec,
            "units": None,
            "status": None,
            "confidence": None,
            "evidence_refs": [],
            "structured": False,
        }
    target_kind = "band" if any(key in spec for key in ("min", "max")) else "scalar"
    return {
        "id": name,
        "kind": target_kind,
        "value": spec.get("value"),
        "min": spec.get("min"),
        "max": spec.get("max"),
        "units": spec.get("units"),
        "status": spec.get("status"),
        "confidence": _normalize_confidence(spec.get("confidence")),
        "evidence_refs": [str(value) for value in spec.get("evidence_refs", [])],
        "structured": bool(spec.get("units") or spec.get("status") or spec.get("confidence") or spec.get("evidence_refs")),
        "note": spec.get("note"),
        "uncertainty": spec.get("uncertainty"),
        "uncertainty_pcm": spec.get("uncertainty_pcm"),
    }


def _collect_benchmark_targets(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    targets = [_normalize_target(name, spec) for name, spec in (benchmark.get("targets") or {}).items()]
    for dataset_index, dataset in enumerate(benchmark.get("datasets", []), start=1):
        if not isinstance(dataset, dict):
            continue
        dataset_id = str(dataset.get("id", f"dataset_{dataset_index}"))
        observables = dataset.get("observables", [])
        if not isinstance(observables, list):
            continue
        for observable_index, observable in enumerate(observables, start=1):
            if not isinstance(observable, dict):
                continue
            observable_id = str(observable.get("id", f"{dataset_id}_observable_{observable_index}"))
            normalized = _normalize_target(observable_id, observable)
            normalized["dataset_id"] = dataset_id
            normalized["dataset_status"] = str(dataset.get("status", "planned"))
            targets.append(normalized)
    return targets


def _normalize_dataset(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": f"dataset_{index}",
            "phenomenon": None,
            "status": "planned",
            "confidence": None,
            "observable_count": 0,
        }
    status = str(item.get("status", "planned")).lower()
    if status not in DATASET_STATUSES:
        status = "planned"
    observables = item.get("observables", [])
    if not isinstance(observables, list):
        observables = []
    return {
        "id": str(item.get("id", f"dataset_{index}")),
        "phenomenon": item.get("phenomenon"),
        "status": status,
        "confidence": _normalize_confidence(item.get("confidence")),
        "observable_count": len(observables),
        "source": item.get("source"),
    }


def _normalize_confidence(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in CONFIDENCE_LEVELS:
        return normalized
    return None


def _infer_benchmark_targets_for_validation(target: dict[str, Any]) -> list[str]:
    benchmark_target_ids = target.get("benchmark_target_ids")
    if isinstance(benchmark_target_ids, list) and benchmark_target_ids:
        return [str(value) for value in benchmark_target_ids if str(value).strip()]
    metric = str(target.get("metric", ""))
    if metric == "keff":
        return ["expected_keff_band"]
    return []


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return numerator / denominator


def _assess_validation_maturity(benchmark: dict[str, Any], targets: list[dict[str, Any]]) -> dict[str, Any]:
    validation = benchmark.get("validation", {})
    if not isinstance(validation, dict):
        validation = {}

    operating_point_source = validation.get("operating_point_source", {})
    if not isinstance(operating_point_source, dict):
        operating_point_source = {"status": str(operating_point_source)} if operating_point_source else {}
    operating_point_status = str(operating_point_source.get("status", "missing")).lower()
    if operating_point_status not in OPERATING_POINT_STATUSES:
        operating_point_status = "missing"

    cross_code_checks_raw = validation.get("cross_code_checks", [])
    if not isinstance(cross_code_checks_raw, list):
        cross_code_checks_raw = []
    cross_code_checks = [_normalize_cross_code_check(index, item) for index, item in enumerate(cross_code_checks_raw, start=1)]
    completed_cross_code = sum(1 for item in cross_code_checks if item["status"] == "completed")

    uncertainty_coverage = validation.get("uncertainty_coverage", {})
    if not isinstance(uncertainty_coverage, dict):
        uncertainty_coverage = {"status": str(uncertainty_coverage)} if uncertainty_coverage else {}
    uncertainty_status = str(uncertainty_coverage.get("status", "missing")).lower()
    if uncertainty_status not in UNCERTAINTY_COVERAGE_STATUSES:
        uncertainty_status = "missing"

    literature_target_fraction = _ratio(
        sum(1 for item in targets if item["status"] == "literature-backed"),
        len(targets),
    )
    operating_point_score = {
        "cross-code-backed": 1.0,
        "literature-backed": 1.0,
        "surrogate": 0.25,
        "missing": 0.0,
    }[operating_point_status]
    uncertainty_score = {
        "quantified": 1.0,
        "partial": 0.75,
        "qualitative": 0.5,
        "missing": 0.0,
    }[uncertainty_status]
    cross_code_score = _ratio(completed_cross_code, len(cross_code_checks)) if cross_code_checks else 0.0

    validation_maturity_score = round(
        100.0
        * (
            0.35 * operating_point_score
            + 0.25 * cross_code_score
            + 0.20 * uncertainty_score
            + 0.20 * literature_target_fraction
        ),
        1,
    )
    stage = "surrogate_only"
    if validation_maturity_score >= 40.0:
        stage = "screening_backed"
    if validation_maturity_score >= 75.0:
        stage = "benchmark_ready"

    gaps: list[str] = []
    if operating_point_status in {"missing", "surrogate"}:
        gaps.append("Benchmark operating point is not yet literature-backed.")
    if not cross_code_checks:
        gaps.append("No cross-code validation checks are declared.")
    elif completed_cross_code < len(cross_code_checks):
        gaps.append("Some cross-code validation checks are still pending.")
    if uncertainty_status == "missing":
        gaps.append("Benchmark uncertainty coverage is missing.")
    elif uncertainty_status in {"partial", "qualitative"}:
        gaps.append("Benchmark uncertainty coverage is not yet fully quantified.")
    surrogate_target_count = sum(1 for item in targets if item["status"] == "surrogate")
    if surrogate_target_count:
        gaps.append(f"{surrogate_target_count} validation target(s) still depend on surrogate values.")

    return {
        "validation_maturity_score": validation_maturity_score,
        "validation_maturity_stage": stage,
        "operating_point_source": {
            "status": operating_point_status,
            "note": operating_point_source.get("note"),
        },
        "cross_code_checks": cross_code_checks,
        "uncertainty_coverage": {
            "status": uncertainty_status,
            "note": uncertainty_coverage.get("note"),
        },
        "gaps": gaps,
    }


def _assess_benchmark_quality(
    config: CaseConfig | dict[str, Any],
    benchmark: dict[str, Any],
    targets: list[dict[str, Any]],
    validation_maturity: dict[str, Any],
) -> dict[str, Any]:
    data = _extract_config_data(config)
    reactor = data.get("reactor", {}) if isinstance(data.get("reactor", {}), dict) else {}
    benchmark_model = data.get("benchmark_model", {}) if isinstance(data.get("benchmark_model", {}), dict) else {}
    mode = str(reactor.get("mode", ""))
    stage = str(reactor.get("stage", ""))
    quality_gates = benchmark.get("quality_gates", [])
    source_dossier = benchmark.get("source_dossier", {})
    has_quality_metadata = bool(source_dossier) or bool(quality_gates)
    if mode != "historic_benchmark" and stage != "benchmark" and not has_quality_metadata:
        return {}

    gates: list[dict[str, Any]] = []

    source_dossier_complete = isinstance(source_dossier, dict) and all(
        str(source_dossier.get(key, "")).strip()
        for key in ("source_index", "parameter_table", "assumption_log")
    )
    gates.append(
        _quality_gate(
            "source_dossier_declared",
            source_dossier_complete,
            "Benchmark source index, parameter table, and assumption log are declared."
            if source_dossier_complete
            else "Benchmark source dossier is missing source_index, parameter_table, or assumption_log.",
        )
    )

    placeholder_targets = [
        item["id"]
        for item in targets
        if item.get("status") == "surrogate" or _contains_placeholder_language(item.get("note"))
    ]
    gates.append(
        _quality_gate(
            "no_placeholder_targets",
            not placeholder_targets,
            "No benchmark targets are marked surrogate or placeholder."
            if not placeholder_targets
            else "Placeholder or surrogate target(s) remain: " + ", ".join(placeholder_targets) + ".",
        )
    )

    has_uncertainty_target = any(_coerce_float(item.get("uncertainty_pcm")) is not None for item in targets)
    gates.append(
        _quality_gate(
            "keff_uncertainty_declared",
            has_uncertainty_target,
            "At least one benchmark target declares numerical uncertainty in pcm."
            if has_uncertainty_target
            else "No benchmark target declares numerical uncertainty in pcm.",
        )
    )

    propagated_uncertainty = validation_maturity.get("uncertainty_coverage", {}).get("status") == "quantified"
    gates.append(
        _quality_gate(
            "uncertainty_propagated",
            propagated_uncertainty,
            "Benchmark uncertainty coverage is quantified."
            if propagated_uncertainty
            else "Benchmark uncertainty coverage is not yet fully quantified.",
        )
    )

    completed_cross_code = [
        item
        for item in validation_maturity.get("cross_code_checks", [])
        if item.get("status") == "completed"
    ]
    declared_cross_code = validation_maturity.get("cross_code_checks", [])
    gates.append(
        _quality_gate(
            "cross_code_completed",
            bool(declared_cross_code) and len(completed_cross_code) == len(declared_cross_code),
            "All declared cross-code checks are complete."
            if declared_cross_code and len(completed_cross_code) == len(declared_cross_code)
            else "One or more declared cross-code checks are not complete.",
        )
    )

    geometry_ready = benchmark_model.get("geometry_fidelity") == "benchmark_reconstruction"
    gates.append(
        _quality_gate(
            "benchmark_geometry_reconstructed",
            geometry_ready,
            "Case geometry is marked as a benchmark reconstruction."
            if geometry_ready
            else "Case geometry is not marked as a benchmark reconstruction.",
        )
    )

    materials_ready = benchmark_model.get("material_fidelity") == "source_indexed_isotopic"
    gates.append(
        _quality_gate(
            "source_indexed_materials",
            materials_ready,
            "Case materials are marked source-indexed and isotopic."
            if materials_ready
            else "Case materials are not marked source-indexed and isotopic.",
        )
    )

    solver_ready = benchmark_model.get("solver_statistics") == "published_solver_bundle"
    gates.append(
        _quality_gate(
            "solver_bundle_published",
            solver_ready,
            "A solver-backed benchmark bundle is marked published."
            if solver_ready
            else "A solver-backed benchmark bundle has not been marked published.",
        )
    )

    if isinstance(quality_gates, list):
        for index, gate in enumerate(quality_gates, start=1):
            if not isinstance(gate, dict):
                continue
            status = str(gate.get("status", "missing")).lower()
            gate_id = str(gate.get("id", f"quality_gate_{index}"))
            gates.append(
                _quality_gate(
                    f"declared::{gate_id}",
                    status in QUALITY_GATE_PASS_STATUSES,
                    str(gate.get("note") or f"Declared gate status is {status}."),
                )
            )

    passed_count = sum(1 for gate in gates if gate["status"] == "pass")
    failed_gates = [gate for gate in gates if gate["status"] == "fail"]
    score = round(100.0 * _ratio(passed_count, len(gates)), 1)
    benchmark_ready = not failed_gates
    stage_name = "benchmark_ready" if benchmark_ready else "benchmark_blocked"
    if mode != "historic_benchmark" and stage != "benchmark":
        stage_name = "screening_or_context"

    return {
        "quality_score": score,
        "quality_stage": stage_name,
        "benchmark_ready": benchmark_ready,
        "passed_gate_count": passed_count,
        "failed_gate_count": len(failed_gates),
        "gates": gates,
        "promotion_blockers": [gate["message"] for gate in failed_gates],
    }


def _quality_gate(gate_id: str, passed: bool, message: str) -> dict[str, Any]:
    return {
        "id": gate_id,
        "status": "pass" if passed else "fail",
        "message": message,
    }


def _contains_placeholder_language(value: Any) -> bool:
    if not value:
        return False
    lowered = str(value).lower()
    return "placeholder" in lowered or "surrogate" in lowered


def _normalize_cross_code_check(index: int, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "name": f"cross_code_check_{index}",
            "status": "missing",
            "note": str(item),
        }
    status = str(item.get("status", "missing")).lower()
    if status not in {"planned", "completed", "missing"}:
        status = "missing"
    return {
        "name": str(item.get("name", f"cross_code_check_{index}")),
        "status": status,
        "note": item.get("note"),
    }


def build_benchmark_residuals(
    config: CaseConfig | dict[str, Any],
    summary: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> dict[str, Any]:
    benchmark = benchmark or {}
    _, validation_targets = _extract_config_parts(config)
    items: list[dict[str, Any]] = []
    for name, target in validation_targets.items():
        if not isinstance(target, dict):
            continue
        items.append(_build_residual_item(name, target, summary, benchmark))
    return {
        "item_count": len(items),
        "items": items,
        "dataset_count": len(benchmark.get("datasets", [])) if isinstance(benchmark.get("datasets"), list) else 0,
    }


def evaluate_validation_target(
    name: str,
    target: dict[str, Any],
    metrics: dict[str, Any],
    manifest: dict[str, Any],
    benchmark: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a validation target with the same residual rules used in reports."""

    summary = {"metrics": metrics}
    item = _build_residual_item(name, target, summary, benchmark or {}, manifest=manifest)
    metric = item.get("metric", "metric")
    if item["status"] == "pending":
        return {
            "name": name,
            "status": "pending",
            "message": item.get("message", f"Metric '{metric}' is not available yet."),
        }
    if item.get("normalized_residual") is not None:
        message = (
            f"{item['value']} gives residual {item['residual_pcm']} pcm, "
            f"normalized residual {item['normalized_residual']} against "
            f"{item['combined_uncertainty_pcm']} pcm combined uncertainty."
        )
    elif item.get("min") is not None or item.get("max") is not None:
        message = item.get("message", f"{item['value']} is within the expected range.")
    else:
        message = item.get("message", f"{metric} target evaluated.")
    return {
        "name": name,
        "status": item["status"],
        "message": message,
    }


def _build_residual_item(
    name: str,
    target: dict[str, Any],
    summary: dict[str, Any],
    benchmark: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_name = str(target.get("source", "metrics"))
    source = _resolve_metric_source(source_name, summary, manifest)
    metric = target.get("metric")
    value = source.get(metric)
    benchmark_target_ids = _infer_benchmark_targets_for_validation(target)
    benchmark_targets = _benchmark_target_map(benchmark)
    reference = _resolve_reference_target(target, benchmark_target_ids, benchmark_targets)
    minimum = target.get("min")
    maximum = target.get("max")
    item: dict[str, Any] = {
        "name": name,
        "metric": metric,
        "value": value,
        "min": minimum,
        "max": maximum,
        "center": None,
        "residual": None,
        "status": "pending",
        "benchmark_target_ids": benchmark_target_ids,
    }
    if reference:
        item["target_value"] = reference.get("value")
        item["target_units"] = reference.get("units")
        item["benchmark_uncertainty_pcm"] = reference.get("uncertainty_pcm")
        item["reference_source"] = reference.get("source")

    if value is None:
        item["message"] = f"Metric '{metric}' is not available yet."
        return item

    numeric_value = _coerce_float(value)
    if numeric_value is None:
        item["message"] = f"Metric '{metric}' is not numeric."
        return item

    reference_value = _coerce_float(reference.get("value") if reference else target.get("value"))
    if reference_value is not None:
        residual = numeric_value - reference_value
        item["center"] = reference_value
        item["residual"] = round(residual, 8)
        item["residual_pcm"] = round(residual * PCM_PER_DELTA_K, 3)
        calculated_uncertainty_pcm = _resolve_calculated_uncertainty_pcm(target, summary, source)
        benchmark_uncertainty_pcm = _coerce_float(reference.get("uncertainty_pcm") if reference else None)
        if benchmark_uncertainty_pcm is None:
            benchmark_uncertainty_pcm = _coerce_float(target.get("uncertainty_pcm"))
        if calculated_uncertainty_pcm is not None:
            item["calculated_uncertainty_pcm"] = round(calculated_uncertainty_pcm, 3)
        if benchmark_uncertainty_pcm is not None:
            item["benchmark_uncertainty_pcm"] = round(benchmark_uncertainty_pcm, 3)
        combined_uncertainty = _combine_uncertainty_pcm(calculated_uncertainty_pcm, benchmark_uncertainty_pcm)
        sigma_multiplier = _coerce_float(target.get("sigma_multiplier")) or 2.0
        item["sigma_multiplier"] = sigma_multiplier
        if combined_uncertainty is not None and combined_uncertainty > 0.0:
            normalized = item["residual_pcm"] / combined_uncertainty
            item["combined_uncertainty_pcm"] = round(combined_uncertainty, 3)
            item["normalized_residual"] = round(normalized, 3)
            item["status"] = "pass" if abs(normalized) <= sigma_multiplier else "fail"
            item["message"] = (
                f"{numeric_value} residual is {item['residual_pcm']} pcm against "
                f"{round(combined_uncertainty, 3)} pcm combined uncertainty."
            )
            return item
        item["message"] = "Point target is available, but uncertainty is missing."
        item["status"] = "pending"
        return item

    if minimum is not None and maximum is not None:
        item["center"] = (float(minimum) + float(maximum)) / 2.0
        item["residual"] = round(numeric_value - item["center"], 8)
    item["status"] = "pass"
    if minimum is not None and numeric_value < float(minimum):
        item["status"] = "fail"
        item["message"] = f"{numeric_value} is below the minimum bound {minimum}."
    elif maximum is not None and numeric_value > float(maximum):
        item["status"] = "fail"
        item["message"] = f"{numeric_value} is above the maximum bound {maximum}."
    else:
        item["message"] = f"{numeric_value} is within the expected range."
    return item


def _resolve_metric_source(
    source_name: str,
    summary: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if source_name == "metrics":
        return summary.get("metrics", {})
    if source_name == "manifest":
        return manifest or {}
    source = summary.get(source_name)
    if isinstance(source, dict):
        return source
    if manifest and source_name in manifest and isinstance(manifest[source_name], dict):
        return manifest[source_name]
    return manifest or {}


def _benchmark_target_map(benchmark: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in _collect_benchmark_targets(benchmark)}


def _resolve_reference_target(
    validation_target: dict[str, Any],
    target_ids: list[str],
    benchmark_targets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if validation_target.get("value") is not None:
        return {
            "value": validation_target.get("value"),
            "units": validation_target.get("units"),
            "uncertainty_pcm": validation_target.get("uncertainty_pcm"),
            "source": "validation_target",
        }
    for target_id in target_ids:
        benchmark_target = benchmark_targets.get(target_id)
        if benchmark_target and benchmark_target.get("value") is not None:
            resolved = dict(benchmark_target)
            resolved["source"] = target_id
            return resolved
    return {}


def _resolve_calculated_uncertainty_pcm(
    target: dict[str, Any],
    summary: dict[str, Any],
    source: dict[str, Any],
) -> float | None:
    direct = _coerce_float(target.get("calculated_uncertainty_pcm"))
    if direct is not None:
        return direct
    metric_name = target.get("calculated_uncertainty_metric")
    metric_value = None
    if metric_name:
        metric_value = source.get(metric_name)
        if metric_value is None:
            metric_value = summary.get("metrics", {}).get(metric_name)
    if metric_value is None and target.get("metric"):
        metric_value = source.get(f"{target['metric']}_std_dev")
        if metric_value is None:
            metric_value = summary.get("metrics", {}).get(f"{target['metric']}_std_dev")
    uncertainty = _coerce_float(metric_value)
    if uncertainty is None:
        return None
    return abs(uncertainty) * PCM_PER_DELTA_K


def _combine_uncertainty_pcm(*values: float | None) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return sum(value * value for value in numeric) ** 0.5


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
