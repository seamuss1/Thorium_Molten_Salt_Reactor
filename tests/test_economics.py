import json
import shutil
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from thorium_reactor.config import load_case_config
from thorium_reactor.cli import main
from thorium_reactor.economics import build_commercial_plan, capital_recovery_factor, run_economics_case
from thorium_reactor.economics.finance import adjusted_overnight_cost_usd, annual_generation_mwh
from thorium_reactor.paths import create_result_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_case(name: str):
    return load_case_config(REPO_ROOT / "configs" / "cases" / name / "case.yaml")


def test_finance_formula_helpers_are_deterministic() -> None:
    assert annual_generation_mwh(300.0, 0.93) == pytest.approx(2_444_040.0)
    assert adjusted_overnight_cost_usd(
        3_000_000_000.0,
        overnight_cost_uplift=1.25,
        learning_cost_multiplier=1.0,
        cost_escalation_factor=1.0,
    ) == pytest.approx(3_750_000_000.0)
    assert capital_recovery_factor(0.0, 50) == pytest.approx(0.02)
    assert capital_recovery_factor(0.08, 60) == pytest.approx(0.080798, rel=1.0e-4)


def test_flagship_commercial_plan_uses_conservative_foak_defaults() -> None:
    config = _load_case("flagship_grid_msr")

    plan = build_commercial_plan(config, scenario_name="conservative_foak", project_start="2026-05-02")

    finance = plan["finance"]
    schedule = plan["schedule"]
    assert plan["status"] == "completed"
    assert finance["inputs"]["net_capacity_mwe"] == 300.0
    assert finance["inputs"]["source_occ_usd_per_kwe"] == 10_000.0
    assert finance["inputs"]["overnight_cost_uplift"] == 1.25
    assert finance["inputs"]["construction_months"] == 86
    assert finance["cost_breakdown_usd"]["reference_overnight_cost"] == pytest.approx(3_000_000_000.0)
    assert finance["cost_breakdown_usd"]["net_overnight_cost"] == pytest.approx(3_750_000_000.0)
    assert finance["cost_breakdown_usd"]["interest_during_construction"] > 0.0
    assert finance["outputs"]["annual_generation_mwh"] == pytest.approx(2_444_040.0)
    assert finance["outputs"]["lcoe_usd_per_mwh"] > 150.0
    assert schedule["construction_start_date"] == "2032-11-02"
    assert schedule["construction_end_date"] == "2040-01-02"
    assert schedule["commercial_operation_date"] == "2040-11-02"
    assert schedule["total_months_to_commercial_operation"] == 174
    assert schedule["total_years_to_commercial_operation"] == pytest.approx(14.5)


def test_scenario_override_changes_lcoe_and_construction_duration() -> None:
    config = _load_case("flagship_grid_msr")
    config.data = deepcopy(config.data)
    config.data["economics"]["scenarios"]["lower_wacc"] = {
        **config.data["economics"]["scenarios"]["conservative_foak"],
        "real_wacc": 0.04,
        "construction_duration_multiplier": 1.0,
    }

    baseline = build_commercial_plan(config, scenario_name="conservative_foak")["finance"]
    lower_wacc = build_commercial_plan(config, scenario_name="lower_wacc")["finance"]

    assert lower_wacc["inputs"]["construction_months"] == 71
    assert lower_wacc["outputs"]["lcoe_usd_per_mwh"] < baseline["outputs"]["lcoe_usd_per_mwh"]


def test_non_commercial_case_returns_not_applicable_without_force() -> None:
    config = _load_case("example_pin")

    plan = build_commercial_plan(config)

    assert plan["status"] == "not_applicable"
    assert plan["finance"]["status"] == "not_applicable"
    assert "commercial_grid" in plan["finance"]["reason"]


def test_run_economics_case_writes_bundle_artifacts_and_summary() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-economics-run" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = _load_case("flagship_grid_msr")
        bundle = create_result_bundle(scratch_root, config.name, "finance")

        plan = run_economics_case(config, bundle, scenario_name="conservative_foak")

        assert plan["status"] == "completed"
        assert (bundle.root / "finance.json").exists()
        assert (bundle.root / "schedule.json").exists()
        assert (bundle.root / "project_plan.json").exists()
        assert (bundle.root / "cash_flow.csv").read_text(encoding="utf-8").startswith("month,date")
        assert "total_capitalized_cost" in (bundle.root / "cost_breakdown.csv").read_text(encoding="utf-8")
        summary = json.loads((bundle.root / "summary.json").read_text(encoding="utf-8"))
        assert summary["finance"]["status"] == "completed"
        assert summary["schedule"]["commercial_operation_date"] == "2040-11-02"
        assert summary["metrics"]["schedule_total_months_to_cod"] == 174
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_economics_cli_runs_flagship_case_end_to_end() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-economics-cli" / uuid.uuid4().hex
    case_dir = scratch_root / "configs" / "cases" / "flagship_grid_msr"
    benchmark_dir = scratch_root / "benchmarks" / "tmsr_lf1"
    case_dir.mkdir(parents=True, exist_ok=True)
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml", case_dir / "case.yaml")
        shutil.copy2(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", benchmark_dir / "benchmark.yaml")

        exit_code = main(
            [
                "--repo-root",
                str(scratch_root),
                "economics",
                "flagship_grid_msr",
                "--run-id",
                "finance-cli",
                "--scenario",
                "conservative_foak",
            ]
        )

        bundle_root = scratch_root / "results" / "flagship_grid_msr" / "finance-cli"
        assert exit_code == 0
        assert (bundle_root / "finance.json").exists()
        assert (bundle_root / "schedule.json").exists()
        assert (bundle_root / "plots" / "finance_cost_waterfall.svg").exists()
        summary = json.loads((bundle_root / "summary.json").read_text(encoding="utf-8"))
        assert summary["finance"]["status"] == "completed"
        assert summary["schedule"]["commercial_operation_date"] == "2040-11-02"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
