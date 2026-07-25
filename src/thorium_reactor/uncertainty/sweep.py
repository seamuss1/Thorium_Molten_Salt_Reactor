from __future__ import annotations

import copy
import json
import math
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy import stats
from scipy.stats import qmc

from thorium_reactor.benchmarking import PCM_PER_DELTA_K, build_benchmark_residuals
from thorium_reactor.config import CaseConfig, ConfigError, load_case_config, load_yaml
from thorium_reactor.paths import ResultBundle, safe_path_segment

DEFAULT_UNCERTAINTY_SWEEP_MODEL = "solver_backed_geometry_material_uq_v1"
DEFAULT_UNCERTAINTY_SWEEP_SAMPLES = 32
SUPPORTED_OPERATIONS = {
    "material_density_relative",
    "nuclide_atom_fraction_relative",
    "geometry_scalar_relative",
    "radial_layer_thickness_relative",
}
PLOT_MANIFEST_NAME = "plots_manifest.json"


@dataclass(frozen=True, slots=True)
class UncertaintyParameter:
    id: str
    label: str
    operation: str
    category: str
    units: str
    sigma: float
    mean: float
    lower: float
    upper: float
    source_backed: bool
    required_for_benchmark_ready: bool
    metadata: dict[str, Any]


SampleExecutor = Callable[[CaseConfig, ResultBundle, dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]


def run_uncertainty_sweep_case(
    repo_root: Path,
    config: CaseConfig,
    bundle: ResultBundle,
    benchmark: dict[str, Any],
    *,
    samples: int = DEFAULT_UNCERTAINTY_SWEEP_SAMPLES,
    seed: int = 42,
    sampler: str = "sobol",
    max_parallel: int = 1,
    resume: bool = False,
    require_source_backed: bool = False,
    provenance: dict[str, Any] | None = None,
    executor: SampleExecutor | None = None,
) -> dict[str, Any]:
    """Run a solver-backed geometry/material uncertainty sweep in child bundles."""

    settings = _uncertainty_settings(config)
    inputs_path = _resolve_uncertainty_inputs_path(repo_root, config, settings)
    inputs = load_yaml(inputs_path)
    parameters = _resolve_enabled_parameters(inputs, settings)
    if not parameters:
        raise ConfigError(f"Case {config.name} uncertainty_sweep has no enabled parameters.")
    if require_source_backed:
        missing = [parameter.id for parameter in parameters if not parameter.source_backed]
        if missing:
            raise ConfigError(
                "Source-backed uncertainty sweep was requested, but these enabled parameters are not source-backed: "
                + ", ".join(missing)
                + "."
            )

    sample_count = max(int(samples), 1)
    worker_count = max(int(max_parallel), 1)
    sample_definitions = build_uncertainty_samples(
        parameters,
        samples=sample_count,
        seed=int(seed),
        sampler=sampler,
    )
    manifest = _build_manifest(
        config=config,
        inputs_path=inputs_path,
        inputs=inputs,
        parameters=parameters,
        sample_definitions=sample_definitions,
        samples=sample_count,
        seed=int(seed),
        sampler=sampler,
        max_parallel=worker_count,
        require_source_backed=require_source_backed,
        provenance=provenance,
    )
    bundle.write_json("uncertainty_manifest.json", manifest)
    bundle.write_json("uncertainty_samples.json", {"samples": sample_definitions})

    sample_executor = executor or _run_openmc_sample
    started = time.perf_counter()
    if worker_count == 1:
        results = [
            _execute_sample(
                repo_root,
                config,
                bundle,
                benchmark,
                sample,
                parameters,
                sample_executor,
                resume=resume,
                provenance=provenance,
            )
            for sample in sample_definitions
        ]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(
                    _execute_sample,
                    repo_root,
                    config,
                    bundle,
                    benchmark,
                    sample,
                    parameters,
                    sample_executor,
                    resume=resume,
                    provenance=provenance,
                )
                for sample in sample_definitions
            ]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: int(item.get("index", 0)))

    runtime_s = time.perf_counter() - started
    result_payload = {
        "case": config.name,
        "model": DEFAULT_UNCERTAINTY_SWEEP_MODEL,
        "runtime_s": round(runtime_s, 6),
        "results": results,
    }
    bundle.write_json("uncertainty_results.json", result_payload)

    budget = build_uncertainty_budget(
        config,
        benchmark,
        parameters,
        sample_definitions,
        results,
        runtime_s=runtime_s,
    )
    budget_path = bundle.write_json("uncertainty_budget.json", budget)
    uncertainty_summary = uncertainty_sweep_summary(budget, budget_path=str(budget_path))
    bundle.write_json("uncertainty_summary.json", uncertainty_summary)
    plot_assets = _write_uncertainty_plots(bundle, budget)
    if plot_assets:
        uncertainty_summary["plot_assets"] = plot_assets
        bundle.write_json("uncertainty_summary.json", uncertainty_summary)

    root_summary = _build_root_summary(config, bundle, benchmark, results, budget, uncertainty_summary, provenance)
    bundle.write_json("summary.json", root_summary)
    bundle.write_json("benchmark_residuals.json", root_summary.get("benchmark_residuals", {}))
    if root_summary.get("benchmark_quality"):
        bundle.write_json("benchmark_quality.json", root_summary["benchmark_quality"])
    bundle.write_metrics(root_summary.get("metrics", {}))
    return root_summary


