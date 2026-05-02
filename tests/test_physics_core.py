import shutil
import uuid
from pathlib import Path

import pytest

from thorium_reactor.config import ConfigError, load_case_config
from thorium_reactor.neutronics.workflows import run_case
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.physics_core import (
    FINITE_VOLUME_PRECURSOR_MODEL,
    FINITE_VOLUME_TH_MODEL,
    build_physics_core_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_run_case_writes_coupled_physics_core_artifact() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-physics-core" / uuid.uuid4().hex
    try:
        config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
        bundle = create_result_bundle(scratch_root, config.name, "physics-core")

        summary = run_case(config, bundle, solver_enabled=False)

        physics_core = summary["physics_core"]
        assert physics_core["status"] == "completed"
        assert physics_core["integrity_checks"]["status"] == "ok"
        assert physics_core["neutronics"]["group_count"] == 11
        assert physics_core["neutronics"]["methods"] == ["diffusion", "sp3", "transport"]
        assert physics_core["neutronics"]["cross_sections"]["interpolation"] == "linear_temperature_dependence_between_declared_grid_points"
        assert physics_core["neutronics"]["k_eff"] > 0.0
        assert physics_core["neutronics"]["beta_eff"] > 0.0
        assert physics_core["neutronics"]["feedback_coefficients"]["fuel_temperature_pcm_per_c"] < 0.0
        assert len(physics_core["neutronics"]["power_shape"]) == physics_core["thermal_hydraulics"]["axial_node_count"]
        assert len(physics_core["neutronics"]["adjoint_weighted_importance"]) == physics_core["thermal_hydraulics"]["axial_node_count"]
        assert physics_core["thermal_hydraulics"]["model"] == FINITE_VOLUME_TH_MODEL
        assert physics_core["thermal_hydraulics"]["porous_core_model"]["bulk_porosity"] > 0.0
        assert "pump_curve" in physics_core["thermal_hydraulics"]
        assert "natural_circulation_flow_m3_s" in physics_core["thermal_hydraulics"]["momentum_balance"]
        assert physics_core["precursor_transport"]["model"] == FINITE_VOLUME_PRECURSOR_MODEL
        assert physics_core["precursor_transport"]["group_count"] == 6
        assert physics_core["precursor_transport"]["cell_count"] > physics_core["thermal_hydraulics"]["axial_node_count"]
        assert 0.0 < physics_core["precursor_transport"]["transport_loss_fraction"] < 1.0
        assert 0.0 < physics_core["precursor_transport"]["decay_heat_precursors"]["core_decay_heat_source_fraction"] < 1.0
        assert (bundle.root / "physics_core.json").exists()
        assert summary["metrics"]["physics_core_k_eff"] == physics_core["neutronics"]["k_eff"]
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_physics_core_honors_configured_method_and_mesh_counts() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    config.data["physics_core"] = {
        "neutronics": {
            "group_count": 7,
            "deterministic_methods": ["transport"],
            "temperature_grid_c": [500.0, 625.0, 750.0],
        },
        "thermal_hydraulics": {"axial_nodes": 8},
        "precursor_transport": {"loop_cells": 3, "diffusion_coefficient_m2_s": 1.0e-5},
    }
    summary = {
        "bop": {"thermal_power_mw": 8.0, "primary_mass_flow_kg_s": 37.0, "primary_cp_kj_kgk": 1.6},
        "flow": {
            "reduced_order": {
                "salt_density_kg_m3": 3100.0,
                "salt_properties": {"dynamic_viscosity_pa_s": 0.006, "thermal_conductivity_w_mk": 1.0},
                "active_flow": {
                    "total_flow_area_cm2": 12.0,
                    "total_salt_volume_cm3": 90000.0,
                    "hydraulic_diameter_cm": 2.5,
                },
            }
        },
        "primary_system": {
            "thermal_profile": {"estimated_hot_leg_temp_c": 690.0, "estimated_cold_leg_temp_c": 555.0},
            "loop_hydraulics": {"pump_head_m": 6.0, "buoyancy_driving_pressure_kpa": 1.2},
        },
        "metrics": {},
        "neutronics": {"status": "dry-run"},
    }

    physics_core = build_physics_core_summary(config, summary)

    assert physics_core["neutronics"]["methods"] == ["transport"]
    assert physics_core["neutronics"]["group_count"] == 7
    assert physics_core["thermal_hydraulics"]["axial_node_count"] == 8
    assert physics_core["precursor_transport"]["loop_cell_count"] == 3


def test_physics_core_uses_defaults_for_null_optional_salt_properties() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "tmsr_lf1_core" / "case.yaml")
    summary = {
        "bop": {"thermal_power_mw": 250.0, "primary_mass_flow_kg_s": 1200.0, "primary_cp_kj_kgk": 1.6},
        "flow": {
            "reduced_order": {
                "salt_density_kg_m3": 3200.0,
                "salt_properties": {"dynamic_viscosity_pa_s": None, "thermal_conductivity_w_mk": None},
                "active_flow": {
                    "total_flow_area_cm2": 1800.0,
                    "total_salt_volume_cm3": 250000.0,
                    "hydraulic_diameter_cm": 8.0,
                },
            }
        },
        "primary_system": {
            "thermal_profile": {"estimated_hot_leg_temp_c": 704.0, "estimated_cold_leg_temp_c": 566.0},
            "loop_hydraulics": {"pump_head_m": 8.0, "buoyancy_driving_pressure_kpa": 1.0},
        },
        "metrics": {},
        "neutronics": {"status": "dry-run"},
    }

    physics_core = build_physics_core_summary(config, summary)

    assert physics_core["thermal_hydraulics"]["fluid_properties"]["dynamic_viscosity_pa_s"] == 0.006
    assert physics_core["thermal_hydraulics"]["fluid_properties"]["thermal_conductivity_w_mk"] == 1.0
    assert physics_core["integrity_checks"]["status"] == "ok"


def test_case_loader_rejects_invalid_physics_core_method(tmp_path: Path) -> None:
    source = REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml"
    case_dir = tmp_path / "configs" / "cases" / "bad"
    case_dir.mkdir(parents=True)
    payload = source.read_text(encoding="utf-8")
    payload += "\nphysics_core:\n  neutronics:\n    deterministic_methods:\n      - bad_method\n"
    case_path = case_dir / "case.yaml"
    case_path.write_text(payload, encoding="utf-8")

    with pytest.raises(ConfigError, match="bad_method"):
        load_case_config(case_path)
