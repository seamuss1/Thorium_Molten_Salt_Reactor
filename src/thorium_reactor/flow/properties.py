from __future__ import annotations

import math
from typing import Any

from thorium_reactor.capabilities import resolve_primary_coolant_material_name


def evaluate_fluid_properties(
    material_spec: dict[str, Any],
    *,
    temperature_c: float,
) -> dict[str, Any]:
    density_spec = material_spec.get("density", {})
    cp_spec = material_spec.get("cp")
    conductivity_spec = material_spec.get("thermal_conductivity")
    viscosity_spec = material_spec.get("dynamic_viscosity")

    density_kg_m3 = _convert_property_value(
        _evaluate_property_spec(density_spec, temperature_c=temperature_c),
        density_spec.get("units"),
        expected_quantity="density",
    )
    cp_j_kgk = _convert_property_value(
        _evaluate_property_spec(cp_spec, temperature_c=temperature_c),
        cp_spec.get("units") if isinstance(cp_spec, dict) else None,
        expected_quantity="specific_heat",
    ) if cp_spec else None
    conductivity_w_mk = _convert_property_value(
        _evaluate_property_spec(conductivity_spec, temperature_c=temperature_c),
        conductivity_spec.get("units") if isinstance(conductivity_spec, dict) else None,
        expected_quantity="thermal_conductivity",
    ) if conductivity_spec else None
    viscosity_pa_s = _convert_property_value(
        _evaluate_property_spec(viscosity_spec, temperature_c=temperature_c),
        viscosity_spec.get("units") if isinstance(viscosity_spec, dict) else None,
        expected_quantity="dynamic_viscosity",
    ) if viscosity_spec else None

    return {
        "temperature_c": _round_float(temperature_c),
        "density_kg_m3": _round_float(density_kg_m3),
        "cp_j_kgk": _round_float(cp_j_kgk) if cp_j_kgk is not None else None,
        "thermal_conductivity_w_mk": _round_float(conductivity_w_mk) if conductivity_w_mk is not None else None,
        "dynamic_viscosity_pa_s": _round_float(viscosity_pa_s) if viscosity_pa_s is not None else None,
    }


def evaluate_property(
    spec: dict[str, Any],
    *,
    temperature_c: float,
    expected_quantity: str,
) -> float:
    return _convert_property_value(
        _evaluate_property_spec(spec, temperature_c=temperature_c),
        spec.get("units"),
        expected_quantity=expected_quantity,
    )


def average_primary_temperature_c(reactor_config: dict[str, Any]) -> float:
    hot_leg_temp_c = float(reactor_config.get("hot_leg_temp_c", 700.0))
    cold_leg_temp_c = float(reactor_config.get("cold_leg_temp_c", 560.0))
    return 0.5 * (hot_leg_temp_c + cold_leg_temp_c)


def constant_property_spec(value: float, units: str) -> dict[str, Any]:
    return {
        "model": "constant",
        "value": float(value),
        "units": units,
    }


def property_reference_temperature_c(
    reactor_config: dict[str, Any],
    spec: dict[str, Any] | None = None,
    *,
    require_declared: bool = False,
) -> float:
    declared_temperature_c = reactor_config.get("property_reference_temperature_c")
    if require_declared and declared_temperature_c is None and spec and spec.get("model", "constant") != "constant":
        raise ValueError("Modeled material properties require reactor.property_reference_temperature_c for reference-state evaluation.")
    default_temperature_c = float(declared_temperature_c if declared_temperature_c is not None else 25.0)
    if spec and spec.get("model") == "linear":
        return float(spec.get("reference_temperature_c", default_temperature_c))
    return default_temperature_c


def primary_fluid_material_name(config: Any) -> str:
    return resolve_primary_coolant_material_name(config)


