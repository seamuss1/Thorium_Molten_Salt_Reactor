from copy import deepcopy
from pathlib import Path

import pytest

from thorium_reactor.bop.steady_state import BOPInputs, run_steady_state_bop
from thorium_reactor.config import load_case_config
from thorium_reactor.flow.properties import average_primary_temperature_c, evaluate_fluid_properties, primary_coolant_cp_kj_kgk, property_reference_temperature_c
from thorium_reactor.flow.primary_system import (
    _darcy_friction_factor,
    _internal_nusselt_number,
    _log_mean_temperature_difference,
    build_primary_system_summary,
)
from thorium_reactor.flow.reduced_order import build_reduced_order_flow_summary
from thorium_reactor.neutronics.workflows import build_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_case(name: str):
    return load_case_config(REPO_ROOT / "configs" / "cases" / name / "case.yaml")


def _primary_bop_inputs(config):
    return BOPInputs(
        thermal_power_mw=float(config.reactor["design_power_mwth"]),
        hot_leg_temp_c=float(config.reactor["hot_leg_temp_c"]),
        cold_leg_temp_c=float(config.reactor["cold_leg_temp_c"]),
        primary_cp_kj_kgk=primary_coolant_cp_kj_kgk(
            config,
            temperature_c=average_primary_temperature_c(config.reactor),
        ),
        steam_generator_effectiveness=float(config.reactor["steam_generator_effectiveness"]),
        turbine_efficiency=float(config.reactor["turbine_efficiency"]),
        generator_efficiency=float(config.reactor["generator_efficiency"]),
    )


def test_reduced_order_flow_uses_only_plenum_connected_salt_channels() -> None:
    config = _load_case("tmsr_lf1_core")
    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))

    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )

    assert reduced_order["allocation_rule"] == "salt_area_weighted"
    assert reduced_order["active_channel_selection"] == "configured_active_variants"
    assert reduced_order["disconnected_inventory_selection"] == "configured_non_active_variants"
    assert reduced_order["core_model"]["kind"] == "channelized_from_geometry"
    assert reduced_order["core_model"]["active_variants"] == ["fuel", "control_guides"]
    assert reduced_order["core_model"]["stagnant_variants"] == ["instrumentation_wells"]
    assert reduced_order["salt_bulk_temperature_c"] == 630.0
    assert reduced_order["primary_mass_flow_kg_s"] == 1116.071429
    active_flow = reduced_order["active_flow"]
    assert active_flow["channel_count"] == 85
    assert active_flow["variant_counts"] == {
        "control_guides": 6,
        "fuel": 79,
    }
    assert active_flow["total_flow_area_cm2"] > 20.0
    assert active_flow["representative_velocity_m_s"] > 12.0
    assert active_flow["representative_residence_time_s"] < 0.02
    assert reduced_order["disconnected_inventory"]["channel_count"] == 0
    assert reduced_order["stagnant_inventory"]["channel_count"] == 0
    assert built.manifest["flow_summary"]["interface_metrics"]["reflector_backed_salt_bearing_channels"] == 48
    assert built.manifest["flow_summary"]["interface_metrics"]["reflector_backed_salt_volume_cm3"] > 0.0

    variant_summary = {item["variant"]: item for item in reduced_order["variant_summary"]}
    assert variant_summary["fuel"]["allocated_mass_flow_kg_s"] > variant_summary["control_guides"]["allocated_mass_flow_kg_s"]

    center_channel = next(channel for channel in reduced_order["active_channels"] if channel["name"] == "fuel_0.00_0")
    control_channel = next(
        channel
        for channel in reduced_order["active_channels"]
        if channel["name"] == "control_guides_18.06_0"
    )
    assert center_channel["allocated_mass_flow_kg_s"] > control_channel["allocated_mass_flow_kg_s"]
    assert center_channel["velocity_m_s"] > 12.0
    assert control_channel["velocity_m_s"] > 12.0
    assert center_channel["residence_time_s"] < 0.02
    assert control_channel["residence_time_s"] < 0.02


