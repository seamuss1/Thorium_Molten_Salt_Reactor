from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any


CHANNEL_RADIUS_TOLERANCE = 1.0e-3


@dataclass(slots=True)
class ResolvedMSRGeometry:
    channels: list[dict[str, Any]]
    channel_variant_counts: dict[str, int]
    channel_envelope_radius: float
    core_radius: float
    plenum_radius: float
    reflector_outer_radius: float
    downcomer_liner_outer_radius: float
    downcomer_outer_radius: float
    vessel_outer_radius: float
    guard_gap_outer_radius: float
    guard_vessel_outer_radius: float
    bottom_z: float
    lower_plenum_top_z: float
    active_top_z: float
    upper_plenum_top_z: float
    top_z: float
    height: float

    @property
    def active_bottom_z(self) -> float:
        return self.lower_plenum_top_z

    @property
    def active_height(self) -> float:
        return self.active_top_z - self.active_bottom_z


def resolve_msr_geometry(config: Any) -> ResolvedMSRGeometry:
    geometry = config.geometry
    base_layers = [dict(layer) for layer in geometry["channel_layers"]]
    specials = _resolve_special_channels(geometry)
    channels = _build_channels(geometry, base_layers, specials)
    variant_counts = Counter(channel["variant"] for channel in channels)

    channel_envelope_radius = max(
        max(float(layer["outer_radius"]) for layer in channel["layers"]) for channel in channels
    )
    core_radius = float(geometry["core_radius"])
    plenum_radius = float(geometry.get("plenum_radius", core_radius * 0.68))
    reflector_outer_radius = float(geometry.get("reflector_outer_radius", core_radius + 11.2))
    downcomer_liner_outer_radius = float(
        geometry.get("downcomer_liner_outer_radius", reflector_outer_radius + 1.4)
    )
    downcomer_outer_radius = float(
        geometry.get("downcomer_outer_radius", downcomer_liner_outer_radius + 7.2)
    )
    vessel_outer_radius = float(geometry.get("vessel_outer_radius", downcomer_outer_radius + 3.2))
    guard_gap_outer_radius = float(geometry.get("guard_gap_outer_radius", vessel_outer_radius + 3.0))
    guard_vessel_outer_radius = float(
        geometry.get("guard_vessel_outer_radius", guard_gap_outer_radius + 2.6)
    )

    lower_plenum_height = float(geometry.get("lower_plenum_height_cm", 24.0))
    active_core_height = float(geometry.get("active_core_height_cm", 192.0))
    upper_plenum_height = float(geometry.get("upper_plenum_height_cm", 16.0))
    cover_gas_height = float(geometry.get("cover_gas_height_cm", 8.0))
    total_height = lower_plenum_height + active_core_height + upper_plenum_height + cover_gas_height

    bottom_z = -total_height / 2.0
    lower_plenum_top_z = bottom_z + lower_plenum_height
    active_top_z = lower_plenum_top_z + active_core_height
    upper_plenum_top_z = active_top_z + upper_plenum_height
    top_z = upper_plenum_top_z + cover_gas_height

    return ResolvedMSRGeometry(
        channels=channels,
        channel_variant_counts=dict(variant_counts),
        channel_envelope_radius=channel_envelope_radius,
        core_radius=core_radius,
        plenum_radius=plenum_radius,
        reflector_outer_radius=reflector_outer_radius,
        downcomer_liner_outer_radius=downcomer_liner_outer_radius,
        downcomer_outer_radius=downcomer_outer_radius,
        vessel_outer_radius=vessel_outer_radius,
        guard_gap_outer_radius=guard_gap_outer_radius,
        guard_vessel_outer_radius=guard_vessel_outer_radius,
        bottom_z=bottom_z,
        lower_plenum_top_z=lower_plenum_top_z,
        active_top_z=active_top_z,
        upper_plenum_top_z=upper_plenum_top_z,
        top_z=top_z,
        height=total_height,
    )


