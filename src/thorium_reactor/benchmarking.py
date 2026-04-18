from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from thorium_reactor.config import CaseConfig


CONFIDENCE_LEVELS = ("high", "medium", "low")
REACTOR_TARGET_LINKS = (
    ("design_power_mwth", "nominal_thermal_power_mwth", "design thermal power"),
    ("hot_leg_temp_c", "nominal_hot_leg_temp_c", "hot-leg temperature"),
    ("cold_leg_temp_c", "nominal_cold_leg_temp_c", "cold-leg temperature"),
)
OPERATING_POINT_STATUSES = {"literature-backed", "cross-code-backed", "surrogate"}
UNCERTAINTY_COVERAGE_STATUSES = {"quantified", "qualitative", "missing"}


def assess_benchmark_traceability(
    config: CaseConfig | dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> dict[str, Any]:
    benchmark = benchmark or {}
    reactor, validation_targets = _extract_config_parts(config)

    evidence = [_normalize_evidence(item, index) for index, item in enumerate(benchmark.get("evidence", []), start=1)]
    assumptions = [_normalize_assumption(item, index) for index, item in enumerate(benchmark.get("assumptions", []), start=1)]
    targets = [_normalize_target(name, spec) for name, spec in (benchmark.get("targets") or {}).items()]

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
        "assumptions": assumptions,
        "targets": targets,
        "evidence": evidence,
    }


def run_solver_backed_benchmark(
    repo_root: Path,
    case_name: str,
    run_id: str,
) -> dict[str, Any]:
    docker_executable = shutil.which("docker")
    if docker_executable is None:
        raise RuntimeError(
            "Solver-backed benchmark runs require Docker on this host. "
            "Install Docker Desktop or use a supported OpenMC runtime."
        )

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


def build_docker_openmc_command(case_name: str, run_id: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        "docker-compose.openmc.yml",
        "run",
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
    }


def _normalize_confidence(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in CONFIDENCE_LEVELS:
        return normalized
    return None


def _infer_benchmark_targets_for_validation(target: dict[str, Any]) -> list[str]:
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
    elif uncertainty_status == "qualitative":
        gaps.append("Benchmark uncertainty coverage is qualitative rather than quantified.")
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
