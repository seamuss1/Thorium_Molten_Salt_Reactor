from __future__ import annotations

import math
from typing import Any, Mapping


DEFAULT_PROPERTY_UNCERTAINTIES_95: dict[str, float] = {
    "density": 0.02,
    "cp": 0.10,
    "thermal_conductivity": 0.10,
    "dynamic_viscosity": 0.10,
}
DEFAULT_GRAPHITE_FAST_FLUENCE_LIMIT_N_CM2 = 3.0e22
SECONDS_PER_YEAR = 365.25 * 24.0 * 3600.0


def build_property_uncertainty_summary(
    config: Any,
    *,
    primary_delta_t_c: float | None = None,
) -> dict[str, Any]:
    settings = _mapping_section(config, "property_uncertainty")
    uncertainties = {
        key: _setting_float(settings, f"{key}_uncertainty_95_fraction", default)
        for key, default in DEFAULT_PROPERTY_UNCERTAINTIES_95.items()
    }
    density = uncertainties["density"]
    cp = uncertainties["cp"]
    conductivity = uncertainties["thermal_conductivity"]
    viscosity = uncertainties["dynamic_viscosity"]
    delta_t = max(float(primary_delta_t_c or 0.0), 0.0)
    propagated_delta_t_c = delta_t * math.sqrt(density * density + cp * cp)
    outlet_uncertainty_c = _setting_float(
        settings,
        "core_outlet_temperature_uncertainty_95_c",
        max(10.0, propagated_delta_t_c),
    )
    flow_uncertainty = math.sqrt(density * density + viscosity * viscosity)
    heat_transfer_uncertainty = math.sqrt(cp * cp + conductivity * conductivity)
    return {
        "model": str(settings.get("model", "tmsr_sf0_property_uncertainty_screen")),
        "confidence_level": _setting_float(settings, "confidence_level", 0.95),
        "density_uncertainty_95_fraction": _round_float(density),
        "cp_uncertainty_95_fraction": _round_float(cp),
        "thermal_conductivity_uncertainty_95_fraction": _round_float(conductivity),
        "dynamic_viscosity_uncertainty_95_fraction": _round_float(viscosity),
        "flow_uncertainty_95_fraction": _round_float(flow_uncertainty),
        "heat_transfer_uncertainty_95_fraction": _round_float(heat_transfer_uncertainty),
        "core_outlet_temperature_uncertainty_95_c": _round_float(outlet_uncertainty_c),
        "basis": "TMSR-SF0 RELAP5 uncertainty study default bands; case config may override.",
        "source": "https://doi.org/10.1016/j.net.2023.11.016",
    }


