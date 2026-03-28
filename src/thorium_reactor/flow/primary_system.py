from __future__ import annotations

import math
from typing import Any


GRAVITY_M_S2 = 9.80665
FISSION_ENERGY_J = 3.204e-11


def build_primary_system_summary(
    config: Any,
    geometry_description: dict[str, Any],
    reduced_order_flow: dict[str, Any],
    bop: dict[str, Any],
) -> dict[str, Any]:
    render_layout = config.geometry.get("render_layout") or {}
    if render_layout.get("type") != "immersed_pool_reference":
        return {}

    primary_loop = render_layout.get("primary_loop") or {}
    pipe_runs = primary_loop.get("pipes") or []
    if not pipe_runs:
        return {}

    salt_material = config.geometry.get("salt_material", "fuel_salt")
    salt_density_kg_m3 = _density_to_kg_m3(config.materials[salt_material]["density"])
    dynamic_viscosity_pa_s = float(config.reactor.get("primary_dynamic_viscosity_pa_s", 0.012))
    pump_efficiency = float(config.reactor.get("primary_pump_efficiency", 0.72))
    elbow_loss_coefficient = float(config.reactor.get("primary_elbow_loss_coefficient", 0.9))
    terminal_loss_coefficient = float(config.reactor.get("primary_terminal_loss_coefficient", 1.6))

    primary_mass_flow_kg_s = float(bop["primary_mass_flow_kg_s"])
    volumetric_flow_m3_s = primary_mass_flow_kg_s / salt_density_kg_m3 if salt_density_kg_m3 > 0.0 else 0.0

    segment_summary = _build_pipe_segment_summary(
        pipe_runs,
        volumetric_flow_m3_s,
        salt_density_kg_m3,
        dynamic_viscosity_pa_s,
        elbow_loss_coefficient,
        terminal_loss_coefficient,
    )
    heat_exchanger_summary = _build_heat_exchanger_summary(config, bop)
    inventory_summary = _build_inventory_summary(config, geometry_description, reduced_order_flow, primary_loop)
    fuel_cycle_summary = _build_fuel_cycle_summary(config, bop, inventory_summary)

    total_pressure_drop_pa = segment_summary["frictional_pressure_drop_pa"] + segment_summary["local_pressure_drop_pa"]
    pump_head_m = total_pressure_drop_pa / (salt_density_kg_m3 * GRAVITY_M_S2) if salt_density_kg_m3 > 0.0 else 0.0
    hydraulic_power_kw = total_pressure_drop_pa * volumetric_flow_m3_s / 1000.0
    shaft_power_kw = hydraulic_power_kw / pump_efficiency if pump_efficiency > 0.0 else 0.0

    return {
        "model": "reduced_order_primary_system",
        "salt_density_kg_m3": _round_float(salt_density_kg_m3),
        "dynamic_viscosity_pa_s": _round_float(dynamic_viscosity_pa_s),
        "primary_mass_flow_kg_s": _round_float(primary_mass_flow_kg_s),
        "primary_volumetric_flow_m3_s": _round_float(volumetric_flow_m3_s),
        "loop_segments": segment_summary["segments"],
        "loop_hydraulics": {
            "total_pipe_length_m": _round_float(segment_summary["total_pipe_length_m"]),
            "limiting_inner_diameter_m": _round_float(segment_summary["limiting_inner_diameter_m"]),
            "limiting_velocity_m_s": _round_float(segment_summary["limiting_velocity_m_s"]),
            "max_reynolds_number": _round_float(segment_summary["max_reynolds_number"]),
            "frictional_pressure_drop_kpa": _round_float(segment_summary["frictional_pressure_drop_pa"] / 1000.0),
            "local_pressure_drop_kpa": _round_float(segment_summary["local_pressure_drop_pa"] / 1000.0),
            "total_pressure_drop_kpa": _round_float(total_pressure_drop_pa / 1000.0),
            "pump_head_m": _round_float(pump_head_m),
            "pump_hydraulic_power_kw": _round_float(hydraulic_power_kw),
            "pump_shaft_power_kw": _round_float(shaft_power_kw),
            "pump_efficiency": _round_float(pump_efficiency),
        },
        "heat_exchanger": heat_exchanger_summary,
        "inventory": inventory_summary,
        "fuel_cycle": fuel_cycle_summary,
        "checks": _build_primary_system_checks(
            segment_summary["max_reynolds_number"],
            pump_head_m,
            heat_exchanger_summary,
            inventory_summary,
        ),
    }


