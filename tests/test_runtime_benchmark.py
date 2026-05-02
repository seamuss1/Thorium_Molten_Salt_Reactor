from pathlib import Path

from thorium_reactor.accelerators import available_backend_report
from thorium_reactor.config import load_case_config
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.runtime_benchmark import parse_backend_list, run_runtime_benchmark_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def _minimal_summary() -> dict:
    return {
        "bop": {"thermal_power_mw": 8.0},
        "flow": {"reduced_order": {"active_flow": {"representative_residence_time_s": 0.85, "total_volumetric_flow_m3_s": 0.014}}},
        "primary_system": {
            "thermal_profile": {"estimated_hot_leg_temp_c": 690.0, "estimated_cold_leg_temp_c": 555.0},
            "inventory": {"fuel_salt": {"total_m3": 0.092}, "coolant_salt": {"net_pool_inventory_m3": 11.4}},
        },
        "fuel_cycle": {"cleanup_turnover_hours": 240.0, "cleanup_turnover_days": 10.0, "cleanup_removal_efficiency": 0.78},
    }


def test_backend_discovery_uses_isolated_subprocesses() -> None:
    report = available_backend_report(dtype="float32", seed=3, names=("python", "numpy"))

    assert {item["name"] for item in report} == {"python", "numpy"}
    assert all(item["probe_isolated_process"] is True for item in report)
    assert all(item["available"] is True for item in report)


def test_runtime_benchmark_compares_cpu_and_numpy_backends() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "runtime-benchmark-test", config.name, "run")
    summary = _minimal_summary()

    payload = run_runtime_benchmark_case(
        config,
        bundle,
        summary,
        scenario_name="partial_heat_sink_loss",
        samples=64,
        seed=19,
        backends=parse_backend_list("python,numpy"),
    )

    assert payload["reference_backend"] == "python"
    assert payload["recommendation"]["backend"] in {"python", "numpy"}
    assert {item["backend"] for item in payload["results"] if item["status"] == "completed"} == {"python", "numpy"}
    assert all(item["numerical_checks"]["status"] == "ok" for item in payload["results"] if item["status"] == "completed")
    assert all(item["comparison_to_reference"]["status"] == "ok" for item in payload["results"] if item["status"] == "completed")
    assert summary["runtime_benchmark"]["status"] == "completed"
    assert (bundle.root / "runtime_benchmark.json").exists()