def evaluate_primary_coolant_properties(config: Any, *, temperature_c: float | None = None) -> dict[str, Any]:
    material_name = primary_fluid_material_name(config)
    material_spec = dict(config.materials[material_name])
    reactor = config.reactor
    if "cp" not in material_spec and "primary_cp_kj_kgk" in reactor:
        material_spec["cp"] = constant_property_spec(float(reactor["primary_cp_kj_kgk"]), "kj/kg-k")
    if "dynamic_viscosity" not in material_spec and "primary_dynamic_viscosity_pa_s" in reactor:
        material_spec["dynamic_viscosity"] = constant_property_spec(float(reactor["primary_dynamic_viscosity_pa_s"]), "pa-s")
    if "thermal_conductivity" not in material_spec and "primary_thermal_conductivity_w_mk" in reactor:
        material_spec["thermal_conductivity"] = constant_property_spec(float(reactor["primary_thermal_conductivity_w_mk"]), "w/m-k")
    evaluation_temperature_c = average_primary_temperature_c(reactor) if temperature_c is None else float(temperature_c)
    return evaluate_fluid_properties(material_spec, temperature_c=evaluation_temperature_c)


def evaluate_secondary_coolant_properties(config: Any, *, temperature_c: float) -> dict[str, Any]:
    reactor = config.reactor
    secondary_spec = {
        "density": reactor.get("secondary_density") or constant_property_spec(
            reactor.get("secondary_density_kg_m3", 1800.0),
            "kg/m3",
        ),
        "cp": reactor.get("secondary_cp") or constant_property_spec(
            reactor.get("secondary_cp_j_kgk", 1700.0),
            "j/kg-k",
        ),
        "dynamic_viscosity": reactor.get("secondary_dynamic_viscosity") or constant_property_spec(
            reactor.get("secondary_dynamic_viscosity_pa_s", 0.0065),
            "pa-s",
        ),
        "thermal_conductivity": reactor.get("secondary_thermal_conductivity") or constant_property_spec(
            reactor.get("secondary_thermal_conductivity_w_mk", 0.85),
            "w/m-k",
        ),
    }
    return evaluate_fluid_properties(secondary_spec, temperature_c=float(temperature_c))


def primary_coolant_cp_kj_kgk(config: Any, *, temperature_c: float | None = None) -> float:
    material_name = primary_fluid_material_name(config)
    material_spec = dict(config.materials[material_name])
    cp_spec = material_spec.get("cp")
    if cp_spec is None:
        reactor_cp = config.reactor.get("primary_cp_kj_kgk")
        if reactor_cp is None:
            raise ValueError(
                f"Primary coolant specific heat is not defined. "
                f"Set reactor.primary_cp_kj_kgk or materials.{material_name}.cp."
            )
        return float(reactor_cp)
    evaluation_temperature_c = average_primary_temperature_c(config.reactor) if temperature_c is None else float(temperature_c)
    cp_j_kgk = _convert_property_value(
        _evaluate_property_spec(cp_spec, temperature_c=evaluation_temperature_c),
        cp_spec.get("units") if isinstance(cp_spec, dict) else None,
        expected_quantity="specific_heat",
    )
    return cp_j_kgk / 1000.0


def _evaluate_property_spec(spec: dict[str, Any] | None, *, temperature_c: float) -> float:
    if not spec:
        raise ValueError("Missing property specification.")

    model = spec.get("model", "constant")
    if model == "constant":
        return float(spec["value"])
    if model == "linear":
        reference_value = float(spec["reference_value"])
        reference_temperature_c = float(spec.get("reference_temperature_c", 25.0))
        slope_per_c = float(spec["slope_per_c"])
        return reference_value + slope_per_c * (temperature_c - reference_temperature_c)
    if model == "arrhenius":
        pre_exponential = float(spec["pre_exponential"])
        activation_temperature_k = float(spec["activation_temperature_k"])
        temperature_k = temperature_c + 273.15
        if temperature_k <= 0.0:
            raise ValueError("Temperature must remain above absolute zero for Arrhenius properties.")
        return pre_exponential * math.exp(activation_temperature_k / temperature_k)
    raise ValueError(f"Unsupported property model: {model}")


def _convert_property_value(value: float, units: str | None, *, expected_quantity: str) -> float:
    if expected_quantity == "density":
        if units == "g/cm3":
            return value * 1000.0
        if units == "kg/m3":
            return value
    if expected_quantity == "specific_heat":
        if units == "kj/kg-k":
            return value * 1000.0
        if units == "j/kg-k":
            return value
    if expected_quantity == "thermal_conductivity":
        if units == "w/m-k":
            return value
    if expected_quantity == "dynamic_viscosity":
        if units == "pa-s":
            return value
    raise ValueError(f"Unsupported units '{units}' for {expected_quantity}.")


def _round_float(value: float) -> float:
    return round(float(value), 6)
