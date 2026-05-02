from pathlib import Path

from thorium_reactor.config import load_case_config
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.transient import run_transient_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_run_transient_case_reports_dominant_loop_precursor_source() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "transient-test", config.name, "run")
    summary = {
        "bop": {"thermal_power_mw": 8.0},
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

    payload = run_transient_case(
        config,
        bundle,
        summary,
        scenario_name="partial_heat_sink_loss",
    )

    dominant = payload["baseline"]["dominant_loop_segment_delayed_neutron_source"]
    metrics = payload["metrics"]

    assert dominant["id"] in {
        "core_to_hx_hot_leg",
        "heat_exchanger_and_gas_contact",
        "hx_to_pump_cold_leg",
        "pump_to_core_return",
    }
    assert 0.0 < dominant["delayed_neutron_source_fraction"] < 1.0
    assert metrics["peak_loop_segment_delayed_neutron_source_fraction"] >= dominant[
        "delayed_neutron_source_fraction"
    ]
    assert summary["metrics"]["transient_peak_loop_segment_delayed_neutron_source_fraction"] == metrics[
        "peak_loop_segment_delayed_neutron_source_fraction"
    ]
    assert summary["transient"]["peak_loop_segment_delayed_neutron_source_segment"]
    assert payload["history"][0]["dominant_loop_segment_delayed_neutron_source_segment"]
