from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any

from thorium_reactor.flow.properties import average_primary_temperature_c, evaluate_fluid_properties, evaluate_primary_coolant_properties, primary_coolant_cp_kj_kgk


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
    render_solids = _build_render_solids(geometry, resolved, shells, channels)
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
        "render_layout": geometry.get("render_layout", {}).get("type"),
        "animation": _build_render_animation(config, resolved),
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
    invariants.extend(_build_render_layout_invariants(geometry, resolved))
    invariants.extend(_build_render_physics_invariants(config, resolved))
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
                "salt_hydraulic_diameter_cm": _salt_hydraulic_diameter_cm(channel["layers"], salt_material),
                "salt_volume_cm3": salt_cross_section_area * resolved.active_height,
            }
        )
    return interfaces


def _annulus_area(layer: dict[str, Any]) -> float:
    inner_radius = float(layer.get("inner_radius", 0.0))
    outer_radius = float(layer["outer_radius"])
    return math.pi * ((outer_radius * outer_radius) - (inner_radius * inner_radius))


def _salt_hydraulic_diameter_cm(layers: list[dict[str, Any]], salt_material: str) -> float:
    total_area_cm2 = 0.0
    wetted_perimeter_cm = 0.0
    for layer in layers:
        if layer.get("material") != salt_material:
            continue
        inner_radius = float(layer.get("inner_radius", 0.0))
        outer_radius = float(layer["outer_radius"])
        total_area_cm2 += math.pi * max(outer_radius * outer_radius - inner_radius * inner_radius, 0.0)
        if inner_radius > 0.0:
            wetted_perimeter_cm += 2.0 * math.pi * inner_radius
        wetted_perimeter_cm += 2.0 * math.pi * outer_radius
    if wetted_perimeter_cm <= 0.0:
        return 0.0
    return 4.0 * total_area_cm2 / wetted_perimeter_cm


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


