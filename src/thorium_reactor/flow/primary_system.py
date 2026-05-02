from __future__ import annotations

import math
from typing import Any

from thorium_reactor.chemistry import build_steady_state_chemistry_summary
from thorium_reactor.flow.properties import (
    average_primary_temperature_c,
    evaluate_fluid_properties,
    evaluate_primary_coolant_properties,
    evaluate_secondary_coolant_properties,
)
from thorium_reactor.transient import build_depletion_assumptions


GRAVITY_M_S2 = 9.80665
FISSION_ENERGY_J = 3.204e-11


def build_primary_system_summary(
    config: Any,
    geometry_description: dict[str, Any],
    reduced_order_flow: dict[str, Any],
    bop: dict[str, Any],
) -> dict[str, Any]:
    render_layout = config.geometry.get("render_layout") or {}
    if render_layout.get("type") not in {"immersed_pool_reference", "plant_schematic"}:
        return {}

    primary_loop = render_layout.get("primary_loop") or {}
    pipe_runs = primary_loop.get("pipes") or []
    if not pipe_runs:
        return {}

    bulk_temperature_c = average_primary_temperature_c(config.reactor)
    salt_properties = evaluate_primary_coolant_properties(config, temperature_c=bulk_temperature_c)
    salt_density_kg_m3 = float(salt_properties["density_kg_m3"])
    hot_leg_properties = evaluate_primary_coolant_properties(
        config,
        temperature_c=float(config.reactor.get("hot_leg_temp_c", bulk_temperature_c)),
    )
    cold_leg_properties = evaluate_primary_coolant_properties(
        config,
        temperature_c=float(config.reactor.get("cold_leg_temp_c", bulk_temperature_c)),
    )
    dynamic_viscosity_pa_s = float(
        salt_properties["dynamic_viscosity_pa_s"]
        if salt_properties.get("dynamic_viscosity_pa_s") is not None
        else 0.012
    )
    pump_efficiency = float(config.reactor.get("primary_pump_efficiency", 0.72))
    elbow_loss_coefficient = float(config.reactor.get("primary_elbow_loss_coefficient", 0.9))
    terminal_loss_coefficient = float(config.reactor.get("primary_terminal_loss_coefficient", 1.6))

    design_basis_primary_mass_flow_kg_s = float(bop["primary_mass_flow_kg_s"])
    volumetric_flow_m3_s = (
        design_basis_primary_mass_flow_kg_s / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0
    )

    segment_summary = _build_pipe_segment_summary(
        pipe_runs,
        volumetric_flow_m3_s,
        salt_density_kg_m3,
        dynamic_viscosity_pa_s,
        elbow_loss_coefficient,
        terminal_loss_coefficient,
    )
    loop_graph = _build_primary_loop_graph(primary_loop, segment_summary["segments"])
    thermal_profile = _build_primary_thermal_profile(
        config,
        primary_loop=primary_loop,
        loop_graph=loop_graph,
        bop=bop,
        salt_properties=salt_properties,
        initial_mass_flow_kg_s=design_basis_primary_mass_flow_kg_s,
    )
    primary_mass_flow_kg_s = float(thermal_profile["solved_primary_mass_flow_kg_s"])
    volumetric_flow_m3_s = primary_mass_flow_kg_s / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0
    branch_flow_summary = _solve_branch_flow_distribution(
        primary_loop=primary_loop,
        loop_graph=loop_graph,
        total_volumetric_flow_m3_s=volumetric_flow_m3_s,
        salt_density_kg_m3=salt_density_kg_m3,
        dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
        elbow_loss_coefficient=elbow_loss_coefficient,
        terminal_loss_coefficient=terminal_loss_coefficient,
    )
    segment_summary = _build_pipe_segment_summary(
        pipe_runs,
        volumetric_flow_m3_s,
        salt_density_kg_m3,
        dynamic_viscosity_pa_s,
        elbow_loss_coefficient,
        terminal_loss_coefficient,
        flow_by_pipe_id_m3_s=branch_flow_summary["edge_flow_m3_s"],
    )
    loop_graph = _build_primary_loop_graph(primary_loop, segment_summary["segments"])
    thermal_profile = _build_primary_thermal_profile(
        config,
        primary_loop=primary_loop,
        loop_graph=loop_graph,
        bop=bop,
        salt_properties=salt_properties,
        initial_mass_flow_kg_s=primary_mass_flow_kg_s,
        fixed_primary_mass_flow_kg_s=primary_mass_flow_kg_s,
        edge_flow_m3_s=branch_flow_summary["edge_flow_m3_s"],
        salt_density_kg_m3=salt_density_kg_m3,
    )
    pressure_budget = _build_pressure_budget(
        segment_summary,
        loop_graph=loop_graph,
        hot_leg_density_kg_m3=float(hot_leg_properties["density_kg_m3"]),
        cold_leg_density_kg_m3=float(cold_leg_properties["density_kg_m3"]),
    )
    heat_exchanger_summary = _build_heat_exchanger_summary(
        config,
        bop,
        salt_properties=salt_properties,
        primary_mass_flow_kg_s=primary_mass_flow_kg_s,
        primary_hot_leg_c=float(thermal_profile["estimated_hot_leg_temp_c"]),
        primary_cold_leg_c=float(thermal_profile["estimated_cold_leg_temp_c"]),
        required_duty_mw=float(thermal_profile["required_heat_exchanger_duty_mw"]),
        loop_graph=loop_graph,
        thermal_profile=thermal_profile,
        branch_flow_summary=branch_flow_summary,
        segment_summary=segment_summary,
    )
    inventory_summary = _build_inventory_summary(config, geometry_description, reduced_order_flow, primary_loop)
    fuel_cycle_summary = _build_fuel_cycle_summary(config, bop, inventory_summary)
    chemistry_summary = build_steady_state_chemistry_summary(
        config,
        fuel_salt_volume_m3=float(inventory_summary["fuel_salt"]["total_m3"]),
        bulk_temperature_c=bulk_temperature_c,
        cleanup_turnover_days=float(fuel_cycle_summary["cleanup_turnover_days"]),
    )

    pump_demand = _build_pump_demand_summary(
        pressure_budget["required_pump_pressure_pa"],
        salt_density_kg_m3=salt_density_kg_m3,
        volumetric_flow_m3_s=volumetric_flow_m3_s,
        pump_efficiency=pump_efficiency,
    )

    summary = {
        "model": "reduced_order_primary_system",
        "bulk_temperature_c": _round_float(bulk_temperature_c),
        "salt_density_kg_m3": _round_float(salt_density_kg_m3),
        "hot_leg_density_kg_m3": _round_float(float(hot_leg_properties["density_kg_m3"])),
        "cold_leg_density_kg_m3": _round_float(float(cold_leg_properties["density_kg_m3"])),
        "dynamic_viscosity_pa_s": _round_float(dynamic_viscosity_pa_s),
        "salt_properties": salt_properties,
        "design_basis_primary_mass_flow_kg_s": _round_float(design_basis_primary_mass_flow_kg_s),
        "primary_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
        "primary_volumetric_flow_m3_s": _round_float(volumetric_flow_m3_s),
        "loop_segments": segment_summary["segments"],
        "loop_topology": loop_graph,
        "branch_flows": branch_flow_summary,
        "loop_hydraulics": {
            "total_pipe_length_m": _round_float(segment_summary["total_pipe_length_m"]),
            "limiting_inner_diameter_m": _round_float(segment_summary["limiting_inner_diameter_m"]),
            "limiting_velocity_m_s": _round_float(segment_summary["limiting_velocity_m_s"]),
            "max_reynolds_number": _round_float(segment_summary["max_reynolds_number"]),
            "frictional_pressure_drop_kpa": _round_float(pressure_budget["frictional_pressure_drop_pa"] / 1000.0),
            "local_pressure_drop_kpa": _round_float(pressure_budget["local_pressure_drop_pa"] / 1000.0),
            "hydrostatic_pressure_change_kpa": _round_float(pressure_budget["hydrostatic_pressure_change_pa"] / 1000.0),
            "buoyancy_driving_pressure_kpa": _round_float(pressure_budget["buoyancy_driving_pressure_pa"] / 1000.0),
            "net_resistive_pressure_kpa": _round_float(pressure_budget["net_resistive_pressure_pa"] / 1000.0),
            "net_required_pump_pressure_kpa": _round_float(pump_demand["net_required_pressure_pa"] / 1000.0),
            "required_pump_pressure_kpa": _round_float(pump_demand["pump_demand_pressure_pa"] / 1000.0),
            "total_pressure_drop_kpa": _round_float(pump_demand["pump_demand_pressure_pa"] / 1000.0),
            "natural_circulation_margin_kpa": _round_float(pump_demand["natural_circulation_margin_pa"] / 1000.0),
            "pump_head_m": _round_float(pump_demand["pump_head_m"]),
            "pump_hydraulic_power_kw": _round_float(pump_demand["hydraulic_power_kw"]),
            "pump_shaft_power_kw": _round_float(pump_demand["shaft_power_kw"]),
            "pump_efficiency": _round_float(pump_efficiency),
            "thermal_expansion_head_m": _round_float(pressure_budget["thermal_expansion_head_m"]),
            "representative_elevation_span_m": _round_float(pressure_budget["representative_elevation_span_m"]),
            "hot_leg_rise_m": _round_float(pressure_budget["hot_leg_rise_m"]),
            "cold_leg_drop_m": _round_float(pressure_budget["cold_leg_drop_m"]),
        },
        "heat_exchanger": heat_exchanger_summary,
        "thermal_profile": thermal_profile,
        "inventory": inventory_summary,
        "fuel_cycle": fuel_cycle_summary,
        "chemistry": chemistry_summary,
        "checks": _build_primary_system_checks(
            reduced_order_flow,
            segment_summary["max_reynolds_number"],
            pump_demand["pump_head_m"],
            heat_exchanger_summary,
            thermal_profile,
            inventory_summary,
        ),
    }
    plant_system = _build_plant_schematic_system_summary(
        config,
        geometry_description,
        primary_loop,
        bop,
        heat_exchanger_summary,
        thermal_profile,
        inventory_summary,
    )
    if plant_system:
        summary["plant_system"] = plant_system
    return summary


def _build_plant_schematic_system_summary(
    config: Any,
    geometry_description: dict[str, Any],
    primary_loop: dict[str, Any],
    bop: dict[str, Any],
    heat_exchanger_summary: dict[str, Any],
    thermal_profile: dict[str, Any],
    inventory_summary: dict[str, Any],
) -> dict[str, Any]:
    plant_system = geometry_description.get("plant_system") or {}
    if plant_system.get("type") != "plant_schematic":
        return {}

    components = list(plant_system.get("components", []))
    networks = list(plant_system.get("networks", []))
    network_summary = [
        {
            "id": str(network.get("id", "")),
            "kind": str(network.get("kind", "flow")),
            "material": str(network.get("material", "")),
            "pipe_ids": list(network.get("pipe_ids", [])),
            "component_ids": list(network.get("component_ids", [])),
        }
        for network in networks
    ]
    characteristics = config.reactor.get("characteristics") or {}
    thermal_power_mw = float(bop.get("thermal_power_mw", config.reactor.get("design_power_mwth", 0.0)))
    electric_power_mw = float(
        bop.get(
            "electric_power_mw",
            characteristics.get("net_electric_power_mwe", 0.0),
        )
    )
    steam_generator_duty_mw = float(bop.get("steam_generator_duty_mw", 0.0))
    case_data = getattr(config, "data", {})
    processing = case_data.get("processing", {}) if isinstance(case_data, dict) else {}
    cleanup_strategy = str(
        characteristics.get(
            "cleanup_strategy",
            processing.get("volatile_removal", "not_declared"),
        )
    )

    return {
        "model": "full_plant_reduced_order_schematic",
        "scope": "reactor_core_primary_loop_heat_rejection_cleanup_power_conversion_grid_interface",
        "component_count": len(components),
        "network_count": len(network_summary),
        "primary_loop_pipe_count": len(primary_loop.get("pipes", [])),
        "components": components,
        "networks": network_summary,
        "design_basis": {
            "thermal_power_mw": _round_float(thermal_power_mw),
            "gross_electric_power_mw": _round_float(electric_power_mw),
            "net_electric_power_mwe": _round_float(float(characteristics.get("net_electric_power_mwe", electric_power_mw))),
            "steam_generator_duty_mw": _round_float(steam_generator_duty_mw),
            "overall_thermal_efficiency": _round_float(electric_power_mw / thermal_power_mw) if thermal_power_mw > 0.0 else 0.0,
            "hot_leg_temp_c": _round_float(float(config.reactor.get("hot_leg_temp_c", 0.0))),
            "cold_leg_temp_c": _round_float(float(config.reactor.get("cold_leg_temp_c", 0.0))),
        },
        "primary_loop": {
            "solved_mass_flow_kg_s": _round_float(float(thermal_profile.get("solved_primary_mass_flow_kg_s", 0.0))),
            "estimated_hot_leg_temp_c": _round_float(float(thermal_profile.get("estimated_hot_leg_temp_c", 0.0))),
            "estimated_cold_leg_temp_c": _round_float(float(thermal_profile.get("estimated_cold_leg_temp_c", 0.0))),
            "heat_exchanger_area_m2": _round_float(float(heat_exchanger_summary.get("required_area_m2", 0.0))),
            "heat_exchanger_duty_mw": _round_float(float(heat_exchanger_summary.get("duty_mw", 0.0))),
        },
        "inventories": {
            "fuel_salt_m3": inventory_summary.get("fuel_salt", {}).get("total_m3", 0.0),
            "coolant_salt_m3": inventory_summary.get("coolant_salt", {}).get("net_pool_inventory_m3", 0.0),
        },
        "processing": {
            "cleanup_strategy": cleanup_strategy,
            "volatile_removal": processing.get("volatile_removal"),
            "noble_gas_path": processing.get("noble_gas_path"),
            "protactinium_strategy": processing.get("protactinium_strategy"),
        },
    }