def _build_pipe_segment_summary(
    pipe_runs: list[dict[str, Any]],
    volumetric_flow_m3_s: float,
    salt_density_kg_m3: float,
    dynamic_viscosity_pa_s: float,
    elbow_loss_coefficient: float,
    terminal_loss_coefficient: float,
) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    total_pipe_length_m = 0.0
    frictional_pressure_drop_pa = 0.0
    local_pressure_drop_pa = 0.0
    max_reynolds_number = 0.0
    limiting_inner_diameter_m = float("inf")
    limiting_velocity_m_s = 0.0

    for run_index, pipe_run in enumerate(pipe_runs):
        radius_m = float(pipe_run.get("radius_cm", 0.0)) / 100.0
        if radius_m <= 0.0:
            continue
        inner_diameter_m = 2.0 * radius_m
        flow_area_m2 = math.pi * radius_m * radius_m
        velocity_m_s = volumetric_flow_m3_s / flow_area_m2 if flow_area_m2 > 0.0 else 0.0
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
        for start, stop in zip(points, points[1:]):
            length_m = math.dist(start, stop) / 100.0
            if length_m <= 0.0:
                continue
            run_length_m += length_m
            segment_count += 1

        friction_drop_pa = friction_factor * (run_length_m / inner_diameter_m) * (salt_density_kg_m3 * velocity_m_s * velocity_m_s / 2.0)
        bend_count = max(0, len(points) - 2)
        local_k = terminal_loss_coefficient + bend_count * elbow_loss_coefficient
        local_drop_pa = local_k * (salt_density_kg_m3 * velocity_m_s * velocity_m_s / 2.0)

        total_pipe_length_m += run_length_m
        frictional_pressure_drop_pa += friction_drop_pa
        local_pressure_drop_pa += local_drop_pa
        segments.append(
            {
                "name": f"run_{run_index}",
                "length_m": _round_float(run_length_m),
                "inner_diameter_m": _round_float(inner_diameter_m),
                "velocity_m_s": _round_float(velocity_m_s),
                "reynolds_number": _round_float(reynolds_number),
                "darcy_friction_factor": _round_float(friction_factor),
                "segment_count": segment_count,
                "bend_count": bend_count,
                "frictional_pressure_drop_kpa": _round_float(friction_drop_pa / 1000.0),
                "local_pressure_drop_kpa": _round_float(local_drop_pa / 1000.0),
            }
        )

    if math.isinf(limiting_inner_diameter_m):
        limiting_inner_diameter_m = 0.0

    return {
        "segments": segments,
        "total_pipe_length_m": total_pipe_length_m,
        "limiting_inner_diameter_m": limiting_inner_diameter_m,
        "limiting_velocity_m_s": limiting_velocity_m_s,
        "max_reynolds_number": max_reynolds_number,
        "frictional_pressure_drop_pa": frictional_pressure_drop_pa,
        "local_pressure_drop_pa": local_pressure_drop_pa,
    }


def _build_heat_exchanger_summary(config: Any, bop: dict[str, Any]) -> dict[str, Any]:
    primary_hot_leg_c = float(config.reactor.get("hot_leg_temp_c", 700.0))
    primary_cold_leg_c = float(config.reactor.get("cold_leg_temp_c", 560.0))
    secondary_inlet_c = float(config.reactor.get("secondary_inlet_temp_c", primary_cold_leg_c - 120.0))
    secondary_outlet_c = float(config.reactor.get("secondary_outlet_temp_c", primary_hot_leg_c - 85.0))
    overall_u_w_m2k = float(config.reactor.get("steam_generator_overall_u_w_m2k", 850.0))
    duty_mw = float(bop.get("steam_generator_duty_mw", 0.0))
    duty_w = duty_mw * 1.0e6

    terminal_hot_delta = primary_hot_leg_c - secondary_outlet_c
    terminal_cold_delta = primary_cold_leg_c - secondary_inlet_c
    lmtd_k = _log_mean_temperature_difference(terminal_hot_delta, terminal_cold_delta)
    required_area_m2 = duty_w / (overall_u_w_m2k * lmtd_k) if overall_u_w_m2k > 0.0 and lmtd_k > 0.0 else 0.0

    return {
        "duty_mw": _round_float(duty_mw),
        "overall_u_w_m2k": _round_float(overall_u_w_m2k),
        "secondary_inlet_temp_c": _round_float(secondary_inlet_c),
        "secondary_outlet_temp_c": _round_float(secondary_outlet_c),
        "terminal_hot_delta_c": _round_float(terminal_hot_delta),
        "terminal_cold_delta_c": _round_float(terminal_cold_delta),
        "lmtd_c": _round_float(lmtd_k),
        "required_area_m2": _round_float(required_area_m2),
    }


def _build_inventory_summary(
    config: Any,
    geometry_description: dict[str, Any],
    reduced_order_flow: dict[str, Any],
    primary_loop: dict[str, Any],
) -> dict[str, Any]:
    salt_material = config.geometry.get("salt_material", "fuel_salt")
    salt_density_kg_m3 = _density_to_kg_m3(config.materials[salt_material]["density"])
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

    coolant_material = pool.get("material", "coolant_salt")
    coolant_density_kg_m3 = _density_to_kg_m3(config.materials[coolant_material]["density"])

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

    return {
        "model": "first_order_cleanup_and_poison_proxy",
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
    }


def _build_primary_system_checks(
    max_reynolds_number: float,
    pump_head_m: float,
    heat_exchanger_summary: dict[str, Any],
    inventory_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = [
        (
            "primary_system::loop_reynolds_reasonable",
            max_reynolds_number >= 4000.0,
            f"Primary loop Reynolds number peaks at {max_reynolds_number:.0f}, consistent with forced convection.",
            "Primary loop Reynolds number should exceed 4000 for this forced-circulation approximation.",
        ),
        (
            "primary_system::pump_head_reasonable",
            0.5 <= pump_head_m <= 60.0,
            f"Primary pump head is {pump_head_m:.2f} m.",
            "Primary pump head should stay between 0.5 and 60 m for this concept-scale loop.",
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
            1.0 <= float(heat_exchanger_summary.get("required_area_m2", 0.0)) <= 250.0,
            f"Required heat exchanger area is {float(heat_exchanger_summary.get('required_area_m2', 0.0)):.2f} m2.",
            "Required heat exchanger area should stay between 1 and 250 m2 for this demonstrator-scale concept.",
        ),
        (
            "primary_system::fuel_inventory_positive",
            float(inventory_summary["fuel_salt"].get("total_m3", 0.0)) > 0.0,
            "Fuel-salt inventory accounting is positive.",
            "Fuel-salt inventory must remain positive.",
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


def _density_to_kg_m3(density: dict[str, Any]) -> float:
    units = density["units"]
    value = float(density["value"])
    if units == "g/cm3":
        return value * 1000.0
    if units == "kg/m3":
        return value
    raise ValueError(f"Unsupported density units for primary system analysis: {units}")


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
