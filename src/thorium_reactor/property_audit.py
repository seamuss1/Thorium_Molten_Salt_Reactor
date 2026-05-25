from __future__ import annotations

from collections import Counter
from typing import Any

from thorium_reactor.flow.properties import (
    average_primary_temperature_c,
    evaluate_primary_coolant_properties,
    evaluate_secondary_coolant_properties,
    primary_fluid_material_name,
)
from thorium_reactor.msd_tp import describe_msd_tp_property


def build_property_audit(config: Any) -> dict[str, Any]:
    properties_config = config.data.get("properties", {}) if hasattr(config, "data") else {}
    if not isinstance(properties_config, dict):
        properties_config = {}
    provider = str(properties_config.get("provider", "legacy_correlation"))
    reference_temperature_c = average_primary_temperature_c(config.reactor)
    audit = {
        "provider": provider,
        "reference_temperature_c": round(float(reference_temperature_c), 6),
        "records": [],
    }

    primary_material_name: str | None = None
    try:
        primary_material_name = primary_fluid_material_name(config)
        audit["primary_material"] = primary_material_name
    except Exception as exc:
        audit["status"] = "partial"
        audit["primary_material_error"] = str(exc)

    if primary_material_name is not None:
        for quantity_name, spec in config.materials[primary_material_name].items():
            if quantity_name in {"density", "cp", "thermal_conductivity", "dynamic_viscosity"} and isinstance(spec, dict):
                audit["records"].append(
                    _describe_property_record(
                        f"materials.{primary_material_name}.{quantity_name}",
                        spec,
                        quantity_name=quantity_name,
                        reference_temperature_c=reference_temperature_c,
                    )
                )
        try:
            audit["primary_properties"] = evaluate_primary_coolant_properties(config, temperature_c=reference_temperature_c)
        except Exception as exc:
            audit["status"] = "partial"
            audit["primary_properties_error"] = str(exc)

    secondary_spec_present = any(
        key in config.reactor
        for key in (
            "secondary_density",
            "secondary_density_kg_m3",
            "secondary_cp",
            "secondary_cp_j_kgk",
            "secondary_dynamic_viscosity",
            "secondary_dynamic_viscosity_pa_s",
            "secondary_thermal_conductivity",
            "secondary_thermal_conductivity_w_mk",
        )
    )
    if secondary_spec_present:
        try:
            audit["secondary_properties"] = evaluate_secondary_coolant_properties(config, temperature_c=reference_temperature_c)
        except Exception as exc:
            audit["status"] = "partial"
            audit["secondary_properties_error"] = str(exc)
    audit["source_backing_counts"] = _source_backing_counts(audit["records"])
    audit["source_backing"] = _source_backing_status(audit["source_backing_counts"])
    audit.setdefault("status", "available")
    return audit


def _describe_property_record(
    path: str,
    spec: dict[str, Any],
    *,
    quantity_name: str,
    reference_temperature_c: float,
) -> dict[str, Any]:
    provider = str(spec.get("provider", "legacy_correlation"))
    record = {
        "path": path,
        "provider": provider,
        "units": spec.get("units"),
        "uncertainty": spec.get("uncertainty"),
    }
    if provider in {"msd_tp", "msd_tp_redlich_kister"}:
        try:
            record["source_evidence"] = describe_msd_tp_property(
                spec,
                temperature_c=reference_temperature_c,
                expected_quantity="specific_heat" if quantity_name == "cp" else quantity_name,
            )
            record["source_backing"] = record["source_evidence"].get("source_kind")
            record["validity"] = record["source_evidence"].get("range_status")
        except Exception as exc:
            record["source_backing"] = "invalid"
            record["error"] = str(exc)
    elif provider == "evaluated_table":
        record["valid_temperature_range_c"] = {
            "min": min(spec.get("temperatures_c", [0.0])),
            "max": max(spec.get("temperatures_c", [0.0])),
        }
        record["table_label"] = spec.get("table_label")
    elif provider == "thermochemical_equilibrium":
        record["fallback_value"] = spec.get("fallback_value", spec.get("value", spec.get("reference_value")))
    else:
        record["model"] = spec.get("model", "constant")
    return record


def _source_backing_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        backing = record.get("source_backing")
        if backing:
            counter[str(backing)] += 1
        else:
            counter["configured"] += 1
    return dict(sorted(counter.items()))


def _source_backing_status(counts: dict[str, int]) -> str:
    if counts.get("invalid"):
        return "invalid"
    source_backed = counts.get("direct_record", 0) + counts.get("redlich_kister_estimate", 0)
    configured = sum(value for key, value in counts.items() if key not in {"direct_record", "redlich_kister_estimate"})
    if source_backed and not configured:
        return "source_backed"
    if source_backed:
        return "partial"
    return "configured"