def test_immersed_pool_reference_primary_system_summary_is_engineering_useful() -> None:
    config = _load_case("immersed_pool_reference")
    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )

    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    hydraulics = primary_system["loop_hydraulics"]
    heat_exchanger = primary_system["heat_exchanger"]
    thermal_profile = primary_system["thermal_profile"]
    inventory = primary_system["inventory"]
    fuel_cycle = primary_system["fuel_cycle"]
    chemistry = primary_system["chemistry"]

    assert primary_system["model"] == "reduced_order_primary_system"
    assert reduced_order["active_channel_selection"] == "configured_active_variants"
    assert reduced_order["core_model"]["active_variants"] == ["fuel", "control_guides"]
    assert reduced_order["active_flow"]["representative_velocity_m_s"] <= 12.0
    assert primary_system["bulk_temperature_c"] == 622.5
    assert primary_system["cold_leg_density_kg_m3"] > primary_system["hot_leg_density_kg_m3"]
    assert hydraulics["total_pipe_length_m"] > 1.0
    assert 1.0 <= hydraulics["limiting_velocity_m_s"] <= 10.0
    assert hydraulics["max_reynolds_number"] > 4000.0
    assert -60.0 <= hydraulics["pump_head_m"] <= 60.0
    assert "hydrostatic_pressure_change_kpa" in hydraulics
    assert "buoyancy_driving_pressure_kpa" in hydraulics
    assert "required_pump_pressure_kpa" in hydraulics
    assert hydraulics["representative_elevation_span_m"] > 0.0
    assert 1.0 <= heat_exchanger["required_area_m2"] <= 250.0
    assert heat_exchanger["terminal_hot_delta_c"] > 0.0
    assert heat_exchanger["terminal_cold_delta_c"] > 0.0
    assert heat_exchanger["estimated_clean_u_w_m2k"] > 0.0
    assert heat_exchanger["primary_hx_side"]["heat_transfer_coefficient_w_m2k"] > 0.0
    assert heat_exchanger["secondary_hx_side"]["heat_transfer_coefficient_w_m2k"] > 0.0
    assert heat_exchanger["primary_pipe_heat_transfer"]["heat_transfer_coefficient_w_m2k"] > 0.0
    assert thermal_profile["estimated_hot_leg_temp_c"] > thermal_profile["estimated_cold_leg_temp_c"]
    assert thermal_profile["total_pipe_heat_loss_kw"] >= 0.0
    assert abs(thermal_profile["loop_closure_error_c"]) < 1.0e-3
    assert thermal_profile["solver_iterations"] >= 1
    assert "cold_leg_closure_error_c" in thermal_profile
    assert len(thermal_profile["segments"]) >= 5
    assert inventory["fuel_salt"]["total_m3"] > 0.0
    assert inventory["coolant_salt"]["net_pool_inventory_m3"] > inventory["fuel_salt"]["total_m3"]
    assert fuel_cycle["heavy_metal_inventory_kg"] > fuel_cycle["fissile_inventory_kg"] > 0.0
    assert fuel_cycle["cleanup_turnover_days"] == 10.0
    assert fuel_cycle["breeding_gain_fraction_per_day"] > 0.0
    assert fuel_cycle["fissile_burn_fraction_per_day_full_power"] > 0.0
    assert "net_fissile_change_fraction_per_day" in fuel_cycle
    assert chemistry["corrosion_index"] >= 0.1
    assert chemistry["corrosion_risk"] in {"low", "moderate", "high"}
    assert chemistry["gas_stripping_efficiency"] > 0.0
    check_status = {check["name"]: check["status"] for check in primary_system["checks"]}
    assert check_status["primary_system::loop_reynolds_reasonable"] == "pass"
    assert check_status["primary_system::pump_head_reasonable"] == "pass"
    assert check_status["primary_system::heat_exchanger_pinch_positive"] == "pass"
    assert check_status["primary_system::heat_exchanger_area_reasonable"] == "pass"
    assert check_status["primary_system::fuel_inventory_positive"] == "pass"
    assert check_status["primary_system::active_channel_velocity_reasonable"] == "pass"
    assert check_status["primary_system::heat_exchanger_duty_closure_reasonable"] == "pass"


