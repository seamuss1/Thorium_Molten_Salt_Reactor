from pathlib import Path
import shutil
import uuid

import pytest
import yaml

from thorium_reactor.config import ConfigError, load_case_config
from thorium_reactor.neutronics.workflows import build_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_case(name: str):
    return load_case_config(REPO_ROOT / "configs" / "cases" / name / "case.yaml")


def test_benchmark_paths_resolve_from_case_configs() -> None:
    config = _load_case("tmsr_lf1_core")

    assert config.benchmark_file is not None
    assert config.benchmark_file.exists()


def test_core_case_manifest_has_expected_channel_count() -> None:
    config = _load_case("tmsr_lf1_core")
    built = build_case(config)

    assert built.manifest["channel_count"] == 91
    assert built.manifest["cell_count"] == 456
    assert built.manifest["channel_variant_counts"] == {
        "fuel": 79,
        "control_guides": 6,
        "instrumentation_wells": 6,
    }
    assert built.manifest["geometry_kind"] == "ring_lattice_core"
    assert built.geometry_description["type"] == "detailed_molten_salt_reactor"
    assert built.manifest["benchmark_traceability"]["traceability_score"] >= 80.0
    assert built.manifest["benchmark_traceability"]["maturity_stage"] == "traceable_surrogate"
    assert built.manifest["simulation"] == {
        "mode": "eigenvalue",
        "particles": 100000,
        "batches": 120,
        "inactive": 20,
        "active_batches": 100,
        "source": {
            "type": "point",
            "parameters": [0.0, 0.0, 0.0],
        },
        "tallies": [
            {
                "name": "fuel_reaction_rates",
                "cell": "fuel",
                "scores": ["total", "fission", "absorption", "(n,gamma)"],
                "nuclides": ["U233"],
            },
            {
                "name": "core_flux",
                "cell": "core_matrix",
                "scores": ["flux"],
                "nuclides": [],
            },
        ],
        "geometry_boundary": "reflective",
        "axial_boundary": "vacuum",
    }


def test_core_case_flow_summary_exposes_plenum_access_split() -> None:
    config = _load_case("tmsr_lf1_core")
    built = build_case(config)
    flow_summary = built.manifest["flow_summary"]

    assert flow_summary["interface_metrics"] == {
        "plenum_connected_channels": 37,
        "reflector_backed_channels": 54,
        "plenum_connected_salt_bearing_channels": 37,
        "reflector_backed_salt_bearing_channels": 48,
        "plenum_connected_salt_area_cm2": 9.813587,
        "reflector_backed_salt_area_cm2": 13.50382,
        "plenum_connected_salt_volume_cm3": 1884.208625,
        "reflector_backed_salt_volume_cm3": 2592.733508,
    }
    assert flow_summary["variant_counts"] == {
        "plenum_connected": {
            "control_guides": 6,
            "fuel": 31,
        },
        "reflector_backed": {
            "fuel": 48,
            "instrumentation_wells": 6,
        },
    }
    first_channel = built.geometry_description["channels"][0]
    assert first_channel["lower_boundary_region"] == "lower_plenum"
    assert first_channel["upper_boundary_region"] == "upper_plenum"
    assert first_channel["interface_class"] == "plenum_connected"


def test_immersed_pool_reference_case_builds_with_reference_render_layout() -> None:
    config = _load_case("immersed_pool_reference")
    built = build_case(config)

    assert built.manifest["channel_count"] == 61
    assert built.manifest["cell_count"] == 306
    assert built.manifest["channel_variant_counts"] == {
        "fuel": 49,
        "control_guides": 6,
        "instrumentation_wells": 6,
    }
    assert built.geometry_description["render_layout"] == "immersed_pool_reference"
    physics = built.geometry_description["animation"]["physics"]
    assert physics["primary_mass_flow_kg_s"] == pytest.approx(37.319852, rel=1.0e-6)
    assert physics["active_flow_area_cm2"] == pytest.approx(11.236156, rel=1.0e-6)
    assert 1.0 <= physics["active_channel_velocity_m_s"] <= 12.0
    assert 1.0 <= physics["loop_pipe_velocity_m_s"] <= 10.0
    assert 0.02 <= physics["pool_circulation_velocity_m_s"] <= 1.5
    assert any(solid.get("type") == "box" for solid in built.geometry_description["render_solids"])
    assert any(solid.get("axis") == "x" for solid in built.geometry_description["render_solids"])
    path_materials = {
        path["name"]: path["material"]
        for path in built.geometry_description["animation"]["paths"]
    }
    assert path_materials["primary_hot_leg"] == "fuel_salt"
    assert path_materials["primary_cold_leg"] == "fuel_salt"
    layout_checks = {
        item["name"]: item["passed"]
        for item in built.manifest["invariants"]
        if item["name"].startswith("render_layout::")
    }
    assert layout_checks == {
        "render_layout::containment_encloses_pool": True,
        "render_layout::core_box_inside_pool": True,
        "render_layout::core_barrel_inside_cavity": True,
        "render_layout::primary_loop_inside_pool": True,
        "render_layout::primary_loop_submerged": True,
    }
    physics_checks = {
        item["name"]: item["passed"]
        for item in built.manifest["invariants"]
        if item["name"].startswith("physics::")
    }
    assert physics_checks == {
        "physics::delta_t_reasonable": True,
        "physics::active_channel_velocity_reasonable": True,
        "physics::loop_pipe_velocity_reasonable": True,
        "physics::pool_circulation_velocity_reasonable": True,
    }


def test_example_pin_case_builds_without_solver() -> None:
    config = _load_case("example_pin")
    built = build_case(config)

    assert built.manifest["cell_count"] == 4
    assert built.geometry_description["type"] == "pin"
    assert built.manifest["simulation"]["axial_boundary"] is None


def test_case_loader_rejects_unsupported_material_units() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml").read_text(encoding="utf-8"))
        payload["materials"]["uo2"]["density"]["units"] = "lb/ft3"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="unsupported units"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_case_loader_rejects_invalid_openmc_batch_configuration() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml").read_text(encoding="utf-8"))
        payload["simulation"]["inactive"] = payload["simulation"]["batches"]
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="simulation.inactive"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_case_loader_rejects_non_numeric_transient_duration() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml").read_text(encoding="utf-8"))
        payload["transient"]["duration_s"] = "fast"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="transient.duration_s"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_case_loader_rejects_non_list_integration_args() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml").read_text(encoding="utf-8"))
        payload.setdefault("integrations", {}).setdefault("moose", {})["args"] = "--fast"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="integrations.moose.args"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_case_loader_rejects_non_numeric_chemistry_field() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml").read_text(encoding="utf-8"))
        payload["chemistry"]["target_redox_state_ev"] = "oxidizing"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="chemistry.target_redox_state_ev"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