def build_uncertainty_samples(
    parameters: Iterable[UncertaintyParameter],
    *,
    samples: int,
    seed: int,
    sampler: str = "sobol",
) -> list[dict[str, Any]]:
    parameter_list = list(parameters)
    if sampler != "sobol":
        raise ValueError(f"Unsupported uncertainty sampler: {sampler}.")
    if not parameter_list:
        return []
    sample_count = max(int(samples), 1)
    definitions: list[dict[str, Any]] = [
        _sample_definition("nominal", 0, "nominal", parameter_list, {parameter.id: 0.0 for parameter in parameter_list})
    ]
    for parameter in parameter_list:
        definitions.append(
            _sample_definition(
                f"oat_{parameter.id}_minus",
                len(definitions),
                "oat_minus",
                parameter_list,
                {item.id: (-1.0 if item.id == parameter.id else 0.0) for item in parameter_list},
            )
        )
        definitions.append(
            _sample_definition(
                f"oat_{parameter.id}_plus",
                len(definitions),
                "oat_plus",
                parameter_list,
                {item.id: (1.0 if item.id == parameter.id else 0.0) for item in parameter_list},
            )
        )

    engine = qmc.Sobol(d=len(parameter_list), scramble=True, seed=int(seed))
    if sample_count > 0 and sample_count & (sample_count - 1) == 0:
        unit_samples = engine.random_base2(m=int(math.log2(sample_count)))
    else:
        unit_samples = engine.random(n=sample_count)
    unit_samples = np.clip(unit_samples, 1.0e-9, 1.0 - 1.0e-9)
    z_values = stats.norm.ppf(unit_samples)
    for row_index, row in enumerate(z_values, start=1):
        standardized = {
            parameter.id: float(np.clip(row[column], -3.0, 3.0)) for column, parameter in enumerate(parameter_list)
        }
        definitions.append(
            _sample_definition(
                f"sobol_{row_index:04d}",
                len(definitions),
                "sobol",
                parameter_list,
                standardized,
            )
        )
    return definitions


def build_uncertainty_budget(
    config: CaseConfig,
    benchmark: dict[str, Any],
    parameters: Iterable[UncertaintyParameter],
    sample_definitions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    runtime_s: float = 0.0,
) -> dict[str, Any]:
    parameter_list = list(parameters)
    result_by_id = {str(result["sample_id"]): result for result in results}
    nominal = result_by_id.get("nominal", {})
    completed = [
        result
        for result in results
        if result.get("status") in {"completed", "skipped_existing"} and _coerce_float(result.get("keff")) is not None
    ]
    failed = [result for result in results if result.get("status") not in {"completed", "skipped_existing"}]
    sobol_completed = [result for result in completed if result.get("kind") == "sobol"]
    keff_values = np.array([float(result["keff"]) for result in sobol_completed], dtype=float)
    stat_values = np.array(
        [float(result.get("keff_std_dev") or 0.0) for result in sobol_completed],
        dtype=float,
    )
    target = _resolve_keff_target(config, benchmark)
    nominal_keff = _coerce_float(nominal.get("keff"))
    nominal_keff_std = _coerce_float(nominal.get("keff_std_dev")) or 0.0
    nominal_residual_pcm = None
    if nominal_keff is not None and target.get("value") is not None:
        nominal_residual_pcm = round((nominal_keff - float(target["value"])) * PCM_PER_DELTA_K, 3)

    sample_variance = float(np.var(keff_values, ddof=1)) if len(keff_values) > 1 else 0.0
    mean_stat_variance = float(np.mean(stat_values * stat_values)) if len(stat_values) else 0.0
    input_variance = max(sample_variance - mean_stat_variance, 0.0)
    input_sigma_pcm = math.sqrt(input_variance) * PCM_PER_DELTA_K
    statistical_sigma_pcm = math.sqrt(mean_stat_variance) * PCM_PER_DELTA_K
    benchmark_sigma_pcm = float(target.get("uncertainty_pcm") or 0.0)
    nominal_mc_sigma_pcm = nominal_keff_std * PCM_PER_DELTA_K
    combined_sigma_pcm = math.sqrt(
        benchmark_sigma_pcm * benchmark_sigma_pcm
        + nominal_mc_sigma_pcm * nominal_mc_sigma_pcm
        + input_sigma_pcm * input_sigma_pcm
    )

    if len(keff_values):
        interval = {
            "p02_5": round(float(np.percentile(keff_values, 2.5)), 8),
            "p50": round(float(np.percentile(keff_values, 50.0)), 8),
            "p97_5": round(float(np.percentile(keff_values, 97.5)), 8),
        }
    else:
        interval = {"p02_5": None, "p50": None, "p97_5": None}
    contributors = _dominant_contributors(parameter_list, sample_definitions, result_by_id)
    coverage = _coverage_summary(parameter_list, completed, failed)
    complete = (
        not failed
        and bool(completed)
        and result_by_id.get("nominal", {}).get("status")
        in {
            "completed",
            "skipped_existing",
        }
    )
    coverage_status = "quantified" if complete and coverage["all_required_source_backed"] else "partial"
    if coverage["assumption_backed_enabled_count"] and not coverage["source_backed_enabled_count"]:
        coverage_status = "assumption_backed"

    return {
        "case": config.name,
        "model": DEFAULT_UNCERTAINTY_SWEEP_MODEL,
        "status": "completed" if complete else "incomplete",
        "runtime_s": round(float(runtime_s), 6),
        "sample_count": len(sample_definitions),
        "completed_sample_count": len(completed),
        "failed_sample_count": len(failed),
        "failed_sample_ids": [str(result["sample_id"]) for result in failed],
        "coverage": {
            **coverage,
            "status": coverage_status,
            "required_for_benchmark_ready": [
                parameter.id for parameter in parameter_list if parameter.required_for_benchmark_ready
            ],
        },
        "target": target,
        "observables": {
            "keff": {
                "nominal": round(nominal_keff, 8) if nominal_keff is not None else None,
                "nominal_std_dev": round(nominal_keff_std, 10),
                "target": target.get("value"),
                "nominal_residual_pcm": nominal_residual_pcm,
                "input_interval": interval,
                "input_interval_width_pcm": _interval_width_pcm(interval),
                "input_sigma_pcm": round(input_sigma_pcm, 3),
                "statistical_sigma_pcm": round(statistical_sigma_pcm, 3),
                "benchmark_sigma_pcm": round(benchmark_sigma_pcm, 3),
                "nominal_mc_sigma_pcm": round(nominal_mc_sigma_pcm, 3),
                "combined_uncertainty_pcm": round(combined_sigma_pcm, 3),
                "normalized_residual_with_input": (
                    round(nominal_residual_pcm / combined_sigma_pcm, 4)
                    if nominal_residual_pcm is not None and combined_sigma_pcm > 0.0
                    else None
                ),
            }
        },
        "uncertainty_categories": {
            "input": {
                "status": coverage_status,
                "sigma_pcm": round(input_sigma_pcm, 3),
                "source_backed_parameter_count": coverage["source_backed_enabled_count"],
                "assumption_backed_parameter_count": coverage["assumption_backed_enabled_count"],
            },
            "statistical": {
                "status": "quantified" if completed else "missing",
                "sigma_pcm": round(statistical_sigma_pcm, 3),
            },
            "model_form": {
                "status": "blocked",
                "note": "Model-form uncertainty remains blocked by the illustrative MSRE geometry/material reconstruction.",
            },
            "nuclear_data": {
                "status": "not_propagated",
                "note": "Nuclear-data covariance sampling is not included in the v1 geometry/material sweep.",
            },
            "cross_code": {
                "status": "not_available",
                "note": "Cross-code uncertainty requires the separate OpenMC-vs-Serpent/SCALE comparison artifact.",
            },
        },
        "dominant_contributors": contributors,
    }