def test_temperature_dependent_property_models_are_supported() -> None:
    material_spec = {
        "density": {
            "units": "kg/m3",
            "model": "linear",
            "reference_value": 3200.0,
            "reference_temperature_c": 600.0,
            "slope_per_c": -0.8,
        },
        "dynamic_viscosity": {
            "units": "pa-s",
            "model": "arrhenius",
            "pre_exponential": 0.0025,
            "activation_temperature_k": 900.0,
        },
        "cp": {
            "units": "j/kg-k",
            "model": "linear",
            "reference_value": 1600.0,
            "reference_temperature_c": 600.0,
            "slope_per_c": 0.4,
        },
        "thermal_conductivity": {
            "units": "w/m-k",
            "model": "constant",
            "value": 1.05,
        },
    }

    properties = evaluate_fluid_properties(material_spec, temperature_c=650.0)

    assert properties["density_kg_m3"] == 3160.0
    assert properties["cp_j_kgk"] == 1620.0
    assert properties["thermal_conductivity_w_mk"] == 1.05
    assert properties["dynamic_viscosity_pa_s"] > 0.0


def test_reduced_order_default_allocation_rule_is_conservative() -> None:
    config = _load_case("immersed_pool_reference")
    config.data = deepcopy(config.data)
    config.data.setdefault("flow", {}).pop("reduced_order", None)
    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))

    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )

    assert reduced_order["allocation_rule"] == "salt_area_weighted"


def test_average_primary_temperature_uses_leg_temperatures() -> None:
    assert average_primary_temperature_c({"hot_leg_temp_c": 700.0, "cold_leg_temp_c": 560.0}) == 630.0


def test_modeled_property_reference_temperature_can_be_required() -> None:
    assert property_reference_temperature_c(
        {"property_reference_temperature_c": 600.0},
        {"model": "linear", "reference_temperature_c": 580.0},
        require_declared=True,
    ) == 580.0


def test_pressure_budget_includes_buoyancy_assist() -> None:
    config = _load_case("immersed_pool_reference")
    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )
    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    hydraulics = primary_system["loop_hydraulics"]

    assert hydraulics["net_resistive_pressure_kpa"] >= hydraulics["frictional_pressure_drop_kpa"]
    assert hydraulics["buoyancy_driving_pressure_kpa"] >= 0.0
    assert hydraulics["required_pump_pressure_kpa"] <= hydraulics["net_resistive_pressure_kpa"]
    assert hydraulics["hot_leg_rise_m"] >= 0.0
    assert hydraulics["cold_leg_drop_m"] >= 0.0


def test_primary_thermal_profile_tracks_component_temperatures() -> None:
    config = _load_case("immersed_pool_reference")
    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )
    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    segments = {segment["name"]: segment for segment in primary_system["thermal_profile"]["segments"]}

    assert segments["core_heating"]["outlet_temp_c"] > segments["core_heating"]["inlet_temp_c"]
    assert segments["heat_exchanger_rejection"]["outlet_temp_c"] < segments["heat_exchanger_rejection"]["inlet_temp_c"]
    assert segments["pump"]["outlet_temp_c"] == segments["pump"]["inlet_temp_c"]
    assert abs(primary_system["thermal_profile"]["loop_closure_error_c"]) < 1.0e-3


def test_primary_system_helper_correlations_are_stable() -> None:
    assert _darcy_friction_factor(1600.0) == pytest.approx(0.04, rel=1.0e-9)
    assert _darcy_friction_factor(10000.0) == pytest.approx(0.03164, rel=1.0e-9)
    assert _internal_nusselt_number(1200.0, 6.5) == pytest.approx(3.66, rel=1.0e-9)
    assert _internal_nusselt_number(10000.0, 6.5) == pytest.approx(77.071388, rel=1.0e-6)
    assert _log_mean_temperature_difference(140.0, 70.0) == pytest.approx(100.988653, rel=1.0e-6)


