import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from thorium_reactor.capabilities import (
    BALANCE_OF_PLANT,
    MSR_PRIMARY_SYSTEM,
    NEUTRONICS_ONLY,
    THERMAL_NETWORK,
    TRANSIENT_ANALYSIS,
    CapabilityConfigurationError,
    get_case_capabilities,
)
from thorium_reactor.cli import main
from thorium_reactor.config import load_case_config
from thorium_reactor.neutronics.workflows import run_case
from thorium_reactor.paths import create_result_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_case(name: str):
    return load_case_config(REPO_ROOT / "configs" / "cases" / name / "case.yaml")


def test_example_pin_run_no_solver_succeeds_end_to_end() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-example-pin-run" / uuid.uuid4().hex
    case_dir = scratch_root / "configs" / "cases" / "example_pin"
    benchmark_dir = scratch_root / "benchmarks" / "tmsr_lf1"
    case_dir.mkdir(parents=True, exist_ok=True)
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml", case_dir / "case.yaml")
        shutil.copy2(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", benchmark_dir / "benchmark.yaml")

        exit_code = main(["--repo-root", str(scratch_root), "run", "example_pin", "--run-id", "smoke", "--no-solver"])

        assert exit_code == 0
        summary = json.loads((scratch_root / "results" / "example_pin" / "smoke" / "summary.json").read_text(encoding="utf-8"))
        validation = json.loads((scratch_root / "results" / "example_pin" / "smoke" / "validation.json").read_text(encoding="utf-8"))
        runtime_context = json.loads((scratch_root / "results" / "example_pin" / "smoke" / "runtime_context.json").read_text(encoding="utf-8"))
        state_store = json.loads((scratch_root / "results" / "example_pin" / "smoke" / "state_store.json").read_text(encoding="utf-8"))
        assert summary["neutronics"]["status"] == "dry-run"
        assert summary["neutronics"]["message"] == "Solver execution was disabled for this run."
        assert summary["workflow_capabilities"] == [NEUTRONICS_ONLY]
        assert (scratch_root / "results" / "example_pin" / "smoke" / "state_store.json").exists()
        assert (scratch_root / "results" / "example_pin" / "smoke" / "property_audit.json").exists()
        assert (scratch_root / "results" / "example_pin" / "smoke" / "benchmark_residuals.json").exists()
        assert (scratch_root / "results" / "example_pin" / "smoke" / "runtime_context.json").exists()
        assert "bop" not in summary
        assert "primary_system" not in summary
        assert "flow" not in summary
        assert "transient" not in summary
        assert runtime_context["service"] == "host"
        assert runtime_context["containerized"] is False
        assert runtime_context["command"] == ["run", "example_pin"]
        assert state_store["runtime_context"]["command"] == ["run", "example_pin"]
        assert isinstance(validation["checks"], list)
        assert not (scratch_root / "results" / "example_pin" / "smoke" / "render_assets.json").exists()
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_solver_enabled_without_openmc_reports_missing_solver_status() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-example-pin-missing-solver" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = _load_case("example_pin")
        bundle = create_result_bundle(scratch_root, config.name, "missing-solver")

        summary = run_case(config, bundle, solver_enabled=True)

        assert summary["neutronics"]["status"] in {"completed", "completed_without_statepoint", "failed", "skipped_missing_solver"}
        if summary["neutronics"]["status"] == "skipped_missing_solver":
            assert "docker compose run --rm openmc" in summary["neutronics"]["message"]
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_msr_run_still_produces_primary_system_summary() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-msr-run" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = _load_case("immersed_pool_reference")
        bundle = create_result_bundle(scratch_root, config.name, "msr-run")

        summary = run_case(config, bundle, solver_enabled=False)

        assert BALANCE_OF_PLANT in summary["workflow_capabilities"]
        assert THERMAL_NETWORK in summary["workflow_capabilities"]
        assert MSR_PRIMARY_SYSTEM in summary["workflow_capabilities"]
        assert TRANSIENT_ANALYSIS in summary["workflow_capabilities"]
        assert "bop" in summary
        assert "flow" in summary
        assert "primary_system" in summary
        assert summary["primary_system"]["model"] == "reduced_order_primary_system"
        assert (bundle.root / "validation.json").exists()
        assert not (bundle.root / "render_assets.json").exists()
        assert not any(bundle.geometry_exports_dir.iterdir())
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_capability_inference_distinguishes_generic_and_msr_cases() -> None:
    example_pin = _load_case("example_pin")
    tmsr_core = _load_case("tmsr_lf1_core")
    immersed_pool = _load_case("immersed_pool_reference")

    assert get_case_capabilities(example_pin) == {NEUTRONICS_ONLY}
    assert get_case_capabilities(tmsr_core) == {NEUTRONICS_ONLY, BALANCE_OF_PLANT, THERMAL_NETWORK, TRANSIENT_ANALYSIS}
    assert get_case_capabilities(immersed_pool) == {NEUTRONICS_ONLY, BALANCE_OF_PLANT, THERMAL_NETWORK, MSR_PRIMARY_SYSTEM, TRANSIENT_ANALYSIS}


def test_explicit_capability_override_can_disable_primary_system_logic() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-capability-override" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = _load_case("immersed_pool_reference")
        config.data = deepcopy(config.data)
        config.data.setdefault("workflow", {})["capabilities"] = {MSR_PRIMARY_SYSTEM: False}
        bundle = create_result_bundle(scratch_root, config.name, "override")

        summary = run_case(config, bundle, solver_enabled=False)

        assert BALANCE_OF_PLANT in summary["workflow_capabilities"]
        assert THERMAL_NETWORK in summary["workflow_capabilities"]
        assert MSR_PRIMARY_SYSTEM not in summary["workflow_capabilities"]
        assert TRANSIENT_ANALYSIS in summary["workflow_capabilities"]
        assert "bop" in summary
        assert "flow" in summary
        assert "primary_system" not in summary
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_missing_molten_salt_inputs_report_capability_and_field() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-capability-error" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = _load_case("tmsr_lf1_core")
        config.data = deepcopy(config.data)
        config.data["materials"]["fuel_salt"].pop("density")
        bundle = create_result_bundle(scratch_root, config.name, "missing-input")

        with pytest.raises(CapabilityConfigurationError) as exc_info:
            run_case(config, bundle, solver_enabled=False)

        message = str(exc_info.value)
        assert "Capability 'thermal_network'" in message
        assert "materials.fuel_salt.density" in message
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_transient_command_produces_configured_history() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-transient-run" / uuid.uuid4().hex
    case_dir = scratch_root / "configs" / "cases" / "immersed_pool_reference"
    benchmark_dir = scratch_root / "benchmarks" / "tmsr_lf1"
    case_dir.mkdir(parents=True, exist_ok=True)
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml", case_dir / "case.yaml")
        shutil.copy2(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", benchmark_dir / "benchmark.yaml")

        exit_code = main(
            [
                "--repo-root",
                str(scratch_root),
                "transient",
                "immersed_pool_reference",
                "--run-id",
                "transient-smoke",
                "--scenario",
                "partial_heat_sink_loss",
            ]
        )

        assert exit_code == 0
        summary = json.loads(
            (scratch_root / "results" / "immersed_pool_reference" / "transient-smoke" / "summary.json").read_text(
                encoding="utf-8"
            )
        )
        transient = json.loads(
            (scratch_root / "results" / "immersed_pool_reference" / "transient-smoke" / "transient.json").read_text(
                encoding="utf-8"
            )
        )
        assert summary["transient"]["scenario_name"] == "partial_heat_sink_loss"
        assert summary["transient"]["peak_fuel_temperature_c"] >= summary["primary_system"]["thermal_profile"]["estimated_hot_leg_temp_c"]
        assert transient["metrics"]["history_points"] > 10
        assert transient["depletion"]["chain"] == "thorium_u233_cleanup_proxy"
        assert transient["chemistry"]["model"] == "salt_redox_cleanup_proxy"
        assert transient["metrics"]["final_fissile_inventory_fraction"] > 0.0
        assert transient["metrics"]["peak_corrosion_index"] >= 0.1
        first_history = transient["history"][0]
        assert "redox_state_ev" in first_history
        assert "fissile_inventory_fraction" in first_history
        assert "chemistry_reactivity_pcm" in first_history
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_external_integration_commands_export_inputs_and_update_summary() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-external-integrations" / uuid.uuid4().hex
    case_dir = scratch_root / "configs" / "cases" / "immersed_pool_reference"
    benchmark_dir = scratch_root / "benchmarks" / "tmsr_lf1"
    case_dir.mkdir(parents=True, exist_ok=True)
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml", case_dir / "case.yaml")
        shutil.copy2(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", benchmark_dir / "benchmark.yaml")
        config = load_case_config(case_dir / "case.yaml")
        bundle = create_result_bundle(scratch_root, config.name, "integrations")

        moose_summary = run_case(config, bundle, solver_enabled=False)
        assert "integrations" not in moose_summary

        moose_exit = main(["--repo-root", str(scratch_root), "moose", "immersed_pool_reference", "--run-id", "integrations"])
        scale_exit = main(["--repo-root", str(scratch_root), "scale", "immersed_pool_reference", "--run-id", "integrations"])
        thermochimica_exit = main(["--repo-root", str(scratch_root), "thermochimica", "immersed_pool_reference", "--run-id", "integrations"])
        saltproc_exit = main(["--repo-root", str(scratch_root), "saltproc", "immersed_pool_reference", "--run-id", "integrations"])
        moltres_exit = main(["--repo-root", str(scratch_root), "moltres", "immersed_pool_reference", "--run-id", "integrations"])

        assert moose_exit == 0
        assert scale_exit == 0
        assert thermochimica_exit == 0
        assert saltproc_exit == 0
        assert moltres_exit == 0

        summary = json.loads((bundle.root / "summary.json").read_text(encoding="utf-8"))
        moose_payload = json.loads((bundle.root / "moose_integration.json").read_text(encoding="utf-8"))
        scale_payload = json.loads((bundle.root / "scale_integration.json").read_text(encoding="utf-8"))
        thermochimica_payload = json.loads((bundle.root / "thermochimica_integration.json").read_text(encoding="utf-8"))
        saltproc_payload = json.loads((bundle.root / "saltproc_integration.json").read_text(encoding="utf-8"))
        moltres_payload = json.loads((bundle.root / "moltres_integration.json").read_text(encoding="utf-8"))
        moose_handoff = json.loads((bundle.root / "moose_handoff.json").read_text(encoding="utf-8"))
        scale_handoff = json.loads((bundle.root / "scale_handoff.json").read_text(encoding="utf-8"))
        thermochimica_handoff = json.loads((bundle.root / "thermochimica_handoff.json").read_text(encoding="utf-8"))
        saltproc_handoff = json.loads((bundle.root / "saltproc_handoff.json").read_text(encoding="utf-8"))
        moltres_handoff = json.loads((bundle.root / "moltres_handoff.json").read_text(encoding="utf-8"))

        assert summary["integrations"]["moose"]["status"] == "input_deck_exported"
        assert summary["integrations"]["scale"]["status"] == "input_deck_exported"
        assert summary["integrations"]["thermochimica"]["status"] == "input_bundle_exported"
        assert summary["integrations"]["saltproc"]["status"] == "input_bundle_exported"
        assert summary["integrations"]["moltres"]["status"] == "input_deck_exported"
        assert Path(moose_payload["input_path"]).exists()
        assert Path(scale_payload["input_path"]).exists()
        assert Path(thermochimica_payload["input_path"]).exists()
        assert Path(saltproc_payload["input_path"]).exists()
        assert Path(moltres_payload["input_path"]).exists()
        assert Path(moose_payload["handoff_path"]).exists()
        assert Path(scale_payload["handoff_path"]).exists()
        assert Path(thermochimica_payload["handoff_path"]).exists()
        assert Path(saltproc_payload["handoff_path"]).exists()
        assert Path(moltres_payload["handoff_path"]).exists()
        assert "Executioner" in Path(moose_payload["input_path"]).read_text(encoding="utf-8")
        assert "=csas6" in Path(scale_payload["input_path"]).read_text(encoding="utf-8")
        assert thermochimica_handoff["tool"] == "thermochimica"
        assert saltproc_handoff["tool"] == "saltproc"
        assert moltres_handoff["tool"] == "moltres"
        assert moose_payload["provenance"]["runtime_context"]["command"] == ["moose", "immersed_pool_reference"]
        assert thermochimica_payload["provenance"]["runtime_context"]["tool_version"] is None
        assert thermochimica_handoff["provenance"]["runtime_context"]["command"] == ["thermochimica", "immersed_pool_reference"]
        assert moose_handoff["geometry"]["channel_count"] == summary["metrics"]["channel_count"]
        assert scale_handoff["materials"]["fuel_salt"]["nuclide_count"] > 0
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_render_command_uses_existing_run_state_and_emits_visual_assets() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-render-command" / uuid.uuid4().hex
    case_dir = scratch_root / "configs" / "cases" / "immersed_pool_reference"
    benchmark_dir = scratch_root / "benchmarks" / "tmsr_lf1"
    case_dir.mkdir(parents=True, exist_ok=True)
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml", case_dir / "case.yaml")
        shutil.copy2(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", benchmark_dir / "benchmark.yaml")

        run_exit = main(["--repo-root", str(scratch_root), "run", "immersed_pool_reference", "--run-id", "render-smoke", "--no-solver"])
        assert run_exit == 0

        render_assets_path = scratch_root / "results" / "immersed_pool_reference" / "render-smoke" / "render_assets.json"
        assert not render_assets_path.exists()

        render_exit = main(["--repo-root", str(scratch_root), "render", "immersed_pool_reference", "--run-id", "render-smoke"])
        assert render_exit == 0

        assets = json.loads(render_assets_path.read_text(encoding="utf-8"))
        summary = json.loads(
            (scratch_root / "results" / "immersed_pool_reference" / "render-smoke" / "summary.json").read_text(encoding="utf-8")
        )
        assert Path(assets["hero_cutaway"]).exists()
        assert Path(assets["annotated_cutaway"]).exists()
        assert Path(assets["physics_overlay"]).exists()
        assert summary["visualization_state"]["has_render_assets"] is True
        assert "hero_cutaway" in summary["visualization_state"]["assets"]
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