def uncertainty_sweep_summary(budget: dict[str, Any], *, budget_path: str) -> dict[str, Any]:
    keff = budget.get("observables", {}).get("keff", {})
    return {
        "status": budget.get("status", "incomplete"),
        "model": DEFAULT_UNCERTAINTY_SWEEP_MODEL,
        "sample_count": budget.get("sample_count", 0),
        "completed_sample_count": budget.get("completed_sample_count", 0),
        "failed_sample_count": budget.get("failed_sample_count", 0),
        "coverage_status": budget.get("coverage", {}).get("status", "missing"),
        "nominal_keff": keff.get("nominal"),
        "nominal_residual_pcm": keff.get("nominal_residual_pcm"),
        "input_interval_width_pcm": keff.get("input_interval_width_pcm"),
        "input_sigma_pcm": keff.get("input_sigma_pcm"),
        "statistical_sigma_pcm": keff.get("statistical_sigma_pcm"),
        "combined_uncertainty_pcm": keff.get("combined_uncertainty_pcm"),
        "normalized_residual_with_input": keff.get("normalized_residual_with_input"),
        "dominant_contributors": budget.get("dominant_contributors", [])[:5],
        "budget_path": budget_path,
    }


def build_docker_uncertainty_sweep_command(
    case_name: str,
    run_id: str,
    *,
    samples: int,
    seed: int,
    sampler: str,
    max_parallel: int,
    resume: bool,
    require_source_backed: bool,
) -> list[str]:
    command = [
        "docker",
        "compose",
        "run",
        "--build",
        "--rm",
        "openmc",
        "python",
        "-m",
        "thorium_reactor.cli",
        "uncertainty-sweep",
        case_name,
        "--run-id",
        run_id,
        "--reuse-run-id",
        "--samples",
        str(int(samples)),
        "--seed",
        str(int(seed)),
        "--sampler",
        sampler,
        "--max-parallel",
        str(int(max_parallel)),
    ]
    if resume:
        command.append("--resume")
    if require_source_backed:
        command.append("--require-source-backed")
    return command


def run_docker_uncertainty_sweep(
    repo_root: Path,
    case_name: str,
    run_id: str,
    *,
    samples: int,
    seed: int,
    sampler: str,
    max_parallel: int,
    resume: bool,
    require_source_backed: bool,
) -> dict[str, Any]:
    command = build_docker_uncertainty_sweep_command(
        case_name,
        run_id,
        samples=samples,
        seed=seed,
        sampler=sampler,
        max_parallel=max_parallel,
        resume=resume,
        require_source_backed=require_source_backed,
    )
    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "runtime": "docker-openmc",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "status": "completed" if completed.returncode == 0 else "failed",
    }


