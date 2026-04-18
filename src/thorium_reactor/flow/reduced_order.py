from __future__ import annotations

from collections import Counter
from typing import Any

from thorium_reactor.flow.properties import average_primary_temperature_c, evaluate_fluid_properties
from thorium_reactor.modeling import get_core_model


DEFAULT_ALLOCATION_RULE = "salt_area_weighted"
PRESSURE_BALANCED_ALLOCATION_RULE = "pressure_balanced"
CONFIGURED_ACTIVE_SELECTION = "configured_active_variants"
CONFIGURED_STAGNANT_SELECTION = "configured_non_active_variants"
HOMOGENIZED_CORE_SELECTION = "homogenized_core"


def build_reduced_order_flow_summary(
    config: Any,
    connectivity_summary: dict[str, Any],
    primary_mass_flow_kg_s: float,
) -> dict[str, Any]:
    reduced_order_config = config.flow.get("reduced_order", {})
    allocation_rule = reduced_order_config.get("allocation_rule", DEFAULT_ALLOCATION_RULE)
    if allocation_rule not in {DEFAULT_ALLOCATION_RULE, PRESSURE_BALANCED_ALLOCATION_RULE}:
        raise ValueError(f"Unsupported reduced-order flow allocation rule: {allocation_rule}")
    core_model = get_core_model(config)

    salt_material_name = config.geometry.get("salt_material", "fuel_salt")
    bulk_temperature_c = average_primary_temperature_c(config.reactor)
    salt_properties = evaluate_fluid_properties(
        config.materials[salt_material_name],
        temperature_c=bulk_temperature_c,
    )
    density_kg_m3 = float(salt_properties["density_kg_m3"])
    total_volumetric_flow_m3_s = primary_mass_flow_kg_s / density_kg_m3 if density_kg_m3 > 0.0 else 0.0

    if core_model["kind"] == "homogenized_core":
        return _build_homogenized_core_summary(
            core_model=core_model,
            allocation_rule=allocation_rule,
            density_kg_m3=density_kg_m3,
            bulk_temperature_c=bulk_temperature_c,
            salt_properties=salt_properties,
            primary_mass_flow_kg_s=primary_mass_flow_kg_s,
            total_volumetric_flow_m3_s=total_volumetric_flow_m3_s,
            geometric_inventory=connectivity_summary.get("interface_metrics", {}),
        )

    active_channels: list[dict[str, Any]] = []
    non_active_channels: list[dict[str, Any]] = []
    stagnant_channels: list[dict[str, Any]] = []
    active_variants = set(core_model["active_variants"])
    stagnant_variants = set(core_model["stagnant_variants"])
    for channel in connectivity_summary.get("channels", []):
        salt_area_cm2 = float(channel.get("salt_cross_section_area_cm2", 0.0))
        if salt_area_cm2 <= 0.0:
            continue
        if str(channel.get("variant")) in active_variants:
            active_channels.append(channel)
        else:
            non_active_channels.append(channel)
            if str(channel.get("variant")) in stagnant_variants:
                stagnant_channels.append(channel)

    total_weight = sum(_allocation_weight(channel, allocation_rule, core_model["family_split_weights"]) for channel in active_channels)
    total_area_cm2 = sum(float(channel["salt_cross_section_area_cm2"]) for channel in active_channels)
    total_volume_cm3 = sum(float(channel["salt_volume_cm3"]) for channel in active_channels)
    representative_velocity_m_s = (
        total_volumetric_flow_m3_s / (total_area_cm2 * 1.0e-4) if total_area_cm2 > 0.0 else 0.0
    )
    representative_residence_time_s = (
        (total_volume_cm3 * 1.0e-6) / total_volumetric_flow_m3_s if total_volumetric_flow_m3_s > 0.0 else 0.0
    )

    active_channel_records: list[dict[str, Any]] = []
    variant_rollups: dict[str, dict[str, float | int | str]] = {}
    for channel in active_channels:
        weight = _allocation_weight(channel, allocation_rule, core_model["family_split_weights"])
        mass_flow_fraction = weight / total_weight if total_weight > 0.0 else 0.0
        allocated_mass_flow_kg_s = primary_mass_flow_kg_s * mass_flow_fraction
        allocated_volumetric_flow_m3_s = total_volumetric_flow_m3_s * mass_flow_fraction
        salt_area_cm2 = float(channel["salt_cross_section_area_cm2"])
        salt_volume_cm3 = float(channel["salt_volume_cm3"])
        velocity_m_s = allocated_volumetric_flow_m3_s / (salt_area_cm2 * 1.0e-4) if salt_area_cm2 > 0.0 else 0.0
        residence_time_s = (
            (salt_volume_cm3 * 1.0e-6) / allocated_volumetric_flow_m3_s if allocated_volumetric_flow_m3_s > 0.0 else 0.0
        )

        active_channel_records.append(
            {
                "name": channel["name"],
                "variant": channel["variant"],
                "salt_cross_section_area_cm2": _round_float(salt_area_cm2),
                "salt_volume_cm3": _round_float(salt_volume_cm3),
                "mass_flow_fraction": _round_float(mass_flow_fraction),
                "allocated_mass_flow_kg_s": _round_float(allocated_mass_flow_kg_s),
                "allocated_volumetric_flow_m3_s": _round_float(allocated_volumetric_flow_m3_s),
                "allocation_weight": _round_float(weight),
                "velocity_m_s": _round_float(velocity_m_s),
                "residence_time_s": _round_float(residence_time_s),
            }
        )

        rollup = variant_rollups.setdefault(
            str(channel["variant"]),
            {
                "variant": str(channel["variant"]),
                "channel_count": 0,
                "total_flow_area_cm2": 0.0,
                "total_salt_volume_cm3": 0.0,
                "allocated_mass_flow_kg_s": 0.0,
                "allocated_volumetric_flow_m3_s": 0.0,
            },
        )
        rollup["channel_count"] += 1
        rollup["total_flow_area_cm2"] += salt_area_cm2
        rollup["total_salt_volume_cm3"] += salt_volume_cm3
        rollup["allocated_mass_flow_kg_s"] += allocated_mass_flow_kg_s
        rollup["allocated_volumetric_flow_m3_s"] += allocated_volumetric_flow_m3_s

    variant_summary: list[dict[str, Any]] = []
    for variant_name in sorted(variant_rollups):
        rollup = variant_rollups[variant_name]
        total_variant_area_cm2 = float(rollup["total_flow_area_cm2"])
        total_variant_volume_cm3 = float(rollup["total_salt_volume_cm3"])
        total_variant_volumetric_flow = float(rollup["allocated_volumetric_flow_m3_s"])
        velocity_m_s = total_variant_volumetric_flow / (total_variant_area_cm2 * 1.0e-4) if total_variant_area_cm2 > 0.0 else 0.0
        residence_time_s = (
            (total_variant_volume_cm3 * 1.0e-6) / total_variant_volumetric_flow
            if total_variant_volumetric_flow > 0.0
            else 0.0
        )
        variant_summary.append(
            {
                "variant": variant_name,
                "channel_count": int(rollup["channel_count"]),
                "total_flow_area_cm2": _round_float(total_variant_area_cm2),
                "total_salt_volume_cm3": _round_float(total_variant_volume_cm3),
                "allocated_mass_flow_kg_s": _round_float(float(rollup["allocated_mass_flow_kg_s"])),
                "allocated_volumetric_flow_m3_s": _round_float(total_variant_volumetric_flow),
                "velocity_m_s": _round_float(velocity_m_s),
                "residence_time_s": _round_float(residence_time_s),
            }
        )

    non_active_variant_counts = Counter(str(channel["variant"]) for channel in non_active_channels)
    non_active_area_cm2 = sum(float(channel["salt_cross_section_area_cm2"]) for channel in non_active_channels)
    non_active_volume_cm3 = sum(float(channel["salt_volume_cm3"]) for channel in non_active_channels)
    stagnant_variant_counts = Counter(str(channel["variant"]) for channel in stagnant_channels)
    stagnant_area_cm2 = sum(float(channel["salt_cross_section_area_cm2"]) for channel in stagnant_channels)
    stagnant_volume_cm3 = sum(float(channel["salt_volume_cm3"]) for channel in stagnant_channels)

    return {
        "status": "completed" if active_channels else "no_active_flow_channels",
        "allocation_rule": allocation_rule,
        "active_channel_selection": CONFIGURED_ACTIVE_SELECTION,
        "disconnected_inventory_selection": CONFIGURED_STAGNANT_SELECTION,
        "core_model": core_model,
        "salt_density_kg_m3": _round_float(density_kg_m3),
        "salt_bulk_temperature_c": _round_float(bulk_temperature_c),
        "salt_properties": salt_properties,
        "primary_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
        "geometric_inventory": connectivity_summary.get("interface_metrics", {}),
        "active_flow": {
            "channel_count": len(active_channels),
            "variant_counts": dict(sorted(Counter(str(channel["variant"]) for channel in active_channels).items())),
            "total_flow_area_cm2": _round_float(total_area_cm2),
            "total_salt_volume_cm3": _round_float(total_volume_cm3),
            "total_volumetric_flow_m3_s": _round_float(total_volumetric_flow_m3_s),
            "representative_velocity_m_s": _round_float(representative_velocity_m_s),
            "representative_residence_time_s": _round_float(representative_residence_time_s),
        },
        "disconnected_inventory": {
            "channel_count": len(non_active_channels),
            "variant_counts": dict(sorted(non_active_variant_counts.items())),
            "salt_area_cm2": _round_float(non_active_area_cm2),
            "salt_volume_cm3": _round_float(non_active_volume_cm3),
        },
        "stagnant_inventory": {
            "channel_count": len(stagnant_channels),
            "variant_counts": dict(sorted(stagnant_variant_counts.items())),
            "salt_area_cm2": _round_float(stagnant_area_cm2),
            "salt_volume_cm3": _round_float(stagnant_volume_cm3),
        },
        "variant_summary": variant_summary,
        "active_channels": active_channel_records,
    }