def build_tritium_transport_summary(
    config: Any,
    *,
    thermal_power_mw: float,
    fuel_salt_volume_m3: float,
    chemistry_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _mapping_section(config, "tritium")
    chemistry = chemistry_summary or {}
    gas_stripping_efficiency = _setting_float(
        settings,
        "gas_stripping_efficiency",
        float(chemistry.get("gas_stripping_efficiency", 0.0)),
    )
    li6_fraction = _setting_float(settings, "lithium6_atom_fraction", _infer_lithium6_fraction(config))
    reference_li6_fraction = _setting_float(settings, "reference_lithium6_atom_fraction", 5.0e-4)
    reference_power_mwth = _setting_float(settings, "reference_power_mwth", 10.0)
    removal_blend = _clamp(gas_stripping_efficiency / max(_setting_float(settings, "reference_gas_stripping_efficiency", 0.88), 1.0e-9), 0.0, 1.0)

    unmitigated_release = _setting_float(settings, "unmitigated_environment_fraction", 0.33)
    mitigated_release = _setting_float(settings, "mitigated_environment_fraction", 0.10)
    spray_removal_fraction = _setting_float(settings, "spray_gas_removal_fraction", 0.66)
    operation_years = _setting_float(settings, "screening_operation_years", 5.0)
    graphite_saturation_years = max(_setting_float(settings, "graphite_saturation_years", 5.0), 1.0e-9)
    graphite_saturation = _clamp(operation_years / graphite_saturation_years, 0.0, 1.0)

    environmental_release_fraction = (
        unmitigated_release * (1.0 - removal_blend)
        + mitigated_release * removal_blend
        + _setting_float(settings, "graphite_saturation_release_penalty", 0.08) * graphite_saturation
    )
    environmental_release_fraction = _clamp(environmental_release_fraction, 0.0, 0.95)
    removal_fraction = _clamp(spray_removal_fraction * removal_blend, 0.0, 1.0 - environmental_release_fraction)
    graphite_retention_capacity = _setting_float(settings, "graphite_retention_fraction", 0.18)
    graphite_retention_fraction = _clamp(
        graphite_retention_capacity * (1.0 - graphite_saturation),
        0.0,
        1.0 - environmental_release_fraction - removal_fraction,
    )
    circulating_inventory_fraction = max(
        1.0 - environmental_release_fraction - removal_fraction - graphite_retention_fraction,
        0.0,
    )
    relative_production_rate = (
        max(float(thermal_power_mw), 0.0)
        / max(reference_power_mwth, 1.0e-9)
        * max(li6_fraction, 0.0)
        / max(reference_li6_fraction, 1.0e-12)
    )

    return {
        "model": str(settings.get("model", "tmsr_tritium_distribution_screen")),
        "relative_production_rate": _round_float(relative_production_rate),
        "thermal_power_mw": _round_float(float(thermal_power_mw)),
        "fuel_salt_volume_m3": _round_float(float(fuel_salt_volume_m3)),
        "lithium6_atom_fraction": _round_float(li6_fraction),
        "gas_stripping_efficiency": _round_float(gas_stripping_efficiency),
        "graphite_saturation_fraction": _round_float(graphite_saturation),
        "environmental_release_fraction": _round_float(environmental_release_fraction),
        "removal_fraction": _round_float(removal_fraction),
        "graphite_retention_fraction": _round_float(graphite_retention_fraction),
        "circulating_inventory_fraction": _round_float(circulating_inventory_fraction),
        "control_effect": _tritium_control_effect(environmental_release_fraction),
        "basis": "Reduced-order distribution screen inspired by the 10 MWe TMSR tritium-removal study.",
        "source": "https://doi.org/10.1016/j.anucene.2023.110272",
    }


def build_graphite_lifetime_summary(
    config: Any,
    *,
    reduced_order_flow: Mapping[str, Any],
    thermal_power_mw: float,
) -> dict[str, Any]:
    settings = _mapping_section(config, "graphite_lifetime")
    fuel_volume_fraction = _fuel_volume_fraction(config, reduced_order_flow)
    target_fuel_volume_fraction = _setting_float(settings, "target_fuel_volume_fraction", 0.08)
    volume_fraction_penalty = abs(fuel_volume_fraction - target_fuel_volume_fraction) / max(target_fuel_volume_fraction, 1.0e-9)
    control_channel_fraction = _control_channel_fraction(reduced_order_flow)
    core_zoning_credit = _setting_float(settings, "core_zoning_flattening_credit", 0.0)
    hpa_credit = _setting_float(
        settings,
        "hexagonal_prism_assembly_credit",
        0.12 if str(settings.get("assembly_style", "")).lower() == "hexagonal_prism" else 0.0,
    )
    configured_peaking = settings.get("fast_flux_peaking_factor")
    if configured_peaking is None:
        fast_flux_peaking_factor = 1.0 + 0.35 * volume_fraction_penalty + 0.55 * control_channel_fraction
        fast_flux_peaking_factor -= core_zoning_credit + hpa_credit
    else:
        fast_flux_peaking_factor = float(configured_peaking)
    fast_flux_peaking_factor = max(fast_flux_peaking_factor, 0.55)

    fast_flux = settings.get("nominal_max_fast_flux_n_cm2_s")
    if fast_flux is None:
        active_core_volume_m3 = _active_core_volume_m3(config)
        power_density_mw_m3 = max(float(thermal_power_mw), 0.0) / max(active_core_volume_m3, 1.0e-9)
        reference_power_density = _setting_float(settings, "reference_power_density_mw_m3", 100.0)
        reference_fast_flux = _setting_float(settings, "reference_fast_flux_n_cm2_s", 9.5e13)
        fast_flux = reference_fast_flux * (max(power_density_mw_m3, 1.0e-9) / reference_power_density) ** 0.9
        fast_flux *= fast_flux_peaking_factor

    fluence_limit = _setting_float(settings, "fast_fluence_limit_n_cm2", DEFAULT_GRAPHITE_FAST_FLUENCE_LIMIT_N_CM2)
    capacity_factor = _clamp(_setting_float(settings, "capacity_factor", 0.85), 0.0, 1.0)
    lifespan_years = fluence_limit / max(float(fast_flux) * SECONDS_PER_YEAR * max(capacity_factor, 1.0e-9), 1.0e-12)
    target_lifespan_years = _setting_float(settings, "target_lifespan_years", 8.0)

    return {
        "model": str(settings.get("model", "fast_flux_graphite_lifetime_screen")),
        "fuel_volume_fraction": _round_float(fuel_volume_fraction),
        "target_fuel_volume_fraction": _round_float(target_fuel_volume_fraction),
        "control_channel_fraction": _round_float(control_channel_fraction),
        "fast_flux_peaking_factor": _round_float(fast_flux_peaking_factor),
        "nominal_max_fast_flux_n_cm2_s": _round_float(float(fast_flux)),
        "fast_fluence_limit_n_cm2": _round_float(fluence_limit),
        "capacity_factor": _round_float(capacity_factor),
        "estimated_lifespan_years": _round_float(lifespan_years),
        "target_lifespan_years": _round_float(target_lifespan_years),
        "lifetime_margin": _round_float(lifespan_years / max(target_lifespan_years, 1.0e-9)),
        "screening_status": "pass" if lifespan_years >= target_lifespan_years else "watch",
        "basis": "Fast-flux flattening and fluence-limit screen from recent SINAP graphite lifetime studies.",
        "source": "https://doi.org/10.3390/jne5020012",
        "deformation_source": "https://doi.org/10.3390/en17112469",
    }


def _mapping_section(config: Any, key: str) -> Mapping[str, Any]:
    data = getattr(config, "data", config if isinstance(config, Mapping) else {})
    section = data.get(key, {}) if isinstance(data, Mapping) else {}
    return section if isinstance(section, Mapping) else {}


def _setting_float(settings: Mapping[str, Any], key: str, default: float) -> float:
    value = settings.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _infer_lithium6_fraction(config: Any) -> float:
    reactor = getattr(config, "reactor", {})
    if isinstance(reactor, Mapping) and "lithium7_enrichment" in reactor:
        return max(1.0 - float(reactor["lithium7_enrichment"]), 0.0)
    return 5.0e-4


def _fuel_volume_fraction(config: Any, reduced_order_flow: Mapping[str, Any]) -> float:
    active_flow = reduced_order_flow.get("active_flow", {})
    active_salt_volume_m3 = float(active_flow.get("total_salt_volume_cm3", 0.0)) * 1.0e-6
    active_core_volume_m3 = _active_core_volume_m3(config)
    return _clamp(active_salt_volume_m3 / max(active_core_volume_m3, 1.0e-12), 0.0, 1.0)


def _active_core_volume_m3(config: Any) -> float:
    geometry = getattr(config, "geometry", {})
    if not isinstance(geometry, Mapping):
        return 0.0
    core_radius_cm = float(geometry.get("core_radius", 0.0))
    active_height_cm = float(geometry.get("active_core_height_cm", geometry.get("height_cm", 0.0)))
    return math.pi * core_radius_cm * core_radius_cm * active_height_cm * 1.0e-6


def _control_channel_fraction(reduced_order_flow: Mapping[str, Any]) -> float:
    active_flow = reduced_order_flow.get("active_flow", {})
    variant_counts = active_flow.get("variant_counts", {})
    if not isinstance(variant_counts, Mapping):
        return 0.0
    total = sum(int(value) for value in variant_counts.values())
    if total <= 0:
        return 0.0
    control = sum(
        int(value)
        for key, value in variant_counts.items()
        if "control" in str(key).lower()
    )
    return control / total


def _tritium_control_effect(environmental_release_fraction: float) -> str:
    if environmental_release_fraction <= 0.12:
        return "strong"
    if environmental_release_fraction <= 0.25:
        return "moderate"
    return "weak"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_float(value: float) -> float:
    return round(float(value), 6)
