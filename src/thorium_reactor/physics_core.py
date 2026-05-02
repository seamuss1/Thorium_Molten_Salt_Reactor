from __future__ import annotations

import math
from typing import Any

import numpy as np

from thorium_reactor.precursors import normalize_loop_segments, normalize_precursor_groups


PHYSICS_CORE_MODEL = "coupled_deterministic_physics_core_v1"
FINITE_VOLUME_TH_MODEL = "one_dimensional_finite_volume_loop"
FINITE_VOLUME_PRECURSOR_MODEL = "finite_volume_advection_diffusion_decay"
DEFAULT_DETERMINISTIC_METHODS = ("diffusion", "sp3", "transport")
SUPPORTED_DETERMINISTIC_METHODS = set(DEFAULT_DETERMINISTIC_METHODS)


def build_physics_core_summary(config: Any, summary: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(config)
    thermal_hydraulics = build_finite_volume_thermal_hydraulics(config, summary, settings)
    precursor_transport = build_finite_volume_precursor_transport(config, summary, settings, thermal_hydraulics)
    neutronics = build_deterministic_neutronics_summary(
        config,
        summary,
        settings,
        thermal_hydraulics=thermal_hydraulics,
        precursor_transport=precursor_transport,
    )
    return {
        "status": "completed",
        "model": PHYSICS_CORE_MODEL,
        "scientific_scope": (
            "Deterministic reduced-order finite-volume physics core for screening, "
            "coupling studies, and regression checks; OpenMC remains the reference "
            "path for Monte Carlo neutronics where available."
        ),
        "coupling": {
            "neutronics_to_thermal_hydraulics": "axial power shape",
            "thermal_hydraulics_to_neutronics": "temperature-dependent multigroup cross sections",
            "flow_to_precursors": "finite-volume advection residence times",
            "precursors_to_neutronics": "core delayed-neutron source importance",
        },
        "neutronics": neutronics,
        "thermal_hydraulics": thermal_hydraulics,
        "precursor_transport": precursor_transport,
        "integrity_checks": _physics_core_checks(neutronics, thermal_hydraulics, precursor_transport),
    }


def build_deterministic_neutronics_summary(
    config: Any,
    summary: dict[str, Any],
    settings: dict[str, Any],
    *,
    thermal_hydraulics: dict[str, Any],
    precursor_transport: dict[str, Any],
) -> dict[str, Any]:
    neutronics_settings = _section(settings, "neutronics")
    methods = tuple(
        method
        for method in neutronics_settings.get("deterministic_methods", DEFAULT_DETERMINISTIC_METHODS)
        if method in SUPPORTED_DETERMINISTIC_METHODS
    ) or DEFAULT_DETERMINISTIC_METHODS
    group_count = max(int(neutronics_settings.get("group_count", 11)), 2)
    temperature_grid_c = _temperature_grid(neutronics_settings)
    axial_nodes = thermal_hydraulics["axial_nodes"]
    fuel_temperatures = [float(node["fuel_salt_temp_c"]) for node in axial_nodes]
    graphite_temperatures = [float(node["graphite_temp_c"]) for node in axial_nodes]
    average_fuel_temp_c = sum(fuel_temperatures) / max(len(fuel_temperatures), 1)
    average_graphite_temp_c = sum(graphite_temperatures) / max(len(graphite_temperatures), 1)

    base_xs = build_temperature_dependent_multigroup_xs(
        config,
        group_count=group_count,
        fuel_temperature_c=average_fuel_temp_c,
        graphite_temperature_c=average_graphite_temp_c,
        temperature_grid_c=temperature_grid_c,
        settings=neutronics_settings,
    )
    raw_results = {
        method: _solve_multigroup_eigenvalue(base_xs, axial_nodes=axial_nodes, method=method)
        for method in methods
    }
    reference_keff = _reference_keff(config, summary)
    calibration_factor = reference_keff / max(raw_results[methods[0]]["k_eff"], 1.0e-12)

    method_results: dict[str, Any] = {}
    for method, result in raw_results.items():
        method_results[method] = _scaled_neutronics_result(result, calibration_factor)

    feedback = _feedback_coefficients(
        config,
        base_result=raw_results[methods[0]],
        calibration_factor=calibration_factor,
        axial_nodes=axial_nodes,
        group_count=group_count,
        average_fuel_temp_c=average_fuel_temp_c,
        average_graphite_temp_c=average_graphite_temp_c,
        temperature_grid_c=temperature_grid_c,
        settings=neutronics_settings,
        method=methods[0],
    )
    total_beta = _total_delayed_neutron_yield(config)
    reference = {
        "source": "openmc" if summary.get("metrics", {}).get("keff") is not None else "case_validation_or_surrogate",
        "k_eff": _round_float(reference_keff),
        "openmc_status": summary.get("neutronics", {}).get("status"),
        "statepoint": summary.get("neutronics", {}).get("statepoint"),
    }
    selected = method_results[methods[0]]
    return {
        "status": "completed",
        "model": "temperature_dependent_multigroup_deterministic",
        "methods": list(methods),
        "selected_method": methods[0],
        "group_count": group_count,
        "temperature_grid_c": [_round_float(value) for value in temperature_grid_c],
        "temperature_state": {
            "average_fuel_salt_temp_c": _round_float(average_fuel_temp_c),
            "average_graphite_temp_c": _round_float(average_graphite_temp_c),
            "min_fuel_salt_temp_c": _round_float(min(fuel_temperatures)),
            "max_fuel_salt_temp_c": _round_float(max(fuel_temperatures)),
        },
        "cross_sections": _xs_report(base_xs),
        "k_eff": selected["k_eff"],
        "beta_eff": _round_float(total_beta * precursor_transport["core_delayed_neutron_source_absolute_fraction"]),
        "delayed_neutron_total_yield_fraction": _round_float(total_beta),
        "adjoint_weighted_importance": selected["adjoint_weighted_importance"],
        "power_shape": selected["power_shape"],
        "feedback_coefficients": feedback,
        "method_results": method_results,
        "monte_carlo_reference": reference,
        "precursor_coupling": {
            "core_delayed_neutron_source_absolute_fraction": precursor_transport[
                "core_delayed_neutron_source_absolute_fraction"
            ],
            "transport_loss_fraction": precursor_transport["transport_loss_fraction"],
        },
    }


def build_finite_volume_thermal_hydraulics(
    config: Any,
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    th_settings = _section(settings, "thermal_hydraulics")
    node_count = max(int(th_settings.get("axial_nodes", th_settings.get("core_nodes", 16))), 4)
    reactor = config.reactor
    geometry = config.geometry
    bop = summary.get("bop", {})
    reduced = summary.get("flow", {}).get("reduced_order", {})
    active_flow = reduced.get("active_flow", {})
    primary_system = summary.get("primary_system", {})
    profile = primary_system.get("thermal_profile", {})
    hydraulics = primary_system.get("loop_hydraulics", {})

    thermal_power_mw = float(bop.get("thermal_power_mw", reactor.get("design_power_mwth", 0.0)))
    mass_flow_kg_s = float(bop.get("primary_mass_flow_kg_s", reduced.get("primary_mass_flow_kg_s", 0.0)))
    cp_j_kgk = float(bop.get("primary_cp_kj_kgk", reactor.get("primary_cp_kj_kgk", 1.5))) * 1000.0
    hot_leg_temp_c = float(profile.get("estimated_hot_leg_temp_c", reactor.get("hot_leg_temp_c", 700.0)))
    cold_leg_temp_c = float(profile.get("estimated_cold_leg_temp_c", reactor.get("cold_leg_temp_c", 560.0)))
    density_kg_m3 = float(reduced.get("salt_density_kg_m3", 3100.0))
    viscosity_pa_s = float(reduced.get("salt_properties", {}).get("dynamic_viscosity_pa_s", 0.006))
    conductivity_w_mk = float(reduced.get("salt_properties", {}).get("thermal_conductivity_w_mk", 1.0))
    flow_area_m2 = max(float(active_flow.get("total_flow_area_cm2", 1.0)) * 1.0e-4, 1.0e-8)
    hydraulic_diameter_m = max(float(active_flow.get("hydraulic_diameter_cm", geometry.get("channel_layers", [{}])[-1].get("outer_radius", 3.0))) * 0.01, 1.0e-4)
    active_volume_m3 = max(float(active_flow.get("total_salt_volume_cm3", 0.0)) * 1.0e-6, flow_area_m2)
    core_length_m = max(active_volume_m3 / flow_area_m2, float(geometry.get("height_cm", 100.0)) * 0.01, 0.1)
    volumetric_flow_m3_s = mass_flow_kg_s / density_kg_m3 if density_kg_m3 > 0.0 else 0.0
    velocity_m_s = volumetric_flow_m3_s / flow_area_m2 if flow_area_m2 > 0.0 else 0.0
    dz_m = core_length_m / node_count
    power_shape = _cosine_power_shape(node_count)
    total_power_w = thermal_power_mw * 1.0e6

    salt_temp_c = cold_leg_temp_c
    axial_nodes: list[dict[str, Any]] = []
    for index, shape in enumerate(power_shape):
        node_power_w = total_power_w * shape / node_count
        delta_t_c = node_power_w / max(mass_flow_kg_s * cp_j_kgk, 1.0)
        inlet_temp_c = salt_temp_c
        outlet_temp_c = inlet_temp_c + delta_t_c
        salt_temp_c = outlet_temp_c
        heat_flux_proxy = node_power_w / max(flow_area_m2 * dz_m, 1.0e-9)
        graphite_temp_c = 0.5 * (inlet_temp_c + outlet_temp_c) + heat_flux_proxy * 1.0e-5
        axial_nodes.append(
            {
                "index": index,
                "z_mid_m": _round_float((index + 0.5) * dz_m),
                "cell_length_m": _round_float(dz_m),
                "power_shape": _round_float(shape),
                "power_mw": _round_float(node_power_w / 1.0e6),
                "fuel_salt_inlet_temp_c": _round_float(inlet_temp_c),
                "fuel_salt_temp_c": _round_float(0.5 * (inlet_temp_c + outlet_temp_c)),
                "fuel_salt_outlet_temp_c": _round_float(outlet_temp_c),
                "graphite_temp_c": _round_float(graphite_temp_c),
                "porosity": _round_float(_core_porosity(config, active_volume_m3, core_length_m)),
                "velocity_m_s": _round_float(velocity_m_s),
            }
        )

    reynolds = density_kg_m3 * abs(velocity_m_s) * hydraulic_diameter_m / max(viscosity_pa_s, 1.0e-12)
    friction_factor = _darcy_friction_factor(reynolds)
    dynamic_pressure_pa = density_kg_m3 * velocity_m_s * velocity_m_s / 2.0
    friction_pa = friction_factor * (core_length_m / hydraulic_diameter_m) * dynamic_pressure_pa
    form_loss_k = float(th_settings.get("core_form_loss_k", 1.8))
    form_pa = form_loss_k * dynamic_pressure_pa
    existing_buoyancy_kpa = float(hydraulics.get("buoyancy_driving_pressure_kpa", 0.0))
    buoyancy_pa = existing_buoyancy_kpa * 1000.0
    if buoyancy_pa <= 0.0:
        beta_thermal_per_k = float(th_settings.get("thermal_expansion_per_k", 3.5e-4))
        buoyancy_pa = density_kg_m3 * 9.80665 * core_length_m * beta_thermal_per_k * max(hot_leg_temp_c - cold_leg_temp_c, 0.0)
    loop_resistance_pa = max(
        float(hydraulics.get("frictional_pressure_drop_kpa", 0.0)) * 1000.0,
        friction_pa,
    )
    loop_form_pa = max(float(hydraulics.get("local_pressure_drop_kpa", 0.0)) * 1000.0, form_pa)
    resistive_pa = loop_resistance_pa + loop_form_pa
    pump_curve = _pump_curve(
        config,
        nominal_flow_m3_s=volumetric_flow_m3_s,
        nominal_head_m=max(float(hydraulics.get("pump_head_m", 0.0)), 0.0),
        density_kg_m3=density_kg_m3,
    )
    pump_pressure_pa = _evaluate_pump_curve(pump_curve, volumetric_flow_m3_s, density_kg_m3)
    flow_reversal_margin_pa = pump_pressure_pa + buoyancy_pa - resistive_pa
    natural_flow_m3_s = _natural_circulation_flow(
        nominal_flow_m3_s=volumetric_flow_m3_s,
        nominal_resistance_pa=resistive_pa,
        buoyancy_pa=buoyancy_pa,
    )
    hx_summary = _heat_exchanger_fv_summary(
        config,
        summary,
        mass_flow_kg_s=mass_flow_kg_s,
        cp_j_kgk=cp_j_kgk,
        hot_leg_temp_c=hot_leg_temp_c,
        cold_leg_temp_c=cold_leg_temp_c,
    )
    return {
        "status": "completed",
        "model": FINITE_VOLUME_TH_MODEL,
        "axial_node_count": node_count,
        "porous_core_model": {
            "model": "homogenized_porous_graphite_fuel_salt_region",
            "core_length_m": _round_float(core_length_m),
            "flow_area_m2": _round_float(flow_area_m2),
            "active_salt_volume_m3": _round_float(active_volume_m3),
            "hydraulic_diameter_m": _round_float(hydraulic_diameter_m),
            "bulk_porosity": _round_float(_core_porosity(config, active_volume_m3, core_length_m)),
        },
        "boundary_conditions": {
            "inlet_temp_c": _round_float(cold_leg_temp_c),
            "target_hot_leg_temp_c": _round_float(hot_leg_temp_c),
            "thermal_power_mw": _round_float(thermal_power_mw),
            "mass_flow_kg_s": _round_float(mass_flow_kg_s),
        },
        "fluid_properties": {
            "density_kg_m3": _round_float(density_kg_m3),
            "cp_j_kgk": _round_float(cp_j_kgk),
            "dynamic_viscosity_pa_s": _round_float(viscosity_pa_s),
            "thermal_conductivity_w_mk": _round_float(conductivity_w_mk),
        },
        "momentum_balance": {
            "reynolds_number": _round_float(reynolds),
            "friction_factor": _round_float(friction_factor),
            "friction_pressure_drop_kpa": _round_float(loop_resistance_pa / 1000.0),
            "form_loss_pressure_drop_kpa": _round_float(loop_form_pa / 1000.0),
            "buoyancy_driving_pressure_kpa": _round_float(buoyancy_pa / 1000.0),
            "pump_curve_pressure_kpa": _round_float(pump_pressure_pa / 1000.0),
            "flow_reversal_margin_kpa": _round_float(flow_reversal_margin_pa / 1000.0),
            "flow_reversal_predicted": flow_reversal_margin_pa < 0.0,
            "natural_circulation_flow_m3_s": _round_float(natural_flow_m3_s),
            "natural_circulation_fraction_of_nominal": _round_float(natural_flow_m3_s / max(volumetric_flow_m3_s, 1.0e-12)),
        },
        "pump_curve": pump_curve,
        "heat_exchanger": hx_summary,
        "transient_flow_reversal_screen": _flow_reversal_screen(
            pump_curve=pump_curve,
            nominal_flow_m3_s=volumetric_flow_m3_s,
            density_kg_m3=density_kg_m3,
            resistive_pressure_pa=resistive_pa,
            buoyancy_pressure_pa=buoyancy_pa,
        ),
        "axial_nodes": axial_nodes,
    }


def build_finite_volume_precursor_transport(
    config: Any,
    summary: dict[str, Any],
    settings: dict[str, Any],
    thermal_hydraulics: dict[str, Any],
) -> dict[str, Any]:
    precursor_settings = _section(settings, "precursor_transport")
    transient_config = config.data.get("transient", {})
    if not isinstance(transient_config, dict):
        transient_config = {}
    groups = normalize_precursor_groups(transient_config.get("delayed_neutron_precursor_groups"))
    loop_segments = normalize_loop_segments(config.data.get("loop_segments"))
    core_nodes = thermal_hydraulics["axial_nodes"]
    loop_cell_count = max(int(precursor_settings.get("loop_cells", len(loop_segments))), 1)
    cells = _precursor_cells(core_nodes, loop_segments, loop_cell_count)
    diffusion_m2_s = max(float(precursor_settings.get("diffusion_coefficient_m2_s", 2.5e-5)), 0.0)
    cleanup_rate_s = _cleanup_rate_s(config, summary)
    delayed_group_results = []
    delayed_core_source = 0.0
    delayed_total_source = 0.0
    delayed_loop_source = 0.0
    for group in groups:
        inventory = _solve_ring_advection_diffusion_decay(
            cells,
            decay_constant_s=float(group["decay_constant_s"]),
            source_strength=float(group["relative_yield_fraction"]),
            diffusion_m2_s=diffusion_m2_s,
            cleanup_rate_s=cleanup_rate_s,
        )
        core_inventory = sum(value for value, cell in zip(inventory, cells) if cell["region"] == "core")
        loop_inventory = sum(value for value, cell in zip(inventory, cells) if cell["region"] != "core")
        core_source = float(group["decay_constant_s"]) * core_inventory
        loop_source = float(group["decay_constant_s"]) * loop_inventory
        delayed_core_source += core_source
        delayed_loop_source += loop_source
        delayed_total_source += core_source + loop_source
        delayed_group_results.append(
            {
                "name": str(group["name"]),
                "decay_constant_s": _round_float(float(group["decay_constant_s"])),
                "yield_fraction": _round_float(float(group["yield_fraction"])),
                "core_inventory": _round_float(core_inventory),
                "loop_inventory": _round_float(loop_inventory),
                "core_delayed_neutron_source": _round_float(core_source),
                "loop_delayed_neutron_source": _round_float(loop_source),
            }
        )
    decay_heat = _decay_heat_precursor_summary(cells, diffusion_m2_s, cleanup_rate_s, precursor_settings)
    cell_report = _cell_inventory_report(cells, groups, diffusion_m2_s, cleanup_rate_s)
    return {
        "status": "completed",
        "model": FINITE_VOLUME_PRECURSOR_MODEL,
        "equation": "dC_i/dt + div(u C_i) = div(D_i grad C_i) + beta_i S_f - lambda_i C_i - cleanup_i C_i",
        "spatial_discretization": "implicit upwind finite volume on core and external-loop ring cells",
        "group_count": len(groups),
        "cell_count": len(cells),
        "core_cell_count": sum(1 for cell in cells if cell["region"] == "core"),
        "loop_cell_count": sum(1 for cell in cells if cell["region"] != "core"),
        "diffusion_coefficient_m2_s": _round_float(diffusion_m2_s),
        "cleanup_rate_s": _round_float(cleanup_rate_s),
        "core_delayed_neutron_source_absolute_fraction": _round_float(
            delayed_core_source / max(delayed_total_source, 1.0e-12)
        ),
        "loop_delayed_neutron_source_absolute_fraction": _round_float(
            delayed_loop_source / max(delayed_total_source, 1.0e-12)
        ),
        "transport_loss_fraction": _round_float(delayed_loop_source / max(delayed_total_source, 1.0e-12)),
        "delayed_neutron_groups": delayed_group_results,
        "decay_heat_precursors": decay_heat,
        "cells": cell_report,
    }


def build_temperature_dependent_multigroup_xs(
    config: Any,
    *,
    group_count: int,
    fuel_temperature_c: float,
    graphite_temperature_c: float,
    temperature_grid_c: list[float],
    settings: dict[str, Any],
) -> dict[str, Any]:
    custom = settings.get("cross_sections")
    if isinstance(custom, dict):
        return _custom_xs(custom, group_count, fuel_temperature_c, graphite_temperature_c, temperature_grid_c)
    reference_temp_c = float(settings.get("reference_temperature_c", 650.0))
    fuel_delta = fuel_temperature_c - reference_temp_c
    graphite_delta = graphite_temperature_c - reference_temp_c
    chi = _fission_spectrum(group_count)
    diffusion = []
    absorption = []
    nu_fission = []
    scatter = [[0.0 for _ in range(group_count)] for _ in range(group_count)]
    for group in range(group_count):
        lethargy = group / max(group_count - 1, 1)
        diffusion.append(max(0.24, 1.42 - 0.075 * group))
        base_absorption = 0.0028 + 0.0017 * lethargy + 0.0009 * lethargy * lethargy
        absorption_factor = 1.0 + 1.25e-4 * fuel_delta + 2.5e-5 * graphite_delta
        absorption.append(max(base_absorption * absorption_factor, 1.0e-6))
        fissile_shape = math.exp(-((lethargy - 0.72) ** 2) / 0.08) + 0.18 * math.exp(-((lethargy - 0.12) ** 2) / 0.015)
        fission_factor = 1.0 - 8.5e-5 * fuel_delta - 2.0e-5 * graphite_delta
        nu_fission.append(max(0.0065 * fissile_shape * fission_factor, 1.0e-7))
        if group + 1 < group_count:
            scatter[group][group + 1] = 0.018 + 0.004 * (1.0 - lethargy)
        if group + 2 < group_count:
            scatter[group][group + 2] = 0.003 * (1.0 - lethargy)
        if group > 0:
            scatter[group][group - 1] = 0.00035 * lethargy
    return {
        "material": str(config.geometry.get("salt_material", "fuel_salt")),
        "reference_temperature_c": _round_float(reference_temp_c),
        "fuel_temperature_c": _round_float(fuel_temperature_c),
        "graphite_temperature_c": _round_float(graphite_temperature_c),
        "temperature_grid_c": [_round_float(value) for value in temperature_grid_c],
        "group_count": group_count,
        "diffusion_coeff_cm": diffusion,
        "absorption_cm_inv": absorption,
        "nu_fission_cm_inv": nu_fission,
        "scatter_cm_inv": scatter,
        "chi": chi,
        "interpolation": "linear_temperature_dependence_between_declared_grid_points",
    }


def _solve_multigroup_eigenvalue(
    xs: dict[str, Any],
    *,
    axial_nodes: list[dict[str, Any]],
    method: str,
) -> dict[str, Any]:
    group_count = int(xs["group_count"])
    node_count = len(axial_nodes)
    dz_cm = max(float(axial_nodes[0].get("cell_length_m", 0.1)) * 100.0, 1.0e-6)
    diffusion = np.asarray(xs["diffusion_coeff_cm"], dtype=float) * _method_diffusion_factor(method)
    absorption = np.asarray(xs["absorption_cm_inv"], dtype=float) * _method_absorption_factor(method)
    nu_fission = np.asarray(xs["nu_fission_cm_inv"], dtype=float) * _method_fission_factor(method)
    scatter = np.asarray(xs["scatter_cm_inv"], dtype=float)
    chi = np.asarray(xs["chi"], dtype=float)
    removal = absorption + np.sum(scatter, axis=1)
    matrix = np.zeros((node_count * group_count, node_count * group_count), dtype=float)
    for node in range(node_count):
        for group in range(group_count):
            row = _index(node, group, group_count)
            leakage = 2.0 * diffusion[group] / (dz_cm * dz_cm)
            matrix[row, row] = removal[group] + leakage
            if node > 0:
                matrix[row, _index(node - 1, group, group_count)] = -diffusion[group] / (dz_cm * dz_cm)
            if node + 1 < node_count:
                matrix[row, _index(node + 1, group, group_count)] = -diffusion[group] / (dz_cm * dz_cm)
    flux = np.ones(node_count * group_count, dtype=float)
    flux /= np.mean(flux)
    k_eff = 1.0
    inverse_matrix = np.linalg.inv(matrix)
    for _ in range(48):
        old_fission = _total_fission_source(flux, nu_fission, node_count, group_count)
        source = _scatter_source(flux, scatter, node_count, group_count)
        source += _fission_source(flux, nu_fission, chi, node_count, group_count) / max(k_eff, 1.0e-12)
        next_flux = inverse_matrix @ source
        next_flux = np.maximum(next_flux, 1.0e-16)
        new_fission = _total_fission_source(next_flux, nu_fission, node_count, group_count)
        next_k = k_eff * new_fission / max(old_fission, 1.0e-16)
        next_flux /= max(np.mean(next_flux), 1.0e-16)
        if abs(next_k - k_eff) < 1.0e-8:
            flux = next_flux
            k_eff = next_k
            break
        flux = next_flux
        k_eff = next_k
    power_by_node = _power_by_node(flux, nu_fission, node_count, group_count)
    power_shape = power_by_node / max(np.mean(power_by_node), 1.0e-16)
    adjoint = np.linalg.solve(matrix.T, np.tile(nu_fission, node_count))
    adjoint = np.maximum(adjoint, 0.0)
    adjoint_by_node = np.array(
        [float(np.mean(adjoint[_index(node, 0, group_count): _index(node + 1, 0, group_count)])) for node in range(node_count)]
    )
    importance = power_shape * adjoint_by_node
    importance /= max(float(np.mean(importance)), 1.0e-16)
    return {
        "method": method,
        "k_eff": float(k_eff),
        "dominance_proxy": float(np.linalg.norm(flux, ord=2) / max(np.linalg.norm(source, ord=2), 1.0e-16)),
        "power_shape": [_round_float(value) for value in power_shape.tolist()],
        "adjoint_weighted_importance": [_round_float(value) for value in importance.tolist()],
    }


def _feedback_coefficients(
    config: Any,
    *,
    base_result: dict[str, Any],
    calibration_factor: float,
    axial_nodes: list[dict[str, Any]],
    group_count: int,
    average_fuel_temp_c: float,
    average_graphite_temp_c: float,
    temperature_grid_c: list[float],
    settings: dict[str, Any],
    method: str,
) -> dict[str, Any]:
    delta_t = 50.0

    def solve(fuel_temp_c: float, graphite_temp_c: float) -> float:
        xs = build_temperature_dependent_multigroup_xs(
            config,
            group_count=group_count,
            fuel_temperature_c=fuel_temp_c,
            graphite_temperature_c=graphite_temp_c,
            temperature_grid_c=temperature_grid_c,
            settings=settings,
        )
        return _solve_multigroup_eigenvalue(xs, axial_nodes=axial_nodes, method=method)["k_eff"] * calibration_factor

    base_k = base_result["k_eff"] * calibration_factor
    fuel_hot = solve(average_fuel_temp_c + delta_t, average_graphite_temp_c)
    graphite_hot = solve(average_fuel_temp_c, average_graphite_temp_c + delta_t)
    uniform_hot = solve(average_fuel_temp_c + delta_t, average_graphite_temp_c + delta_t)
    return {
        "fuel_temperature_pcm_per_c": _round_float(((fuel_hot - base_k) / max(base_k, 1.0e-12)) * 1.0e5 / delta_t),
        "graphite_temperature_pcm_per_c": _round_float(((graphite_hot - base_k) / max(base_k, 1.0e-12)) * 1.0e5 / delta_t),
        "uniform_temperature_pcm_per_c": _round_float(((uniform_hot - base_k) / max(base_k, 1.0e-12)) * 1.0e5 / delta_t),
        "perturbation_c": _round_float(delta_t),
    }


def _scaled_neutronics_result(result: dict[str, Any], calibration_factor: float) -> dict[str, Any]:
    return {
        "method": result["method"],
        "k_eff": _round_float(result["k_eff"] * calibration_factor),
        "raw_k_eff": _round_float(result["k_eff"]),
        "dominance_proxy": _round_float(result["dominance_proxy"]),
        "power_shape": result["power_shape"],
        "adjoint_weighted_importance": result["adjoint_weighted_importance"],
    }


def _precursor_cells(
    core_nodes: list[dict[str, Any]],
    loop_segments: list[dict[str, Any]],
    loop_cell_count: int,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    core_total_residence_s = sum(float(node.get("cell_length_m", 0.1)) / max(float(node.get("velocity_m_s", 0.0)), 1.0e-6) for node in core_nodes)
    power_total = sum(float(node["power_shape"]) for node in core_nodes)
    for node in core_nodes:
        cells.append(
            {
                "id": f"core_{node['index']}",
                "region": "core",
                "residence_time_s": max(core_total_residence_s / max(len(core_nodes), 1), 1.0e-6),
                "source_fraction": float(node["power_shape"]) / max(power_total, 1.0e-12),
                "cleanup_weight": 0.0,
            }
        )
    normalized_segments = normalize_loop_segments(loop_segments)
    for index in range(loop_cell_count):
        segment = normalized_segments[index % len(normalized_segments)]
        cells.append(
            {
                "id": f"loop_{index}_{segment['id']}",
                "region": "loop",
                "residence_time_s": max(4.0 * float(segment["residence_fraction"]), 0.05),
                "source_fraction": 0.0,
                "cleanup_weight": float(segment["cleanup_weight"]),
            }
        )
    return cells


def _solve_ring_advection_diffusion_decay(
    cells: list[dict[str, Any]],
    *,
    decay_constant_s: float,
    source_strength: float,
    diffusion_m2_s: float,
    cleanup_rate_s: float,
) -> list[float]:
    count = len(cells)
    inventory = [source_strength * float(cell["source_fraction"]) / max(decay_constant_s, 1.0e-12) for cell in cells]
    min_tau = min(float(cell["residence_time_s"]) for cell in cells)
    dt = min(max(0.2 * min_tau, 0.01), 0.5 / max(decay_constant_s, 1.0e-12))
    diffusion_rate = diffusion_m2_s / max(min_tau * min_tau, 1.0e-12)
    for _ in range(500):
        updated = [0.0 for _ in cells]
        max_delta = 0.0
        for index, cell in enumerate(cells):
            previous = (index - 1) % count
            next_index = (index + 1) % count
            out_rate = 1.0 / max(float(cell["residence_time_s"]), 1.0e-12)
            in_rate = 1.0 / max(float(cells[previous]["residence_time_s"]), 1.0e-12)
            cleanup = cleanup_rate_s * float(cell.get("cleanup_weight", 0.0))
            source = source_strength * float(cell["source_fraction"])
            numerator = inventory[index] + dt * (
                source
                + in_rate * inventory[previous]
                + diffusion_rate * (inventory[previous] + inventory[next_index])
            )
            denominator = 1.0 + dt * (out_rate + decay_constant_s + cleanup + 2.0 * diffusion_rate)
            updated[index] = max(numerator / max(denominator, 1.0e-18), 0.0)
            max_delta = max(max_delta, abs(updated[index] - inventory[index]))
        inventory = updated
        if max_delta < 1.0e-10:
            break
    return inventory


def _decay_heat_precursor_summary(
    cells: list[dict[str, Any]],
    diffusion_m2_s: float,
    cleanup_rate_s: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    raw_groups = settings.get("decay_heat_groups")
    groups = raw_groups if isinstance(raw_groups, list) and raw_groups else [
        {"name": "short_lived", "decay_constant_s": 0.08, "yield_fraction": 0.45},
        {"name": "intermediate", "decay_constant_s": 0.006, "yield_fraction": 0.35},
        {"name": "long_lived", "decay_constant_s": 0.00045, "yield_fraction": 0.20},
    ]
    group_results = []
    total_source = 0.0
    core_source = 0.0
    for index, group in enumerate(groups):
        decay = max(float(group.get("decay_constant_s", 0.0)), 1.0e-12)
        yield_fraction = max(float(group.get("yield_fraction", 0.0)), 0.0)
        inventory = _solve_ring_advection_diffusion_decay(
            cells,
            decay_constant_s=decay,
            source_strength=yield_fraction,
            diffusion_m2_s=diffusion_m2_s,
            cleanup_rate_s=cleanup_rate_s,
        )
        source = decay * sum(inventory)
        source_core = decay * sum(value for value, cell in zip(inventory, cells) if cell["region"] == "core")
        total_source += source
        core_source += source_core
        group_results.append(
            {
                "name": str(group.get("name", f"decay_heat_group_{index + 1}")),
                "decay_constant_s": _round_float(decay),
                "yield_fraction": _round_float(yield_fraction),
                "core_decay_heat_source_fraction": _round_float(source_core / max(source, 1.0e-12)),
            }
        )
    return {
        "group_count": len(group_results),
        "core_decay_heat_source_fraction": _round_float(core_source / max(total_source, 1.0e-12)),
        "loop_decay_heat_source_fraction": _round_float(1.0 - core_source / max(total_source, 1.0e-12)),
        "groups": group_results,
    }


def _cell_inventory_report(
    cells: list[dict[str, Any]],
    groups: list[dict[str, float | str]],
    diffusion_m2_s: float,
    cleanup_rate_s: float,
) -> list[dict[str, Any]]:
    totals = [0.0 for _ in cells]
    for group in groups:
        inventory = _solve_ring_advection_diffusion_decay(
            cells,
            decay_constant_s=float(group["decay_constant_s"]),
            source_strength=float(group["relative_yield_fraction"]),
            diffusion_m2_s=diffusion_m2_s,
            cleanup_rate_s=cleanup_rate_s,
        )
        for index, value in enumerate(inventory):
            totals[index] += value
    total_inventory = sum(totals)
    return [
        {
            "id": str(cell["id"]),
            "region": str(cell["region"]),
            "residence_time_s": _round_float(float(cell["residence_time_s"])),
            "source_fraction": _round_float(float(cell["source_fraction"])),
            "inventory_fraction": _round_float(totals[index] / max(total_inventory, 1.0e-12)),
        }
        for index, cell in enumerate(cells)
    ]


def _settings(config: Any) -> dict[str, Any]:
    settings = config.data.get("physics_core", {})
    return settings if isinstance(settings, dict) else {}


def _section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    section = settings.get(name, {})
    return section if isinstance(section, dict) else {}


def _temperature_grid(settings: dict[str, Any]) -> list[float]:
    raw = settings.get("temperature_grid_c", [500.0, 650.0, 800.0])
    if not isinstance(raw, list) or len(raw) < 2:
        return [500.0, 650.0, 800.0]
    return sorted(float(value) for value in raw)


def _custom_xs(
    payload: dict[str, Any],
    group_count: int,
    fuel_temperature_c: float,
    graphite_temperature_c: float,
    temperature_grid_c: list[float],
) -> dict[str, Any]:
    def array(name: str, default: float) -> list[float]:
        values = payload.get(name, [default for _ in range(group_count)])
        if not isinstance(values, list):
            values = [default for _ in range(group_count)]
        parsed = [float(value) for value in values[:group_count]]
        if len(parsed) < group_count:
            parsed.extend([default for _ in range(group_count - len(parsed))])
        return parsed

    scatter = payload.get("scatter_cm_inv")
    if not isinstance(scatter, list):
        scatter = [[0.0 for _ in range(group_count)] for _ in range(group_count)]
    chi = payload.get("chi", _fission_spectrum(group_count))
    return {
        "material": str(payload.get("material", "configured")),
        "reference_temperature_c": float(payload.get("reference_temperature_c", temperature_grid_c[len(temperature_grid_c) // 2])),
        "fuel_temperature_c": _round_float(fuel_temperature_c),
        "graphite_temperature_c": _round_float(graphite_temperature_c),
        "temperature_grid_c": [_round_float(value) for value in temperature_grid_c],
        "group_count": group_count,
        "diffusion_coeff_cm": array("diffusion_coeff_cm", 1.0),
        "absorption_cm_inv": array("absorption_cm_inv", 0.004),
        "nu_fission_cm_inv": array("nu_fission_cm_inv", 0.004),
        "scatter_cm_inv": [[float(value) for value in row[:group_count]] for row in scatter[:group_count]],
        "chi": [float(value) for value in chi[:group_count]],
        "interpolation": "configured_temperature_dependent_library",
    }


def _xs_report(xs: dict[str, Any]) -> dict[str, Any]:
    return {
        "material": xs["material"],
        "interpolation": xs["interpolation"],
        "reference_temperature_c": xs["reference_temperature_c"],
        "fuel_temperature_c": xs["fuel_temperature_c"],
        "graphite_temperature_c": xs["graphite_temperature_c"],
        "diffusion_coeff_cm": [_round_float(value) for value in xs["diffusion_coeff_cm"]],
        "absorption_cm_inv": [_round_float(value) for value in xs["absorption_cm_inv"]],
        "nu_fission_cm_inv": [_round_float(value) for value in xs["nu_fission_cm_inv"]],
        "chi": [_round_float(value) for value in xs["chi"]],
    }


def _reference_keff(config: Any, summary: dict[str, Any]) -> float:
    metrics = summary.get("metrics", {})
    if metrics.get("keff") is not None:
        return float(metrics["keff"])
    for target in config.validation_targets.values():
        if not isinstance(target, dict) or target.get("metric") != "keff":
            continue
        if target.get("min") is not None and target.get("max") is not None:
            return 0.5 * (float(target["min"]) + float(target["max"]))
    return 1.01


def _total_delayed_neutron_yield(config: Any) -> float:
    transient_config = config.data.get("transient", {})
    if not isinstance(transient_config, dict):
        transient_config = {}
    return sum(float(group["yield_fraction"]) for group in normalize_precursor_groups(transient_config.get("delayed_neutron_precursor_groups")))


def _fission_spectrum(group_count: int) -> list[float]:
    values = [math.exp(-group / max(group_count / 4.0, 1.0)) for group in range(group_count)]
    total = sum(values)
    return [value / total for value in values]


def _cosine_power_shape(node_count: int) -> list[float]:
    values = [
        1.0 + 0.24 * math.cos(math.pi * ((index + 0.5) / node_count - 0.5))
        for index in range(node_count)
    ]
    mean = sum(values) / max(len(values), 1)
    return [value / mean for value in values]


def _method_diffusion_factor(method: str) -> float:
    return {"diffusion": 1.0, "sp3": 0.92, "transport": 0.86}.get(method, 1.0)


def _method_absorption_factor(method: str) -> float:
    return {"diffusion": 1.0, "sp3": 1.01, "transport": 1.018}.get(method, 1.0)


def _method_fission_factor(method: str) -> float:
    return {"diffusion": 1.0, "sp3": 0.997, "transport": 0.992}.get(method, 1.0)


def _index(node: int, group: int, group_count: int) -> int:
    return node * group_count + group


def _scatter_source(flux: np.ndarray, scatter: np.ndarray, node_count: int, group_count: int) -> np.ndarray:
    source = np.zeros(node_count * group_count, dtype=float)
    for node in range(node_count):
        local_flux = flux[_index(node, 0, group_count): _index(node + 1, 0, group_count)]
        local_source = scatter.T @ local_flux
        source[_index(node, 0, group_count): _index(node + 1, 0, group_count)] = local_source
    return source


def _fission_source(
    flux: np.ndarray,
    nu_fission: np.ndarray,
    chi: np.ndarray,
    node_count: int,
    group_count: int,
) -> np.ndarray:
    source = np.zeros(node_count * group_count, dtype=float)
    for node in range(node_count):
        local_flux = flux[_index(node, 0, group_count): _index(node + 1, 0, group_count)]
        fission = float(np.dot(nu_fission, local_flux))
        source[_index(node, 0, group_count): _index(node + 1, 0, group_count)] = chi * fission
    return source


def _total_fission_source(flux: np.ndarray, nu_fission: np.ndarray, node_count: int, group_count: int) -> float:
    return float(sum(np.dot(nu_fission, flux[_index(node, 0, group_count): _index(node + 1, 0, group_count)]) for node in range(node_count)))


def _power_by_node(flux: np.ndarray, nu_fission: np.ndarray, node_count: int, group_count: int) -> np.ndarray:
    return np.array(
        [float(np.dot(nu_fission, flux[_index(node, 0, group_count): _index(node + 1, 0, group_count)])) for node in range(node_count)],
        dtype=float,
    )


def _core_porosity(config: Any, active_volume_m3: float, core_length_m: float) -> float:
    core_radius_m = max(float(config.geometry.get("core_radius", 100.0)) * 0.01, 0.01)
    gross_volume_m3 = math.pi * core_radius_m * core_radius_m * max(core_length_m, 0.01)
    return max(0.01, min(0.85, active_volume_m3 / max(gross_volume_m3, 1.0e-12)))


def _darcy_friction_factor(reynolds: float) -> float:
    if reynolds <= 0.0:
        return 0.0
    if reynolds < 2300.0:
        return 64.0 / reynolds
    return 0.3164 / (reynolds ** 0.25)


def _pump_curve(config: Any, *, nominal_flow_m3_s: float, nominal_head_m: float, density_kg_m3: float) -> dict[str, Any]:
    reactor = config.reactor
    shutoff_head_m = float(reactor.get("primary_pump_shutoff_head_m", max(nominal_head_m * 1.8, 20.0)))
    runout_flow_m3_s = float(reactor.get("primary_pump_runout_flow_m3_s", max(nominal_flow_m3_s * 1.75, nominal_flow_m3_s + 1.0e-6)))
    return {
        "model": "quadratic_head_flow_curve",
        "shutoff_head_m": _round_float(shutoff_head_m),
        "runout_flow_m3_s": _round_float(runout_flow_m3_s),
        "nominal_flow_m3_s": _round_float(nominal_flow_m3_s),
        "nominal_pressure_kpa": _round_float(_evaluate_pump_curve_raw(shutoff_head_m, runout_flow_m3_s, nominal_flow_m3_s, density_kg_m3) / 1000.0),
    }


def _evaluate_pump_curve(pump_curve: dict[str, Any], flow_m3_s: float, density_kg_m3: float) -> float:
    return _evaluate_pump_curve_raw(
        float(pump_curve["shutoff_head_m"]),
        float(pump_curve["runout_flow_m3_s"]),
        flow_m3_s,
        density_kg_m3,
    )


def _evaluate_pump_curve_raw(shutoff_head_m: float, runout_flow_m3_s: float, flow_m3_s: float, density_kg_m3: float) -> float:
    flow_ratio = max(flow_m3_s, 0.0) / max(runout_flow_m3_s, 1.0e-12)
    head_m = max(shutoff_head_m * (1.0 - flow_ratio * flow_ratio), 0.0)
    return density_kg_m3 * 9.80665 * head_m


def _natural_circulation_flow(*, nominal_flow_m3_s: float, nominal_resistance_pa: float, buoyancy_pa: float) -> float:
    if nominal_resistance_pa <= 0.0 or buoyancy_pa <= 0.0:
        return 0.0
    return max(nominal_flow_m3_s, 0.0) * math.sqrt(buoyancy_pa / nominal_resistance_pa)


def _heat_exchanger_fv_summary(
    config: Any,
    summary: dict[str, Any],
    *,
    mass_flow_kg_s: float,
    cp_j_kgk: float,
    hot_leg_temp_c: float,
    cold_leg_temp_c: float,
) -> dict[str, Any]:
    hx = summary.get("primary_system", {}).get("heat_exchanger", {})
    area_m2 = float(hx.get("required_area_m2", config.reactor.get("primary_heat_exchanger_area_m2", 1.0)))
    u_w_m2k = float(hx.get("estimated_clean_u_w_m2k", 900.0))
    ua_w_k = max(area_m2 * u_w_m2k, 0.0)
    capacity_w_k = max(mass_flow_kg_s * cp_j_kgk, 1.0)
    ntu = ua_w_k / capacity_w_k
    effectiveness = 1.0 - math.exp(-ntu)
    rejected_mw = capacity_w_k * max(hot_leg_temp_c - cold_leg_temp_c, 0.0) * effectiveness / 1.0e6
    return {
        "model": "finite_volume_effectiveness_ntu",
        "area_m2": _round_float(area_m2),
        "overall_u_w_m2k": _round_float(u_w_m2k),
        "ntu": _round_float(ntu),
        "effectiveness": _round_float(effectiveness),
        "estimated_rejected_power_mw": _round_float(rejected_mw),
    }


def _flow_reversal_screen(
    *,
    pump_curve: dict[str, Any],
    nominal_flow_m3_s: float,
    density_kg_m3: float,
    resistive_pressure_pa: float,
    buoyancy_pressure_pa: float,
) -> list[dict[str, Any]]:
    screen = []
    for fraction in (1.0, 0.75, 0.5, 0.25, 0.0):
        pump_pressure = _evaluate_pump_curve(pump_curve, nominal_flow_m3_s * fraction, density_kg_m3) * fraction
        resistance = resistive_pressure_pa * fraction * fraction
        margin = pump_pressure + buoyancy_pressure_pa - resistance
        screen.append(
            {
                "pump_speed_fraction": _round_float(fraction),
                "flow_fraction_assumption": _round_float(fraction),
                "margin_kpa": _round_float(margin / 1000.0),
                "flow_reversal_predicted": margin < 0.0,
            }
        )
    return screen


def _cleanup_rate_s(config: Any, summary: dict[str, Any]) -> float:
    fuel_cycle = summary.get("fuel_cycle", summary.get("primary_system", {}).get("fuel_cycle", {}))
    turnover_days = float(fuel_cycle.get("cleanup_turnover_days", config.reactor.get("cleanup_turnover_days", 14.0)))
    efficiency = float(fuel_cycle.get("cleanup_removal_efficiency", config.reactor.get("cleanup_removal_efficiency", 0.75)))
    return efficiency / max(turnover_days * 86400.0, 1.0)


def _physics_core_checks(
    neutronics: dict[str, Any],
    thermal_hydraulics: dict[str, Any],
    precursor_transport: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "finite_k_eff": math.isfinite(float(neutronics["k_eff"])),
        "positive_beta_eff": float(neutronics["beta_eff"]) > 0.0,
        "power_shape_positive": all(float(value) > 0.0 for value in neutronics["power_shape"]),
        "finite_volume_temperatures_positive": all(float(node["fuel_salt_temp_c"]) > 0.0 for node in thermal_hydraulics["axial_nodes"]),
        "precursor_transport_fraction_bounded": 0.0 <= float(precursor_transport["transport_loss_fraction"]) <= 1.0,
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "failures": [name for name, passed in checks.items() if not passed],
    }


def _round_float(value: float) -> float:
    return round(float(value), 6)
