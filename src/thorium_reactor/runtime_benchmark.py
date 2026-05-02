from __future__ import annotations

import copy
import json
import math
from typing import Any

from thorium_reactor.accelerators import (
    SUPPORTED_ARRAY_BACKENDS,
    available_backend_report,
    runtime_environment_report,
)
from thorium_reactor.transient_sweep import build_transient_sweep_payload


DEFAULT_RUNTIME_BENCHMARK_BACKENDS = ("python", "numpy", "torch-xpu")
COMPARISON_METRICS = (
    "peak_power_fraction_p95",
    "peak_power_fraction_max",
    "peak_fuel_temperature_c_p95",
    "peak_fuel_temperature_c_max",
    "final_power_fraction_p50",
    "final_power_fraction_p95",
    "final_total_reactivity_pcm_p50",
    "final_total_reactivity_pcm_p95",
    "final_core_delayed_neutron_source_fraction_p50",
    "minimum_core_delayed_neutron_source_fraction_p05",
    "peak_corrosion_index_p95",
)


def parse_backend_list(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_RUNTIME_BENCHMARK_BACKENDS)
    names = [item.strip() for item in raw.split(",") if item.strip()]
    if not names:
        return list(DEFAULT_RUNTIME_BENCHMARK_BACKENDS)
    unknown = [name for name in names if name not in (*SUPPORTED_ARRAY_BACKENDS, "auto")]
    if unknown:
        raise ValueError(f"Unsupported runtime benchmark backend(s): {', '.join(unknown)}")
    return names


def run_runtime_benchmark_case(
    config: Any,
    bundle,
    summary: dict[str, Any],
    *,
    scenario_name: str | None = None,
    samples: int = 1_048_576,
    seed: int = 42,
    backends: list[str] | tuple[str, ...] = DEFAULT_RUNTIME_BENCHMARK_BACKENDS,
    dtype: str = "float32",
    fail_on_gpu_fallback: bool = False,
    provenance: dict[str, Any] | None = None,
    rtol: float = 1.0e-4,
    atol: float = 1.0e-5,
) -> dict[str, Any]:
    requested_backends = list(backends)
    discovery_names = tuple(name for name in requested_backends if name != "auto")
    discovery = available_backend_report(dtype=dtype, seed=seed, names=discovery_names)
    availability = {item["name"]: bool(item.get("available")) for item in discovery}

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for backend_name in requested_backends:
        if backend_name != "auto" and not availability.get(backend_name, False):
            failure = {
                "backend": backend_name,
                "status": "unavailable",
                "reason": next((item.get("reason") for item in discovery if item["name"] == backend_name), "backend unavailable"),
            }
            failures.append(failure)
            results.append(failure)
            continue
        try:
            payload = build_transient_sweep_payload(
                config,
                copy.deepcopy(summary),
                scenario_name=scenario_name,
                samples=samples,
                seed=seed,
                backend=backend_name,
                dtype=dtype,
                provenance=provenance,
            )
        except BaseException as exc:
            failure = {
                "backend": backend_name,
                "status": "failed",
                "reason": repr(exc),
                "error_type": type(exc).__name__,
            }
            failures.append(failure)
            results.append(failure)
            continue

        fallback_enabled = bool(
            payload.get("backend_report", {})
            .get("environment", {})
            .get("pytorch_xpu_fallback_enabled", False)
        )
        if payload["backend"].startswith("torch-xpu") and fallback_enabled and fail_on_gpu_fallback:
            raise RuntimeError("torch-xpu benchmark ran with PYTORCH_ENABLE_XPU_FALLBACK enabled.")
        results.append(
            {
                "backend": payload["backend"],
                "requested_backend": backend_name,
                "status": "completed",
                "metrics": {key: payload["metrics"][key] for key in COMPARISON_METRICS},
                "runtime_performance": payload["runtime_performance"],
                "numerical_checks": payload["numerical_checks"],
                "backend_report": payload["backend_report"],
                "gpu_fallback_enabled": fallback_enabled,
            }
        )

    reference = _select_reference_result(results)
    for result in results:
        if result.get("status") != "completed":
            continue
        result["comparison_to_reference"] = _compare_to_reference(result, reference, rtol=rtol, atol=atol)
        result["valid_for_recommendation"] = (
            result["numerical_checks"]["status"] == "ok"
            and result["comparison_to_reference"]["status"] == "ok"
            and not result.get("gpu_fallback_enabled", False)
        )

    recommendation = _recommend_backend(results, reference)
    payload = {
        "case": config.name,
        "scenario_name": scenario_name,
        "samples": max(int(samples), 32),
        "seed": int(seed),
        "dtype": dtype,
        "requested_backends": requested_backends,
        "backend_discovery": discovery,
        "runtime_environment": runtime_environment_report(),
        "comparison_metrics": list(COMPARISON_METRICS),
        "tolerances": {"rtol": rtol, "atol": atol},
        "reference_backend": reference.get("backend") if reference else None,
        "recommendation": recommendation,
        "results": results,
        "failures": failures,
        "scientific_integrity": (
            "Runtime benchmarks compare accelerated reduced-order transient sweeps. "
            "They are performance and regression evidence, not validated licensing analyses."
        ),
    }
    if provenance:
        payload["provenance"] = json.loads(json.dumps(provenance))

    benchmark_path = bundle.write_json("runtime_benchmark.json", payload)
    summary["runtime_benchmark"] = {
        "status": "completed" if any(item.get("status") == "completed" for item in results) else "failed",
        "path": str(benchmark_path),
        "recommended_backend": recommendation.get("backend"),
        "reference_backend": payload["reference_backend"],
        "best_speedup": recommendation.get("speedup_vs_reference"),
        "samples": payload["samples"],
        "dtype": dtype,
    }
    bundle.write_json("summary.json", summary)
    return payload