def test_primary_loop_uses_declared_component_graph() -> None:
    config = _load_case("immersed_pool_reference")
    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )
    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    loop_topology = primary_system["loop_topology"]
    edge_lookup = {edge["id"]: edge for edge in loop_topology["edges"]}

    assert loop_topology["start_component_id"] == "heat_exchanger"
    assert loop_topology["cycle_component_ids"] == ["heat_exchanger", "pump", "core", "heat_exchanger"]
    assert loop_topology["cycle_edge_ids"] == ["hx_to_pump", "pump_to_core", "core_to_hx"]
    assert loop_topology["cold_leg_edge_ids"] == ["hx_to_pump", "pump_to_core"]
    assert loop_topology["hot_leg_edge_ids"] == ["core_to_hx"]
    assert edge_lookup["pump_to_core"]["from"] == "pump"
    assert edge_lookup["pump_to_core"]["to"] == "core"


def test_primary_loop_graph_is_independent_of_pipe_list_order() -> None:
    config = _load_case("immersed_pool_reference")
    config.data = deepcopy(config.data)
    pipe_runs = list(config.geometry["render_layout"]["primary_loop"]["pipes"])
    config.geometry["render_layout"]["primary_loop"]["pipes"] = [pipe_runs[1], pipe_runs[2], pipe_runs[0]]

    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )
    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    loop_topology = primary_system["loop_topology"]
    thermal_segment_names = [segment["name"] for segment in primary_system["thermal_profile"]["segments"]]

    assert loop_topology["cycle_edge_ids"] == ["hx_to_pump", "pump_to_core", "core_to_hx"]
    assert thermal_segment_names[:6] == [
        "hx_to_pump",
        "pump",
        "pump_to_core",
        "core_heating",
        "core_to_hx",
        "heat_exchanger_rejection",
    ]


def test_branch_flow_split_favors_lower_resistance_path() -> None:
    config = _load_case("immersed_pool_reference")
    config.data = deepcopy(config.data)
    primary_loop = config.geometry["render_layout"]["primary_loop"]
    primary_loop["components"].append({"id": "mix_header", "kind": "junction"})
    primary_loop["connections"] = [
        {"id": "hx_to_pump", "from": "heat_exchanger", "to": "pump", "leg": "cold_leg"},
        {"id": "pump_to_core_a", "from": "pump", "to": "mix_header", "leg": "cold_leg"},
        {"id": "pump_to_core_b", "from": "pump", "to": "mix_header", "leg": "cold_leg"},
        {"id": "mix_to_core", "from": "mix_header", "to": "core", "leg": "cold_leg"},
        {"id": "core_to_hx", "from": "core", "to": "heat_exchanger", "leg": "hot_leg"},
    ]
    primary_loop["pipes"] = [
        pipe for pipe in primary_loop["pipes"] if pipe["id"] in {"hx_to_pump", "core_to_hx"}
    ] + [
        {
            "id": "pump_to_core_a",
            "radius_cm": 2.1,
            "material": "pipe",
            "points": [
                [-34.0, -8.0, 34.0],
                [-8.0, -8.0, 34.0],
                [4.0, -8.0, 30.0],
            ],
        },
        {
            "id": "pump_to_core_b",
            "radius_cm": 1.3,
            "material": "pipe",
            "points": [
                [-34.0, -8.0, 34.0],
                [-20.0, -8.0, 42.0],
                [0.0, -8.0, 42.0],
                [4.0, -8.0, 30.0],
            ],
        },
        {
            "id": "mix_to_core",
            "radius_cm": 2.0,
            "material": "pipe",
            "points": [
                [4.0, -8.0, 30.0],
                [16.0, -8.0, 30.0],
                [16.0, -8.0, 26.0],
            ],
        },
    ]

    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )
    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    branch_groups = primary_system["branch_flows"]["branch_groups"]
    assert len(branch_groups) == 1
    branch_summary = {branch["branch_id"]: branch for branch in branch_groups[0]["branches"]}
    thermal_segments = {segment["name"]: segment for segment in primary_system["thermal_profile"]["segments"]}

    assert branch_summary["pump_to_core_a"]["flow_fraction"] > branch_summary["pump_to_core_b"]["flow_fraction"]
    assert branch_summary["pump_to_core_a"]["volumetric_flow_m3_s"] > branch_summary["pump_to_core_b"]["volumetric_flow_m3_s"]
    assert branch_summary["pump_to_core_a"]["path_pressure_drop_kpa"] == pytest.approx(
        branch_summary["pump_to_core_b"]["path_pressure_drop_kpa"],
        rel=1.0e-3,
    )
    assert "mix_mix_header" in thermal_segments
    assert thermal_segments["mix_mix_header"]["kind"] == "mix"


