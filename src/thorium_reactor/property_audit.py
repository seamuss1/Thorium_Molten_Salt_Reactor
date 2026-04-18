from __future__ import annotations

from typing import Any

from thorium_reactor.flow.properties import (
    average_primary_temperature_c,
    evaluate_primary_coolant_properties,
    evaluate_secondary_coolant_properties,
    primary_fluid_material_name,
)


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
                audit["records"].append(_describe_property_record(f"materials.{primary_material_name}.{quantity_name}", spec))
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
    audit.setdefault("status", "available")
    return audit


def _describe_property_record(path: str, spec: dict[str, Any]) -> dict[str, Any]:
    provider = str(spec.get("provider", "legacy_correlation"))
    record = {
        "path": path,
        "provider": provider,
        "units": spec.get("units"),
        "uncertainty": spec.get("uncertainty"),
    }
    if provider == "evaluated_table":
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
