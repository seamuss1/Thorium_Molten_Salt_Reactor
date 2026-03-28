from __future__ import annotations

from collections import Counter
from typing import Any

from thorium_reactor.flow.properties import average_primary_temperature_c, evaluate_fluid_properties


DEFAULT_ALLOCATION_RULE = "pressure_balanced"
LEGACY_ALLOCATION_RULE = "salt_area_weighted"
ACTIVE_CHANNEL_SELECTION = "plenum_connected_salt_bearing_channels"
DISCONNECTED_INVENTORY_SELECTION = "reflector_backed_salt_bearing_channels"


def build_reduced_order_flow_summary(
    config: Any,
    connectivity_summary: dict[str, Any],
    primary_mass_flow_kg_s: float,
) -> dict[str, Any]:
    reduced_order_config = config.flow.get("reduced_order", {})
    allocation_rule = reduced_order_config.get("allocation_rule", DEFAULT_ALLOCATION_RULE)
    if allocation_rule not in {DEFAULT_ALLOCATION_RULE, LEGACY_ALLOCATION_RULE}:
        raise ValueError(f"Unsupported reduced-order flow allocation rule: {allocation_rule}")

    salt_material_name = config.geometry.get("salt_material", "fuel_salt")
    bulk_temperature_c = average_primary_temperature_c(config.reactor)
    salt_properties = evaluate_fluid_properties(
        config.materials[salt_material_name],
        temperature_c=bulk_temperature_c,
    )
    density_kg_m3 = float(salt_properties["density_kg_m3"])

    active_channels: list[dict[str, Any]] = []
    disconnected_channels: list[dict[str, Any]] = []
    for channel in connectivity_summary.get("channels", []):
        salt_area_cm2 = float(channel.get("salt_cross_section_area_cm2", 0.0))
        if salt_area_cm2 <= 0.0:
            continue
        if channel.get("interface_class") == "plenum_connected":
            active_channels.append(channel)
        else:
            disconnected_channels.append(channel)

    total_weight = sum(_allocation_weight(channel, allocation_rule) for channel in active_channels)
    total_area_cm2 = sum(float(channel["salt_cross_section_area_cm2"]) for channel in active_channels)
    total_volume_cm3 = sum(float(channel["salt_volume_cm3"]) for channel in active_channels)
    total_volumetric_flow_m3_s = primary_mass_flow_kg_s / density_kg_m3 if density_kg_m3 > 0.0 else 0.0
    representative_velocity_m_s = (
        total_volumetric_flow_m3_s / (total_area_cm2 * 1.0e-4) if total_area_cm2 > 0.0 else 0.0
    )
    representative_residence_time_s = (
        (total_volume_cm3 * 1.0e-6) / total_volumetric_flow_m3_s if total_volumetric_flow_m3_s > 0.0 else 0.0
    )

    active_channel_records: list[dict[str, Any]] = []
    variant_rollups: dict[str, dict[str, float | int | str]] = {}
    for channel in active_channels:
        weight = _allocation_weight(channel, allocation_rule)
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

    disconnected_variant_counts = Counter(str(channel["variant"]) for channel in disconnected_channels)
    disconnected_area_cm2 = sum(float(channel["salt_cross_section_area_cm2"]) for channel in disconnected_channels)
    disconnected_volume_cm3 = sum(float(channel["salt_volume_cm3"]) for channel in disconnected_channels)

    return {
        "status": "completed" if active_channels else "no_active_flow_channels",
        "allocation_rule": allocation_rule,
        "active_channel_selection": ACTIVE_CHANNEL_SELECTION,
        "disconnected_inventory_selection": DISCONNECTED_INVENTORY_SELECTION,
        "salt_density_kg_m3": _round_float(density_kg_m3),
        "salt_bulk_temperature_c": _round_float(bulk_temperature_c),
        "salt_properties": salt_properties,
        "primary_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
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
            "channel_count": len(disconnected_channels),
            "variant_counts": dict(sorted(disconnected_variant_counts.items())),
            "salt_area_cm2": _round_float(disconnected_area_cm2),
            "salt_volume_cm3": _round_float(disconnected_volume_cm3),
        },
        "variant_summary": variant_summary,
        "active_channels": active_channel_records,
    }


def _allocation_weight(channel: dict[str, Any], allocation_rule: str) -> float:
    if allocation_rule == LEGACY_ALLOCATION_RULE:
        return float(channel["salt_cross_section_area_cm2"])
    if allocation_rule == DEFAULT_ALLOCATION_RULE:
        area_cm2 = float(channel["salt_cross_section_area_cm2"])
        hydraulic_diameter_cm = float(channel.get("salt_hydraulic_diameter_cm", 0.0))
        flow_length_cm = area_cm2 and float(channel.get("salt_volume_cm3", 0.0)) / area_cm2 or 0.0
        if area_cm2 <= 0.0 or hydraulic_diameter_cm <= 0.0 or flow_length_cm <= 0.0:
            return 0.0
        # First-pass conductance proxy for equal-dp channel splitting.
        return area_cm2 * hydraulic_diameter_cm * hydraulic_diameter_cm / flow_length_cm
    raise ValueError(f"Unsupported reduced-order flow allocation rule: {allocation_rule}")

def _round_float(value: float) -> float:
    return round(float(value), 6)
