from pathlib import Path

from thorium_reactor.bop.steady_state import BOPInputs, run_steady_state_bop
from thorium_reactor.config import load_case_config
from thorium_reactor.flow.reduced_order import build_reduced_order_flow_summary
from thorium_reactor.neutronics.workflows import build_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_case(name: str):
    return load_case_config(REPO_ROOT / "configs" / "cases" / name / "case.yaml")


def test_reduced_order_flow_uses_only_plenum_connected_salt_channels() -> None:
    config = _load_case("tmsr_lf1_core")
    built = build_case(config)
    bop = run_steady_state_bop(
        BOPInputs(
            thermal_power_mw=float(config.reactor["design_power_mwth"]),
            hot_leg_temp_c=float(config.reactor["hot_leg_temp_c"]),
            cold_leg_temp_c=float(config.reactor["cold_leg_temp_c"]),
            primary_cp_kj_kgk=float(config.reactor["primary_cp_kj_kgk"]),
            steam_generator_effectiveness=float(config.reactor["steam_generator_effectiveness"]),
            turbine_efficiency=float(config.reactor["turbine_efficiency"]),
            generator_efficiency=float(config.reactor["generator_efficiency"]),
        )
    )

    reduced_order = build_reduced_order_flow_summary(
        config,
        built.manifest["flow_summary"],
        bop.primary_mass_flow_kg_s,
    )

    assert reduced_order["allocation_rule"] == "salt_area_weighted"
    assert reduced_order["active_channel_selection"] == "plenum_connected_salt_bearing_channels"
    assert reduced_order["disconnected_inventory_selection"] == "reflector_backed_salt_bearing_channels"
    assert reduced_order["primary_mass_flow_kg_s"] == 1116.071429
    assert reduced_order["active_flow"] == {
        "channel_count": 37,
        "variant_counts": {
            "control_guides": 6,
            "fuel": 31,
        },
        "total_flow_area_cm2": 9.813587,
        "total_salt_volume_cm3": 1884.208625,
        "total_volumetric_flow_m3_s": 0.348772,
        "representative_velocity_m_s": 355.397406,
        "representative_residence_time_s": 0.005402,
    }
    assert reduced_order["disconnected_inventory"] == {
        "channel_count": 48,
        "variant_counts": {
            "fuel": 48,
        },
        "salt_area_cm2": 13.50382,
        "salt_volume_cm3": 2592.733508,
    }

    variant_summary = {item["variant"]: item for item in reduced_order["variant_summary"]}
    assert variant_summary["fuel"]["allocated_mass_flow_kg_s"] == 991.839362
    assert variant_summary["control_guides"]["allocated_mass_flow_kg_s"] == 124.232066

    center_channel = next(channel for channel in reduced_order["active_channels"] if channel["name"] == "fuel_0.00_0")
    control_channel = next(
        channel
        for channel in reduced_order["active_channels"]
        if channel["name"] == "control_guides_18.06_0"
    )
    assert center_channel["allocated_mass_flow_kg_s"] == 31.994818
    assert center_channel["velocity_m_s"] == 355.397406
    assert center_channel["residence_time_s"] == 0.005402
    assert control_channel["allocated_mass_flow_kg_s"] == 20.705344
    assert control_channel["velocity_m_s"] == 355.397406
    assert control_channel["residence_time_s"] == 0.005402
