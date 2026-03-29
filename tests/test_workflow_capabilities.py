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
        assert summary["neutronics"]["status"] in {"dry-run", "skipped_missing_solver"}
        assert summary["workflow_capabilities"] == [NEUTRONICS_ONLY]
        assert "bop" not in summary
        assert "primary_system" not in summary
        assert "flow" not in summary
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
        assert "bop" in summary
        assert "flow" in summary
        assert "primary_system" in summary
        assert summary["primary_system"]["model"] == "reduced_order_primary_system"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_capability_inference_distinguishes_generic_and_msr_cases() -> None:
    example_pin = _load_case("example_pin")
    tmsr_core = _load_case("tmsr_lf1_core")
    immersed_pool = _load_case("immersed_pool_reference")

    assert get_case_capabilities(example_pin) == {NEUTRONICS_ONLY}
    assert get_case_capabilities(tmsr_core) == {NEUTRONICS_ONLY, BALANCE_OF_PLANT, THERMAL_NETWORK}
    assert get_case_capabilities(immersed_pool) == {NEUTRONICS_ONLY, BALANCE_OF_PLANT, THERMAL_NETWORK, MSR_PRIMARY_SYSTEM}


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