def _build_render_solids(
    geometry: dict[str, Any],
    resolved: ResolvedMSRGeometry,
    shells: list[dict[str, Any]],
    channels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    render_layout = geometry.get("render_layout") or {}
    layout_type = render_layout.get("type")
    if layout_type == "immersed_pool_reference":
        return _build_immersed_pool_render_solids(geometry, resolved, channels, render_layout)
    return shells + _build_channel_render_solids(channels)


def _build_render_animation(config: Any, resolved: ResolvedMSRGeometry) -> dict[str, Any] | None:
    render_layout = config.geometry.get("render_layout") or {}
    if render_layout.get("type") == "immersed_pool_reference":
        return _build_immersed_pool_render_animation(config, resolved, render_layout)
    return None


def _build_immersed_pool_render_solids(
    geometry: dict[str, Any],
    resolved: ResolvedMSRGeometry,
    channels: list[dict[str, Any]],
    render_layout: dict[str, Any],
) -> list[dict[str, Any]]:
    core_offset_x = float(render_layout.get("core_offset_x_cm", 0.0))
    core_offset_y = float(render_layout.get("core_offset_y_cm", 0.0))
    cutaway_start = float(render_layout.get("cutaway_start_rad", 0.92))
    cutaway_stop = float(render_layout.get("cutaway_stop_rad", 5.08))

    pool = render_layout.get("pool", {})
    containment = render_layout.get("containment", {})
    core_box = render_layout.get("core_box", {})
    loop = render_layout.get("primary_loop", {})

    pool_radius = float(pool.get("radius_cm", resolved.core_radius * 2.2))
    pool_wall_thickness = float(pool.get("wall_thickness_cm", 1.8))
    pool_top_z = float(pool.get("top_z_cm", resolved.top_z + 8.0))
    pool_cylinder_bottom_z = float(pool.get("cylinder_bottom_z_cm", resolved.bottom_z - 8.0))
    pool_bottom_head_depth = float(pool.get("bottom_head_depth_cm", resolved.height * 0.18))
    pool_fill_top_z = float(pool.get("fill_top_z_cm", resolved.active_top_z + 16.0))
    pool_material = str(pool.get("material", "coolant_salt"))
    pool_shell_material = str(pool.get("shell_material", geometry.get("structure_material", "pipe")))
    pool_shell_opacity = float(pool.get("shell_opacity", 0.72))
    pool_fill_opacity = float(pool.get("fill_opacity", 0.54))

    containment_inner_radius = float(containment.get("inner_radius_cm", pool_radius + 10.0))
    containment_outer_radius = float(containment.get("outer_radius_cm", containment_inner_radius + 8.0))
    containment_top_z = float(containment.get("top_z_cm", pool_top_z + 12.0))
    containment_bottom_z = float(containment.get("bottom_z_cm", pool_cylinder_bottom_z - pool_bottom_head_depth - 10.0))
    containment_material = str(containment.get("material", geometry.get("structure_material", "pipe")))
    containment_opacity = float(containment.get("opacity", 0.58))

    pool_solids = _build_dished_vessel_solids(
        "primary_pool",
        inner_radius=pool_radius - pool_wall_thickness,
        shell_thickness=pool_wall_thickness,
        cylinder_bottom_z=pool_cylinder_bottom_z,
        cylinder_top_z=pool_top_z,
        bottom_head_depth=pool_bottom_head_depth,
        shell_material=pool_shell_material,
        fill_material=pool_material,
        fill_top_z=pool_fill_top_z,
        cutaway=True,
        shell_opacity=pool_shell_opacity,
        fill_opacity=pool_fill_opacity,
        cutaway_start=cutaway_start,
        cutaway_stop=cutaway_stop,
    )

    containment_solids = [
        {
            "name": "containment_shell",
            "material": containment_material,
            "inner_radius": containment_inner_radius,
            "outer_radius": containment_outer_radius,
            "z_min": containment_bottom_z,
            "z_max": containment_top_z,
            "cutaway": True,
            "opacity": containment_opacity,
            "cutaway_start": cutaway_start,
            "cutaway_stop": cutaway_stop,
        }
    ]

    core_box_solids = _build_open_core_box(core_box, render_layout, geometry, resolved)
    shifted_channels = _shift_render_solids(_build_channel_render_solids(channels), core_offset_x, core_offset_y)

    core_barrel_radius = float(core_box.get("barrel_radius_cm", resolved.core_radius + 1.4))
    barrel_material = str(core_box.get("barrel_material", geometry["matrix_material"]))
    active_mid_z = (resolved.active_bottom_z + resolved.active_top_z) / 2.0
    control_riser_top_z = float(core_box.get("control_riser_top_z_cm", resolved.active_top_z + 14.0))
    core_scene_solids = [
        {
            "name": "core_barrel",
            "material": barrel_material,
            "inner_radius": max(0.0, core_barrel_radius - 1.2),
            "outer_radius": core_barrel_radius,
            "x": core_offset_x,
            "y": core_offset_y,
            "z_min": resolved.active_bottom_z - 3.0,
            "z_max": resolved.active_top_z + 2.0,
            "cutaway": False,
            "opacity": 0.96,
        },
        {
            "name": "core_base_plenum",
            "material": pool_material,
            "inner_radius": 0.0,
            "outer_radius": core_barrel_radius - 1.2,
            "x": core_offset_x,
            "y": core_offset_y,
            "z_min": resolved.active_bottom_z - 8.0,
            "z_max": resolved.active_bottom_z - 1.2,
            "cutaway": False,
            "opacity": 0.96,
        },
        {
            "name": "control_riser",
            "material": str(core_box.get("riser_material", "insulation")),
            "inner_radius": 1.2,
            "outer_radius": 2.7,
            "x": core_offset_x,
            "y": core_offset_y,
            "z_min": resolved.active_top_z - 2.0,
            "z_max": control_riser_top_z,
            "cutaway": False,
            "opacity": 0.98,
        },
        {
            "name": "control_column",
            "material": str(core_box.get("control_material", geometry.get("structure_material", "pipe"))),
            "inner_radius": 0.0,
            "outer_radius": 1.1,
            "x": core_offset_x,
            "y": core_offset_y,
            "z_min": active_mid_z + 8.0,
            "z_max": control_riser_top_z + 4.0,
            "cutaway": False,
            "opacity": 0.98,
        },
    ]

    primary_loop_solids = _build_primary_loop_render_solids(loop, core_offset_x, core_offset_y, resolved)

    return containment_solids + pool_solids + core_box_solids + core_scene_solids + shifted_channels + primary_loop_solids


def _build_immersed_pool_render_animation(
    config: Any,
    resolved: ResolvedMSRGeometry,
    render_layout: dict[str, Any],
) -> dict[str, Any]:
    geometry = config.geometry
    core_offset_x = float(render_layout.get("core_offset_x_cm", 0.0))
    core_offset_y = float(render_layout.get("core_offset_y_cm", 0.0))
    pool = render_layout.get("pool", {})
    loop = render_layout.get("primary_loop", {})
    exchanger = loop.get("heat_exchanger", {})
    pipe_runs = [
        [tuple(float(value) for value in point) for point in pipe_run.get("points", [])]
        for pipe_run in loop.get("pipes", [])
    ]

    exchanger_x_min = float(exchanger.get("x_min_cm", core_offset_x - resolved.core_radius - 42.0))
    exchanger_x_max = float(exchanger.get("x_max_cm", exchanger_x_min + 28.0))
    exchanger_y = float(exchanger.get("y_cm", core_offset_y - 13.0))
    exchanger_z = float(exchanger.get("z_cm", resolved.active_top_z - 18.0))
    pool_radius = float(pool.get("radius_cm", resolved.core_radius * 2.2))
    pool_mid_z = float(pool.get("fill_top_z_cm", resolved.active_top_z + 8.0)) - resolved.active_height * 0.33
    physics = _estimate_animation_physics(config, resolved, render_layout)
    reference_velocity = max(
        float(physics["active_channel_velocity_m_s"]),
        float(physics["loop_pipe_velocity_m_s"]),
        float(physics["pool_circulation_velocity_m_s"]),
        1.0,
    )

    core_inlet = (core_offset_x - 6.0, core_offset_y - 2.0, resolved.active_bottom_z - 6.0)
    core_centerline = (core_offset_x, core_offset_y, resolved.active_bottom_z - 6.0)
    core_outlet = (core_offset_x + 8.0, core_offset_y + 6.0, resolved.active_top_z + 2.0)

    hot_leg: list[tuple[float, float, float]] = [core_outlet]
    if len(pipe_runs) >= 3:
        hot_leg.extend(reversed(pipe_runs[2]))
    if len(pipe_runs) >= 1 and pipe_runs[0]:
        if hot_leg[-1] != pipe_runs[0][-1]:
            hot_leg.append(pipe_runs[0][-1])
        hot_leg.extend(reversed(pipe_runs[0][:-1]))
    hot_leg.append((exchanger_x_min, exchanger_y, exchanger_z))

    cold_leg: list[tuple[float, float, float]] = [(exchanger_x_max, exchanger_y, exchanger_z)]
    if len(pipe_runs) >= 2 and pipe_runs[1]:
        cold_leg.extend(pipe_runs[1])
    cold_leg.append(core_inlet)

    coolant_swirl = [
        (core_offset_x + pool_radius * 0.34, core_offset_y - pool_radius * 0.38, pool_mid_z - 4.0),
        (core_offset_x - pool_radius * 0.58, core_offset_y - pool_radius * 0.22, pool_mid_z + 6.0),
        (core_offset_x - pool_radius * 0.42, core_offset_y + pool_radius * 0.36, pool_mid_z + 10.0),
        (core_offset_x + pool_radius * 0.18, core_offset_y + pool_radius * 0.42, pool_mid_z + 2.0),
        (core_offset_x + pool_radius * 0.38, core_offset_y - pool_radius * 0.04, pool_mid_z - 6.0),
        (core_offset_x + pool_radius * 0.34, core_offset_y - pool_radius * 0.38, pool_mid_z - 4.0),
    ]

    return {
        "frame_count": 28,
        "fps": 12,
        "physics": physics,
        "paths": [
            {
                "name": "core_upflow",
                "material": geometry.get("salt_material", "fuel_salt"),
                "width_cm": 1.8,
                "packet_count": 8,
                "packet_length_cm": 11.0,
                "speed": _normalize_animation_speed(float(physics["active_channel_velocity_m_s"]), reference_velocity),
                "points": [core_centerline, (core_offset_x, core_offset_y, resolved.active_top_z + 1.2)],
            },
            {
                "name": "primary_hot_leg",
                "material": geometry.get("salt_material", "fuel_salt"),
                "width_cm": 1.15,
                "packet_count": 6,
                "packet_length_cm": 12.0,
                "speed": _normalize_animation_speed(float(physics["loop_pipe_velocity_m_s"]) * 1.08, reference_velocity),
                "points": hot_leg,
            },
            {
                "name": "primary_cold_leg",
                "material": geometry.get("salt_material", "fuel_salt"),
                "width_cm": 1.05,
                "packet_count": 5,
                "packet_length_cm": 12.0,
                "speed": _normalize_animation_speed(float(physics["loop_pipe_velocity_m_s"]) * 0.82, reference_velocity),
                "phase_offset": 0.33,
                "points": cold_leg,
            },
            {
                "name": "pool_recirculation",
                "material": pool.get("material", "coolant_salt"),
                "width_cm": 2.2,
                "packet_count": 7,
                "packet_length_cm": 16.0,
                "speed": _normalize_animation_speed(float(physics["pool_circulation_velocity_m_s"]), reference_velocity),
                "phase_offset": 0.17,
                "loop": True,
                "points": coolant_swirl,
            },
        ],
    }


def _estimate_animation_physics(
    config: Any,
    resolved: ResolvedMSRGeometry,
    render_layout: dict[str, Any],
) -> dict[str, float]:
    thermal_power_mw = float(config.reactor.get("design_power_mwth", 0.0))
    hot_leg_temp_c = float(config.reactor.get("hot_leg_temp_c", 700.0))
    cold_leg_temp_c = float(config.reactor.get("cold_leg_temp_c", 560.0))
    primary_cp_kj_kgk = primary_coolant_cp_kj_kgk(config, temperature_c=average_primary_temperature_c(config.reactor))
    delta_t = max(hot_leg_temp_c - cold_leg_temp_c, 1.0)
    primary_mass_flow_kg_s = thermal_power_mw * 1000.0 / (primary_cp_kj_kgk * delta_t)

    flow_summary = build_msr_flow_summary(config, resolved)
    interface_metrics = flow_summary["interface_metrics"]
    reduced_order_config = config.flow.get("reduced_order", {})
    active_channel_selection = reduced_order_config.get(
        "active_channel_selection",
        "plenum_connected_salt_bearing_channels",
    )
    if active_channel_selection == "all_salt_bearing_channels":
        active_flow_area_cm2 = float(interface_metrics["plenum_connected_salt_area_cm2"]) + float(
            interface_metrics["reflector_backed_salt_area_cm2"]
        )
    else:
        active_flow_area_cm2 = float(interface_metrics["plenum_connected_salt_area_cm2"])
    bulk_temperature_c = average_primary_temperature_c(config.reactor)
    salt_density_kg_m3 = float(evaluate_primary_coolant_properties(config, temperature_c=bulk_temperature_c)["density_kg_m3"])
    coolant_density_kg_m3 = float(
        evaluate_fluid_properties(
            config.materials[str(render_layout.get("pool", {}).get("material", config.geometry.get("salt_material", "fuel_salt")))],
            temperature_c=bulk_temperature_c,
        )["density_kg_m3"]
    )

    volumetric_flow_m3_s = primary_mass_flow_kg_s / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0
    active_channel_velocity_m_s = volumetric_flow_m3_s / (active_flow_area_cm2 * 1.0e-4) if active_flow_area_cm2 > 0.0 else 0.0

    pipe_runs = (render_layout.get("primary_loop", {}) or {}).get("pipes", [])
    pipe_radius_cm = min(float(pipe_run.get("radius_cm", 1.0)) for pipe_run in pipe_runs) if pipe_runs else 1.0
    pipe_area_m2 = math.pi * ((pipe_radius_cm * 1.0e-2) ** 2)
    loop_pipe_velocity_m_s = volumetric_flow_m3_s / pipe_area_m2 if pipe_area_m2 > 0.0 else 0.0

    pool_circulation_mass_flow_kg_s = primary_mass_flow_kg_s * 0.34
    pool_circulation_volumetric_m3_s = (
        pool_circulation_mass_flow_kg_s / coolant_density_kg_m3 if coolant_density_kg_m3 > 0.0 else 0.0
    )
    pool_path_area_cm2 = _estimate_pool_recirculation_area_cm2(render_layout, resolved, active_flow_area_cm2)
    pool_path_area_m2 = pool_path_area_cm2 * 1.0e-4
    pool_circulation_velocity_m_s = (
        pool_circulation_volumetric_m3_s / pool_path_area_m2 if pool_path_area_m2 > 0.0 else 0.0
    )

    return {
        "primary_mass_flow_kg_s": _round_metric(primary_mass_flow_kg_s),
        "primary_volumetric_flow_m3_s": _round_metric(volumetric_flow_m3_s),
        "active_flow_area_cm2": _round_metric(active_flow_area_cm2),
        "limiting_pipe_radius_cm": _round_metric(pipe_radius_cm),
        "pool_recirculation_area_cm2": _round_metric(pool_path_area_cm2),
        "active_channel_velocity_m_s": _round_metric(active_channel_velocity_m_s),
        "loop_pipe_velocity_m_s": _round_metric(loop_pipe_velocity_m_s),
        "pool_circulation_velocity_m_s": _round_metric(pool_circulation_velocity_m_s),
    }


def _normalize_animation_speed(velocity_m_s: float, reference_velocity_m_s: float) -> float:
    if reference_velocity_m_s <= 0.0:
        return 0.5
    scaled = velocity_m_s / reference_velocity_m_s
    return max(0.2, min(1.2, 0.18 + scaled))

def _estimate_pool_recirculation_area_cm2(
    render_layout: dict[str, Any],
    resolved: ResolvedMSRGeometry,
    active_flow_area_cm2: float,
) -> float:
    pool = render_layout.get("pool", {})
    core_box = render_layout.get("core_box", {})
    inner_pool_radius = _pool_inner_radius_cm(pool, resolved)
    free_pool_area_cm2 = math.pi * (inner_pool_radius ** 2)
    free_pool_area_cm2 -= float(core_box.get("outer_width_cm", resolved.core_radius * 2.3)) * float(
        core_box.get("outer_depth_cm", resolved.core_radius * 1.9)
    )
    effective_pool_area_cm2 = max(free_pool_area_cm2 * 0.08, active_flow_area_cm2 * 4.0)
    return max(effective_pool_area_cm2, 1.0)


def _build_render_layout_invariants(
    geometry: dict[str, Any],
    resolved: ResolvedMSRGeometry,
) -> list[dict[str, Any]]:
    render_layout = geometry.get("render_layout") or {}
    if render_layout.get("type") != "immersed_pool_reference":
        return []

    pool = render_layout.get("pool", {})
    containment = render_layout.get("containment", {})
    core_box = render_layout.get("core_box", {})
    loop = render_layout.get("primary_loop", {})

    core_offset_x = float(render_layout.get("core_offset_x_cm", 0.0))
    core_offset_y = float(render_layout.get("core_offset_y_cm", 0.0))
    pool_radius = _pool_inner_radius_cm(pool, resolved)
    containment_inner_radius = float(containment.get("inner_radius_cm", pool_radius + 10.0))
    core_box_half_width = float(core_box.get("outer_width_cm", resolved.core_radius * 2.3)) / 2.0
    core_box_half_depth = float(core_box.get("outer_depth_cm", resolved.core_radius * 1.9)) / 2.0
    core_box_wall_thickness = float(core_box.get("wall_thickness_cm", 4.0))
    cavity_half_width = max(0.0, core_box_half_width - core_box_wall_thickness)
    cavity_half_depth = max(0.0, core_box_half_depth - core_box_wall_thickness)
    barrel_radius = float(core_box.get("barrel_radius_cm", resolved.core_radius + 1.4))
    fill_top_z = float(pool.get("fill_top_z_cm", resolved.active_top_z + 8.0))

    exchanger = loop.get("heat_exchanger", {})
    pump = loop.get("pump", {})

    loop_points = [
        tuple(float(value) for value in point)
        for pipe_run in loop.get("pipes", [])
        for point in pipe_run.get("points", [])
    ]
    loop_clear = all(math.hypot(x_value, y_value) <= pool_radius for x_value, y_value, _ in loop_points) if loop_points else True
    loop_top_z = max(
        _primary_component_top_z(exchanger, kind="heat_exchanger"),
        _primary_component_top_z(pump, kind="pump"),
        *[
            _pipe_run_top_z(
                [tuple(float(value) for value in point) for point in pipe_run.get("points", [])],
                float(pipe_run.get("radius_cm", 0.0)),
            )
            for pipe_run in loop.get("pipes", [])
        ],
    )
    core_box_corners = [
        (core_offset_x + x_sign * core_box_half_width, core_offset_y + y_sign * core_box_half_depth)
        for x_sign in (-1.0, 1.0)
        for y_sign in (-1.0, 1.0)
    ]
    core_box_in_pool = all(math.hypot(x_value, y_value) <= pool_radius for x_value, y_value in core_box_corners)
    pool_in_containment = containment_inner_radius > pool_radius
    barrel_in_cavity = barrel_radius <= min(cavity_half_width, cavity_half_depth)
    loop_submerged = loop_top_z <= fill_top_z

    return [
        {
            "name": "render_layout::containment_encloses_pool",
            "passed": pool_in_containment,
            "message": (
                "Containment clearance is larger than the primary pool radius."
                if pool_in_containment
                else "Containment inner radius must fully enclose the primary pool."
            ),
        },
        {
            "name": "render_layout::core_box_inside_pool",
            "passed": core_box_in_pool,
            "message": (
                "Core box footprint stays inside the primary pool envelope."
                if core_box_in_pool
                else "Core box extends beyond the primary pool envelope and may clip the shell."
            ),
        },
        {
            "name": "render_layout::core_barrel_inside_cavity",
            "passed": barrel_in_cavity,
            "message": (
                "Core barrel fits inside the insulated cavity with positive clearance."
                if barrel_in_cavity
                else "Core barrel exceeds the cavity clearance and may clip the enclosure."
            ),
        },
        {
            "name": "render_layout::primary_loop_inside_pool",
            "passed": loop_clear,
            "message": (
                "Primary loop control points remain inside the primary pool envelope."
                if loop_clear
                else "Primary loop control points extend outside the primary pool envelope."
            ),
        },
        {
            "name": "render_layout::primary_loop_submerged",
            "passed": loop_submerged,
            "message": (
                "Primary loop hardware remains below the molten-salt free surface."
                if loop_submerged
                else "Primary loop hardware rises above the molten-salt free surface."
            ),
        },
    ]


def _build_render_physics_invariants(
    config: Any,
    resolved: ResolvedMSRGeometry,
) -> list[dict[str, Any]]:
    render_layout = config.geometry.get("render_layout") or {}
    if render_layout.get("type") != "immersed_pool_reference":
        return []

    delta_t = float(config.reactor.get("hot_leg_temp_c", 700.0)) - float(config.reactor.get("cold_leg_temp_c", 560.0))
    physics = _estimate_animation_physics(config, resolved, render_layout)
    active_velocity = float(physics["active_channel_velocity_m_s"])
    loop_velocity = float(physics["loop_pipe_velocity_m_s"])
    pool_velocity = float(physics["pool_circulation_velocity_m_s"])

    checks = [
        (
            "physics::delta_t_reasonable",
            60.0 <= delta_t <= 180.0,
            f"Primary salt delta-T is {delta_t:.1f} C.",
            "Primary salt delta-T should stay in a representative MSR design band.",
        ),
        (
            "physics::active_channel_velocity_reasonable",
            1.0 <= active_velocity <= 12.0,
            f"Representative active-channel velocity is {active_velocity:.2f} m/s.",
            "Representative active-channel velocity should remain between 1 and 12 m/s.",
        ),
        (
            "physics::loop_pipe_velocity_reasonable",
            1.0 <= loop_velocity <= 10.0,
            f"Limiting primary-loop pipe velocity is {loop_velocity:.2f} m/s.",
            "Limiting primary-loop pipe velocity should remain between 1 and 10 m/s.",
        ),
        (
            "physics::pool_circulation_velocity_reasonable",
            0.02 <= pool_velocity <= 1.5,
            f"Representative pool recirculation velocity is {pool_velocity:.3f} m/s.",
            "Representative pool recirculation velocity should remain between 0.02 and 1.5 m/s.",
        ),
    ]

    return [
        {
            "name": name,
            "passed": passed,
            "message": success if passed else failure,
        }
        for name, passed, success, failure in checks
    ]


def _pool_inner_radius_cm(pool: dict[str, Any], resolved: ResolvedMSRGeometry) -> float:
    return float(pool.get("radius_cm", resolved.core_radius * 2.2)) - float(pool.get("wall_thickness_cm", 1.8))


def _primary_component_top_z(component: dict[str, Any], *, kind: str) -> float:
    if not component:
        return float("-inf")
    if kind == "heat_exchanger":
        return float(component.get("z_cm", 0.0)) + float(component.get("radius_cm", 0.0))
    if kind == "pump":
        return float(component.get("z_max_cm", 0.0)) + float(component.get("header_radius_cm", component.get("radius_cm", 0.0)))
    return float("-inf")


def _pipe_run_top_z(points: list[tuple[float, float, float]], radius_cm: float) -> float:
    if not points:
        return float("-inf")
    return max(point[2] for point in points) + radius_cm


def _build_dished_vessel_solids(
    name_prefix: str,
    *,
    inner_radius: float,
    shell_thickness: float,
    cylinder_bottom_z: float,
    cylinder_top_z: float,
    bottom_head_depth: float,
    shell_material: str,
    fill_material: str,
    fill_top_z: float,
    cutaway: bool,
    shell_opacity: float,
    fill_opacity: float,
    cutaway_start: float,
    cutaway_stop: float,
    segments: int = 12,
) -> list[dict[str, Any]]:
    solids = [
        {
            "name": f"{name_prefix}_shell_body",
            "material": shell_material,
            "inner_radius": inner_radius,
            "outer_radius": inner_radius + shell_thickness,
            "z_min": cylinder_bottom_z,
            "z_max": cylinder_top_z,
            "cutaway": cutaway,
            "opacity": shell_opacity,
            "cutaway_start": cutaway_start,
            "cutaway_stop": cutaway_stop,
        }
    ]
    if fill_top_z > cylinder_bottom_z:
        solids.append(
            {
                "name": f"{name_prefix}_fill_body",
                "material": fill_material,
                "inner_radius": 0.0,
                "outer_radius": inner_radius,
                "z_min": cylinder_bottom_z,
                "z_max": min(fill_top_z, cylinder_top_z),
                "cutaway": cutaway,
                "opacity": fill_opacity,
                "cutaway_start": cutaway_start,
                "cutaway_stop": cutaway_stop,
            }
        )

    slice_height = bottom_head_depth / max(segments, 1)
    cap_bottom_z = cylinder_bottom_z - bottom_head_depth
    for index in range(segments):
        z_min = cap_bottom_z + index * slice_height
        z_max = z_min + slice_height
        top_fraction = (index + 0.5) / max(segments, 1)
        fill_radius = inner_radius * math.sqrt(max(0.0, 1.0 - (1.0 - top_fraction) ** 2))
        if fill_radius <= 0.0:
            continue
        solids.append(
            {
                "name": f"{name_prefix}_shell_head_{index}",
                "material": shell_material,
                "inner_radius": fill_radius,
                "outer_radius": fill_radius + shell_thickness,
                "z_min": z_min,
                "z_max": z_max,
                "cutaway": cutaway,
                "opacity": shell_opacity,
                "cutaway_start": cutaway_start,
                "cutaway_stop": cutaway_stop,
            }
        )
        if z_min < fill_top_z:
            solids.append(
                {
                    "name": f"{name_prefix}_fill_head_{index}",
                    "material": fill_material,
                    "inner_radius": 0.0,
                    "outer_radius": fill_radius,
                    "z_min": z_min,
                    "z_max": min(z_max, fill_top_z),
                    "cutaway": cutaway,
                    "opacity": fill_opacity,
                    "cutaway_start": cutaway_start,
                    "cutaway_stop": cutaway_stop,
                }
            )
    return solids


def _build_open_core_box(
    core_box: dict[str, Any],
    render_layout: dict[str, Any],
    geometry: dict[str, Any],
    resolved: ResolvedMSRGeometry,
) -> list[dict[str, Any]]:
    center_x = float(core_box.get("center_x_cm", render_layout.get("core_offset_x_cm", 0.0)))
    center_y = float(core_box.get("center_y_cm", render_layout.get("core_offset_y_cm", 0.0)))
    outer_width = float(core_box.get("outer_width_cm", resolved.core_radius * 2.3))
    outer_depth = float(core_box.get("outer_depth_cm", resolved.core_radius * 1.9))
    wall_thickness = float(core_box.get("wall_thickness_cm", 4.0))
    base_height = float(core_box.get("base_height_cm", 5.0))
    floor_z = float(core_box.get("floor_z_cm", resolved.active_bottom_z - 12.0))
    cavity_top_z = float(core_box.get("cavity_top_z_cm", resolved.active_top_z + 10.0))
    wall_material = str(core_box.get("wall_material", "insulation"))
    cavity_material = str(core_box.get("cavity_material", "coolant_salt"))
    back_y_max = center_y + outer_depth / 2.0
    front_y_min = center_y - outer_depth / 2.0
    x_min = center_x - outer_width / 2.0
    x_max = center_x + outer_width / 2.0
    inner_x_min = x_min + wall_thickness
    inner_x_max = x_max - wall_thickness
    inner_y_min = front_y_min + wall_thickness
    inner_y_max = back_y_max - wall_thickness

    return [
        {
            "name": "core_box_base",
            "type": "box",
            "material": wall_material,
            "x_min": x_min - 2.4,
            "x_max": x_max + 2.4,
            "y_min": front_y_min - 2.0,
            "y_max": back_y_max + 2.0,
            "z_min": floor_z - base_height,
            "z_max": floor_z,
            "opacity": 0.98,
        },
        {
            "name": "core_box_cavity",
            "type": "box",
            "material": cavity_material,
            "x_min": inner_x_min,
            "x_max": inner_x_max,
            "y_min": inner_y_min,
            "y_max": inner_y_max,
            "z_min": floor_z,
            "z_max": cavity_top_z,
            "opacity": 0.68,
        },
        {
            "name": "core_box_left_wall",
            "type": "box",
            "material": wall_material,
            "x_min": x_min,
            "x_max": inner_x_min,
            "y_min": front_y_min,
            "y_max": back_y_max,
            "z_min": floor_z,
            "z_max": cavity_top_z,
            "opacity": 0.98,
        },
        {
            "name": "core_box_right_wall",
            "type": "box",
            "material": wall_material,
            "x_min": inner_x_max,
            "x_max": x_max,
            "y_min": front_y_min,
            "y_max": back_y_max,
            "z_min": floor_z,
            "z_max": cavity_top_z,
            "opacity": 0.98,
        },
        {
            "name": "core_box_back_wall",
            "type": "box",
            "material": wall_material,
            "x_min": x_min,
            "x_max": x_max,
            "y_min": inner_y_max,
            "y_max": back_y_max,
            "z_min": floor_z,
            "z_max": cavity_top_z,
            "opacity": 0.98,
        },
        {
            "name": "core_box_roof",
            "type": "box",
            "material": wall_material,
            "x_min": x_min,
            "x_max": x_max,
            "y_min": front_y_min + wall_thickness * 0.45,
            "y_max": back_y_max,
            "z_min": cavity_top_z,
            "z_max": cavity_top_z + wall_thickness * 0.82,
            "opacity": 0.98,
        },
        {
            "name": "core_box_support",
            "type": "box",
            "material": geometry.get("structure_material", "pipe"),
            "x_min": x_min - 1.6,
            "x_max": x_min + wall_thickness * 1.2,
            "y_min": front_y_min - 2.0,
            "y_max": front_y_min + wall_thickness * 0.7,
            "z_min": floor_z - base_height,
            "z_max": floor_z + wall_thickness * 0.65,
            "opacity": 0.96,
        },
    ]


def _build_primary_loop_render_solids(
    loop: dict[str, Any],
    core_offset_x: float,
    core_offset_y: float,
    resolved: ResolvedMSRGeometry,
) -> list[dict[str, Any]]:
    exchanger = loop.get("heat_exchanger", {})
    pump = loop.get("pump", {})
    pipe_runs = loop.get("pipes", [])

    exchanger_material = str(exchanger.get("material", "pipe"))
    exchanger_y = float(exchanger.get("y_cm", core_offset_y - 13.0))
    exchanger_z = float(exchanger.get("z_cm", resolved.active_top_z - 18.0))
    exchanger_x_min = float(exchanger.get("x_min_cm", core_offset_x - resolved.core_radius - 42.0))
    exchanger_x_max = float(exchanger.get("x_max_cm", exchanger_x_min + 28.0))
    exchanger_radius = float(exchanger.get("radius_cm", 2.7))

    pump_material = str(pump.get("material", "pipe"))
    pump_x = float(pump.get("x_cm", exchanger_x_min + 13.0))
    pump_y = float(pump.get("y_cm", exchanger_y + 4.2))
    pump_z_min = float(pump.get("z_min_cm", exchanger_z + 1.4))
    pump_z_max = float(pump.get("z_max_cm", pump_z_min + 10.0))
    pump_radius = float(pump.get("radius_cm", 1.7))
    header_radius = float(pump.get("header_radius_cm", 2.8))

    solids = [
        {
            "name": "primary_heat_exchanger",
            "material": exchanger_material,
            "axis": "x",
            "x_min": exchanger_x_min,
            "x_max": exchanger_x_max,
            "y": exchanger_y,
            "z": exchanger_z,
            "outer_radius": exchanger_radius,
            "opacity": 0.98,
        },
        {
            "name": "primary_heat_exchanger_cap",
            "material": exchanger_material,
            "axis": "x",
            "x_min": exchanger_x_min - 2.2,
            "x_max": exchanger_x_min,
            "y": exchanger_y,
            "z": exchanger_z,
            "outer_radius": exchanger_radius * 0.85,
            "opacity": 0.98,
        },
        {
            "name": "primary_pump_body",
            "material": pump_material,
            "z_min": pump_z_min,
            "z_max": pump_z_max,
            "x": pump_x,
            "y": pump_y,
            "outer_radius": pump_radius,
            "opacity": 0.98,
        },
        {
            "name": "primary_pump_header",
            "material": "insulation",
            "z_min": pump_z_max - 1.0,
            "z_max": pump_z_max + 2.5,
            "x": pump_x,
            "y": pump_y,
            "outer_radius": header_radius,
            "inner_radius": pump_radius * 0.9,
            "opacity": 0.98,
        },
    ]

    for pipe_index, pipe_run in enumerate(pipe_runs):
        radius = float(pipe_run.get("radius_cm", 0.95))
        material = str(pipe_run.get("material", "pipe"))
        points = [tuple(float(value) for value in point) for point in pipe_run.get("points", [])]
        solids.extend(_build_pipe_run_solids(f"primary_loop_{pipe_index}", points, radius, material))
    return solids


def _build_pipe_run_solids(
    name_prefix: str,
    points: list[tuple[float, float, float]],
    radius: float,
    material: str,
) -> list[dict[str, Any]]:
    solids: list[dict[str, Any]] = []
    for index, (start, stop) in enumerate(zip(points, points[1:])):
        x0, y0, z0 = start
        x1, y1, z1 = stop
        if math.isclose(x0, x1, abs_tol=1.0e-6) and math.isclose(y0, y1, abs_tol=1.0e-6):
            solids.append(
                {
                    "name": f"{name_prefix}_segment_{index}",
                    "material": material,
                    "x": x0,
                    "y": y0,
                    "z_min": min(z0, z1),
                    "z_max": max(z0, z1),
                    "outer_radius": radius,
                    "opacity": 0.98,
                }
            )
        elif math.isclose(y0, y1, abs_tol=1.0e-6) and math.isclose(z0, z1, abs_tol=1.0e-6):
            solids.append(
                {
                    "name": f"{name_prefix}_segment_{index}",
                    "material": material,
                    "axis": "x",
                    "x_min": min(x0, x1),
                    "x_max": max(x0, x1),
                    "y": y0,
                    "z": z0,
                    "outer_radius": radius,
                    "opacity": 0.98,
                }
            )
        elif math.isclose(x0, x1, abs_tol=1.0e-6) and math.isclose(z0, z1, abs_tol=1.0e-6):
            solids.append(
                {
                    "name": f"{name_prefix}_segment_{index}",
                    "material": material,
                    "axis": "y",
                    "y_min": min(y0, y1),
                    "y_max": max(y0, y1),
                    "x": x0,
                    "z": z0,
                    "outer_radius": radius,
                    "opacity": 0.98,
                }
            )
    return solids


def _shift_render_solids(solids: list[dict[str, Any]], offset_x: float, offset_y: float) -> list[dict[str, Any]]:
    shifted: list[dict[str, Any]] = []
    for solid in solids:
        updated = dict(solid)
        solid_type = updated.get("type", "cylinder")
        axis = str(updated.get("axis", "z"))
        if solid_type == "box":
            updated["x_min"] = float(updated["x_min"]) + offset_x
            updated["x_max"] = float(updated["x_max"]) + offset_x
            updated["y_min"] = float(updated["y_min"]) + offset_y
            updated["y_max"] = float(updated["y_max"]) + offset_y
        else:
            if axis == "z":
                updated["x"] = float(updated.get("x", 0.0)) + offset_x
                updated["y"] = float(updated.get("y", 0.0)) + offset_y
            elif axis == "x":
                updated["x_min"] = float(updated["x_min"]) + offset_x
                updated["x_max"] = float(updated["x_max"]) + offset_x
                updated["y"] = float(updated.get("y", 0.0)) + offset_y
            elif axis == "y":
                updated["y_min"] = float(updated["y_min"]) + offset_y
                updated["y_max"] = float(updated["y_max"]) + offset_y
                updated["x"] = float(updated.get("x", 0.0)) + offset_x
        shifted.append(updated)
    return shifted
