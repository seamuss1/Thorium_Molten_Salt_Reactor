from __future__ import annotations

from typing import Any


def build_chemistry_assumptions(config: Any) -> dict[str, Any]:
    chemistry = config.data.get("chemistry", {})
    if not isinstance(chemistry, dict):
        chemistry = {}
    return {
        "model": str(chemistry.get("model", "salt_redox_cleanup_proxy")),
        "target_redox_state_ev": _round_float(float(chemistry.get("target_redox_state_ev", -0.03))),
        "initial_redox_state_ev": _round_float(float(chemistry.get("initial_redox_state_ev", -0.02))),
        "redox_control_time_days": _round_float(float(chemistry.get("redox_control_time_days", 2.0))),
        "oxidant_ingress_fraction_per_day": _round_float(float(chemistry.get("oxidant_ingress_fraction_per_day", 2.0e-4))),
        "impurity_capture_efficiency": _round_float(float(chemistry.get("impurity_capture_efficiency", 0.65))),
        "gas_stripping_efficiency": _round_float(float(chemistry.get("gas_stripping_efficiency", 0.88))),
        "noble_metal_plateout_fraction": _round_float(float(chemistry.get("noble_metal_plateout_fraction", 0.12))),
        "corrosion_acceleration_per_ev": _round_float(float(chemistry.get("corrosion_acceleration_per_ev", 4.0))),
        "tritium_release_fraction": _round_float(float(chemistry.get("tritium_release_fraction", 0.35))),
    }


def build_steady_state_chemistry_summary(
    config: Any,
    *,
    fuel_salt_volume_m3: float,
    bulk_temperature_c: float,
    cleanup_turnover_days: float,
) -> dict[str, Any]:
    assumptions = build_chemistry_assumptions(config)
    redox_state_ev = float(assumptions["initial_redox_state_ev"])
    target_redox_state_ev = float(assumptions["target_redox_state_ev"])
    redox_deviation_ev = redox_state_ev - target_redox_state_ev
    oxidant_ingress_fraction_per_day = float(assumptions["oxidant_ingress_fraction_per_day"])
    impurity_capture_efficiency = float(assumptions["impurity_capture_efficiency"])
    gas_stripping_efficiency = float(assumptions["gas_stripping_efficiency"])
    cleanup_damping = max(cleanup_turnover_days, 0.25)
    impurity_fraction = oxidant_ingress_fraction_per_day / (1.0 + (impurity_capture_efficiency + gas_stripping_efficiency) * cleanup_damping)
    corrosion_index = _corrosion_index(
        redox_deviation_ev=redox_deviation_ev,
        impurity_fraction=impurity_fraction,
        corrosion_acceleration_per_ev=float(assumptions["corrosion_acceleration_per_ev"]),
    )
    noble_metal_suspended_fraction = max(1.0 - float(assumptions["noble_metal_plateout_fraction"]), 0.0)
    tritium_release_fraction = min(
        1.0,
        max(0.0, float(assumptions["tritium_release_fraction"]) * (0.75 + 0.25 * gas_stripping_efficiency)),
    )
    return {
        "model": assumptions["model"],
        "bulk_temperature_c": _round_float(bulk_temperature_c),
        "fuel_salt_volume_m3": _round_float(fuel_salt_volume_m3),
        "target_redox_state_ev": assumptions["target_redox_state_ev"],
        "redox_state_ev": _round_float(redox_state_ev),
        "redox_deviation_ev": _round_float(redox_deviation_ev),
        "oxidant_ingress_fraction_per_day": assumptions["oxidant_ingress_fraction_per_day"],
        "impurity_capture_efficiency": assumptions["impurity_capture_efficiency"],
        "gas_stripping_efficiency": assumptions["gas_stripping_efficiency"],
        "impurity_fraction": _round_float(impurity_fraction),
        "corrosion_index": _round_float(corrosion_index),
        "corrosion_risk": _corrosion_risk(corrosion_index),
        "noble_metal_plateout_fraction": assumptions["noble_metal_plateout_fraction"],
        "noble_metal_suspended_fraction": _round_float(noble_metal_suspended_fraction),
        "tritium_release_fraction": _round_float(tritium_release_fraction),
        "assumptions": assumptions,
    }


def corrosion_index_from_state(
    *,
    redox_state_ev: float,
    target_redox_state_ev: float,
    impurity_fraction: float,
    corrosion_acceleration_per_ev: float,
) -> float:
    return _corrosion_index(
        redox_deviation_ev=redox_state_ev - target_redox_state_ev,
        impurity_fraction=impurity_fraction,
        corrosion_acceleration_per_ev=corrosion_acceleration_per_ev,
    )


def _corrosion_index(
    *,
    redox_deviation_ev: float,
    impurity_fraction: float,
    corrosion_acceleration_per_ev: float,
) -> float:
    oxidizing_penalty = max(redox_deviation_ev, 0.0) * corrosion_acceleration_per_ev
    impurity_penalty = impurity_fraction * 400.0
    return max(0.1, 1.0 + oxidizing_penalty + impurity_penalty)


def _corrosion_risk(corrosion_index: float) -> str:
    if corrosion_index >= 1.8:
        return "high"
    if corrosion_index >= 1.25:
        return "moderate"
    return "low"


def _round_float(value: float) -> float:
    return round(float(value), 6)
