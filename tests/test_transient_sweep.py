from pathlib import Path

from thorium_reactor.config import load_case_config
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.transient_sweep import run_transient_sweep_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def _minimal_summary() -> dict:
    return {
        "bop": {
            "thermal_power_mw": 8.0,
        },
        "flow": {
            "reduced_order": {
                "active_flow": {
                    "representative_residence_time_s": 0.85,
                    "total_volumetric_flow_m3_s": 0.014,
                }
            }
        },
        "primary_system": {
            "thermal_profile": {
                "estimated_hot_leg_temp_c": 690.0,
                "estimated_cold_leg_temp_c": 555.0,
            },
            "inventory": {
                "fuel_salt": {"total_m3": 0.092},
                "coolant_salt": {"net_pool_inventory_m3": 11.4},
            },
        },
        "fuel_cycle": {
            "cleanup_turnover_hours": 240.0,
            "cleanup_turnover_days": 10.0,
            "cleanup_removal_efficiency": 0.78,
        },
    }


def test_run_transient_sweep_case_produces_cpu_backed_bundle() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "transient-sweep-test", config.name, "run")
    summary = _minimal_summary()

    payload = run_transient_sweep_case(
        config,
        bundle,
        summary,
        scenario_name="partial_heat_sink_loss",
        samples=128,
        seed=7,
        prefer_gpu=True,
    )

    assert payload["backend"] in {"python", "numpy"}
    assert payload["samples"] == 128
    assert len(payload["history"]) >= 100
    assert payload["metrics"]["peak_power_fraction_p95"] >= 1.0
    assert payload["metrics"]["final_core_delayed_neutron_source_fraction_p50"] > 0.0
    assert payload["backend_report"]["selected"] == payload["backend"]
    assert payload["runtime_performance"]["sample_steps_per_s"] > 0.0
    assert payload["numerical_checks"]["status"] == "ok"
    assert summary["transient_sweep"]["samples"] == 128
    assert summary["transient_sweep"]["backend"] in {"python", "numpy"}
    assert summary["transient_sweep"]["final_core_delayed_neutron_source_fraction_p50"] > 0.0
    assert summary["transient_sweep"]["numerical_checks"]["status"] == "ok"
    assert (bundle.root / "transient_sweep.json").exists()


def test_run_transient_sweep_case_enforces_minimum_sample_floor() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "transient-sweep-floor-test", config.name, "run")
    summary = {
        "bop": {"thermal_power_mw": 8.0},
        "flow": {"reduced_order": {"active_flow": {"representative_residence_time_s": 0.85, "total_volumetric_flow_m3_s": 0.014}}},
        "primary_system": {
            "thermal_profile": {"estimated_hot_leg_temp_c": 690.0, "estimated_cold_leg_temp_c": 555.0},
            "inventory": {"fuel_salt": {"total_m3": 0.092}},
        },
        "fuel_cycle": {"cleanup_turnover_hours": 240.0, "cleanup_turnover_days": 10.0, "cleanup_removal_efficiency": 0.78},
    }

    payload = run_transient_sweep_case(config, bundle, summary, samples=4, seed=11)

    assert payload["samples"] == 32
    assert summary["transient_sweep"]["samples"] == 32


def test_numpy_transient_sweep_matches_python_reference() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    python_bundle = create_result_bundle(REPO_ROOT / ".tmp" / "transient-sweep-python-reference", config.name, "run")
    numpy_bundle = create_result_bundle(REPO_ROOT / ".tmp" / "transient-sweep-numpy-reference", config.name, "run")
    python_payload = run_transient_sweep_case(
        config,
        python_bundle,
        _minimal_summary(),
        scenario_name="partial_heat_sink_loss",
        samples=64,
        seed=13,
        backend="python",
    )
    numpy_payload = run_transient_sweep_case(
        config,
        numpy_bundle,
        _minimal_summary(),
        scenario_name="partial_heat_sink_loss",
        samples=64,
        seed=13,
        backend="numpy",
    )

    assert python_payload["backend"] == "python"
    assert numpy_payload["backend"] == "numpy"
    assert numpy_payload["numerical_checks"]["status"] == "ok"
    for key in (
        "peak_power_fraction_p95",
        "final_power_fraction_p50",
        "final_total_reactivity_pcm_p50",
        "final_core_delayed_neutron_source_fraction_p50",
    ):
        assert abs(numpy_payload["metrics"][key] - python_payload["metrics"][key]) < 5.0e-4
