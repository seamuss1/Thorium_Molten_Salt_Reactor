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


def test_msre_benchmark_case_uses_historic_mode_and_resolves_benchmark() -> None:
    config = _load_case("msre_first_criticality")

    assert config.reactor["mode"] == "historic_benchmark"
    assert config.benchmark_file is not None
    assert config.benchmark_file.exists()


def test_flagship_case_declares_commercial_grid_characteristics() -> None:
    config = _load_case("flagship_grid_msr")

    assert config.reactor["mode"] == "commercial_grid"
    assert config.reactor["characteristics"]["net_electric_power_mwe"] == 300.0
    assert config.geometry["render_layout"]["type"] == "plant_schematic"
    assert config.flow["core_model"]["kind"] == "homogenized_core"
    assert config.geometry["render_layout"]["primary_loop"]["pipes"]
    assert config.economics["default_scenario"] == "conservative_foak"
    assert str(config.project_schedule["project_start"]) == "2026-05-02"


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
    assert built.manifest["validation_maturity"]["validation_maturity_score"] >= 40.0
    assert built.manifest["model_representation"] == {
        "materials": "isotopic_explicit",
        "fuel_cycle": "proxy_breeding",
    }
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
        "physics::primary_delta_t_reasonable": True,
        "physics::active_channel_velocity_reasonable": True,
        "physics::loop_pipe_velocity_reasonable": True,
        "physics::pool_circulation_velocity_reasonable": True,
    }


def test_flagship_case_builds_with_full_plant_schematic_layout() -> None:
    config = _load_case("flagship_grid_msr")
    built = build_case(config)

    assert built.geometry_description["render_layout"] == "plant_schematic"
    assert built.geometry_description["plant_system"]["type"] == "plant_schematic"
    assert built.geometry_description["plant_system"]["design_basis"]["net_electric_power_mwe"] == 300.0
    assert len(built.geometry_description["plant_system"]["components"]) >= 12
    network_ids = {network["id"] for network in built.geometry_description["plant_system"]["networks"]}
    assert {"primary_loop", "secondary_loop", "offgas_system", "drain_system", "power_conversion", "grid_interface"} <= network_ids
    solid_names = {solid["name"] for solid in built.geometry_description["render_solids"]}
    assert "plant_primary_heat_exchanger" in solid_names
    assert "plant_turbine" in solid_names
    assert any(name.startswith("primary_loop_core_to_hx") for name in solid_names)
    animation_path_names = {path["name"] for path in built.geometry_description["animation"]["paths"]}
    assert {"core_upflow", "core_to_hx", "steam_to_turbine", "reactor_to_freeze_drain_tank"} <= animation_path_names


def test_isotopically_explicit_thorium_case_requires_th232_in_fuel_salt() -> None:
    config = _load_case("tmsr_lf1_core")
    config.data = yaml.safe_load(yaml.safe_dump(config.data, sort_keys=False))
    config.data["materials"]["fuel_salt"]["nuclides"] = [
        nuclide
        for nuclide in config.data["materials"]["fuel_salt"]["nuclides"]
        if nuclide["name"] != "Th232"
    ]

    built = build_case(config)

    thorium_check = next(
        item
        for item in built.manifest["invariants"]
        if item["name"] == "model_representation::fuel_salt_contains_thorium"
    )
    assert thorium_check["passed"] is False


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


def test_case_loader_rejects_invalid_delayed_neutron_group() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml").read_text(encoding="utf-8"))
        payload["transient"]["delayed_neutron_precursor_groups"] = [
            {"name": "bad", "decay_constant_s": -0.1, "yield_fraction": 0.001}
        ]
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="decay_constant_s"):
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


def test_case_loader_rejects_invalid_commercial_grid_characteristics() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-commercial-grid-config" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml").read_text(encoding="utf-8"))
        payload["reactor"]["characteristics"].pop("net_electric_power_mwe")
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="net_electric_power_mwe"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_case_loader_rejects_invalid_economics_default_scenario() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-economics-config" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml").read_text(encoding="utf-8"))
        payload["economics"]["default_scenario"] = "missing"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="default_scenario"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_case_loader_rejects_invalid_project_schedule_date() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-project-schedule-config" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml").read_text(encoding="utf-8"))
        payload["project_schedule"]["project_start"] = "soon"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="project_schedule.project_start"):
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


def test_case_loader_rejects_unsupported_property_provider() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-config-and-build" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        payload = yaml.safe_load((REPO_ROOT / "configs" / "cases" / "msre_first_criticality" / "case.yaml").read_text(encoding="utf-8"))
        payload.setdefault("properties", {})["provider"] = "mystery_provider"
        case_path = scratch_root / "case.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

        with pytest.raises(ConfigError, match="properties.provider"):
            load_case_config(case_path)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