def test_heat_exchanger_summary_uses_branch_resolved_hot_leg_inlets() -> None:
    config = _load_case("immersed_pool_reference")
    config.data = deepcopy(config.data)
    primary_loop = config.geometry["render_layout"]["primary_loop"]
    primary_loop["components"].append({"id": "splitter", "kind": "junction"})
    primary_loop["connections"] = [
        {"id": "hx_to_pump", "from": "heat_exchanger", "to": "pump", "leg": "cold_leg"},
        {"id": "pump_to_core", "from": "pump", "to": "core", "leg": "cold_leg"},
        {"id": "core_to_hx_a", "from": "core", "to": "heat_exchanger", "leg": "hot_leg", "heat_exchanger_area_fraction": 0.7},
        {"id": "core_to_hx_b", "from": "core", "to": "heat_exchanger", "leg": "hot_leg", "heat_exchanger_area_fraction": 0.3},
    ]
    primary_loop["pipes"] = [
        pipe for pipe in primary_loop["pipes"] if pipe["id"] == "hx_to_pump"
    ] + [
        {
            "id": "pump_to_core",
            "radius_cm": 2.0,
            "material": "pipe",
            "points": [
                [-34.0, -8.0, 34.0],
                [2.0, -8.0, 34.0],
                [16.0, -8.0, 34.0],
                [16.0, -8.0, 26.0],
            ],
        },
        {
            "id": "core_to_hx_a",
            "radius_cm": 2.1,
            "material": "pipe",
            "points": [
                [22.0, -12.0, -8.0],
                [0.0, -12.0, -8.0],
                [-24.0, -14.0, 8.0],
                [-24.0, -14.0, 16.0],
            ],
        },
        {
            "id": "core_to_hx_b",
            "radius_cm": 1.3,
            "material": "pipe",
            "points": [
                [22.0, -12.0, -8.0],
                [10.0, -12.0, 6.0],
                [-10.0, -14.0, 12.0],
                [-24.0, -14.0, 16.0],
            ],
        },
    ]

    built = build_case(config)
    bop = run_steady_state_bop(_primary_bop_inputs(config))
    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )
    primary_system = build_primary_system_summary(
        config,
        built.geometry_description,
        reduced_order,
        bop.to_dict(),
    )

    hx = primary_system["heat_exchanger"]
    branches = hx["primary_inlet_branches"]

    assert len(branches) == 2
    weighted_temp_c = sum(branch["mass_flow_kg_s"] * branch["inlet_temp_c"] for branch in branches) / sum(
        branch["mass_flow_kg_s"] for branch in branches
    )
    assert hx["primary_inlet_mixed_temp_c"] == round(weighted_temp_c, 6)
    assert sum(branch["duty_share_mw"] for branch in branches) == pytest.approx(hx["duty_mw"], rel=1.0e-6)
    assert sum(branch["duty_fraction"] for branch in branches) == pytest.approx(1.0, rel=1.0e-6)
    assert all(branch["local_hot_side_entry_h_w_m2k"] > 0.0 for branch in branches)
    assert hx["primary_hx_side"]["branch_modeling_mode"] == "explicit_area_fraction"
    assert all(branch["hx_area_fraction"] is not None for branch in branches)
    assert all(branch["hx_local_h_w_m2k"] is not None and branch["hx_local_h_w_m2k"] > 0.0 for branch in branches)
    assert branches[0]["hx_local_velocity_m_s"] != branches[1]["hx_local_velocity_m_s"]
    weighted_branch_h = sum(branch["duty_fraction"] * branch["hx_local_h_w_m2k"] for branch in branches)
    assert hx["primary_hx_side"]["branch_weighted_heat_transfer_coefficient_w_m2k"] == pytest.approx(
        weighted_branch_h,
        rel=1.0e-6,
    )