def _build_pipe_segment_summary(
    pipe_runs: list[dict[str, Any]],
    volumetric_flow_m3_s: float,
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
    flow_by_pipe_id_m3_s: dict[str, float] | None = None,
) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    segment_lookup: dict[str, dict[str, Any]] = {}
    total_pipe_length_m = 0.0
    frictional_pressure_drop_pa = 0.0
    local_pressure_drop_pa = 0.0
    hydrostatic_pressure_change_pa = 0.0
    max_reynolds_number = 0.0
    limiting_inner_diameter_m = float("inf")
    limiting_velocity_m_s = 0.0

    for run_index, pipe_run in enumerate(pipe_runs):
        radius_m = float(pipe_run.get("radius_cm", 0.0)) / 100.0
        if radius_m <= 0.0:
            continue
        pipe_id = str(pipe_run.get("id", f"run_{run_index}"))
        local_volumetric_flow_m3_s = (
            float(flow_by_pipe_id_m3_s[pipe_id])
            if flow_by_pipe_id_m3_s and pipe_id in flow_by_pipe_id_m3_s
            else volumetric_flow_m3_s
        )
        inner_diameter_m = 2.0 * radius_m
        flow_area_m2 = math.pi * radius_m * radius_m
        velocity_m_s = local_volumetric_flow_m3_s / flow_area_m2 if flow_area_m2 > 0.0 else 0.0
        reynolds_number = (
            salt_density_kg_m3 * velocity_m_s * inner_diameter_m / dynamic_viscosity_pa_s
            if dynamic_viscosity_pa_s > 0.0
            else 0.0
        )
        friction_factor = _darcy_friction_factor(reynolds_number)
        limiting_inner_diameter_m = min(limiting_inner_diameter_m, inner_diameter_m)
        limiting_velocity_m_s = max(limiting_velocity_m_s, velocity_m_s)
        max_reynolds_number = max(max_reynolds_number, reynolds_number)

        points = [tuple(float(value) for value in point) for point in pipe_run.get("points", [])]
        run_length_m = 0.0
        segment_count = 0
        total_elevation_change_m = 0.0
        vertical_rise_m = 0.0
        vertical_drop_m = 0.0
        min_elevation_m = 0.0
        max_elevation_m = 0.0
        for start, stop in zip(points, points[1:]):
            length_m = math.dist(start, stop) / 100.0
            if length_m <= 0.0:
                continue
            run_length_m += length_m
            segment_count += 1
            delta_z_m = (stop[2] - start[2]) / 100.0
            total_elevation_change_m += delta_z_m
            is_vertical_segment = math.isclose(start[0], stop[0], abs_tol=1.0e-9) and math.isclose(
                start[1], stop[1], abs_tol=1.0e-9
            )
            if is_vertical_segment and delta_z_m > 0.0:
                vertical_rise_m += delta_z_m
            elif is_vertical_segment and delta_z_m < 0.0:
                vertical_drop_m += -delta_z_m
        if points:
            elevations_m = [point[2] / 100.0 for point in points]
            min_elevation_m = min(elevations_m)
            max_elevation_m = max(elevations_m)

        friction_drop_pa = friction_factor * (run_length_m / inner_diameter_m) * (salt_density_kg_m3 * velocity_m_s * velocity_m_s / 2.0)
        bend_count = max(0, len(points) - 2)
        local_k = terminal_loss_coefficient + bend_count * elbow_loss_coefficient
        local_drop_pa = local_k * (salt_density_kg_m3 * velocity_m_s * velocity_m_s / 2.0)
        hydrostatic_change_pa = salt_density_kg_m3 * GRAVITY_M_S2 * total_elevation_change_m

        total_pipe_length_m += run_length_m
        frictional_pressure_drop_pa += friction_drop_pa
        local_pressure_drop_pa += local_drop_pa
        hydrostatic_pressure_change_pa += hydrostatic_change_pa
        segment = {
            "name": pipe_id,
            "id": pipe_id,
            "volumetric_flow_m3_s": _round_float(local_volumetric_flow_m3_s),
            "length_m": _round_float(run_length_m),
            "inner_diameter_m": _round_float(inner_diameter_m),
            "velocity_m_s": _round_float(velocity_m_s),
            "reynolds_number": _round_float(reynolds_number),
            "darcy_friction_factor": _round_float(friction_factor),
            "segment_count": segment_count,
            "bend_count": bend_count,
            "elevation_change_m": _round_float(total_elevation_change_m),
            "vertical_rise_m": _round_float(vertical_rise_m),
            "vertical_drop_m": _round_float(vertical_drop_m),
            "min_elevation_m": _round_float(min_elevation_m),
            "max_elevation_m": _round_float(max_elevation_m),
            "frictional_pressure_drop_kpa": _round_float(friction_drop_pa / 1000.0),
            "local_pressure_drop_kpa": _round_float(local_drop_pa / 1000.0),
            "hydrostatic_pressure_change_kpa": _round_float(hydrostatic_change_pa / 1000.0),
        }
        segments.append(segment)
        segment_lookup[pipe_id] = segment

    if math.isinf(limiting_inner_diameter_m):
        limiting_inner_diameter_m = 0.0

    return {
        "segments": segments,
        "segment_lookup": segment_lookup,
        "total_pipe_length_m": total_pipe_length_m,
        "limiting_inner_diameter_m": limiting_inner_diameter_m,
        "limiting_velocity_m_s": limiting_velocity_m_s,
        "max_reynolds_number": max_reynolds_number,
        "frictional_pressure_drop_pa": frictional_pressure_drop_pa,
        "local_pressure_drop_pa": local_pressure_drop_pa,
        "hydrostatic_pressure_change_pa": hydrostatic_pressure_change_pa,
    }


