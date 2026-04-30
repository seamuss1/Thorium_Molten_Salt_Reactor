from pathlib import Path

from thorium_reactor.config import load_case_config
from thorium_reactor.literature_models import (
    build_graphite_lifetime_summary,
    build_property_uncertainty_summary,
    build_tritium_transport_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_property_uncertainty_defaults_reflect_tmsr_sf0_bands() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")

    summary = build_property_uncertainty_summary(config, primary_delta_t_c=20.0)

    assert summary["density_uncertainty_95_fraction"] == 0.02
    assert summary["cp_uncertainty_95_fraction"] == 0.10
    assert summary["dynamic_viscosity_uncertainty_95_fraction"] == 0.10
    assert summary["core_outlet_temperature_uncertainty_95_c"] == 10.0


def test_tritium_screen_credits_gas_removal_and_reports_distribution() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")

    summary = build_tritium_transport_summary(
        config,
        thermal_power_mw=8.0,
        fuel_salt_volume_m3=0.1,
        chemistry_summary={"gas_stripping_efficiency": 0.88},
    )

    fractions = (
        summary["environmental_release_fraction"]
        + summary["removal_fraction"]
        + summary["graphite_retention_fraction"]
        + summary["circulating_inventory_fraction"]
    )
    assert fractions == 1.0
    assert summary["removal_fraction"] > summary["environmental_release_fraction"]
    assert summary["control_effect"] in {"moderate", "strong"}


def test_graphite_lifetime_screen_reports_fast_flux_margin() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    reduced_order_flow = {
        "active_flow": {
            "total_salt_volume_cm3": 7600.0,
            "variant_counts": {"fuel": 49, "control_guides": 6},
        }
    }

    summary = build_graphite_lifetime_summary(
        config,
        reduced_order_flow=reduced_order_flow,
        thermal_power_mw=8.0,
    )

    assert summary["fast_fluence_limit_n_cm2"] == 3.0e22
    assert summary["fast_flux_peaking_factor"] > 0.0
    assert summary["estimated_lifespan_years"] > 0.0
    assert summary["screening_status"] in {"pass", "watch"}