def _build_homogenized_core_summary(
    *,
    core_model: dict[str, Any],
    allocation_rule: str,
    density_kg_m3: float,
    bulk_temperature_c: float,
    salt_properties: dict[str, Any],
    primary_mass_flow_kg_s: float,
    total_volumetric_flow_m3_s: float,
    geometric_inventory: dict[str, Any],
) -> dict[str, Any]:
    total_area_cm2 = float(core_model["effective_flow_area_cm2"])
    total_volume_cm3 = float(core_model["active_salt_volume_cm3"])
    hydraulic_diameter_cm = float(core_model["hydraulic_diameter_cm"])
    representative_velocity_m_s = (
        total_volumetric_flow_m3_s / (total_area_cm2 * 1.0e-4) if total_area_cm2 > 0.0 else 0.0
    )
    representative_residence_time_s = (
        (total_volume_cm3 * 1.0e-6) / total_volumetric_flow_m3_s if total_volumetric_flow_m3_s > 0.0 else 0.0
    )
    return {
        "status": "completed" if total_area_cm2 > 0.0 else "no_active_flow_area",
        "allocation_rule": allocation_rule,
        "active_channel_selection": HOMOGENIZED_CORE_SELECTION,
        "disconnected_inventory_selection": CONFIGURED_STAGNANT_SELECTION,
        "core_model": core_model,
        "salt_density_kg_m3": _round_float(density_kg_m3),
        "salt_bulk_temperature_c": _round_float(bulk_temperature_c),
        "salt_properties": salt_properties,
        "primary_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
        "geometric_inventory": geometric_inventory,
        "active_flow": {
            "channel_count": 1,
            "variant_counts": {"homogenized_core": 1},
            "total_flow_area_cm2": _round_float(total_area_cm2),
            "total_salt_volume_cm3": _round_float(total_volume_cm3),
            "hydraulic_diameter_cm": _round_float(hydraulic_diameter_cm),
            "total_volumetric_flow_m3_s": _round_float(total_volumetric_flow_m3_s),
            "representative_velocity_m_s": _round_float(representative_velocity_m_s),
            "representative_residence_time_s": _round_float(representative_residence_time_s),
        },
        "disconnected_inventory": {
            "channel_count": 0,
            "variant_counts": {},
            "salt_area_cm2": 0.0,
            "salt_volume_cm3": 0.0,
        },
        "stagnant_inventory": {
            "channel_count": 0,
            "variant_counts": {},
            "salt_area_cm2": 0.0,
            "salt_volume_cm3": 0.0,
        },
        "variant_summary": [
            {
                "variant": "homogenized_core",
                "channel_count": 1,
                "total_flow_area_cm2": _round_float(total_area_cm2),
                "total_salt_volume_cm3": _round_float(total_volume_cm3),
                "allocated_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
                "allocated_volumetric_flow_m3_s": _round_float(total_volumetric_flow_m3_s),
                "velocity_m_s": _round_float(representative_velocity_m_s),
                "residence_time_s": _round_float(representative_residence_time_s),
            }
        ],
        "active_channels": [
            {
                "name": "homogenized_core",
                "variant": "homogenized_core",
                "salt_cross_section_area_cm2": _round_float(total_area_cm2),
                "salt_volume_cm3": _round_float(total_volume_cm3),
                "mass_flow_fraction": 1.0,
                "allocated_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
                "allocated_volumetric_flow_m3_s": _round_float(total_volumetric_flow_m3_s),
                "allocation_weight": 1.0,
                "velocity_m_s": _round_float(representative_velocity_m_s),
                "residence_time_s": _round_float(representative_residence_time_s),
            }
        ],
    }


