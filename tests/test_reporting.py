import json
import shutil
import uuid
from pathlib import Path

from thorium_reactor.paths import create_result_bundle
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot
from thorium_reactor.reporting.reports import generate_report


def test_generate_report_includes_benchmark_evidence_and_novelty_tracks() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"keff": 1.01, "channel_count": 91},
                    "bop": {"electric_power_mw": 94.668},
                }
            ),
            encoding="utf-8",
        )
        validation_path = scratch_root / "validation.json"
        validation_path.write_text(
            json.dumps(
                {
                    "checks": [
                        {
                            "name": "keff_core_band",
                            "status": "pass",
                            "message": "1.01 is within the expected range.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "tmsr_lf1_core",
            {
                "reactor": {
                    "name": "TMSR-LF1-Inspired Core",
                    "family": "TMSR-LF1-inspired MSR",
                    "stage": "full-core",
                    "design_power_mwth": 250.0,
                    "benchmark": "benchmarks/tmsr_lf1/benchmark.yaml",
                },
                "validation_targets": {
                    "keff_core_band": {
                        "metric": "keff",
                        "source": "metrics",
                        "min": 0.98,
                        "max": 1.08,
                    }
                },
            },
            summary_path,
            validation_path,
            {"render_png": "geometry/exports/core.png"},
            {
                "title": "TMSR-LF1-inspired surrogate benchmark",
                "references": ["Current values are surrogate acceptance bands."],
                "assumptions": [
                    {
                        "id": "open_scope",
                        "text": "The model is openly reproducible rather than proprietary.",
                        "basis": "project_scope",
                        "confidence": "medium",
                        "evidence_refs": ["msrdynamics"],
                    }
                ],
                "evidence": [
                    {
                        "id": "msrdynamics",
                        "topic": "Transient modeling bridge",
                        "source": "https://github.com/openmsr/msrDynamics",
                        "claim": "msrDynamics provides a nodal dynamics API for flowing-fuel systems.",
                        "relevance": "This repo can grow from steady-state BOP into transients.",
                        "confidence": "medium",
                    }
                ],
                "novelty_tracks": [
                    {
                        "name": "Evidence-linked reactor twin",
                        "summary": "Tie assumptions to source-backed evidence and confidence.",
                    }
                ],
                "targets": {
                    "nominal_thermal_power_mwth": {
                        "value": 250.0,
                        "units": "MWth",
                        "status": "surrogate",
                        "confidence": "low",
                        "evidence_refs": ["msrdynamics"],
                    },
                    "expected_keff_band": {
                        "min": 0.98,
                        "max": 1.08,
                        "units": "delta-k/k",
                        "status": "surrogate",
                        "confidence": "low",
                        "evidence_refs": ["msrdynamics"],
                    },
                },
            },
        )

        assert "## Benchmark Context" in report
        assert "Current values are surrogate acceptance bands." in report
        assert "## Benchmark Traceability" in report
        assert "Traceability score" in report
        assert "Surrogate targets remaining" in report
        assert "## Evidence Trail" in report
        assert "Transient modeling bridge" in report
        assert "https://github.com/openmsr/msrDynamics" in report
        assert "## Novelty Tracks" in report
        assert "Evidence-linked reactor twin" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_can_include_plot_outputs() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-plots" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        bundle = create_result_bundle(scratch_root, "example_pin", "report-with-plots")
        summary = {
            "case": "example_pin",
            "result_dir": str(bundle.root),
            "neutronics": {"status": "dry-run"},
            "metrics": {"keff": 1.02, "channel_count": 1},
            "bop": {"thermal_power_mw": 1.0, "electric_power_mw": 0.269},
        }
        validation = {
            "case": "example_pin",
            "checks": [
                {"name": "keff_smoke", "status": "pass", "message": "Within expected range."},
                {"name": "fuel_density", "status": "pass", "message": "Positive density."},
            ],
        }
        summary_path = bundle.write_json("summary.json", summary)
        validation_path = bundle.write_json("validation.json", validation)
        plot_assets = generate_summary_plots(bundle, summary)
        plot_assets.update(generate_validation_plot(bundle, validation))

        report = generate_report(
            "example_pin",
            {
                "reactor": {
                    "name": "Example Pin",
                    "family": "smoke-test",
                    "stage": "example",
                    "design_power_mwth": 1.0,
                    "benchmark": "n/a",
                }
            },
            summary_path,
            validation_path,
            None,
            None,
            plot_assets,
        )

        assert "## Plot Outputs" in report
        assert "metrics_overview" in report
        assert "validation_summary" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_reduced_order_flow_section() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-flow" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"active_flow_channel_count": 37},
                    "bop": {"primary_mass_flow_kg_s": 1116.071429},
                    "flow": {
                        "reduced_order": {
                            "allocation_rule": "salt_area_weighted",
                            "active_flow": {
                                "channel_count": 37,
                                "total_flow_area_cm2": 9.813587,
                                "representative_velocity_m_s": 355.397391,
                                "representative_residence_time_s": 0.005402,
                            },
                            "disconnected_inventory": {
                                "channel_count": 48,
                            },
                        }
                    },
                    "primary_system": {
                        "loop_hydraulics": {
                            "total_pressure_drop_kpa": 31.5,
                            "pump_head_m": 1.04,
                            "pump_shaft_power_kw": 1.7,
                            "max_reynolds_number": 91234.0,
                        },
                        "heat_exchanger": {
                            "duty_mw": 7.28,
                            "required_area_m2": 63.4,
                            "lmtd_c": 104.1,
                        },
                        "inventory": {
                            "fuel_salt": {"total_m3": 0.092},
                            "coolant_salt": {"net_pool_inventory_m3": 11.4},
                        },
                    },
                    "fuel_cycle": {
                        "heavy_metal_inventory_kg": 3.9,
                        "fissile_inventory_kg": 0.25,
                        "specific_power_mw_per_t_hm": 2051.3,
                        "cleanup_turnover_days": 10.0,
                        "cleanup_removal_efficiency": 0.78,
                        "xenon_generation_rate_atoms_s": 6.99e14,
                        "xenon_removal_fraction": 0.9,
                        "protactinium_holdup_days": 2.0,
                    },
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "tmsr_lf1_core",
            {
                "reactor": {
                    "name": "TMSR-LF1-Inspired Core",
                    "family": "TMSR-LF1-inspired MSR",
                    "stage": "full-core",
                    "design_power_mwth": 250.0,
                    "benchmark": "benchmarks/tmsr_lf1/benchmark.yaml",
                }
            },
            summary_path,
            None,
            None,
        )

        assert "## Reduced-Order Flow" in report
        assert "## Primary System" in report
        assert "## Fuel Cycle Assumptions" in report
        assert "salt_area_weighted" in report
        assert "37" in report
        assert "355.397391" in report
        assert "0.005402" in report
        assert "31.5" in report
        assert "63.4" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
