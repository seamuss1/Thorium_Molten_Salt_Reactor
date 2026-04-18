from __future__ import annotations

import json
from typing import Any


def build_state_store(
    config: Any,
    summary: dict[str, Any],
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    property_audit: dict[str, Any] | None = None,
    benchmark_residuals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reactor = config.reactor if hasattr(config, "reactor") else config.get("reactor", {})
    mode = str(reactor.get("mode", _infer_reactor_mode(summary)))
    primary_system = summary.get("primary_system", {})
    transient = summary.get("transient", {})
    chemistry = summary.get("chemistry", {})
    fuel_cycle = summary.get("fuel_cycle", {})
    return {
        "case": config.name if hasattr(config, "name") else summary.get("case"),
        "reactor": {
            "name": reactor.get("name"),
            "family": reactor.get("family"),
            "stage": reactor.get("stage"),
            "mode": mode,
        },
        "runtime_context": json.loads(json.dumps(runtime_context or {})),
        "input_provenance": json.loads(json.dumps(provenance or {})),
        "benchmark": {
            "title": benchmark.get("title") if isinstance(benchmark, dict) else None,
            "dataset_count": len(benchmark.get("datasets", [])) if isinstance(benchmark, dict) and isinstance(benchmark.get("datasets"), list) else 0,
        },
        "loop_segments": _build_loop_segments(config, summary),
        "fields": {
            "metrics": json.loads(json.dumps(summary.get("metrics", {}))),
            "flow": json.loads(json.dumps(summary.get("flow", {}))),
            "bop": json.loads(json.dumps(summary.get("bop", {}))),
            "primary_system": json.loads(json.dumps(primary_system)),
        },
        "inventories": {
            "fuel_cycle": json.loads(json.dumps(fuel_cycle)),
            "chemistry": json.loads(json.dumps(chemistry)),
            "transient": {
                "final_fissile_inventory_fraction": transient.get("final_fissile_inventory_fraction"),
                "peak_protactinium_inventory_fraction": transient.get("peak_protactinium_inventory_fraction"),
                "final_redox_state_ev": transient.get("final_redox_state_ev"),
            },
        },
        "processing": {
            "config": json.loads(json.dumps(config.data.get("processing", {}))) if hasattr(config, "data") else {},
            "integrations": json.loads(json.dumps(summary.get("integrations", {}))),
        },
        "source_term": {
            "offgas_fraction": chemistry.get("gas_stripping_efficiency"),
            "tritium_release_fraction": chemistry.get("tritium_release_fraction"),
            "corrosion_index": chemistry.get("corrosion_index"),
        },
        "benchmark_residuals": json.loads(json.dumps(benchmark_residuals or {})),
        "property_audit": json.loads(json.dumps(property_audit or {})),
        "event_log": _build_event_log(summary),
    }


def _build_loop_segments(config: Any, summary: dict[str, Any]) -> list[dict[str, Any]]:
    configured = config.data.get("loop_segments", []) if hasattr(config, "data") else []
    if isinstance(configured, list) and configured:
        return json.loads(json.dumps(configured))

    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    active_flow = reduced_order.get("active_flow", {})
    core_fraction = float(summary.get("transient", {}).get("minimum_precursor_core_fraction", 0.5) or 0.5)
    core_fraction = max(0.0, min(core_fraction, 1.0))
    residence_time_s = float(active_flow.get("representative_residence_time_s", 1.0) or 1.0)
    return [
        {"id": "core", "label": "Core", "residence_fraction": round(core_fraction, 6), "residence_time_s": round(residence_time_s, 6)},
        {"id": "external_loop", "label": "External Loop", "residence_fraction": round(1.0 - core_fraction, 6), "residence_time_s": round(max(residence_time_s * 4.0, 1.0), 6)},
        {"id": "offgas", "label": "Off-Gas", "residence_fraction": 0.0, "residence_time_s": 0.0},
        {"id": "drain_tank", "label": "Drain Tank", "residence_fraction": 0.0, "residence_time_s": 0.0},
    ]


def _build_event_log(summary: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    transient = summary.get("transient", {})
    if transient:
        events.append(
            {
                "event": "transient_completed",
                "scenario_name": transient.get("scenario_name"),
                "duration_s": transient.get("duration_s"),
                "event_count": transient.get("event_count"),
            }
        )
    if summary.get("integrations"):
        for name, payload in summary["integrations"].items():
            events.append(
                {
                    "event": "integration_recorded",
                    "tool": name,
                    "status": payload.get("status"),
                }
            )
    return events


def _infer_reactor_mode(summary: dict[str, Any]) -> str:
    traceability = summary.get("benchmark_traceability", {})
    if traceability:
        stage = str(traceability.get("maturity_stage", ""))
        if "literature" in stage or "surrogate" in stage:
            return "historic_benchmark"
    return "modern_test_reactor"