def _allocation_weight(
    channel: dict[str, Any],
    allocation_rule: str,
    family_split_weights: dict[str, float],
) -> float:
    family_weight = max(float(family_split_weights.get(str(channel.get("variant", "")), 1.0)), 0.0)
    if allocation_rule == DEFAULT_ALLOCATION_RULE:
        return family_weight * float(channel["salt_cross_section_area_cm2"])
    if allocation_rule == PRESSURE_BALANCED_ALLOCATION_RULE:
        area_cm2 = float(channel["salt_cross_section_area_cm2"])
        hydraulic_diameter_cm = float(channel.get("salt_hydraulic_diameter_cm", 0.0))
        flow_length_cm = area_cm2 and float(channel.get("salt_volume_cm3", 0.0)) / area_cm2 or 0.0
        if area_cm2 <= 0.0 or hydraulic_diameter_cm <= 0.0 or flow_length_cm <= 0.0:
            return 0.0
        # First-pass conductance proxy for equal-dp channel splitting.
        return family_weight * area_cm2 * hydraulic_diameter_cm * hydraulic_diameter_cm / flow_length_cm
    raise ValueError(f"Unsupported reduced-order flow allocation rule: {allocation_rule}")

def _round_float(value: float) -> float:
    return round(float(value), 6)
