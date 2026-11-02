import math
from copy import deepcopy
from pathlib import Path

import pytest

from thorium_reactor.config import load_case_config
from thorium_reactor.literature_models import (
    build_graphite_lifetime_summary,
    build_msre_pump_transient_benchmark_screen,
    build_property_uncertainty_summary,
    build_tritium_transport_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_turbulent_heat_transfer_screen_matches_correlation_perturbations() -> None:
    config = load_case_config(REPO_ROOT / "configs/cases/immersed_pool_reference/case.yaml")
    config.data = deepcopy(config.data)
    config.data["property_uncertainty"] = {"heat_transfer_model": "dittus_boelter_fixed_mass_flux"}
    summary = build_property_uncertainty_summary(config)

    def coefficient(values):
        # Independently evaluate dimensional Re, Pr, Nu and h at fixed G and D.
        cp, conductivity, viscosity = values
        reynolds = 10000.0 * 0.05 / viscosity
        prandtl = cp * viscosity / conductivity
        return 0.023 * reynolds**0.8 * prandtl**0.4 * conductivity / 0.05

    base = [1800.0, 1.0, 0.003]
    for index, key in enumerate(("cp", "thermal_conductivity", "dynamic_viscosity")):
        perturbed = base.copy()
        perturbed[index] *= 1.0001
        derivative = math.log(coefficient(perturbed) / coefficient(base)) / math.log(1.0001)
        assert summary["heat_transfer_log_sensitivities"][key] == pytest.approx(derivative)
    assert summary["heat_transfer_uncertainty_95_fraction"] == pytest.approx(math.sqrt(0.0068), abs=1e-6)
    assert "Flow regime is not checked" in summary["heat_transfer_applicability"]
    from thorium_reactor.transient_sweep import _resolve_uncertainty_model

    # Integration retains the existing floor and explicit ensemble precedence.
    assert _resolve_uncertainty_model({}, property_uncertainty=summary)["heat_sink_sigma_fraction"] == 0.09
    config.data["property_uncertainty"]["thermal_conductivity_uncertainty_95_fraction"] = 0.4
    larger = build_property_uncertainty_summary(config)
    assert _resolve_uncertainty_model({}, property_uncertainty=larger)["heat_sink_sigma_fraction"] == pytest.approx(
        larger["heat_transfer_uncertainty_95_fraction"] / 1.96
    )
    override = {"ensemble": {"uncertainties": {"heat_sink_sigma_fraction": 0.2}}}
    assert _resolve_uncertainty_model(override, property_uncertainty=larger)["heat_sink_sigma_fraction"] == 0.2


@pytest.mark.parametrize(
    "range_status, uncertainty",
    [
        ("out_of_range", 0.001),
        ("unknown", 0.001),
        ("in_range", float("nan")),
        ("in_range", -0.01),
        ("in_range", "bad"),
    ],
)
def test_inapplicable_source_uncertainty_falls_back_visibly(monkeypatch, range_status, uncertainty) -> None:
    config = load_case_config(REPO_ROOT / "configs/cases/immersed_pool_reference/case.yaml")
    config.data = deepcopy(config.data)
    config.data["property_uncertainty"] = {}
    monkeypatch.setattr(
        "thorium_reactor.literature_models.evaluate_primary_coolant_properties",
        lambda *a, **kw: {
            "source_backing": "source_backed",
            "property_sources": {"density": {"range_status": range_status, "uncertainty_95_fraction": uncertainty}},
        },
    )
    summary = build_property_uncertainty_summary(config)
    assert summary["density_uncertainty_95_fraction"] == 0.02
    assert summary["basis_by_property"]["density"] == "default_band"
    assert "density" in summary["property_source_uncertainty_exclusions"]
    # An explicit assumption remains available even when source metadata is excluded.
    config.data["property_uncertainty"]["density_uncertainty_95_fraction"] = 0.07
    assert build_property_uncertainty_summary(config)["density_uncertainty_95_fraction"] == 0.07


def test_property_uncertainty_model_rejects_unknown_selector() -> None:
    from thorium_reactor.config import ConfigError, _validate_optional_property_uncertainty_settings

    with pytest.raises(ConfigError, match="heat_transfer_model"):
        _validate_optional_property_uncertainty_settings(Path("case.yaml"), {"heat_transfer_model": "typo"})


def test_property_uncertainty_defaults_reflect_tmsr_sf0_bands() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")

    summary = build_property_uncertainty_summary(config, primary_delta_t_c=20.0)

    assert summary["density_uncertainty_95_fraction"] == 0.02
    assert summary["cp_uncertainty_95_fraction"] == 0.10
    assert summary["dynamic_viscosity_uncertainty_95_fraction"] == 0.10
    assert summary["core_outlet_temperature_uncertainty_95_c"] == 10.0


def test_property_uncertainty_uses_msd_tp_source_metadata_when_not_overridden(synthetic_msd_tp_data_dir: str) -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml")
    config.data = deepcopy(config.data)
    config.materials["fuel_salt"]["density"] = {
        "provider": "msd_tp",
        "units": "g/cm3",
        "formula": "A-B",
        "composition": [0.25, 0.75],
        "data_dir": synthetic_msd_tp_data_dir,
    }

    summary = build_property_uncertainty_summary(config, primary_delta_t_c=140.0)

    assert summary["density_uncertainty_95_fraction"] == 0.05
    assert summary["cp_uncertainty_95_fraction"] == 0.10
    assert summary["basis_by_property"]["density"] == "source_metadata"
    assert summary["basis_by_property"]["cp"] == "default_band"
    assert summary["property_source_backing"] == "partial"
    density_source = summary["property_source_applicability"]["density"]
    assert density_source["record_id"] == "101"
    assert density_source["range_status"] == "in_range"


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


def test_msre_pump_transient_screen_reports_1d_validation_bounds() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    reduced_order_flow = {
        "active_flow": {
            "channel_count": 49,
            "total_salt_volume_cm3": 9000.0,
        },
        "disconnected_inventory": {
            "channel_count": 6,
            "salt_volume_cm3": 1200.0,
        },
        "stagnant_inventory": {
            "channel_count": 2,
            "salt_volume_cm3": 300.0,
        },
    }

    summary = build_msre_pump_transient_benchmark_screen(
        config,
        reduced_order_flow=reduced_order_flow,
    )

    assert summary["benchmark_mean_error_startup_pcm"]["max"] == 21.0
    assert summary["benchmark_mean_error_coastdown_pcm"]["min"] == 5.0
    assert summary["non_active_salt_inventory_fraction"] == 0.117647
    assert summary["stagnant_salt_inventory_fraction"] == 0.029412
    assert summary["screening_status"] == "watch"
    assert "bypass_flow" in summary["sensitivity_drivers"]