def build_msr_geometry_description(config: Any, resolved: ResolvedMSRGeometry | None = None) -> dict[str, Any]:
    geometry = config.geometry
    resolved = resolved or resolve_msr_geometry(config)
    channel_flow_interfaces = {
        channel["name"]: channel for channel in _build_channel_flow_interfaces(geometry, resolved)
    }
    shells = _build_shells(geometry, resolved)
    channels = [
        {
            "name": channel["name"],
            "variant": channel["variant"],
            "x": channel["x"],
            "y": channel["y"],
            "layers": [dict(layer) for layer in channel["layers"]],
            "z_min": resolved.active_bottom_z,
            "z_max": resolved.active_top_z,
            "lower_boundary_region": channel_flow_interfaces[channel["name"]]["lower_boundary_region"],
            "upper_boundary_region": channel_flow_interfaces[channel["name"]]["upper_boundary_region"],
            "interface_class": channel_flow_interfaces[channel["name"]]["interface_class"],
            "salt_cross_section_area_cm2": channel_flow_interfaces[channel["name"]]["salt_cross_section_area_cm2"],
            "salt_volume_cm3": channel_flow_interfaces[channel["name"]]["salt_volume_cm3"],
        }
        for channel in resolved.channels
    ]
    render_solids = shells + _build_channel_render_solids(channels)
    return {
        "name": config.name,
        "type": "detailed_molten_salt_reactor",
        "pitch": geometry["pitch"],
        "height": resolved.height,
        "z_min": resolved.bottom_z,
        "z_max": resolved.top_z,
        "core_radius": resolved.core_radius,
        "plenum_radius": resolved.plenum_radius,
        "reflector_outer_radius": resolved.reflector_outer_radius,
        "guard_vessel_outer_radius": resolved.guard_vessel_outer_radius,
        "channel_variant_counts": dict(resolved.channel_variant_counts),
        "channel_envelope_radius": resolved.channel_envelope_radius,
        "flow_summary": build_msr_flow_summary(config, resolved),
        "shells": shells,
        "channels": channels,
        "render_solids": render_solids,
    }


def build_msr_invariants(config: Any, resolved: ResolvedMSRGeometry | None = None) -> list[dict[str, Any]]:
    geometry = config.geometry
    resolved = resolved or resolve_msr_geometry(config)
    invariants: list[dict[str, Any]] = []

    radial_chain = [
        ("plenum_radius", resolved.plenum_radius),
        ("core_radius", resolved.core_radius),
        ("reflector_outer_radius", resolved.reflector_outer_radius),
        ("downcomer_liner_outer_radius", resolved.downcomer_liner_outer_radius),
        ("downcomer_outer_radius", resolved.downcomer_outer_radius),
        ("vessel_outer_radius", resolved.vessel_outer_radius),
        ("guard_gap_outer_radius", resolved.guard_gap_outer_radius),
        ("guard_vessel_outer_radius", resolved.guard_vessel_outer_radius),
        ("half_pitch_boundary", float(geometry["pitch"]) / 2.0),
    ]
    previous_radius = 0.0
    for name, radius in radial_chain:
        passed = radius > previous_radius
        invariants.append(
            {
                "name": f"radial_stack::{name}",
                "passed": passed,
                "message": (
                    f"{name} expands monotonically to {radius:.3f} cm."
                    if passed
                    else f"{name} must be larger than the previous radius ({previous_radius:.3f} cm)."
                ),
            }
        )
        previous_radius = radius

    for channel in resolved.channels:
        previous_outer = 0.0
        for layer in channel["layers"]:
            inner = float(layer.get("inner_radius", previous_outer))
            outer = float(layer["outer_radius"])
            passed = inner >= previous_outer and outer > inner
            invariants.append(
                {
                    "name": f"monotonic_radius::{channel['name']}::{layer['name']}",
                    "passed": passed,
                    "message": (
                        f"{channel['name']} layer {layer['name']} radii are monotonic."
                        if passed
                        else f"{channel['name']} layer {layer['name']} has invalid radii ({inner}, {outer})."
                    ),
                }
            )
            previous_outer = outer
        fit_limit = math.hypot(float(channel["x"]), float(channel["y"])) + previous_outer
        fits = fit_limit <= resolved.core_radius
        invariants.append(
            {
                "name": f"channel_fit::{channel['name']}",
                "passed": fits,
                "message": (
                    f"Channel {channel['name']} fits inside the active graphite core."
                    if fits
                    else f"Channel {channel['name']} exceeds the active graphite core radius."
                ),
            }
        )
    return invariants