def _execute_sample(
    repo_root: Path,
    config: CaseConfig,
    root_bundle: ResultBundle,
    benchmark: dict[str, Any],
    sample: dict[str, Any],
    parameters: list[UncertaintyParameter],
    executor: SampleExecutor,
    *,
    resume: bool,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    child_bundle = _child_bundle(root_bundle, str(sample["id"]))
    summary_path = child_bundle.root / "summary.json"
    if resume and summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
        if summary.get("metrics", {}).get("keff") is not None:
            return _sample_result(sample, child_bundle, summary, status="skipped_existing")

    perturbed_data = apply_sample_to_case(config.data, parameters, sample)
    snapshot_path = child_bundle.root / "case_snapshot.yaml"
    snapshot_path.write_text(yaml.safe_dump(perturbed_data, sort_keys=False), encoding="utf-8")
    if benchmark:
        child_bundle.write_text("benchmark_snapshot.yaml", yaml.safe_dump(benchmark, sort_keys=False))
    sample_provenance = {
        "parent_run_id": root_bundle.run_id,
        "sample_id": sample["id"],
        "sample_kind": sample["kind"],
        "sample_index": sample["index"],
        "perturbations": sample["values"],
        "source_case_path": str(config.path),
        "source_benchmark_path": provenance.get("source_benchmark_path") if provenance else None,
    }
    child_bundle.write_json("provenance.json", sample_provenance)
    try:
        sample_config = load_case_config(snapshot_path)
        summary = executor(sample_config, child_bundle, benchmark, sample, sample_provenance)
        return _sample_result(sample, child_bundle, summary)
    except Exception as exc:  # pragma: no cover - real solver failures are runtime-dependent
        return {
            "index": sample["index"],
            "sample_id": sample["id"],
            "kind": sample["kind"],
            "status": "failed",
            "bundle_path": str(child_bundle.root),
            "error": str(exc),
            "values": sample["values"],
            "standardized": sample["standardized"],
        }


def apply_sample_to_case(
    case_data: Mapping[str, Any],
    parameters: Iterable[UncertaintyParameter],
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    data = copy.deepcopy(dict(case_data))
    values = sample.get("values", {})
    parameter_lookup = {parameter.id: parameter for parameter in parameters}
    for parameter_id, value in values.items():
        parameter = parameter_lookup[str(parameter_id)]
        _apply_parameter(data, parameter, float(value))
    data["uncertainty_sample"] = {
        "id": sample.get("id"),
        "kind": sample.get("kind"),
        "values": copy.deepcopy(values),
        "standardized": copy.deepcopy(sample.get("standardized", {})),
    }
    return data


def _apply_parameter(data: dict[str, Any], parameter: UncertaintyParameter, value: float) -> None:
    if parameter.operation == "material_density_relative":
        material = _require_mapping(data.get("materials", {}), parameter.metadata.get("material"), "material")
        density = _require_mapping(material, "density", "density")
        _scale_property_spec(density, value)
        return
    if parameter.operation == "nuclide_atom_fraction_relative":
        material = _require_mapping(data.get("materials", {}), parameter.metadata.get("material"), "material")
        nuclides = material.get("nuclides")
        if not isinstance(nuclides, list) or not nuclides:
            raise ConfigError(f"Parameter {parameter.id} requires material nuclides.")
        target_name = str(parameter.metadata.get("nuclide", ""))
        found = False
        for nuclide in nuclides:
            if isinstance(nuclide, dict) and str(nuclide.get("name")) == target_name:
                nuclide["ao"] = float(nuclide["ao"]) * value
                found = True
                break
        if not found:
            raise ConfigError(f"Parameter {parameter.id} could not find nuclide {target_name}.")
        total = sum(float(item.get("ao", 0.0)) for item in nuclides if isinstance(item, dict))
        if total <= 0.0:
            raise ConfigError(f"Parameter {parameter.id} produced non-positive total atom fraction.")
        for nuclide in nuclides:
            if isinstance(nuclide, dict):
                nuclide["ao"] = float(nuclide.get("ao", 0.0)) / total
        return
    if parameter.operation == "geometry_scalar_relative":
        path = str(parameter.metadata.get("path") or parameter.metadata.get("field") or "")
        if not path:
            raise ConfigError(f"Parameter {parameter.id} requires a geometry scalar path.")
        _scale_path_value(data, path, value)
        return
    if parameter.operation == "radial_layer_thickness_relative":
        _scale_radial_layer_thickness(data, parameter, value)
        return
    raise ConfigError(f"Unsupported uncertainty operation {parameter.operation}.")


def _run_openmc_sample(
    config: CaseConfig,
    bundle: ResultBundle,
    benchmark: dict[str, Any],
    sample: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    from thorium_reactor.neutronics.workflows import run_case

    return run_case(
        config,
        bundle,
        benchmark=benchmark,
        solver_enabled=True,
        provenance=provenance,
    )


def _build_root_summary(
    config: CaseConfig,
    bundle: ResultBundle,
    benchmark: dict[str, Any],
    results: list[dict[str, Any]],
    budget: dict[str, Any],
    uncertainty_summary: dict[str, Any],
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    nominal = next((result for result in results if result.get("sample_id") == "nominal"), {})
    nominal_summary_path = Path(str(nominal.get("bundle_path", ""))) / "summary.json"
    if nominal_summary_path.exists():
        try:
            summary = json.loads(nominal_summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    else:
        summary = {"case": config.name, "metrics": {}}
    summary["case"] = config.name
    summary["result_dir"] = str(bundle.root)
    summary.setdefault("metrics", {})
    summary["uncertainty_sweep"] = uncertainty_summary
    summary["uncertainty_budget"] = budget
    if provenance:
        summary["input_provenance"] = copy.deepcopy(provenance)
    keff = budget.get("observables", {}).get("keff", {})
    metric_updates = {
        "uncertainty_keff_input_sigma_pcm": keff.get("input_sigma_pcm"),
        "uncertainty_keff_statistical_sigma_pcm": keff.get("statistical_sigma_pcm"),
        "uncertainty_keff_combined_uncertainty_pcm": keff.get("combined_uncertainty_pcm"),
        "uncertainty_keff_input_interval_width_pcm": keff.get("input_interval_width_pcm"),
        "uncertainty_source_backed_parameter_count": budget.get("coverage", {}).get("source_backed_enabled_count"),
        "uncertainty_assumption_backed_parameter_count": budget.get("coverage", {}).get(
            "assumption_backed_enabled_count"
        ),
    }
    summary["metrics"].update({key: value for key, value in metric_updates.items() if value is not None})
    residuals = build_benchmark_residuals(config, summary, benchmark)
    _add_input_uncertainty_to_residuals(residuals, keff)
    summary["benchmark_residuals"] = residuals
    _update_benchmark_quality(summary, budget)
    return summary


def _add_input_uncertainty_to_residuals(residuals: dict[str, Any], keff_budget: dict[str, Any]) -> None:
    input_sigma = _coerce_float(keff_budget.get("input_sigma_pcm"))
    if input_sigma is None:
        return
    for item in residuals.get("items", []):
        if item.get("metric") != "keff":
            continue
        item["input_uncertainty_pcm"] = round(input_sigma, 3)
        base_combined = _coerce_float(item.get("combined_uncertainty_pcm")) or 0.0
        combined = math.sqrt(base_combined * base_combined + input_sigma * input_sigma)
        item["combined_with_input_uncertainty_pcm"] = round(combined, 3)
        residual_pcm = _coerce_float(item.get("residual_pcm"))
        if residual_pcm is not None and combined > 0.0:
            item["normalized_residual_with_input"] = round(residual_pcm / combined, 4)


def _update_benchmark_quality(summary: dict[str, Any], budget: dict[str, Any]) -> None:
    quality = summary.get("benchmark_quality") or summary.get("benchmark_traceability", {}).get("benchmark_quality")
    if not isinstance(quality, dict) or not quality.get("gates"):
        return
    coverage = budget.get("coverage", {})
    pass_uncertainty = (
        budget.get("status") == "completed"
        and coverage.get("status") == "quantified"
        and coverage.get("all_required_source_backed") is True
    )
    message = (
        "Solver-backed geometry/material uncertainty sweep completed with source-backed required parameters."
        if pass_uncertainty
        else "Solver-backed uncertainty sweep is incomplete or still depends on assumption-backed parameters."
    )
    gates = quality.get("gates", [])
    seen = False
    for gate in gates:
        if gate.get("id") == "uncertainty_propagated":
            gate["status"] = "pass" if pass_uncertainty else "fail"
            gate["message"] = message
            seen = True
            break
    if not seen:
        gates.append(
            {"id": "uncertainty_propagated", "status": "pass" if pass_uncertainty else "fail", "message": message}
        )
    passed = sum(1 for gate in gates if gate.get("status") == "pass")
    failed = [gate for gate in gates if gate.get("status") != "pass"]
    quality["passed_gate_count"] = passed
    quality["failed_gate_count"] = len(failed)
    quality["quality_score"] = round(100.0 * passed / len(gates), 1) if gates else 0.0
    quality["benchmark_ready"] = not failed
    quality["quality_stage"] = "benchmark_ready" if not failed else "benchmark_blocked"
    quality["promotion_blockers"] = [str(gate.get("message", "")) for gate in failed if gate.get("message")]
    summary["benchmark_quality"] = quality
    if isinstance(summary.get("benchmark_traceability"), dict):
        summary["benchmark_traceability"]["benchmark_quality"] = quality
    summary.setdefault("metrics", {})["benchmark_quality_score"] = quality["quality_score"]


def _sample_result(
    sample: dict[str, Any],
    child_bundle: ResultBundle,
    summary: dict[str, Any],
    *,
    status: str | None = None,
) -> dict[str, Any]:
    metrics = summary.get("metrics", {}) if isinstance(summary, dict) else {}
    neutronics = summary.get("neutronics", {}) if isinstance(summary, dict) else {}
    keff = _coerce_float(metrics.get("keff"))
    resolved_status = status or (
        "completed" if keff is not None and neutronics.get("status") == "completed" else "failed"
    )
    result = {
        "index": sample["index"],
        "sample_id": sample["id"],
        "kind": sample["kind"],
        "status": resolved_status,
        "bundle_path": str(child_bundle.root),
        "neutronics_status": neutronics.get("status"),
        "keff": keff,
        "keff_std_dev": _coerce_float(metrics.get("keff_std_dev")),
        "values": sample["values"],
        "standardized": sample["standardized"],
    }
    if resolved_status == "failed":
        result["error"] = (
            neutronics.get("error") or neutronics.get("message") or "Sample did not produce solver-backed keff."
        )
    return result


def _resolve_enabled_parameters(inputs: Mapping[str, Any], settings: Mapping[str, Any]) -> list[UncertaintyParameter]:
    raw_parameters = inputs.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ConfigError("Uncertainty inputs must define a parameters list.")
    configured_ids = settings.get("enabled_parameter_ids")
    enabled_ids = {str(item) for item in configured_ids} if isinstance(configured_ids, list) else None
    parameters = []
    for raw in raw_parameters:
        if not isinstance(raw, dict):
            continue
        parameter_id = str(raw.get("id", "")).strip()
        if not parameter_id:
            continue
        if enabled_ids is not None and parameter_id not in enabled_ids:
            continue
        if enabled_ids is None and raw.get("enabled", True) is False:
            continue
        operation = str(raw.get("operation", ""))
        if operation not in SUPPORTED_OPERATIONS:
            raise ConfigError(f"Uncertainty parameter {parameter_id} uses unsupported operation {operation}.")
        sigma = _coerce_float(raw.get("sigma_fraction"))
        if sigma is None:
            sigma = _coerce_float(raw.get("sigma"))
        if sigma is None or sigma <= 0.0:
            raise ConfigError(f"Uncertainty parameter {parameter_id} must declare positive sigma_fraction.")
        bounds = raw.get("bounds", [max(1.0 - 4.0 * sigma, 1.0e-8), 1.0 + 4.0 * sigma])
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ConfigError(f"Uncertainty parameter {parameter_id} bounds must contain two values.")
        source_backed = bool(raw.get("source_backed", False)) or str(raw.get("source_status", "")) == "source_backed"
        parameters.append(
            UncertaintyParameter(
                id=parameter_id,
                label=str(raw.get("label", parameter_id)),
                operation=operation,
                category=str(raw.get("category", "input")),
                units=str(raw.get("units", "relative")),
                sigma=float(sigma),
                mean=float(raw.get("mean", 1.0)),
                lower=float(bounds[0]),
                upper=float(bounds[1]),
                source_backed=source_backed,
                required_for_benchmark_ready=bool(raw.get("required_for_benchmark_ready", True)),
                metadata=copy.deepcopy(raw),
            )
        )
    return parameters


def _sample_definition(
    sample_id: str,
    index: int,
    kind: str,
    parameters: list[UncertaintyParameter],
    standardized: dict[str, float],
) -> dict[str, Any]:
    values = {
        parameter.id: _parameter_value(parameter, float(standardized.get(parameter.id, 0.0)))
        for parameter in parameters
    }
    return {
        "id": sample_id,
        "index": index,
        "kind": kind,
        "values": values,
        "standardized": {key: round(float(value), 8) for key, value in standardized.items()},
    }


def _parameter_value(parameter: UncertaintyParameter, z_value: float) -> float:
    value = parameter.mean + parameter.sigma * z_value
    return round(min(max(value, parameter.lower), parameter.upper), 10)


def _dominant_contributors(
    parameters: list[UncertaintyParameter],
    samples: list[dict[str, Any]],
    result_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    qmc_samples = [sample for sample in samples if sample.get("kind") == "sobol"]
    qmc_rows: list[list[float]] = []
    qmc_values: list[float] = []
    for sample in qmc_samples:
        result = result_by_id.get(str(sample["id"]), {})
        keff = _coerce_float(result.get("keff"))
        if keff is None:
            continue
        qmc_rows.append([float(sample["standardized"].get(parameter.id, 0.0)) for parameter in parameters])
        qmc_values.append(keff)
    regression_scores: dict[str, float] = {}
    if len(qmc_rows) > len(parameters):
        y_values = np.array(qmc_values, dtype=float)
        rows = np.array(qmc_rows, dtype=float)
        if len(rows) == len(y_values) and len(rows) > len(parameters):
            centered_y = (y_values - float(np.mean(y_values))) * PCM_PER_DELTA_K
            coefficients, *_ = np.linalg.lstsq(rows, centered_y, rcond=None)
            regression_scores = {parameter.id: float(coefficients[index]) for index, parameter in enumerate(parameters)}

    contributors = []
    for parameter in parameters:
        minus = result_by_id.get(f"oat_{parameter.id}_minus", {})
        plus = result_by_id.get(f"oat_{parameter.id}_plus", {})
        minus_keff = _coerce_float(minus.get("keff"))
        plus_keff = _coerce_float(plus.get("keff"))
        oat_pcm_per_sigma = None
        if minus_keff is not None and plus_keff is not None:
            oat_pcm_per_sigma = (plus_keff - minus_keff) * PCM_PER_DELTA_K / 2.0
        regression_pcm_per_sigma = regression_scores.get(parameter.id)
        ranking_score = max(abs(oat_pcm_per_sigma or 0.0), abs(regression_pcm_per_sigma or 0.0))
        contributors.append(
            {
                "parameter_id": parameter.id,
                "label": parameter.label,
                "category": parameter.category,
                "source_backed": parameter.source_backed,
                "oat_pcm_per_sigma": round(oat_pcm_per_sigma, 3) if oat_pcm_per_sigma is not None else None,
                "regression_pcm_per_sigma": (
                    round(regression_pcm_per_sigma, 3) if regression_pcm_per_sigma is not None else None
                ),
                "ranking_score_pcm": round(ranking_score, 3),
            }
        )
    contributors.sort(key=lambda item: abs(float(item["ranking_score_pcm"])), reverse=True)
    return contributors


def _coverage_summary(
    parameters: list[UncertaintyParameter],
    completed: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> dict[str, Any]:
    source_backed = [parameter.id for parameter in parameters if parameter.source_backed]
    assumption_backed = [parameter.id for parameter in parameters if not parameter.source_backed]
    required_missing = [
        parameter.id
        for parameter in parameters
        if parameter.required_for_benchmark_ready and not parameter.source_backed
    ]
    return {
        "enabled_parameter_count": len(parameters),
        "source_backed_enabled_count": len(source_backed),
        "assumption_backed_enabled_count": len(assumption_backed),
        "source_backed_parameter_ids": source_backed,
        "assumption_backed_parameter_ids": assumption_backed,
        "required_missing_source_backing_ids": required_missing,
        "all_required_source_backed": not required_missing,
        "solver_backed_sample_count": len(completed),
        "failed_sample_count": len(failed),
    }


def _build_manifest(
    *,
    config: CaseConfig,
    inputs_path: Path,
    inputs: Mapping[str, Any],
    parameters: list[UncertaintyParameter],
    sample_definitions: list[dict[str, Any]],
    samples: int,
    seed: int,
    sampler: str,
    max_parallel: int,
    require_source_backed: bool,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "case": config.name,
        "model": DEFAULT_UNCERTAINTY_SWEEP_MODEL,
        "inputs_path": str(inputs_path),
        "input_schema_version": inputs.get("schema_version"),
        "sampler": sampler,
        "sobol_samples": samples,
        "seed": seed,
        "max_parallel": max_parallel,
        "require_source_backed": require_source_backed,
        "sample_count": len(sample_definitions),
        "observable_ids": list(_uncertainty_settings(config).get("observable_ids", ["keff"])),
        "parameters": [
            {
                "id": parameter.id,
                "label": parameter.label,
                "operation": parameter.operation,
                "category": parameter.category,
                "sigma": parameter.sigma,
                "bounds": [parameter.lower, parameter.upper],
                "source_backed": parameter.source_backed,
                "required_for_benchmark_ready": parameter.required_for_benchmark_ready,
            }
            for parameter in parameters
        ],
        "provenance": copy.deepcopy(provenance) if provenance else None,
    }


def _resolve_uncertainty_inputs_path(repo_root: Path, config: CaseConfig, settings: Mapping[str, Any]) -> Path:
    raw_path = settings.get("inputs") or f"benchmarks/{config.name}/uncertainty_inputs.yaml"
    path = (repo_root / str(raw_path)).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Uncertainty input file does not exist: {path}")
    return path


def _uncertainty_settings(config: CaseConfig) -> dict[str, Any]:
    settings = config.data.get("uncertainty_sweep", {})
    return settings if isinstance(settings, dict) else {}


def _child_bundle(root_bundle: ResultBundle, sample_id: str) -> ResultBundle:
    safe_id = safe_path_segment(sample_id, "sample id")
    root = root_bundle.root / "samples" / safe_id
    openmc_dir = root / "openmc"
    plots_dir = root / "plots"
    images_dir = root / "images"
    geometry_dir = root / "geometry"
    for path in (openmc_dir, plots_dir, images_dir, geometry_dir, geometry_dir / "exports"):
        path.mkdir(parents=True, exist_ok=True)
    return ResultBundle(
        case_name=root_bundle.case_name,
        run_id=safe_id,
        root=root,
        openmc_dir=openmc_dir,
        plots_dir=plots_dir,
        images_dir=images_dir,
        geometry_dir=geometry_dir,
    )


def _scale_property_spec(spec: dict[str, Any], scale: float) -> None:
    for key in ("value", "reference_value", "fallback_value"):
        if isinstance(spec.get(key), (int, float)) and not isinstance(spec.get(key), bool):
            spec[key] = float(spec[key]) * scale
    if isinstance(spec.get("values"), list):
        spec["values"] = [
            float(item) * scale if isinstance(item, (int, float)) and not isinstance(item, bool) else item
            for item in spec["values"]
        ]
    spec["uncertainty_sample_scale"] = scale


def _scale_path_value(data: dict[str, Any], dotted_path: str, scale: float) -> None:
    parts = dotted_path.split(".")
    current: Any = data
    for part in parts[:-1]:
        current = _require_mapping(current, part, dotted_path)
    leaf = parts[-1]
    value = current.get(leaf)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"Uncertainty path {dotted_path} is not numeric.")
    current[leaf] = float(value) * scale


def _scale_radial_layer_thickness(data: dict[str, Any], parameter: UncertaintyParameter, scale: float) -> None:
    layers_path = str(parameter.metadata.get("layers_path", "geometry.layers"))
    layers = _resolve_path(data, layers_path)
    if not isinstance(layers, list):
        raise ConfigError(f"Parameter {parameter.id} layers_path must resolve to a list.")
    layer_name = str(parameter.metadata.get("layer", ""))
    target_index = next(
        (
            index
            for index, layer in enumerate(layers)
            if isinstance(layer, dict) and str(layer.get("name")) == layer_name
        ),
        None,
    )
    if target_index is None:
        raise ConfigError(f"Parameter {parameter.id} could not find layer {layer_name}.")
    target = layers[target_index]
    inner = float(target.get("inner_radius", 0.0))
    outer = float(target["outer_radius"])
    old_thickness = outer - inner
    new_thickness = old_thickness * scale
    if new_thickness <= 1.0e-9:
        raise ConfigError(f"Parameter {parameter.id} produced non-positive layer thickness.")
    delta = new_thickness - old_thickness
    target["outer_radius"] = outer + delta
    for later in layers[target_index + 1 :]:
        if not isinstance(later, dict):
            continue
        if "inner_radius" in later:
            later["inner_radius"] = float(later["inner_radius"]) + delta
        if "outer_radius" in later:
            later["outer_radius"] = float(later["outer_radius"]) + delta


def _resolve_path(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ConfigError(f"Could not resolve uncertainty path {dotted_path}.")
        current = current[part]
    return current


def _require_mapping(parent: Mapping[str, Any], key: Any, label: str) -> dict[str, Any]:
    key_text = str(key)
    value = parent.get(key_text)
    if not isinstance(value, dict):
        raise ConfigError(f"Missing or invalid {label} '{key_text}'.")
    return value


def _resolve_keff_target(config: CaseConfig, benchmark: dict[str, Any]) -> dict[str, Any]:
    for dataset in benchmark.get("datasets", []):
        if not isinstance(dataset, dict):
            continue
        for observable in dataset.get("observables", []):
            if isinstance(observable, dict) and observable.get("id") == "experimental_critical_keff":
                return {
                    "id": "experimental_critical_keff",
                    "value": _coerce_float(observable.get("value")),
                    "uncertainty_pcm": _coerce_float(observable.get("uncertainty_pcm")),
                    "source": dataset.get("source"),
                }
    for target_id, target in config.validation_targets.items():
        if isinstance(target, dict) and target.get("metric") == "keff" and target.get("value") is not None:
            return {
                "id": str(target_id),
                "value": _coerce_float(target.get("value")),
                "uncertainty_pcm": _coerce_float(target.get("uncertainty_pcm")),
                "source": "validation_targets",
            }
    return {"id": "keff", "value": None, "uncertainty_pcm": None, "source": None}


def _interval_width_pcm(interval: Mapping[str, Any]) -> float | None:
    low = _coerce_float(interval.get("p02_5"))
    high = _coerce_float(interval.get("p97_5"))
    if low is None or high is None:
        return None
    return round((high - low) * PCM_PER_DELTA_K, 3)


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _write_uncertainty_plots(bundle: ResultBundle, budget: dict[str, Any]) -> dict[str, str]:
    bundle.plots_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[str, str] = {}
    keff = budget.get("observables", {}).get("keff", {})
    interval = keff.get("input_interval", {})
    if isinstance(interval, dict) and interval.get("p02_5") is not None and interval.get("p97_5") is not None:
        path = bundle.plots_dir / "uncertainty_keff_interval.svg"
        path.write_text(_interval_svg(keff), encoding="utf-8")
        assets["uncertainty_keff_interval"] = str(path)
    contributors = budget.get("dominant_contributors", [])
    if contributors:
        path = bundle.plots_dir / "uncertainty_tornado.svg"
        path.write_text(_tornado_svg(contributors[:8]), encoding="utf-8")
        assets["uncertainty_tornado"] = str(path)
    if assets:
        manifest_path = bundle.root / PLOT_MANIFEST_NAME
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
        manifest.update(assets)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return assets


def _interval_svg(keff: Mapping[str, Any]) -> str:
    interval = keff.get("input_interval", {})
    low = float(interval["p02_5"])
    mid = float(interval["p50"])
    high = float(interval["p97_5"])
    nominal = float(keff.get("nominal") or mid)
    target = _coerce_float(keff.get("target"))
    domain_low = min(low, nominal, target if target is not None else low)
    domain_high = max(high, nominal, target if target is not None else high)
    width = max(domain_high - domain_low, 1.0e-9)

    def x(value: float) -> float:
        return 70.0 + (value - domain_low) / width * 500.0

    target_line = ""
    if target is not None:
        tx = x(target)
        target_line = f'<line x1="{tx:.2f}" y1="45" x2="{tx:.2f}" y2="125" stroke="#8e44ad" stroke-width="2"/><text x="{tx + 4:.2f}" y="42" font-size="12">target</text>'
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="170" viewBox="0 0 640 170">
  <rect width="640" height="170" fill="#ffffff"/>
  <text x="24" y="28" font-size="16" font-family="Arial">OpenMC keff input uncertainty interval</text>
  <line x1="70" y1="88" x2="570" y2="88" stroke="#d0d7de" stroke-width="2"/>
  <line x1="{x(low):.2f}" y1="88" x2="{x(high):.2f}" y2="88" stroke="#1f77b4" stroke-width="10" stroke-linecap="round"/>
  <circle cx="{x(mid):.2f}" cy="88" r="6" fill="#1f77b4"/>
  <circle cx="{x(nominal):.2f}" cy="88" r="5" fill="#d62728"/>
  {target_line}
  <text x="70" y="140" font-size="12" font-family="Arial">{domain_low:.6f}</text>
  <text x="505" y="140" font-size="12" font-family="Arial">{domain_high:.6f}</text>
  <text x="24" y="160" font-size="12" font-family="Arial">Blue band: input p2.5-p97.5, red point: nominal</text>
</svg>
"""


def _tornado_svg(contributors: list[dict[str, Any]]) -> str:
    width = 760
    row_h = 32
    height = 70 + row_h * len(contributors)
    max_score = max(abs(float(item.get("ranking_score_pcm") or 0.0)) for item in contributors) or 1.0
    rows = []
    for index, item in enumerate(contributors):
        y = 52 + index * row_h
        score = abs(float(item.get("ranking_score_pcm") or 0.0))
        bar_w = 440.0 * score / max_score
        rows.append(
            f'<text x="24" y="{y + 15}" font-size="12" font-family="Arial">{item.get("parameter_id")}</text>'
            f'<rect x="250" y="{y}" width="{bar_w:.2f}" height="18" fill="#2e86ab"/>'
            f'<text x="{260 + bar_w:.2f}" y="{y + 14}" font-size="12" font-family="Arial">{score:.1f} pcm/sigma</text>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="24" y="28" font-size="16" font-family="Arial">Dominant geometry/material uncertainty contributors</text>
  {"".join(rows)}
</svg>
"""