def _build_primary_loop_graph(
    primary_loop: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    component_specs = primary_loop.get("components") or [
        {"id": "heat_exchanger", "kind": "heat_sink"},
        {"id": "pump", "kind": "pump"},
        {"id": "core", "kind": "heat_source"},
    ]
    components = {
        str(spec["id"]): {
            "id": str(spec["id"]),
            "kind": str(spec.get("kind", "junction")),
        }
        for spec in component_specs
        if spec.get("id")
    }
    segment_lookup = {
        str(segment.get("id") or segment["name"]): dict(segment)
        for segment in segments
    }
    connection_specs = primary_loop.get("connections") or []
    edges = []
    outgoing: dict[str, list[dict[str, Any]]] = {}
    incoming: dict[str, list[dict[str, Any]]] = {}
    for connection in connection_specs:
        edge_id = str(connection.get("id") or "")
        if not edge_id:
            continue
        if edge_id not in segment_lookup:
            raise ValueError(f"Primary-loop connection '{edge_id}' does not match any pipe geometry.")
        edge = {
            **segment_lookup[edge_id],
            "id": edge_id,
            "name": edge_id,
            "from": str(connection.get("from") or ""),
            "to": str(connection.get("to") or ""),
            "leg": str(connection.get("leg", "unknown")),
            "heat_exchanger_area_fraction": connection.get("heat_exchanger_area_fraction"),
        }
        edges.append(edge)
        if edge["from"]:
            outgoing.setdefault(edge["from"], []).append(edge)
        if edge["to"]:
            incoming.setdefault(edge["to"], []).append(edge)

    if not edges:
        raise ValueError("Primary-loop graph requires explicit connections.")

    start_component_id = _pick_loop_start_component(components, outgoing)
    cycle_edges = _traverse_single_loop_cycle(start_component_id, outgoing)
    cycle_component_ids = [start_component_id]
    for edge in cycle_edges:
        if edge["to"]:
            cycle_component_ids.append(edge["to"])

    hot_leg_edge_ids, cold_leg_edge_ids = _partition_thermal_legs(cycle_edges, components)
    branch_groups = _identify_branch_groups(components, outgoing, incoming)

    return {
        "components": list(components.values()),
        "edges": edges,
        "start_component_id": start_component_id,
        "cycle_component_ids": cycle_component_ids,
        "cycle_edge_ids": [str(edge["id"]) for edge in cycle_edges],
        "hot_leg_edge_ids": hot_leg_edge_ids,
        "cold_leg_edge_ids": cold_leg_edge_ids,
        "branch_groups": branch_groups,
    }


def _pick_loop_start_component(
    components: dict[str, dict[str, Any]],
    outgoing: dict[str, list[dict[str, Any]]],
) -> str:
    for preferred_kind in ("heat_sink", "pump", "heat_source"):
        for component_id, component in components.items():
            if component.get("kind") == preferred_kind and outgoing.get(component_id):
                return component_id
    for component_id in components:
        if outgoing.get(component_id):
            return component_id
    return next(iter(components), "")


def _traverse_single_loop_cycle(
    start_component_id: str,
    outgoing: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    cycle_edges: list[dict[str, Any]] = []
    if not start_component_id:
        return cycle_edges

    current_component_id = start_component_id
    visited_edge_ids: set[str] = set()
    for _ in range(max(len(outgoing) * 2, 1) + 8):
        candidate_edges = [
            edge
            for edge in outgoing.get(current_component_id, [])
            if str(edge["id"]) not in visited_edge_ids
        ]
        if len(candidate_edges) != 1:
            break
        edge = candidate_edges[0]
        cycle_edges.append(edge)
        visited_edge_ids.add(str(edge["id"]))
        current_component_id = str(edge.get("to") or "")
        if current_component_id == start_component_id:
            break
    return cycle_edges


def _partition_thermal_legs(
    cycle_edges: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    if not cycle_edges:
        return [], []

    source_component_id = next(
        (component_id for component_id, component in components.items() if component.get("kind") == "heat_source"),
        "",
    )
    sink_component_id = next(
        (component_id for component_id, component in components.items() if component.get("kind") == "heat_sink"),
        "",
    )
    if not source_component_id or not sink_component_id:
        return [], []

    cold_leg_edge_ids: list[str] = []
    hot_leg_edge_ids: list[str] = []
    encountered_source = False
    for edge in cycle_edges:
        edge_id = str(edge["id"])
        if str(edge.get("from") or "") == source_component_id:
            encountered_source = True
        if encountered_source:
            hot_leg_edge_ids.append(edge_id)
        else:
            cold_leg_edge_ids.append(edge_id)
        if str(edge.get("to") or "") == sink_component_id and encountered_source:
            break
    if not hot_leg_edge_ids or not cold_leg_edge_ids:
        return (
            [str(edge["id"]) for edge in cycle_edges if str(edge.get("leg", "")) == "hot_leg"],
            [str(edge["id"]) for edge in cycle_edges if str(edge.get("leg", "")) == "cold_leg"],
        )
    return hot_leg_edge_ids, cold_leg_edge_ids


def _identify_branch_groups(
    components: dict[str, dict[str, Any]],
    outgoing: dict[str, list[dict[str, Any]]],
    incoming: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    branch_groups: list[dict[str, Any]] = []
    for split_component_id, outgoing_edges in outgoing.items():
        if len(outgoing_edges) < 2:
            continue
        branch_paths = []
        merge_component_ids: set[str] = set()
        valid_group = True
        for edge in outgoing_edges:
            path_edge_ids = [str(edge["id"])]
            current_component_id = str(edge.get("to") or "")
            if not current_component_id:
                valid_group = False
                break
            while True:
                if len(incoming.get(current_component_id, [])) > 1:
                    merge_component_ids.add(current_component_id)
                    break
                next_edges = outgoing.get(current_component_id, [])
                if len(next_edges) != 1:
                    valid_group = False
                    break
                next_edge = next_edges[0]
                path_edge_ids.append(str(next_edge["id"]))
                current_component_id = str(next_edge.get("to") or "")
                if not current_component_id:
                    valid_group = False
                    break
            if not valid_group:
                break
            branch_paths.append(
                {
                    "branch_id": str(edge["id"]),
                    "edge_ids": path_edge_ids,
                    "merge_component_id": current_component_id,
                }
            )
        if not valid_group or len(merge_component_ids) != 1:
            continue
        merge_component_id = next(iter(merge_component_ids))
        branch_groups.append(
            {
                "split_component_id": split_component_id,
                "merge_component_id": merge_component_id,
                "branches": branch_paths,
            }
        )
    return branch_groups


def _build_pressure_budget(
    segment_summary: dict[str, Any],
    *,
    loop_graph: dict[str, Any],
    hot_leg_density_kg_m3: float,
    cold_leg_density_kg_m3: float,
) -> dict[str, float]:
    frictional_pressure_drop_pa = float(segment_summary["frictional_pressure_drop_pa"])
    local_pressure_drop_pa = float(segment_summary["local_pressure_drop_pa"])
    hydrostatic_pressure_change_pa = float(segment_summary["hydrostatic_pressure_change_pa"])
    segments = segment_summary.get("segments", [])
    edge_lookup = {
        str(edge.get("id") or edge.get("name")): edge
        for edge in loop_graph.get("edges", [])
    }
    hot_leg_segments = [
        edge_lookup[edge_id]
        for edge_id in loop_graph.get("hot_leg_edge_ids", [])
        if edge_id in edge_lookup
    ]
    cold_leg_segments = [
        edge_lookup[edge_id]
        for edge_id in loop_graph.get("cold_leg_edge_ids", [])
        if edge_id in edge_lookup
    ]
    hot_leg_rise_m = sum(float(segment.get("vertical_rise_m", 0.0)) for segment in hot_leg_segments)
    cold_leg_drop_m = sum(float(segment.get("vertical_drop_m", 0.0)) for segment in cold_leg_segments)
    buoyancy_geometry_complete = hot_leg_rise_m > 0.0 and cold_leg_drop_m > 0.0
    if buoyancy_geometry_complete:
        representative_elevation_span_m = 0.5 * (hot_leg_rise_m + cold_leg_drop_m)
    else:
        hot_leg_high_m = max((float(segment.get("max_elevation_m", 0.0)) for segment in hot_leg_segments), default=0.0)
        cold_leg_low_m = min((float(segment.get("min_elevation_m", 0.0)) for segment in cold_leg_segments), default=0.0)
        representative_elevation_span_m = max(hot_leg_high_m - cold_leg_low_m, 0.0)
    if representative_elevation_span_m <= 0.0:
        highest_elevation_m = max((float(segment.get("max_elevation_m", 0.0)) for segment in segments), default=0.0)
        lowest_elevation_m = min((float(segment.get("min_elevation_m", 0.0)) for segment in segments), default=0.0)
        representative_elevation_span_m = max(highest_elevation_m - lowest_elevation_m, 0.0)
    density_difference_kg_m3 = max(cold_leg_density_kg_m3 - hot_leg_density_kg_m3, 0.0)
    buoyancy_driving_pressure_pa = (
        density_difference_kg_m3 * GRAVITY_M_S2 * representative_elevation_span_m
        if buoyancy_geometry_complete
        else 0.0
    )
    net_resistive_pressure_pa = frictional_pressure_drop_pa + local_pressure_drop_pa + hydrostatic_pressure_change_pa
    required_pump_pressure_pa = net_resistive_pressure_pa - buoyancy_driving_pressure_pa
    average_density_kg_m3 = 0.5 * (hot_leg_density_kg_m3 + cold_leg_density_kg_m3)
    thermal_expansion_head_m = (
        buoyancy_driving_pressure_pa / (average_density_kg_m3 * GRAVITY_M_S2)
        if average_density_kg_m3 > 0.0
        else 0.0
    )

    return {
        "frictional_pressure_drop_pa": frictional_pressure_drop_pa,
        "local_pressure_drop_pa": local_pressure_drop_pa,
        "hydrostatic_pressure_change_pa": hydrostatic_pressure_change_pa,
        "buoyancy_driving_pressure_pa": buoyancy_driving_pressure_pa,
        "net_resistive_pressure_pa": net_resistive_pressure_pa,
        "required_pump_pressure_pa": required_pump_pressure_pa,
        "thermal_expansion_head_m": thermal_expansion_head_m,
        "representative_elevation_span_m": representative_elevation_span_m,
        "hot_leg_rise_m": hot_leg_rise_m,
        "cold_leg_drop_m": cold_leg_drop_m,
        "buoyancy_geometry_complete": buoyancy_geometry_complete,
    }


def _build_pump_demand_summary(
    net_required_pressure_pa: float,
    *,
    salt_density_kg_m3: float,
    volumetric_flow_m3_s: float,
    pump_efficiency: float,
) -> dict[str, float]:
    pump_demand_pressure_pa = max(float(net_required_pressure_pa), 0.0)
    natural_circulation_margin_pa = max(-float(net_required_pressure_pa), 0.0)
    pump_head_m = (
        pump_demand_pressure_pa / (salt_density_kg_m3 * GRAVITY_M_S2)
        if salt_density_kg_m3 > 0.0
        else 0.0
    )
    hydraulic_power_kw = pump_demand_pressure_pa * max(volumetric_flow_m3_s, 0.0) / 1000.0
    shaft_power_kw = hydraulic_power_kw / pump_efficiency if pump_efficiency > 0.0 else 0.0
    return {
        "net_required_pressure_pa": float(net_required_pressure_pa),
        "pump_demand_pressure_pa": pump_demand_pressure_pa,
        "natural_circulation_margin_pa": natural_circulation_margin_pa,
        "pump_head_m": pump_head_m,
        "hydraulic_power_kw": hydraulic_power_kw,
        "shaft_power_kw": shaft_power_kw,
    }


def _build_heat_exchanger_summary(
    config: Any,
    bop: dict[str, Any],
    *,
    salt_properties: dict[str, Any],
    primary_mass_flow_kg_s: float,
    primary_hot_leg_c: float | None = None,
    primary_cold_leg_c: float | None = None,
    required_duty_mw: float | None = None,
    loop_graph: dict[str, Any] | None = None,
    thermal_profile: dict[str, Any] | None = None,
    branch_flow_summary: dict[str, Any] | None = None,
    segment_summary: dict[str, Any],
) -> dict[str, Any]:
    primary_cp_j_kgk = float(salt_properties["cp_j_kgk"] or 1600.0)
    primary_dynamic_viscosity_pa_s = float(salt_properties["dynamic_viscosity_pa_s"] or 0.012)
    primary_thermal_conductivity_w_mk = float(salt_properties["thermal_conductivity_w_mk"] or 1.0)
    hx_geometry = _build_heat_exchanger_geometry(config)
    primary_inlet_summary = _build_primary_hx_inlet_summary(
        loop_graph=loop_graph or {},
        thermal_profile=thermal_profile or {},
        branch_flow_summary=branch_flow_summary or {},
        segment_summary=segment_summary,
        salt_density_kg_m3=float(salt_properties["density_kg_m3"]),
        default_primary_mass_flow_kg_s=primary_mass_flow_kg_s,
        target_cold_leg_temp_c=(
            config.reactor.get("cold_leg_temp_c", 560.0) if primary_cold_leg_c is None else primary_cold_leg_c
        ),
        required_duty_mw=float(required_duty_mw if required_duty_mw is not None else bop.get("steam_generator_duty_mw", 0.0)),
        hx_primary_hydraulic_diameter_m=hx_geometry["primary_hydraulic_diameter_m"],
        hx_primary_flow_area_m2=hx_geometry["primary_flow_area_m2"],
        dynamic_viscosity_pa_s=primary_dynamic_viscosity_pa_s,
        cp_j_kgk=primary_cp_j_kgk,
        thermal_conductivity_w_mk=primary_thermal_conductivity_w_mk,
    )
    primary_hot_leg_c = float(
        primary_inlet_summary.get("mixed_inlet_temp_c")
        if primary_inlet_summary.get("mixed_inlet_temp_c") is not None
        else config.reactor.get("hot_leg_temp_c", 700.0) if primary_hot_leg_c is None else primary_hot_leg_c
    )
    primary_cold_leg_c = float(
        config.reactor.get("cold_leg_temp_c", 560.0) if primary_cold_leg_c is None else primary_cold_leg_c
    )
    secondary_inlet_c = float(config.reactor.get("secondary_inlet_temp_c", primary_cold_leg_c - 120.0))
    secondary_outlet_c = float(config.reactor.get("secondary_outlet_temp_c", primary_hot_leg_c - 85.0))
    overall_u_w_m2k = float(config.reactor.get("steam_generator_overall_u_w_m2k", 850.0))
    secondary_bulk_temperature_c = 0.5 * (secondary_inlet_c + secondary_outlet_c)
    secondary_properties = evaluate_secondary_coolant_properties(config, temperature_c=secondary_bulk_temperature_c)
    available_duty_mw = float(bop.get("steam_generator_duty_mw", 0.0))
    duty_mw = abs(float(required_duty_mw if required_duty_mw is not None else available_duty_mw))
    duty_w = duty_mw * 1.0e6

    terminal_hot_delta = primary_hot_leg_c - secondary_outlet_c
    terminal_cold_delta = primary_cold_leg_c - secondary_inlet_c
    lmtd_k = _log_mean_temperature_difference(terminal_hot_delta, terminal_cold_delta)
    secondary_cp_j_kgk = float(secondary_properties["cp_j_kgk"])
    secondary_delta_t_k = max(secondary_outlet_c - secondary_inlet_c, 1.0)
    secondary_mass_flow_kg_s = duty_w / (secondary_cp_j_kgk * secondary_delta_t_k) if secondary_cp_j_kgk > 0.0 else 0.0

    limiting_inner_diameter_m = float(segment_summary.get("limiting_inner_diameter_m", 0.0))
    limiting_velocity_m_s = float(segment_summary.get("limiting_velocity_m_s", 0.0))
    primary_pipe_heat_transfer = _estimate_internal_convection(
        density_kg_m3=float(salt_properties["density_kg_m3"]),
        dynamic_viscosity_pa_s=primary_dynamic_viscosity_pa_s,
        cp_j_kgk=primary_cp_j_kgk,
        thermal_conductivity_w_mk=primary_thermal_conductivity_w_mk,
        hydraulic_diameter_m=limiting_inner_diameter_m,
        velocity_m_s=limiting_velocity_m_s,
    )

    primary_hx_velocity_m_s = _velocity_from_mass_flow(
        mass_flow_kg_s=primary_mass_flow_kg_s,
        density_kg_m3=float(salt_properties["density_kg_m3"]),
        flow_area_m2=hx_geometry["primary_flow_area_m2"],
    )
    secondary_hx_velocity_m_s = _velocity_from_mass_flow(
        mass_flow_kg_s=secondary_mass_flow_kg_s,
        density_kg_m3=float(secondary_properties["density_kg_m3"]),
        flow_area_m2=hx_geometry["secondary_flow_area_m2"],
    )
    primary_hx_heat_transfer = _estimate_internal_convection(
        density_kg_m3=float(salt_properties["density_kg_m3"]),
        dynamic_viscosity_pa_s=primary_dynamic_viscosity_pa_s,
        cp_j_kgk=primary_cp_j_kgk,
        thermal_conductivity_w_mk=primary_thermal_conductivity_w_mk,
        hydraulic_diameter_m=hx_geometry["primary_hydraulic_diameter_m"],
        velocity_m_s=primary_hx_velocity_m_s,
    )
    branch_weighted_primary_h_w_m2k = primary_inlet_summary.get("branch_weighted_heat_transfer_coefficient_w_m2k")
    effective_primary_h_w_m2k = (
        float(branch_weighted_primary_h_w_m2k)
        if branch_weighted_primary_h_w_m2k is not None and float(branch_weighted_primary_h_w_m2k) > 0.0
        else float(primary_hx_heat_transfer["heat_transfer_coefficient_w_m2k"])
    )
    secondary_hx_heat_transfer = _estimate_internal_convection(
        density_kg_m3=float(secondary_properties["density_kg_m3"]),
        dynamic_viscosity_pa_s=float(secondary_properties["dynamic_viscosity_pa_s"]),
        cp_j_kgk=float(secondary_properties["cp_j_kgk"]),
        thermal_conductivity_w_mk=float(secondary_properties["thermal_conductivity_w_mk"]),
        hydraulic_diameter_m=hx_geometry["secondary_hydraulic_diameter_m"],
        velocity_m_s=secondary_hx_velocity_m_s,
    )
    estimated_clean_u_w_m2k = _combine_film_coefficients(
        primary_h_w_m2k=effective_primary_h_w_m2k,
        secondary_h_w_m2k=float(secondary_hx_heat_transfer["heat_transfer_coefficient_w_m2k"]),
    )
    effective_u_w_m2k = overall_u_w_m2k if overall_u_w_m2k > 0.0 else estimated_clean_u_w_m2k
    required_area_m2 = duty_w / (effective_u_w_m2k * lmtd_k) if effective_u_w_m2k > 0.0 and lmtd_k > 0.0 else 0.0

    return {
        "duty_mw": _round_float(duty_mw),
        "available_duty_mw": _round_float(available_duty_mw),
        "duty_error_mw": _round_float(available_duty_mw - duty_mw),
        "overall_u_w_m2k": _round_float(overall_u_w_m2k),
        "estimated_clean_u_w_m2k": _round_float(estimated_clean_u_w_m2k),
        "effective_u_w_m2k": _round_float(effective_u_w_m2k),
        "secondary_inlet_temp_c": _round_float(secondary_inlet_c),
        "secondary_outlet_temp_c": _round_float(secondary_outlet_c),
        "secondary_bulk_temperature_c": _round_float(secondary_bulk_temperature_c),
        "secondary_mass_flow_kg_s": _round_float(secondary_mass_flow_kg_s),
        "primary_inlet_mixed_temp_c": _round_float(primary_hot_leg_c),
        "primary_inlet_branches": primary_inlet_summary.get("branches", []),
        "terminal_hot_delta_c": _round_float(terminal_hot_delta),
        "terminal_cold_delta_c": _round_float(terminal_cold_delta),
        "lmtd_c": _round_float(lmtd_k),
        "required_area_m2": _round_float(required_area_m2),
        "primary_pipe_heat_transfer": primary_pipe_heat_transfer,
        "primary_hx_side": {
            **primary_hx_heat_transfer,
            "effective_heat_transfer_coefficient_w_m2k": _round_float(effective_primary_h_w_m2k),
            "branch_modeling_mode": str(primary_inlet_summary.get("hx_modeling_mode", "lumped_primary_side")),
            "branch_weighted_heat_transfer_coefficient_w_m2k": (
                _round_float(float(branch_weighted_primary_h_w_m2k))
                if branch_weighted_primary_h_w_m2k is not None
                else None
            ),
            "velocity_m_s": _round_float(primary_hx_velocity_m_s),
            "hydraulic_diameter_m": _round_float(hx_geometry["primary_hydraulic_diameter_m"]),
            "flow_area_m2": _round_float(hx_geometry["primary_flow_area_m2"]),
        },
        "secondary_hx_side": {
            **secondary_hx_heat_transfer,
            "velocity_m_s": _round_float(secondary_hx_velocity_m_s),
            "hydraulic_diameter_m": _round_float(hx_geometry["secondary_hydraulic_diameter_m"]),
            "flow_area_m2": _round_float(hx_geometry["secondary_flow_area_m2"]),
            "properties": secondary_properties,
        },
    }


def _solve_branch_flow_distribution(
    *,
    primary_loop: dict[str, Any],
    loop_graph: dict[str, Any],
    total_volumetric_flow_m3_s: float,
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
) -> dict[str, Any]:
    pipe_runs = {
        str(pipe_run.get("id")): pipe_run
        for pipe_run in (primary_loop.get("pipes") or [])
        if pipe_run.get("id")
    }
    edge_flow_m3_s = {
        str(edge["id"]): float(total_volumetric_flow_m3_s)
        for edge in loop_graph.get("edges", [])
    }
    branch_groups_summary: list[dict[str, Any]] = []

    for group in loop_graph.get("branch_groups", []):
        branches = group.get("branches", [])
        if len(branches) < 2:
            continue
        branch_flows = _solve_parallel_branch_group_flows(
            branches=branches,
            total_volumetric_flow_m3_s=total_volumetric_flow_m3_s,
            pipe_runs=pipe_runs,
            salt_density_kg_m3=salt_density_kg_m3,
            dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
            elbow_loss_coefficient=elbow_loss_coefficient,
            terminal_loss_coefficient=terminal_loss_coefficient,
        )
        for branch in branches:
            branch_id = str(branch["branch_id"])
            for edge_id in branch.get("edge_ids", []):
                edge_flow_m3_s[str(edge_id)] = branch_flows[branch_id]
        branch_groups_summary.append(
            {
                "split_component_id": str(group.get("split_component_id", "")),
                "merge_component_id": str(group.get("merge_component_id", "")),
                "branches": [
                    {
                        "branch_id": str(branch["branch_id"]),
                        "edge_ids": [str(edge_id) for edge_id in branch.get("edge_ids", [])],
                        "volumetric_flow_m3_s": _round_float(branch_flows[str(branch["branch_id"])]),
                        "path_pressure_drop_kpa": _round_float(
                            _evaluate_branch_path_pressure_drop(
                                edge_ids=[str(edge_id) for edge_id in branch.get("edge_ids", [])],
                                volumetric_flow_m3_s=branch_flows[str(branch["branch_id"])],
                                pipe_runs=pipe_runs,
                                salt_density_kg_m3=salt_density_kg_m3,
                                dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
                                elbow_loss_coefficient=elbow_loss_coefficient,
                                terminal_loss_coefficient=terminal_loss_coefficient,
                            )
                            / 1000.0
                        ),
                        "flow_fraction": _round_float(
                            branch_flows[str(branch["branch_id"])] / total_volumetric_flow_m3_s
                            if total_volumetric_flow_m3_s > 0.0
                            else 0.0
                        ),
                    }
                    for branch in branches
                ],
            }
        )

    return {
        "edge_flow_m3_s": {edge_id: _round_float(flow) for edge_id, flow in edge_flow_m3_s.items()},
        "branch_groups": branch_groups_summary,
    }


def _solve_parallel_branch_group_flows(
    *,
    branches: list[dict[str, Any]],
    total_volumetric_flow_m3_s: float,
    pipe_runs: dict[str, dict[str, Any]],
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
) -> dict[str, float]:
    if total_volumetric_flow_m3_s <= 0.0:
        return {str(branch["branch_id"]): 0.0 for branch in branches}

    def total_group_flow_for_drop(target_drop_pa: float) -> float:
        return sum(
            _solve_branch_flow_for_pressure_drop(
                edge_ids=[str(edge_id) for edge_id in branch.get("edge_ids", [])],
                target_drop_pa=target_drop_pa,
                max_volumetric_flow_m3_s=total_volumetric_flow_m3_s,
                pipe_runs=pipe_runs,
                salt_density_kg_m3=salt_density_kg_m3,
                dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
                elbow_loss_coefficient=elbow_loss_coefficient,
                terminal_loss_coefficient=terminal_loss_coefficient,
            )
            for branch in branches
        )

    high_drop_pa = max(
        _evaluate_branch_path_pressure_drop(
            edge_ids=[str(edge_id) for edge_id in branch.get("edge_ids", [])],
            volumetric_flow_m3_s=total_volumetric_flow_m3_s,
            pipe_runs=pipe_runs,
            salt_density_kg_m3=salt_density_kg_m3,
            dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
            elbow_loss_coefficient=elbow_loss_coefficient,
            terminal_loss_coefficient=terminal_loss_coefficient,
        )
        for branch in branches
    )
    high_drop_pa = max(high_drop_pa, 1.0)
    low_drop_pa = 0.0
    for _ in range(40):
        mid_drop_pa = 0.5 * (low_drop_pa + high_drop_pa)
        if total_group_flow_for_drop(mid_drop_pa) >= total_volumetric_flow_m3_s:
            high_drop_pa = mid_drop_pa
        else:
            low_drop_pa = mid_drop_pa

    solved_flows = {
        str(branch["branch_id"]): _solve_branch_flow_for_pressure_drop(
            edge_ids=[str(edge_id) for edge_id in branch.get("edge_ids", [])],
            target_drop_pa=high_drop_pa,
            max_volumetric_flow_m3_s=total_volumetric_flow_m3_s,
            pipe_runs=pipe_runs,
            salt_density_kg_m3=salt_density_kg_m3,
            dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
            elbow_loss_coefficient=elbow_loss_coefficient,
            terminal_loss_coefficient=terminal_loss_coefficient,
        )
        for branch in branches
    }
    solved_total_flow_m3_s = sum(solved_flows.values())
    if solved_total_flow_m3_s > 0.0:
        flow_scale = total_volumetric_flow_m3_s / solved_total_flow_m3_s
        solved_flows = {
            branch_id: flow_m3_s * flow_scale
            for branch_id, flow_m3_s in solved_flows.items()
        }
    return solved_flows


def _solve_branch_flow_for_pressure_drop(
    *,
    edge_ids: list[str],
    target_drop_pa: float,
    max_volumetric_flow_m3_s: float,
    pipe_runs: dict[str, dict[str, Any]],
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
) -> float:
    if target_drop_pa <= 0.0 or max_volumetric_flow_m3_s <= 0.0:
        return 0.0
    full_flow_drop_pa = _evaluate_branch_path_pressure_drop(
        edge_ids=edge_ids,
        volumetric_flow_m3_s=max_volumetric_flow_m3_s,
        pipe_runs=pipe_runs,
        salt_density_kg_m3=salt_density_kg_m3,
        dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
        elbow_loss_coefficient=elbow_loss_coefficient,
        terminal_loss_coefficient=terminal_loss_coefficient,
    )
    if full_flow_drop_pa <= target_drop_pa:
        return max_volumetric_flow_m3_s

    low_flow_m3_s = 0.0
    high_flow_m3_s = max_volumetric_flow_m3_s
    for _ in range(40):
        mid_flow_m3_s = 0.5 * (low_flow_m3_s + high_flow_m3_s)
        mid_drop_pa = _evaluate_branch_path_pressure_drop(
            edge_ids=edge_ids,
            volumetric_flow_m3_s=mid_flow_m3_s,
            pipe_runs=pipe_runs,
            salt_density_kg_m3=salt_density_kg_m3,
            dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
            elbow_loss_coefficient=elbow_loss_coefficient,
            terminal_loss_coefficient=terminal_loss_coefficient,
        )
        if mid_drop_pa >= target_drop_pa:
            high_flow_m3_s = mid_flow_m3_s
        else:
            low_flow_m3_s = mid_flow_m3_s
    return high_flow_m3_s


def _build_primary_hx_inlet_summary(
    *,
    loop_graph: dict[str, Any],
    thermal_profile: dict[str, Any],
    branch_flow_summary: dict[str, Any],
    segment_summary: dict[str, Any],
    salt_density_kg_m3: float,
    default_primary_mass_flow_kg_s: float,
    target_cold_leg_temp_c: float,
    required_duty_mw: float,
    hx_primary_hydraulic_diameter_m: float,
    hx_primary_flow_area_m2: float,
    dynamic_viscosity_pa_s: float,
    cp_j_kgk: float,
    thermal_conductivity_w_mk: float,
) -> dict[str, Any]:
    edge_lookup = {
        str(edge.get("id") or edge.get("name")): edge
        for edge in loop_graph.get("edges", [])
    }
    segment_lookup = {
        str(segment.get("name")): segment
        for segment in thermal_profile.get("segments", [])
        if segment.get("name")
    }
    hydraulic_segment_lookup = {
        str(segment.get("id") or segment.get("name")): segment
        for segment in segment_summary.get("segments", [])
        if segment.get("id") or segment.get("name")
    }
    edge_flow_m3_s = branch_flow_summary.get("edge_flow_m3_s", {})
    incoming_edges = [
        edge
        for edge in edge_lookup.values()
        if str(edge.get("to") or "") == "heat_exchanger"
    ]
    explicit_area_fraction_sum = 0.0
    explicit_area_fractions: dict[str, float] = {}
    for edge in incoming_edges:
        edge_id = str(edge["id"])
        area_fraction = edge.get("heat_exchanger_area_fraction")
        if area_fraction is None:
            explicit_area_fractions = {}
            explicit_area_fraction_sum = 0.0
            break
        area_fraction_value = float(area_fraction)
        if area_fraction_value <= 0.0:
            explicit_area_fractions = {}
            explicit_area_fraction_sum = 0.0
            break
        explicit_area_fractions[edge_id] = area_fraction_value
        explicit_area_fraction_sum += area_fraction_value
    has_explicit_hx_area_split = len(explicit_area_fractions) == len(incoming_edges) and explicit_area_fraction_sum > 0.0
    branches: list[dict[str, Any]] = []
    for edge in incoming_edges:
        edge_id = str(edge["id"])
        segment = segment_lookup.get(edge_id)
        if not segment:
            continue
        volumetric_flow_m3_s = float(edge_flow_m3_s.get(edge_id, 0.0))
        mass_flow_kg_s = (
            volumetric_flow_m3_s * salt_density_kg_m3
            if volumetric_flow_m3_s > 0.0 and salt_density_kg_m3 > 0.0
            else default_primary_mass_flow_kg_s
        )
        hydraulic_segment = hydraulic_segment_lookup.get(edge_id, {})
        inlet_temp_c = float(segment.get("outlet_temp_c", segment.get("inlet_temp_c", 0.0)))
        branch_flow_fraction = mass_flow_kg_s / default_primary_mass_flow_kg_s if default_primary_mass_flow_kg_s > 0.0 else 0.0
        local_hot_side_heat_transfer = _estimate_internal_convection(
            density_kg_m3=salt_density_kg_m3,
            dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
            cp_j_kgk=cp_j_kgk,
            thermal_conductivity_w_mk=thermal_conductivity_w_mk,
            hydraulic_diameter_m=float(
                hydraulic_segment.get("inner_diameter_m", hx_primary_hydraulic_diameter_m)
            ),
            velocity_m_s=float(hydraulic_segment.get("velocity_m_s", 0.0)),
        )
        branch_thermal_capacity_w = max(inlet_temp_c - target_cold_leg_temp_c, 0.0) * mass_flow_kg_s * cp_j_kgk
        hx_area_fraction = (
            explicit_area_fractions[edge_id] / explicit_area_fraction_sum
            if has_explicit_hx_area_split
            else None
        )
        branch_hx_area_m2 = hx_primary_flow_area_m2 * hx_area_fraction if hx_area_fraction is not None else None
        branch_hx_velocity_m_s = (
            _velocity_from_mass_flow(
                mass_flow_kg_s=mass_flow_kg_s,
                density_kg_m3=salt_density_kg_m3,
                flow_area_m2=branch_hx_area_m2 or 0.0,
            )
            if branch_hx_area_m2 is not None
            else None
        )
        branch_hx_heat_transfer = (
            _estimate_internal_convection(
                density_kg_m3=salt_density_kg_m3,
                dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
                cp_j_kgk=cp_j_kgk,
                thermal_conductivity_w_mk=thermal_conductivity_w_mk,
                hydraulic_diameter_m=hx_primary_hydraulic_diameter_m,
                velocity_m_s=branch_hx_velocity_m_s or 0.0,
            )
            if branch_hx_velocity_m_s is not None
            else None
        )
        branches.append(
            {
                "edge_id": edge_id,
                "inlet_temp_c": _round_float(inlet_temp_c),
                "mass_flow_kg_s": _round_float(mass_flow_kg_s),
                "volumetric_flow_m3_s": _round_float(volumetric_flow_m3_s),
                "flow_fraction": _round_float(branch_flow_fraction),
                "thermal_capacity_mw": _round_float(branch_thermal_capacity_w / 1.0e6),
                "hx_area_fraction": _round_float(hx_area_fraction) if hx_area_fraction is not None else None,
                "local_hot_side_entry_h_w_m2k": local_hot_side_heat_transfer["heat_transfer_coefficient_w_m2k"],
                "local_hot_side_entry_velocity_m_s": _round_float(float(hydraulic_segment.get("velocity_m_s", 0.0))),
                "hx_local_h_w_m2k": (
                    branch_hx_heat_transfer["heat_transfer_coefficient_w_m2k"]
                    if branch_hx_heat_transfer is not None
                    else None
                ),
                "hx_local_velocity_m_s": _round_float(branch_hx_velocity_m_s) if branch_hx_velocity_m_s is not None else None,
                "hx_allocated_flow_area_m2": _round_float(branch_hx_area_m2) if branch_hx_area_m2 is not None else None,
            }
        )
    total_mass_flow_kg_s = sum(float(branch["mass_flow_kg_s"]) for branch in branches)
    total_thermal_capacity_mw = sum(float(branch["thermal_capacity_mw"]) for branch in branches)
    duty_mw = abs(float(required_duty_mw))
    for branch in branches:
        thermal_capacity_mw = float(branch["thermal_capacity_mw"])
        duty_fraction = (
            thermal_capacity_mw / total_thermal_capacity_mw
            if total_thermal_capacity_mw > 0.0
            else float(branch["mass_flow_kg_s"]) / total_mass_flow_kg_s if total_mass_flow_kg_s > 0.0 else 0.0
        )
        branch["duty_fraction"] = _round_float(duty_fraction)
        branch["duty_share_mw"] = _round_float(duty_fraction * duty_mw)
    mixed_inlet_temp_c = (
        sum(float(branch["mass_flow_kg_s"]) * float(branch["inlet_temp_c"]) for branch in branches) / total_mass_flow_kg_s
        if total_mass_flow_kg_s > 0.0
        else None
    )
    branch_weighted_heat_transfer_coefficient_w_m2k = (
        sum(float(branch["duty_fraction"]) * float(branch["hx_local_h_w_m2k"]) for branch in branches)
        if has_explicit_hx_area_split and branches
        else None
    )
    return {
        "mixed_inlet_temp_c": mixed_inlet_temp_c,
        "branches": branches,
        "hx_modeling_mode": "explicit_area_fraction" if has_explicit_hx_area_split else "lumped_primary_side",
        "branch_weighted_heat_transfer_coefficient_w_m2k": branch_weighted_heat_transfer_coefficient_w_m2k,
    }


def _evaluate_branch_path_pressure_drop(
    *,
    edge_ids: list[str],
    volumetric_flow_m3_s: float,
    pipe_runs: dict[str, dict[str, Any]],
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
) -> float:
    total_drop_pa = 0.0
    for edge_id in edge_ids:
        pipe_run = pipe_runs.get(edge_id)
        if not pipe_run:
            continue
        segment = _build_pipe_segment_summary(
            [pipe_run],
            volumetric_flow_m3_s,
            salt_density_kg_m3,
            dynamic_viscosity_pa_s,
            elbow_loss_coefficient,
            terminal_loss_coefficient,
        )["segments"][0]
        total_drop_pa += (
            float(segment.get("frictional_pressure_drop_kpa", 0.0)) * 1000.0
            + float(segment.get("local_pressure_drop_kpa", 0.0)) * 1000.0
        )
    return total_drop_pa

def _build_heat_exchanger_geometry(config: Any) -> dict[str, float]:
    reactor = config.reactor
    primary_hydraulic_diameter_m = float(reactor.get("hx_primary_hydraulic_diameter_m", 0.032))
    secondary_hydraulic_diameter_m = float(reactor.get("hx_secondary_hydraulic_diameter_m", 0.024))
    primary_flow_area_m2 = float(reactor.get("hx_primary_flow_area_m2", 0.0105))
    secondary_flow_area_m2 = float(reactor.get("hx_secondary_flow_area_m2", 0.0115))
    return {
        "primary_hydraulic_diameter_m": primary_hydraulic_diameter_m,
        "secondary_hydraulic_diameter_m": secondary_hydraulic_diameter_m,
        "primary_flow_area_m2": primary_flow_area_m2,
        "secondary_flow_area_m2": secondary_flow_area_m2,
    }


def _velocity_from_mass_flow(*, mass_flow_kg_s: float, density_kg_m3: float, flow_area_m2: float) -> float:
    if density_kg_m3 <= 0.0 or flow_area_m2 <= 0.0:
        return 0.0
    return mass_flow_kg_s / (density_kg_m3 * flow_area_m2)


def _estimate_internal_convection(
    *,
    density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    cp_j_kgk: float,
    thermal_conductivity_w_mk: float,
    hydraulic_diameter_m: float,
    velocity_m_s: float,
) -> dict[str, Any]:
    reynolds_number = (
        density_kg_m3 * velocity_m_s * hydraulic_diameter_m / dynamic_viscosity_pa_s
        if dynamic_viscosity_pa_s > 0.0 and hydraulic_diameter_m > 0.0
        else 0.0
    )
    prandtl_number = (
        cp_j_kgk * dynamic_viscosity_pa_s / thermal_conductivity_w_mk
        if thermal_conductivity_w_mk > 0.0
        else 0.0
    )
    nusselt_number = _internal_nusselt_number(reynolds_number, prandtl_number)
    heat_transfer_coefficient_w_m2k = (
        nusselt_number * thermal_conductivity_w_mk / hydraulic_diameter_m
        if hydraulic_diameter_m > 0.0
        else 0.0
    )
    regime = "turbulent" if reynolds_number >= 4000.0 else "transitional" if reynolds_number >= 2300.0 else "laminar"
    return {
        "reynolds_number": _round_float(reynolds_number),
        "prandtl_number": _round_float(prandtl_number),
        "nusselt_number": _round_float(nusselt_number),
        "heat_transfer_coefficient_w_m2k": _round_float(heat_transfer_coefficient_w_m2k),
        "regime": regime,
    }


def _internal_nusselt_number(reynolds_number: float, prandtl_number: float) -> float:
    if reynolds_number <= 0.0 or prandtl_number <= 0.0:
        return 0.0
    if reynolds_number < 2300.0:
        return 3.66
    if reynolds_number < 4000.0:
        turbulent_nu = 0.023 * (4000.0 ** 0.8) * (prandtl_number ** 0.4)
        laminar_nu = 3.66
        blend = (reynolds_number - 2300.0) / (4000.0 - 2300.0)
        return laminar_nu + blend * (turbulent_nu - laminar_nu)
    return 0.023 * (reynolds_number ** 0.8) * (prandtl_number ** 0.4)


def _combine_film_coefficients(*, primary_h_w_m2k: float, secondary_h_w_m2k: float) -> float:
    if primary_h_w_m2k <= 0.0 or secondary_h_w_m2k <= 0.0:
        return 0.0
    return 1.0 / ((1.0 / primary_h_w_m2k) + (1.0 / secondary_h_w_m2k))


def _build_inventory_summary(
    config: Any,
    geometry_description: dict[str, Any],
    reduced_order_flow: dict[str, Any],
    primary_loop: dict[str, Any],
) -> dict[str, Any]:
    bulk_temperature_c = average_primary_temperature_c(config.reactor)
    salt_density_kg_m3 = float(evaluate_primary_coolant_properties(config, temperature_c=bulk_temperature_c)["density_kg_m3"])
    shell_lookup = {shell["name"]: shell for shell in geometry_description.get("shells", [])}
    lower_plenum_m3 = _shell_volume_m3(shell_lookup.get("lower_plenum"))
    upper_plenum_m3 = _shell_volume_m3(shell_lookup.get("upper_plenum"))
    downcomer_m3 = _shell_volume_m3(shell_lookup.get("downcomer"))
    active_connected_m3 = float(reduced_order_flow["active_flow"]["total_salt_volume_cm3"]) * 1.0e-6
    active_disconnected_m3 = float(reduced_order_flow["disconnected_inventory"]["salt_volume_cm3"]) * 1.0e-6
    total_fuel_salt_m3 = active_connected_m3 + active_disconnected_m3 + lower_plenum_m3 + upper_plenum_m3 + downcomer_m3

    render_layout = config.geometry.get("render_layout") or {}
    pool = render_layout.get("pool") or {}
    core_box = render_layout.get("core_box") or {}
    pool_inner_radius_cm = float(pool.get("radius_cm", 0.0)) - float(pool.get("wall_thickness_cm", 0.0))
    pool_fill_top_z_cm = float(pool.get("fill_top_z_cm", 0.0))
    pool_cylinder_bottom_z_cm = float(pool.get("cylinder_bottom_z_cm", 0.0))
    pool_bottom_head_depth_cm = float(pool.get("bottom_head_depth_cm", 0.0))

    gross_pool_volume_m3 = _dished_pool_fill_volume_m3(
        pool_inner_radius_cm,
        pool_cylinder_bottom_z_cm,
        pool_fill_top_z_cm,
        pool_bottom_head_depth_cm,
    )
    core_box_displacement_m3 = _core_box_displacement_m3(core_box)
    barrel_displacement_m3 = _barrel_displacement_m3(core_box, geometry_description)
    hardware_displacement_m3 = _primary_loop_hardware_displacement_m3(primary_loop)
    total_displacement_m3 = core_box_displacement_m3 + barrel_displacement_m3 + hardware_displacement_m3
    net_pool_volume_m3 = max(gross_pool_volume_m3 - total_displacement_m3, 0.0)

    coolant_material = pool.get("material")
    coolant_density_kg_m3 = (
        float(evaluate_fluid_properties(config.materials[coolant_material], temperature_c=bulk_temperature_c)["density_kg_m3"])
        if coolant_material in config.materials
        else 0.0
    )

    return {
        "fuel_salt": {
            "active_connected_m3": _round_float(active_connected_m3),
            "active_disconnected_m3": _round_float(active_disconnected_m3),
            "lower_plenum_m3": _round_float(lower_plenum_m3),
            "upper_plenum_m3": _round_float(upper_plenum_m3),
            "downcomer_m3": _round_float(downcomer_m3),
            "total_m3": _round_float(total_fuel_salt_m3),
            "total_mass_kg": _round_float(total_fuel_salt_m3 * salt_density_kg_m3),
        },
        "coolant_salt": {
            "gross_pool_fill_volume_m3": _round_float(gross_pool_volume_m3),
            "estimated_internal_displacement_m3": _round_float(total_displacement_m3),
            "net_pool_inventory_m3": _round_float(net_pool_volume_m3),
            "net_pool_inventory_kg": _round_float(net_pool_volume_m3 * coolant_density_kg_m3),
        },
    }


def _build_primary_thermal_profile(
    config: Any,
    *,
    primary_loop: dict[str, Any] | None = None,
    loop_graph: dict[str, Any],
    bop: dict[str, Any],
    salt_properties: dict[str, Any],
    initial_mass_flow_kg_s: float,
    fixed_primary_mass_flow_kg_s: float | None = None,
    edge_flow_m3_s: dict[str, float] | None = None,
    salt_density_kg_m3: float | None = None,
) -> dict[str, Any]:
    reactor = config.reactor
    primary_cp_j_kgk = float(salt_properties["cp_j_kgk"] or float(reactor.get("primary_cp_kj_kgk", 1.6)) * 1000.0)
    ambient_temp_c = float(reactor.get("primary_loop_ambient_temp_c", 480.0))
    pipe_overall_u_w_m2k = float(reactor.get("primary_pipe_overall_u_w_m2k", 7.5))
    target_cold_leg_temp_c = float(reactor.get("cold_leg_temp_c", 560.0))
    target_hot_leg_temp_c = float(reactor.get("hot_leg_temp_c", 700.0))
    target_delta_t_c = target_hot_leg_temp_c - target_cold_leg_temp_c
    component_lookup = {
        str(component["id"]): component
        for component in loop_graph.get("components", [])
    }
    edge_lookup = {
        str(edge["id"]): edge
        for edge in loop_graph.get("edges", [])
    }
    cycle_edge_ids = list(loop_graph.get("cycle_edge_ids", []))
    if fixed_primary_mass_flow_kg_s is not None:
        solved_profile = _simulate_primary_loop_network_pass(
            loop_graph=loop_graph,
            component_lookup=component_lookup,
            edge_lookup=edge_lookup,
            ambient_temp_c=ambient_temp_c,
            pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
            target_cold_leg_temp_c=target_cold_leg_temp_c,
            primary_cp_j_kgk=primary_cp_j_kgk,
            core_heat_kw=float(bop.get("thermal_power_mw", 0.0)) * 1000.0,
            default_primary_mass_flow_kg_s=float(fixed_primary_mass_flow_kg_s),
            edge_flow_m3_s=edge_flow_m3_s or {},
            salt_density_kg_m3=float(salt_density_kg_m3 or 0.0),
        )
        solved_profile["iterations"] = 1
        solved_profile["primary_mass_flow_kg_s"] = float(fixed_primary_mass_flow_kg_s)
    else:
        solved_profile = _solve_primary_loop_thermal_state(
            primary_loop=primary_loop or {},
            component_lookup=component_lookup,
            loop_graph=loop_graph,
            edge_lookup=edge_lookup,
            cycle_edge_ids=cycle_edge_ids,
            ambient_temp_c=ambient_temp_c,
            pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
            primary_cp_j_kgk=primary_cp_j_kgk,
            core_heat_kw=float(bop.get("thermal_power_mw", 0.0)) * 1000.0,
            target_delta_t_c=target_delta_t_c,
            target_cold_leg_temp_c=target_cold_leg_temp_c,
            initial_mass_flow_kg_s=initial_mass_flow_kg_s,
            salt_density_kg_m3=float(salt_density_kg_m3 or 0.0),
            dynamic_viscosity_pa_s=float(
                salt_properties["dynamic_viscosity_pa_s"] if salt_properties.get("dynamic_viscosity_pa_s") is not None else 0.012
            ),
            elbow_loss_coefficient=float(reactor.get("primary_elbow_loss_coefficient", 0.9)),
            terminal_loss_coefficient=float(reactor.get("primary_terminal_loss_coefficient", 1.6)),
        )

    return {
        "ambient_temp_c": _round_float(ambient_temp_c),
        "pipe_overall_u_w_m2k": _round_float(pipe_overall_u_w_m2k),
        "target_hot_leg_temp_c": _round_float(target_hot_leg_temp_c),
        "target_cold_leg_temp_c": _round_float(target_cold_leg_temp_c),
        "target_delta_t_c": _round_float(target_delta_t_c),
        "estimated_hot_leg_temp_c": _round_float(solved_profile["hot_leg_temp_c"]),
        "estimated_cold_leg_temp_c": _round_float(solved_profile["cold_leg_temp_c"]),
        "estimated_delta_t_c": _round_float(solved_profile["hot_leg_temp_c"] - solved_profile["cold_leg_temp_c"]),
        "hot_leg_temp_error_c": _round_float(solved_profile["hot_leg_temp_c"] - target_hot_leg_temp_c),
        "cold_leg_closure_error_c": _round_float(solved_profile["cold_leg_temp_c"] - target_cold_leg_temp_c),
        "loop_closure_error_c": _round_float(solved_profile["loop_closure_error_c"]),
        "solver_iterations": int(solved_profile["iterations"]),
        "solved_primary_mass_flow_kg_s": _round_float(solved_profile["primary_mass_flow_kg_s"]),
        "mass_flow_error_kg_s": _round_float(solved_profile["primary_mass_flow_kg_s"] - initial_mass_flow_kg_s),
        "required_heat_exchanger_duty_mw": _round_float(abs(solved_profile["required_heat_exchanger_heat_kw"]) / 1000.0),
        "available_heat_exchanger_duty_mw": _round_float(float(bop.get("steam_generator_duty_mw", 0.0))),
        "heat_exchanger_duty_error_mw": _round_float(
            float(bop.get("steam_generator_duty_mw", 0.0)) - abs(solved_profile["required_heat_exchanger_heat_kw"]) / 1000.0
        ),
        "total_pipe_heat_loss_kw": _round_float(solved_profile["total_pipe_heat_loss_kw"]),
        "segments": solved_profile["segments"],
    }


def _solve_primary_loop_thermal_state(
    *,
    primary_loop: dict[str, Any],
    component_lookup: dict[str, dict[str, Any]],
    loop_graph: dict[str, Any],
    edge_lookup: dict[str, dict[str, Any]],
    cycle_edge_ids: list[str],
    ambient_temp_c: float,
    pipe_overall_u_w_m2k: float,
    primary_cp_j_kgk: float,
    core_heat_kw: float,
    target_delta_t_c: float,
    target_cold_leg_temp_c: float,
    initial_mass_flow_kg_s: float,
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
) -> dict[str, Any]:
    if not cycle_edge_ids or initial_mass_flow_kg_s <= 0.0:
        return {
            "cold_leg_temp_c": 0.0,
            "hot_leg_temp_c": 0.0,
            "loop_closure_error_c": 0.0,
            "total_pipe_heat_loss_kw": 0.0,
            "segments": [],
            "iterations": 0,
            "primary_mass_flow_kg_s": max(initial_mass_flow_kg_s, 0.0),
            "required_heat_exchanger_heat_kw": 0.0,
        }

    guess_a = max(float(initial_mass_flow_kg_s), 1.0e-6)
    branch_flow_a = _solve_branch_flow_distribution(
        primary_loop=primary_loop,
        loop_graph=loop_graph,
        total_volumetric_flow_m3_s=guess_a / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0,
        salt_density_kg_m3=salt_density_kg_m3,
        dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
        elbow_loss_coefficient=elbow_loss_coefficient,
        terminal_loss_coefficient=terminal_loss_coefficient,
    )
    result_a = _simulate_primary_loop_pass(
        primary_mass_flow_kg_s=guess_a,
        component_lookup=component_lookup,
        edge_lookup=edge_lookup,
        cycle_edge_ids=cycle_edge_ids,
        ambient_temp_c=ambient_temp_c,
        pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
        target_cold_leg_temp_c=target_cold_leg_temp_c,
        primary_cp_j_kgk=primary_cp_j_kgk,
        core_heat_kw=core_heat_kw,
    )
    if loop_graph.get("branch_groups"):
        result_a = _simulate_primary_loop_network_pass(
            loop_graph=loop_graph,
            component_lookup=component_lookup,
            edge_lookup=edge_lookup,
            ambient_temp_c=ambient_temp_c,
            pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
            target_cold_leg_temp_c=target_cold_leg_temp_c,
            primary_cp_j_kgk=primary_cp_j_kgk,
            core_heat_kw=core_heat_kw,
            default_primary_mass_flow_kg_s=guess_a,
            edge_flow_m3_s=branch_flow_a["edge_flow_m3_s"],
            salt_density_kg_m3=salt_density_kg_m3,
        )
    error_a = float((result_a["hot_leg_temp_c"] - result_a["cold_leg_temp_c"]) - target_delta_t_c)
    if abs(error_a) <= 1.0e-6:
        result_a["iterations"] = 1
        result_a["primary_mass_flow_kg_s"] = guess_a
        return result_a

    guess_b = max(guess_a * 1.05, guess_a + 1.0)
    if math.isclose(guess_b, guess_a, rel_tol=1.0e-9, abs_tol=1.0e-9):
        guess_b = guess_a + 5.0
    branch_flow_b = _solve_branch_flow_distribution(
        primary_loop=primary_loop,
        loop_graph=loop_graph,
        total_volumetric_flow_m3_s=guess_b / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0,
        salt_density_kg_m3=salt_density_kg_m3,
        dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
        elbow_loss_coefficient=elbow_loss_coefficient,
        terminal_loss_coefficient=terminal_loss_coefficient,
    )
    result_b = _simulate_primary_loop_pass(
        primary_mass_flow_kg_s=guess_b,
        component_lookup=component_lookup,
        edge_lookup=edge_lookup,
        cycle_edge_ids=cycle_edge_ids,
        ambient_temp_c=ambient_temp_c,
        pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
        target_cold_leg_temp_c=target_cold_leg_temp_c,
        primary_cp_j_kgk=primary_cp_j_kgk,
        core_heat_kw=core_heat_kw,
    )
    if loop_graph.get("branch_groups"):
        result_b = _simulate_primary_loop_network_pass(
            loop_graph=loop_graph,
            component_lookup=component_lookup,
            edge_lookup=edge_lookup,
            ambient_temp_c=ambient_temp_c,
            pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
            target_cold_leg_temp_c=target_cold_leg_temp_c,
            primary_cp_j_kgk=primary_cp_j_kgk,
            core_heat_kw=core_heat_kw,
            default_primary_mass_flow_kg_s=guess_b,
            edge_flow_m3_s=branch_flow_b["edge_flow_m3_s"],
            salt_density_kg_m3=salt_density_kg_m3,
        )
    error_b = float((result_b["hot_leg_temp_c"] - result_b["cold_leg_temp_c"]) - target_delta_t_c)

    latest_result = result_b
    latest_error = error_b
    for iteration in range(2, 13):
        if abs(latest_error) <= 1.0e-6:
            latest_result["iterations"] = iteration
            latest_result["primary_mass_flow_kg_s"] = guess_b
            return latest_result
        denominator = error_b - error_a
        if math.isclose(denominator, 0.0, rel_tol=1.0e-12, abs_tol=1.0e-12):
            next_guess = max(guess_b * 0.95, 1.0e-6) if error_b > 0.0 else guess_b * 1.05
        else:
            next_guess = guess_b - error_b * (guess_b - guess_a) / denominator
        next_guess = max(next_guess, 1.0e-6)
        guess_a, error_a = guess_b, error_b
        guess_b = next_guess
        branch_flow_b = _solve_branch_flow_distribution(
            primary_loop=primary_loop,
            loop_graph=loop_graph,
            total_volumetric_flow_m3_s=guess_b / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0,
            salt_density_kg_m3=salt_density_kg_m3,
            dynamic_viscosity_pa_s=dynamic_viscosity_pa_s,
            elbow_loss_coefficient=elbow_loss_coefficient,
            terminal_loss_coefficient=terminal_loss_coefficient,
        )
        latest_result = _simulate_primary_loop_pass(
            primary_mass_flow_kg_s=guess_b,
            component_lookup=component_lookup,
            edge_lookup=edge_lookup,
            cycle_edge_ids=cycle_edge_ids,
            ambient_temp_c=ambient_temp_c,
            pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
            target_cold_leg_temp_c=target_cold_leg_temp_c,
            primary_cp_j_kgk=primary_cp_j_kgk,
            core_heat_kw=core_heat_kw,
        )
        if loop_graph.get("branch_groups"):
            latest_result = _simulate_primary_loop_network_pass(
                loop_graph=loop_graph,
                component_lookup=component_lookup,
                edge_lookup=edge_lookup,
                ambient_temp_c=ambient_temp_c,
                pipe_overall_u_w_m2k=pipe_overall_u_w_m2k,
                target_cold_leg_temp_c=target_cold_leg_temp_c,
                primary_cp_j_kgk=primary_cp_j_kgk,
                core_heat_kw=core_heat_kw,
                default_primary_mass_flow_kg_s=guess_b,
                edge_flow_m3_s=branch_flow_b["edge_flow_m3_s"],
                salt_density_kg_m3=salt_density_kg_m3,
            )
        error_b = float((latest_result["hot_leg_temp_c"] - latest_result["cold_leg_temp_c"]) - target_delta_t_c)
        latest_error = error_b

    latest_result["iterations"] = 12
    latest_result["primary_mass_flow_kg_s"] = guess_b
    return latest_result


def _simulate_primary_loop_pass(
    *,
    primary_mass_flow_kg_s: float,
    component_lookup: dict[str, dict[str, Any]],
    edge_lookup: dict[str, dict[str, Any]],
    cycle_edge_ids: list[str],
    ambient_temp_c: float,
    pipe_overall_u_w_m2k: float,
    target_cold_leg_temp_c: float,
    primary_cp_j_kgk: float,
    core_heat_kw: float,
) -> dict[str, Any]:
    thermal_segments: list[dict[str, Any]] = []
    running_temp_c = float(target_cold_leg_temp_c)
    total_pipe_heat_loss_kw = 0.0
    hot_leg_temp_c = running_temp_c
    required_heat_exchanger_heat_kw = 0.0

    for edge_id in cycle_edge_ids:
        segment_input = edge_lookup.get(edge_id)
        if not segment_input:
            continue
        segment = _build_pipe_thermal_segment(
            name=str(segment_input["name"]),
            inlet_temp_c=running_temp_c,
            length_m=float(segment_input["length_m"]),
            radius_m=float(segment_input["inner_diameter_m"]) / 2.0,
            ambient_temp_c=ambient_temp_c,
            overall_u_w_m2k=pipe_overall_u_w_m2k,
            mass_flow_kg_s=primary_mass_flow_kg_s,
            cp_j_kgk=primary_cp_j_kgk,
        )
        thermal_segments.append(segment)
        running_temp_c = float(segment["outlet_temp_c"])
        total_pipe_heat_loss_kw += -float(segment["heat_transfer_kw"])

        component = component_lookup.get(str(segment_input.get("to") or ""))
        if not component:
            continue
        component_kind = str(component.get("kind", "junction"))
        if component_kind == "pump":
            thermal_segments.append(
                {
                    "name": str(component["id"]),
                    "kind": "pump",
                    "inlet_temp_c": _round_float(running_temp_c),
                    "outlet_temp_c": _round_float(running_temp_c),
                    "heat_transfer_kw": 0.0,
                }
            )
            continue
        if component_kind == "heat_source":
            core_outlet_temp_c = _apply_heat_to_stream(
                inlet_temp_c=running_temp_c,
                heat_transfer_kw=core_heat_kw,
                mass_flow_kg_s=primary_mass_flow_kg_s,
                cp_j_kgk=primary_cp_j_kgk,
            )
            thermal_segments.append(
                {
                    "name": "core_heating",
                    "kind": "heat_source",
                    "component_id": str(component["id"]),
                    "inlet_temp_c": _round_float(running_temp_c),
                    "outlet_temp_c": _round_float(core_outlet_temp_c),
                    "heat_transfer_kw": _round_float(core_heat_kw),
                }
            )
            running_temp_c = core_outlet_temp_c
            hot_leg_temp_c = core_outlet_temp_c
            continue
        if component_kind == "heat_sink":
            required_heat_exchanger_heat_kw = -max(
                (running_temp_c - target_cold_leg_temp_c) * primary_mass_flow_kg_s * primary_cp_j_kgk / 1000.0,
                0.0,
            )
            hx_outlet_temp_c = target_cold_leg_temp_c
            thermal_segments.append(
                {
                    "name": "heat_exchanger_rejection",
                    "kind": "heat_sink",
                    "component_id": str(component["id"]),
                    "inlet_temp_c": _round_float(running_temp_c),
                    "outlet_temp_c": _round_float(hx_outlet_temp_c),
                    "heat_transfer_kw": _round_float(required_heat_exchanger_heat_kw),
                }
            )
            running_temp_c = hx_outlet_temp_c

    return {
        "cold_leg_temp_c": target_cold_leg_temp_c,
        "hot_leg_temp_c": hot_leg_temp_c,
        "end_temp_c": running_temp_c,
        "loop_closure_error_c": running_temp_c - target_cold_leg_temp_c,
        "total_pipe_heat_loss_kw": total_pipe_heat_loss_kw,
        "segments": thermal_segments,
        "primary_mass_flow_kg_s": primary_mass_flow_kg_s,
        "required_heat_exchanger_heat_kw": required_heat_exchanger_heat_kw,
    }


def _simulate_primary_loop_network_pass(
    *,
    loop_graph: dict[str, Any],
    component_lookup: dict[str, dict[str, Any]],
    edge_lookup: dict[str, dict[str, Any]],
    ambient_temp_c: float,
    pipe_overall_u_w_m2k: float,
    target_cold_leg_temp_c: float,
    primary_cp_j_kgk: float,
    core_heat_kw: float,
    default_primary_mass_flow_kg_s: float,
    edge_flow_m3_s: dict[str, float],
    salt_density_kg_m3: float,
) -> dict[str, Any]:
    thermal_segments: list[dict[str, Any]] = []
    total_pipe_heat_loss_kw = 0.0
    required_heat_exchanger_heat_kw = 0.0
    hot_leg_temp_c = target_cold_leg_temp_c
    current_component_id = str(loop_graph.get("start_component_id", ""))
    current_temp_c = float(target_cold_leg_temp_c)
    outgoing_edges: dict[str, list[dict[str, Any]]] = {}
    for edge in loop_graph.get("edges", []):
        outgoing_edges.setdefault(str(edge.get("from") or ""), []).append(edge)
    branch_groups_by_split = {
        str(group.get("split_component_id", "")): group
        for group in loop_graph.get("branch_groups", [])
    }
    visited_edges: set[str] = set()
    visited_branch_splits: set[str] = set()

    for _ in range(max(len(loop_graph.get("edges", [])) * 3, 1) + 8):
        branch_group = branch_groups_by_split.get(current_component_id)
        if branch_group and current_component_id not in visited_branch_splits:
            visited_branch_splits.add(current_component_id)
            branch_results: list[tuple[float, float]] = []
            for branch in branch_group.get("branches", []):
                branch_id = str(branch.get("branch_id", ""))
                branch_mass_flow_kg_s = _edge_mass_flow_kg_s(
                    edge_id=branch_id,
                    default_primary_mass_flow_kg_s=default_primary_mass_flow_kg_s,
                    edge_flow_m3_s=edge_flow_m3_s,
                    salt_density_kg_m3=salt_density_kg_m3,
                )
                branch_temp_c = current_temp_c
                for edge_id in branch.get("edge_ids", []):
                    edge = edge_lookup.get(str(edge_id))
                    if not edge:
                        continue
                    visited_edges.add(str(edge["id"]))
                    segment = _build_pipe_thermal_segment(
                        name=str(edge["id"]),
                        inlet_temp_c=branch_temp_c,
                        length_m=float(edge["length_m"]),
                        radius_m=float(edge["inner_diameter_m"]) / 2.0,
                        ambient_temp_c=ambient_temp_c,
                        overall_u_w_m2k=pipe_overall_u_w_m2k,
                        mass_flow_kg_s=branch_mass_flow_kg_s,
                        cp_j_kgk=primary_cp_j_kgk,
                    )
                    segment["branch_id"] = branch_id
                    thermal_segments.append(segment)
                    branch_temp_c = float(segment["outlet_temp_c"])
                    total_pipe_heat_loss_kw += -float(segment["heat_transfer_kw"])
                branch_results.append((branch_mass_flow_kg_s, branch_temp_c))
            mixed_mass_flow_kg_s = sum(item[0] for item in branch_results)
            mixed_temp_c = (
                sum(mass_flow_kg_s * temp_c for mass_flow_kg_s, temp_c in branch_results) / mixed_mass_flow_kg_s
                if mixed_mass_flow_kg_s > 0.0
                else current_temp_c
            )
            merge_component_id = str(branch_group.get("merge_component_id", ""))
            thermal_segments.append(
                {
                    "name": f"mix_{merge_component_id}",
                    "kind": "mix",
                    "component_id": merge_component_id,
                    "inlet_temp_c": _round_float(current_temp_c),
                    "outlet_temp_c": _round_float(mixed_temp_c),
                    "heat_transfer_kw": 0.0,
                }
            )
            current_temp_c = mixed_temp_c
            current_component_id = merge_component_id
            continue

        candidate_edges = [
            edge
            for edge in outgoing_edges.get(current_component_id, [])
            if str(edge["id"]) not in visited_edges
        ]
        if len(candidate_edges) != 1:
            break
        edge = candidate_edges[0]
        edge_id = str(edge["id"])
        visited_edges.add(edge_id)
        edge_mass_flow_kg_s = _edge_mass_flow_kg_s(
            edge_id=edge_id,
            default_primary_mass_flow_kg_s=default_primary_mass_flow_kg_s,
            edge_flow_m3_s=edge_flow_m3_s,
            salt_density_kg_m3=salt_density_kg_m3,
        )
        segment = _build_pipe_thermal_segment(
            name=edge_id,
            inlet_temp_c=current_temp_c,
            length_m=float(edge["length_m"]),
            radius_m=float(edge["inner_diameter_m"]) / 2.0,
            ambient_temp_c=ambient_temp_c,
            overall_u_w_m2k=pipe_overall_u_w_m2k,
            mass_flow_kg_s=edge_mass_flow_kg_s,
            cp_j_kgk=primary_cp_j_kgk,
        )
        thermal_segments.append(segment)
        current_temp_c = float(segment["outlet_temp_c"])
        total_pipe_heat_loss_kw += -float(segment["heat_transfer_kw"])
        next_component_id = str(edge.get("to") or "")
        component = component_lookup.get(next_component_id)
        if component:
            component_kind = str(component.get("kind", "junction"))
            if component_kind == "pump":
                thermal_segments.append(
                    {
                        "name": str(component["id"]),
                        "kind": "pump",
                        "inlet_temp_c": _round_float(current_temp_c),
                        "outlet_temp_c": _round_float(current_temp_c),
                        "heat_transfer_kw": 0.0,
                    }
                )
            elif component_kind == "heat_source":
                core_outlet_temp_c = _apply_heat_to_stream(
                    inlet_temp_c=current_temp_c,
                    heat_transfer_kw=core_heat_kw,
                    mass_flow_kg_s=default_primary_mass_flow_kg_s,
                    cp_j_kgk=primary_cp_j_kgk,
                )
                thermal_segments.append(
                    {
                        "name": "core_heating",
                        "kind": "heat_source",
                        "component_id": str(component["id"]),
                        "inlet_temp_c": _round_float(current_temp_c),
                        "outlet_temp_c": _round_float(core_outlet_temp_c),
                        "heat_transfer_kw": _round_float(core_heat_kw),
                    }
                )
                current_temp_c = core_outlet_temp_c
                hot_leg_temp_c = core_outlet_temp_c
            elif component_kind == "heat_sink":
                required_heat_exchanger_heat_kw = -max(
                    (current_temp_c - target_cold_leg_temp_c) * default_primary_mass_flow_kg_s * primary_cp_j_kgk / 1000.0,
                    0.0,
                )
                thermal_segments.append(
                    {
                        "name": "heat_exchanger_rejection",
                        "kind": "heat_sink",
                        "component_id": str(component["id"]),
                        "inlet_temp_c": _round_float(current_temp_c),
                        "outlet_temp_c": _round_float(target_cold_leg_temp_c),
                        "heat_transfer_kw": _round_float(required_heat_exchanger_heat_kw),
                    }
                )
                current_temp_c = target_cold_leg_temp_c
                break
        current_component_id = next_component_id

    return {
        "cold_leg_temp_c": target_cold_leg_temp_c,
        "hot_leg_temp_c": hot_leg_temp_c,
        "loop_closure_error_c": current_temp_c - target_cold_leg_temp_c,
        "total_pipe_heat_loss_kw": total_pipe_heat_loss_kw,
        "required_heat_exchanger_heat_kw": required_heat_exchanger_heat_kw,
        "segments": thermal_segments,
    }


def _edge_mass_flow_kg_s(
    *,
    edge_id: str,
    default_primary_mass_flow_kg_s: float,
    edge_flow_m3_s: dict[str, float],
    salt_density_kg_m3: float,
) -> float:
    if edge_id not in edge_flow_m3_s or salt_density_kg_m3 <= 0.0:
        return default_primary_mass_flow_kg_s
    return float(edge_flow_m3_s[edge_id]) * salt_density_kg_m3


def _build_pipe_thermal_segment(
    *,
    name: str,
    inlet_temp_c: float,
    length_m: float,
    radius_m: float,
    ambient_temp_c: float,
    overall_u_w_m2k: float,
    mass_flow_kg_s: float,
    cp_j_kgk: float,
) -> dict[str, Any]:
    external_area_m2 = 2.0 * math.pi * radius_m * length_m if radius_m > 0.0 else 0.0
    average_temp_c = 0.5 * (inlet_temp_c + ambient_temp_c)
    heat_loss_kw = overall_u_w_m2k * external_area_m2 * max(average_temp_c - ambient_temp_c, 0.0) / 1000.0
    outlet_temp_c = _apply_heat_to_stream(
        inlet_temp_c=inlet_temp_c,
        heat_transfer_kw=-heat_loss_kw,
        mass_flow_kg_s=mass_flow_kg_s,
        cp_j_kgk=cp_j_kgk,
    )
    return {
        "name": name,
        "kind": "pipe",
        "length_m": _round_float(length_m),
        "external_area_m2": _round_float(external_area_m2),
        "inlet_temp_c": _round_float(inlet_temp_c),
        "outlet_temp_c": _round_float(outlet_temp_c),
        "ambient_temp_c": _round_float(ambient_temp_c),
        "heat_transfer_kw": _round_float(-heat_loss_kw),
    }


def _apply_heat_to_stream(
    *,
    inlet_temp_c: float,
    heat_transfer_kw: float,
    mass_flow_kg_s: float,
    cp_j_kgk: float,
) -> float:
    if mass_flow_kg_s <= 0.0 or cp_j_kgk <= 0.0:
        return inlet_temp_c
    delta_t_k = heat_transfer_kw * 1000.0 / (mass_flow_kg_s * cp_j_kgk)
    return inlet_temp_c + delta_t_k


def _build_fuel_cycle_summary(config: Any, bop: dict[str, Any], inventory_summary: dict[str, Any]) -> dict[str, Any]:
    heavy_metal_mass_fraction = float(config.reactor.get("fuel_heavy_metal_mass_fraction", 0.012))
    fissile_mass_fraction = float(config.reactor.get("fissile_mass_fraction_of_heavy_metal", 0.065))
    cleanup_turnover_days = float(config.reactor.get("cleanup_turnover_days", 14.0))
    cleanup_removal_efficiency = float(config.reactor.get("cleanup_removal_efficiency", 0.75))
    xenon_yield_fraction = float(config.reactor.get("xenon_yield_fraction", 0.0028))
    xenon_removal_fraction = float(config.reactor.get("xenon_removal_fraction", 0.9))
    protactinium_holdup_days = float(config.reactor.get("protactinium_holdup_days", 2.0))

    total_fuel_salt_mass_kg = float(inventory_summary["fuel_salt"]["total_mass_kg"])
    heavy_metal_mass_kg = total_fuel_salt_mass_kg * heavy_metal_mass_fraction
    fissile_mass_kg = heavy_metal_mass_kg * fissile_mass_fraction
    thermal_power_mw = float(bop.get("thermal_power_mw", 0.0))
    fission_rate_per_s = thermal_power_mw * 1.0e6 / FISSION_ENERGY_J if thermal_power_mw > 0.0 else 0.0
    xenon_generation_rate_atoms_s = fission_rate_per_s * xenon_yield_fraction
    specific_power_mw_per_t_hm = thermal_power_mw / (heavy_metal_mass_kg / 1000.0) if heavy_metal_mass_kg > 0.0 else 0.0
    cleanup_turnover_hours = cleanup_turnover_days * 24.0
    depletion_assumptions = build_depletion_assumptions(config)
    fissile_burn_fraction_per_day_full_power = float(
        depletion_assumptions["fissile_burn_fraction_per_day_full_power"]
    )
    breeding_gain_fraction_per_day = float(depletion_assumptions["breeding_gain_fraction_per_day"])
    minor_actinide_sink_fraction_per_day = float(
        depletion_assumptions["minor_actinide_sink_fraction_per_day"]
    )
    equilibrium_protactinium_inventory_fraction = breeding_gain_fraction_per_day * float(
        depletion_assumptions["protactinium_holdup_days"]
    )
    net_fissile_change_fraction_per_day = (
        breeding_gain_fraction_per_day
        - fissile_burn_fraction_per_day_full_power
        - minor_actinide_sink_fraction_per_day
    )

    return {
        "model": "first_order_cleanup_and_poison_proxy",
        "depletion_model": "thorium_breeding_proxy",
        "depletion_chain": depletion_assumptions["chain"],
        "cleanup_scenario": depletion_assumptions["cleanup_scenario"],
        "fuel_heavy_metal_mass_fraction": _round_float(heavy_metal_mass_fraction),
        "fissile_mass_fraction_of_heavy_metal": _round_float(fissile_mass_fraction),
        "heavy_metal_inventory_kg": _round_float(heavy_metal_mass_kg),
        "fissile_inventory_kg": _round_float(fissile_mass_kg),
        "specific_power_mw_per_t_hm": _round_float(specific_power_mw_per_t_hm),
        "cleanup_turnover_days": _round_float(cleanup_turnover_days),
        "cleanup_turnover_hours": _round_float(cleanup_turnover_hours),
        "cleanup_removal_efficiency": _round_float(cleanup_removal_efficiency),
        "protactinium_holdup_days": _round_float(protactinium_holdup_days),
        "xenon_yield_fraction": _round_float(xenon_yield_fraction),
        "xenon_generation_rate_atoms_s": _round_float(xenon_generation_rate_atoms_s),
        "xenon_removal_fraction": _round_float(xenon_removal_fraction),
        "fissile_burn_fraction_per_day_full_power": _round_float(fissile_burn_fraction_per_day_full_power),
        "breeding_gain_fraction_per_day": _round_float(breeding_gain_fraction_per_day),
        "minor_actinide_sink_fraction_per_day": _round_float(minor_actinide_sink_fraction_per_day),
        "net_fissile_change_fraction_per_day": _round_float(net_fissile_change_fraction_per_day),
        "equilibrium_protactinium_inventory_fraction": _round_float(equilibrium_protactinium_inventory_fraction),
        "depletion_assumptions": depletion_assumptions,
    }


def _build_primary_system_checks(
    reduced_order_flow: dict[str, Any],
    max_reynolds_number: float,
    pump_head_m: float,
    heat_exchanger_summary: dict[str, Any],
    thermal_profile: dict[str, Any],
    inventory_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    active_flow_velocity_m_s = float(reduced_order_flow.get("active_flow", {}).get("representative_velocity_m_s", 0.0))
    heat_exchanger_duty_error_mw = abs(float(thermal_profile.get("heat_exchanger_duty_error_mw", 0.0)))
    heat_exchanger_area_m2 = float(heat_exchanger_summary.get("required_area_m2", 0.0))
    heat_exchanger_duty_mw = float(heat_exchanger_summary.get("duty_mw", 0.0))
    heat_exchanger_area_upper_bound_m2 = max(250.0, heat_exchanger_duty_mw * 15.0)
    checks = [
        (
            "primary_system::loop_reynolds_reasonable",
            max_reynolds_number >= 4000.0,
            f"Primary loop Reynolds number peaks at {max_reynolds_number:.0f}, consistent with forced convection.",
            "Primary loop Reynolds number should exceed 4000 for this forced-circulation approximation.",
        ),
        (
            "primary_system::pump_head_reasonable",
            -60.0 <= pump_head_m <= 60.0,
            f"Primary pump head is {pump_head_m:.2f} m.",
            "Primary pump head magnitude should stay below 60 m for this concept-scale loop.",
        ),
        (
            "primary_system::heat_exchanger_pinch_positive",
            float(heat_exchanger_summary.get("terminal_hot_delta_c", 0.0)) > 0.0
            and float(heat_exchanger_summary.get("terminal_cold_delta_c", 0.0)) > 0.0,
            "Heat exchanger terminal temperature differences remain positive.",
            "Heat exchanger terminal temperature differences must stay positive.",
        ),
        (
            "primary_system::heat_exchanger_area_reasonable",
            1.0 <= heat_exchanger_area_m2 <= heat_exchanger_area_upper_bound_m2,
            f"Required heat exchanger area is {heat_exchanger_area_m2:.2f} m2.",
            (
                "Required heat exchanger area should stay within a reduced-order screening envelope "
                f"up to {heat_exchanger_area_upper_bound_m2:.0f} m2 for this duty."
            ),
        ),
        (
            "primary_system::fuel_inventory_positive",
            float(inventory_summary["fuel_salt"].get("total_m3", 0.0)) > 0.0,
            "Fuel-salt inventory accounting is positive.",
            "Fuel-salt inventory must remain positive.",
        ),
        (
            "primary_system::active_channel_velocity_reasonable",
            1.0 <= active_flow_velocity_m_s <= 12.0,
            f"Representative active-channel velocity is {active_flow_velocity_m_s:.2f} m/s.",
            "Representative active-channel velocity should stay between 1 and 12 m/s for this reduced-order screening model.",
        ),
        (
            "primary_system::heat_exchanger_duty_closure_reasonable",
            heat_exchanger_duty_error_mw <= 0.25,
            f"Heat-exchanger available-duty mismatch is {heat_exchanger_duty_error_mw:.3f} MW.",
            "Heat-exchanger required duty should stay within 0.25 MW of the available BOP duty for a self-consistent steady-state screening case.",
        ),
    ]
    return [
        {
            "name": name,
            "status": "pass" if passed else "fail",
            "message": success if passed else failure,
        }
        for name, passed, success, failure in checks
    ]

def _shell_volume_m3(shell: dict[str, Any] | None) -> float:
    if not shell:
        return 0.0
    inner_radius_cm = float(shell.get("inner_radius", 0.0))
    outer_radius_cm = float(shell.get("outer_radius", 0.0))
    z_min_cm = float(shell.get("z_min", 0.0))
    z_max_cm = float(shell.get("z_max", 0.0))
    area_cm2 = math.pi * max(outer_radius_cm * outer_radius_cm - inner_radius_cm * inner_radius_cm, 0.0)
    return area_cm2 * max(z_max_cm - z_min_cm, 0.0) * 1.0e-6


def _dished_pool_fill_volume_m3(
    inner_radius_cm: float,
    cylinder_bottom_z_cm: float,
    fill_top_z_cm: float,
    head_depth_cm: float,
) -> float:
    if inner_radius_cm <= 0.0 or fill_top_z_cm <= cylinder_bottom_z_cm - head_depth_cm:
        return 0.0
    cylinder_fill_height_cm = max(min(fill_top_z_cm - cylinder_bottom_z_cm, fill_top_z_cm), 0.0)
    cylinder_volume_cm3 = math.pi * inner_radius_cm * inner_radius_cm * cylinder_fill_height_cm
    head_volume_cm3 = _dished_head_volume_cm3(inner_radius_cm, head_depth_cm) if fill_top_z_cm > cylinder_bottom_z_cm else 0.0
    return (cylinder_volume_cm3 + head_volume_cm3) * 1.0e-6


def _dished_head_volume_cm3(inner_radius_cm: float, head_depth_cm: float, slices: int = 96) -> float:
    if inner_radius_cm <= 0.0 or head_depth_cm <= 0.0:
        return 0.0
    dz_cm = head_depth_cm / slices
    volume_cm3 = 0.0
    for index in range(slices):
        fraction = (index + 0.5) / slices
        local_radius_cm = inner_radius_cm * math.sqrt(max(0.0, 1.0 - (1.0 - fraction) ** 2))
        volume_cm3 += math.pi * local_radius_cm * local_radius_cm * dz_cm
    return volume_cm3


def _core_box_displacement_m3(core_box: dict[str, Any]) -> float:
    outer_width_cm = float(core_box.get("outer_width_cm", 0.0))
    outer_depth_cm = float(core_box.get("outer_depth_cm", 0.0))
    wall_thickness_cm = float(core_box.get("wall_thickness_cm", 0.0))
    floor_z_cm = float(core_box.get("floor_z_cm", 0.0))
    cavity_top_z_cm = float(core_box.get("cavity_top_z_cm", floor_z_cm))
    base_height_cm = float(core_box.get("base_height_cm", 0.0))

    outer_height_cm = max(cavity_top_z_cm - floor_z_cm, 0.0)
    cavity_width_cm = max(outer_width_cm - 2.0 * wall_thickness_cm, 0.0)
    cavity_depth_cm = max(outer_depth_cm - 2.0 * wall_thickness_cm, 0.0)
    cavity_volume_cm3 = cavity_width_cm * cavity_depth_cm * outer_height_cm
    shell_volume_cm3 = max(outer_width_cm * outer_depth_cm * outer_height_cm - cavity_volume_cm3, 0.0)
    base_volume_cm3 = (outer_width_cm + 4.8) * (outer_depth_cm + 4.0) * max(base_height_cm, 0.0)
    return (shell_volume_cm3 + base_volume_cm3) * 1.0e-6


def _barrel_displacement_m3(core_box: dict[str, Any], geometry_description: dict[str, Any]) -> float:
    barrel_radius_cm = float(core_box.get("barrel_radius_cm", 0.0))
    active_height_cm = float(geometry_description.get("flow_summary", {}).get("active_height_cm", 0.0))
    return math.pi * barrel_radius_cm * barrel_radius_cm * active_height_cm * 1.0e-6


def _primary_loop_hardware_displacement_m3(primary_loop: dict[str, Any]) -> float:
    exchanger = primary_loop.get("heat_exchanger") or {}
    pump = primary_loop.get("pump") or {}
    pipe_runs = primary_loop.get("pipes") or []

    exchanger_length_cm = max(
        float(exchanger.get("x_max_cm", 0.0)) - float(exchanger.get("x_min_cm", 0.0)),
        0.0,
    )
    exchanger_radius_cm = float(exchanger.get("radius_cm", 0.0))
    exchanger_volume_cm3 = math.pi * exchanger_radius_cm * exchanger_radius_cm * exchanger_length_cm

    pump_radius_cm = float(pump.get("radius_cm", 0.0))
    pump_height_cm = max(float(pump.get("z_max_cm", 0.0)) - float(pump.get("z_min_cm", 0.0)), 0.0)
    pump_body_volume_cm3 = math.pi * pump_radius_cm * pump_radius_cm * pump_height_cm
    header_radius_cm = float(pump.get("header_radius_cm", 0.0))
    header_height_cm = max(header_radius_cm * 1.5, 0.0)
    pump_header_volume_cm3 = math.pi * header_radius_cm * header_radius_cm * header_height_cm

    pipe_volume_cm3 = 0.0
    for pipe_run in pipe_runs:
        radius_cm = float(pipe_run.get("radius_cm", 0.0))
        points = [tuple(float(value) for value in point) for point in pipe_run.get("points", [])]
        for start, stop in zip(points, points[1:]):
            length_cm = math.dist(start, stop)
            pipe_volume_cm3 += math.pi * radius_cm * radius_cm * length_cm

    return (exchanger_volume_cm3 + pump_body_volume_cm3 + pump_header_volume_cm3 + pipe_volume_cm3) * 1.0e-6


def _log_mean_temperature_difference(delta_hot_c: float, delta_cold_c: float) -> float:
    if delta_hot_c <= 0.0 or delta_cold_c <= 0.0:
        return 0.0
    if math.isclose(delta_hot_c, delta_cold_c, rel_tol=1.0e-9, abs_tol=1.0e-9):
        return delta_hot_c
    return (delta_hot_c - delta_cold_c) / math.log(delta_hot_c / delta_cold_c)


def _darcy_friction_factor(reynolds_number: float) -> float:
    if reynolds_number <= 0.0:
        return 0.0
    if reynolds_number < 2300.0:
        return 64.0 / reynolds_number
    return 0.3164 / (reynolds_number ** 0.25)


def _round_float(value: float) -> float:
    return round(float(value), 6)