def build_msr_flow_summary(config: Any, resolved: ResolvedMSRGeometry | None = None) -> dict[str, Any]:
    geometry = config.geometry
    resolved = resolved or resolve_msr_geometry(config)
    channel_interfaces = _build_channel_flow_interfaces(geometry, resolved)

    interface_metrics = {
        "plenum_connected_channels": 0,
        "reflector_backed_channels": 0,
        "plenum_connected_salt_bearing_channels": 0,
        "reflector_backed_salt_bearing_channels": 0,
        "plenum_connected_salt_area_cm2": 0.0,
        "reflector_backed_salt_area_cm2": 0.0,
        "plenum_connected_salt_volume_cm3": 0.0,
        "reflector_backed_salt_volume_cm3": 0.0,
    }
    variant_counts = {
        "plenum_connected": Counter(),
        "reflector_backed": Counter(),
    }

    for channel in channel_interfaces:
        interface_class = channel["interface_class"]
        interface_metrics[f"{interface_class}_channels"] += 1
        variant_counts[interface_class][channel["variant"]] += 1
        if channel["salt_cross_section_area_cm2"] > 0.0:
            interface_metrics[f"{interface_class}_salt_bearing_channels"] += 1
            interface_metrics[f"{interface_class}_salt_area_cm2"] += channel["salt_cross_section_area_cm2"]
            interface_metrics[f"{interface_class}_salt_volume_cm3"] += channel["salt_volume_cm3"]

    return {
        "plenum_radius_cm": resolved.plenum_radius,
        "active_height_cm": resolved.active_height,
        "interface_metrics": {name: _round_metric(value) for name, value in interface_metrics.items()},
        "variant_counts": {
            name: dict(sorted(counts.items()))
            for name, counts in variant_counts.items()
        },
        "channels": channel_interfaces,
    }