def _select_reference_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    for preferred in ("python", "numpy", "torch-cpu"):
        for result in results:
            if result.get("status") == "completed" and result.get("backend") == preferred:
                return result
    for result in results:
        if result.get("status") == "completed":
            return result
    return {}


def _compare_to_reference(
    result: dict[str, Any],
    reference: dict[str, Any],
    *,
    rtol: float,
    atol: float,
) -> dict[str, Any]:
    if not reference or result is reference:
        return {"status": "ok", "max_abs_error": 0.0, "max_rel_error": 0.0, "failures": []}
    failures = []
    max_abs_error = 0.0
    max_rel_error = 0.0
    for key in COMPARISON_METRICS:
        value = float(result["metrics"][key])
        expected = float(reference["metrics"][key])
        abs_error = abs(value - expected)
        rel_error = abs_error / max(abs(expected), atol)
        max_abs_error = max(max_abs_error, abs_error)
        max_rel_error = max(max_rel_error, rel_error)
        if not math.isfinite(value) or abs_error > atol + rtol * abs(expected):
            failures.append({"metric": key, "value": value, "reference": expected, "abs_error": abs_error, "rel_error": rel_error})
    return {
        "status": "ok" if not failures else "failed",
        "max_abs_error": max_abs_error,
        "max_rel_error": max_rel_error,
        "failures": failures,
    }


def _recommend_backend(results: list[dict[str, Any]], reference: dict[str, Any]) -> dict[str, Any]:
    if not reference:
        return {"backend": None, "reason": "no completed reference backend"}
    reference_rate = float(reference.get("runtime_performance", {}).get("sample_steps_per_s", 0.0))
    candidates = []
    for result in results:
        if result.get("status") != "completed" or not result.get("valid_for_recommendation", False):
            continue
        rate = float(result.get("runtime_performance", {}).get("sample_steps_per_s", 0.0))
        speedup = rate / max(reference_rate, 1.0e-12)
        candidates.append((speedup, rate, result))
    if not candidates:
        return {"backend": reference.get("backend"), "speedup_vs_reference": 1.0, "reason": "no valid accelerated candidate"}
    speedup, rate, result = max(candidates, key=lambda item: item[1])
    if result is reference or speedup < 1.5:
        return {
            "backend": reference.get("backend"),
            "speedup_vs_reference": 1.0,
            "reason": "no valid backend exceeded the 1.5x recommendation threshold",
        }
    return {
        "backend": result["backend"],
        "speedup_vs_reference": speedup,
        "sample_steps_per_s": rate,
        "reason": "fastest valid backend above the recommendation threshold",
    }