def _resolve_special_channels(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    selectors: list[dict[str, Any]] = []
    for variant_name, spec in geometry.get("special_channels", {}).items():
        selectors.append(
            {
                "variant": variant_name,
                "ring_radius": float(spec["ring_radius"]),
                "every": int(spec.get("every", 1)),
                "start_index": int(spec.get("start_index", 0)),
                "indices": tuple(int(index) for index in spec.get("indices", [])),
                "layers": [dict(layer) for layer in spec["layers"]],
            }
        )
    return selectors


def _build_channels(
    geometry: dict[str, Any],
    base_layers: list[dict[str, Any]],
    specials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    for ring in geometry["rings"]:
        radius = float(ring["radius"])
        count = int(ring["count"])
        positions = _ring_positions(radius, count)
        for index, (x_pos, y_pos) in enumerate(positions):
            layers = [dict(layer) for layer in base_layers]
            variant = "fuel"
            for special in specials:
                if not math.isclose(radius, special["ring_radius"], rel_tol=0.0, abs_tol=CHANNEL_RADIUS_TOLERANCE):
                    continue
                use_special = index in special["indices"] if special["indices"] else (index - special["start_index"]) % special["every"] == 0
                if use_special:
                    layers = [dict(layer) for layer in special["layers"]]
                    variant = special["variant"]
                    break
            channels.append(
                {
                    "name": f"{variant}_{radius:.2f}_{index}",
                    "variant": variant,
                    "ring_radius": radius,
                    "index": index,
                    "x": x_pos,
                    "y": y_pos,
                    "layers": layers,
                }
            )
    return channels


def _build_channel_flow_interfaces(
    geometry: dict[str, Any],
    resolved: ResolvedMSRGeometry,
) -> list[dict[str, Any]]:
    salt_material = geometry.get("salt_material", "fuel_salt")
    interfaces: list[dict[str, Any]] = []
    for channel in resolved.channels:
        envelope_radius = max(float(layer["outer_radius"]) for layer in channel["layers"])
        center_radius = math.hypot(float(channel["x"]), float(channel["y"]))
        plenum_connected = center_radius + envelope_radius <= resolved.plenum_radius + CHANNEL_RADIUS_TOLERANCE
        salt_cross_section_area = 0.0
        for layer in channel["layers"]:
            if layer.get("material") != salt_material:
                continue
            salt_cross_section_area += _annulus_area(layer)
        interfaces.append(
            {
                "name": channel["name"],
                "variant": channel["variant"],
                "lower_boundary_region": "lower_plenum" if plenum_connected else "lower_reflector",
                "upper_boundary_region": "upper_plenum" if plenum_connected else "upper_reflector",
                "interface_class": "plenum_connected" if plenum_connected else "reflector_backed",
                "salt_cross_section_area_cm2": salt_cross_section_area,
                "salt_volume_cm3": salt_cross_section_area * resolved.active_height,
            }
        )
    return interfaces


def _annulus_area(layer: dict[str, Any]) -> float:
    inner_radius = float(layer.get("inner_radius", 0.0))
    outer_radius = float(layer["outer_radius"])
    return math.pi * ((outer_radius * outer_radius) - (inner_radius * inner_radius))


def _round_metric(value: float | int) -> float | int:
    if isinstance(value, int):
        return value
    return round(float(value), 6)


def _ring_positions(radius: float, count: int) -> list[tuple[float, float]]:
    if count == 1:
        return [(0.0, 0.0)]
    return [
        (
            radius * math.cos(2.0 * math.pi * index / count),
            radius * math.sin(2.0 * math.pi * index / count),
        )
        for index in range(count)
    ]


def _build_shells(geometry: dict[str, Any], resolved: ResolvedMSRGeometry) -> list[dict[str, Any]]:
    return [
        {
            "name": "lower_plenum",
            "material": geometry.get("salt_material", "fuel_salt"),
            "inner_radius": 0.0,
            "outer_radius": resolved.plenum_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.active_bottom_z,
            "cutaway": False,
            "opacity": 0.96,
        },
        {
            "name": "lower_reflector",
            "material": geometry["matrix_material"],
            "inner_radius": resolved.plenum_radius,
            "outer_radius": resolved.reflector_outer_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.active_bottom_z,
            "cutaway": True,
            "opacity": 0.22,
        },
        {
            "name": "active_core_graphite",
            "material": geometry["matrix_material"],
            "inner_radius": 0.0,
            "outer_radius": resolved.core_radius,
            "z_min": resolved.active_bottom_z,
            "z_max": resolved.active_top_z,
            "cutaway": True,
            "opacity": 0.16,
        },
        {
            "name": "radial_reflector",
            "material": geometry["matrix_material"],
            "inner_radius": resolved.core_radius,
            "outer_radius": resolved.reflector_outer_radius,
            "z_min": resolved.active_bottom_z,
            "z_max": resolved.active_top_z,
            "cutaway": True,
            "opacity": 0.24,
        },
        {
            "name": "upper_plenum",
            "material": geometry.get("salt_material", "fuel_salt"),
            "inner_radius": 0.0,
            "outer_radius": resolved.plenum_radius,
            "z_min": resolved.active_top_z,
            "z_max": resolved.upper_plenum_top_z,
            "cutaway": False,
            "opacity": 0.9,
        },
        {
            "name": "cover_gas",
            "material": geometry["background_material"],
            "inner_radius": 0.0,
            "outer_radius": resolved.plenum_radius,
            "z_min": resolved.upper_plenum_top_z,
            "z_max": resolved.top_z,
            "cutaway": False,
            "opacity": 0.12,
        },
        {
            "name": "upper_reflector",
            "material": geometry["matrix_material"],
            "inner_radius": resolved.plenum_radius,
            "outer_radius": resolved.reflector_outer_radius,
            "z_min": resolved.active_top_z,
            "z_max": resolved.top_z,
            "cutaway": True,
            "opacity": 0.22,
        },
        {
            "name": "downcomer_liner",
            "material": geometry.get("structure_material", "pipe"),
            "inner_radius": resolved.reflector_outer_radius,
            "outer_radius": resolved.downcomer_liner_outer_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.top_z,
            "cutaway": True,
            "opacity": 0.82,
        },
        {
            "name": "downcomer",
            "material": geometry.get("salt_material", "fuel_salt"),
            "inner_radius": resolved.downcomer_liner_outer_radius,
            "outer_radius": resolved.downcomer_outer_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.top_z,
            "cutaway": True,
            "opacity": 0.42,
        },
        {
            "name": "reactor_vessel",
            "material": geometry.get("structure_material", "pipe"),
            "inner_radius": resolved.downcomer_outer_radius,
            "outer_radius": resolved.vessel_outer_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.top_z,
            "cutaway": True,
            "opacity": 0.88,
        },
        {
            "name": "guard_gap",
            "material": geometry["background_material"],
            "inner_radius": resolved.vessel_outer_radius,
            "outer_radius": resolved.guard_gap_outer_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.top_z,
            "cutaway": True,
            "opacity": 0.08,
        },
        {
            "name": "guard_vessel",
            "material": geometry.get("structure_material", "pipe"),
            "inner_radius": resolved.guard_gap_outer_radius,
            "outer_radius": resolved.guard_vessel_outer_radius,
            "z_min": resolved.bottom_z,
            "z_max": resolved.top_z,
            "cutaway": True,
            "opacity": 0.76,
        },
    ]


def _build_channel_render_solids(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible_layers = {
        "fuel": {"fuel_annulus", "primary_pipe"},
        "control_guides": {"control_void", "guide_tube", "bypass_salt"},
        "instrumentation_wells": {"sensor_well", "sensor_sheath"},
    }
    solids: list[dict[str, Any]] = []
    for channel in channels:
        wanted = visible_layers.get(channel["variant"], set())
        for layer in channel["layers"]:
            if wanted and layer["name"] not in wanted:
                continue
            solids.append(
                {
                    "name": f"{channel['name']}::{layer['name']}",
                    "material": layer.get("material"),
                    "inner_radius": float(layer.get("inner_radius", 0.0)),
                    "outer_radius": float(layer["outer_radius"]),
                    "x": float(channel["x"]),
                    "y": float(channel["y"]),
                    "z_min": float(channel["z_min"]),
                    "z_max": float(channel["z_max"]),
                    "cutaway": False,
                    "opacity": 0.95,
                }
            )
    return solids
