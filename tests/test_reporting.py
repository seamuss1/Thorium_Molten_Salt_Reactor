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


def test_report_includes_neutronics_input_section() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-neutronics" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {
                        "status": "dry-run",
                        "openmc_available": False,
                        "simulation": {
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
                                    "name": "core_flux",
                                    "cell": "core_matrix",
                                    "scores": ["flux"],
                                    "nuclides": [],
                                }
                            ],
                            "geometry_boundary": "reflective",
                            "axial_boundary": "vacuum",
                        },
                    },
                    "metrics": {"keff": 1.01},
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

        assert "## Neutronics Inputs" in report
        assert "Particles per generation" in report
        assert "Active batches" in report
        assert "Radial boundary" in report
        assert "Axial boundary" in report
        assert "Tally `core_flux`" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_transient_and_depletion_sections() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-transient" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"keff": 1.01},
                    "fuel_cycle": {
                        "depletion_chain": "thorium_u233_cleanup_proxy",
                        "cleanup_scenario": "baseline_online_cleanup",
                        "heavy_metal_inventory_kg": 3.9,
                        "fissile_inventory_kg": 0.25,
                        "specific_power_mw_per_t_hm": 2051.3,
                        "cleanup_turnover_days": 10.0,
                        "cleanup_removal_efficiency": 0.78,
                        "xenon_generation_rate_atoms_s": 6.99e14,
                        "xenon_removal_fraction": 0.9,
                        "protactinium_holdup_days": 2.0,
                        "fissile_burn_fraction_per_day_full_power": 0.0008,
                        "breeding_gain_fraction_per_day": 0.00055,
                        "net_fissile_change_fraction_per_day": -0.00037,
                        "equilibrium_protactinium_inventory_fraction": 0.0011,
                        "depletion_assumptions": {
                            "volatile_removal_efficiency": 0.78,
                        },
                    },
                    "chemistry": {
                        "model": "salt_redox_cleanup_proxy",
                        "redox_state_ev": -0.02,
                        "target_redox_state_ev": -0.03,
                        "redox_deviation_ev": 0.01,
                        "impurity_fraction": 0.0001,
                        "corrosion_index": 1.08,
                        "corrosion_risk": "low",
                        "gas_stripping_efficiency": 0.88,
                        "tritium_release_fraction": 0.33,
                    },
                    "transient": {
                        "status": "completed",
                        "model": "reduced_order_transient_proxy",
                        "scenario_name": "partial_heat_sink_loss",
                        "duration_s": 120.0,
                        "time_step_s": 1.0,
                        "event_count": 1,
                        "peak_power_fraction": 1.05,
                        "final_power_fraction": 1.02,
                        "peak_fuel_temperature_c": 705.0,
                        "peak_graphite_temperature_c": 666.0,
                        "peak_coolant_temperature_c": 681.0,
                        "minimum_precursor_core_fraction": 0.34,
                        "peak_loop_segment_delayed_neutron_source_fraction": 0.19,
                        "peak_loop_segment_delayed_neutron_source_segment": "heat_exchanger_and_gas_contact",
                        "final_dominant_loop_segment_delayed_neutron_source_fraction": 0.17,
                        "final_dominant_loop_segment_delayed_neutron_source_segment": "core_to_hx_hot_leg",
                        "final_total_reactivity_pcm": -22.0,
                        "depletion_chain": "thorium_u233_cleanup_proxy",
                        "cleanup_scenario": "baseline_online_cleanup",
                        "final_fissile_inventory_fraction": 0.997,
                        "peak_protactinium_inventory_fraction": 0.0013,
                        "final_redox_state_ev": -0.024,
                        "peak_corrosion_index": 1.12,
                        "history_path": "transient.json",
                    },
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "immersed_pool_reference",
            {
                "reactor": {
                    "name": "Immersed Pool MSR Reference",
                    "family": "reference-inspired immersed pool MSR demonstrator",
                    "stage": "full-core",
                    "design_power_mwth": 8.0,
                    "benchmark": "benchmarks/tmsr_lf1/benchmark.yaml",
                }
            },
            summary_path,
            None,
            None,
        )

        assert "## Fuel Cycle Assumptions" in report
        assert "## Salt Chemistry" in report
        assert "Depletion chain" in report
        assert "## Transient Scenario" in report
        assert "partial_heat_sink_loss" in report
        assert "transient.json" in report
        assert "Final redox state" in report
        assert "Peak external-loop delayed-neutron source segment" in report
        assert "heat_exchanger_and_gas_contact" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_external_integration_section() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-integrations" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"keff": 1.01},
                    "integrations": {
                        "moose": {
                            "status": "exported",
                            "input_path": "moose_input.i",
                            "handoff_path": "moose_handoff.json",
                            "application": "app-opt",
                        },
                        "scale": {
                            "status": "exported_missing_runtime",
                            "input_path": "scale_input.inp",
                            "handoff_path": "scale_handoff.json",
                            "sequence": "csas6",
                            "error": "Executable 'scalerte' was not found on PATH.",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "immersed_pool_reference",
            {
                "reactor": {
                    "name": "Immersed Pool MSR Reference",
                    "family": "reference-inspired immersed pool MSR demonstrator",
                    "stage": "full-core",
                    "design_power_mwth": 8.0,
                    "benchmark": "benchmarks/tmsr_lf1/benchmark.yaml",
                }
            },
            summary_path,
            None,
            None,
        )

        assert "## External Integrations" in report
        assert "`moose` status" in report
        assert "moose_input.i" in report
        assert "moose_handoff.json" in report
        assert "csas6" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_runtime_context_and_benchmark_residuals() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-runtime-context" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"keff": 1.01},
                    "runtime_context": {
                        "service": "app",
                        "image": "thorium-reactor-app:latest",
                        "tool_runtime": None,
                        "git_branch": "main",
                        "git_commit": "deadbeef",
                    },
                    "benchmark_residuals": {
                        "item_count": 1,
                        "dataset_count": 1,
                        "items": [
                            {
                                "name": "keff_band",
                                "metric": "keff",
                                "status": "pass",
                                "residual": 0.0,
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "msre_first_criticality",
            {
                "reactor": {
                    "name": "MSRE First Criticality Harness",
                    "family": "MSRE-inspired historical benchmark",
                    "stage": "benchmark",
                    "mode": "historic_benchmark",
                    "design_power_mwth": 8.0,
                    "benchmark": "benchmarks/msre_first_criticality/benchmark.yaml",
                }
            },
            summary_path,
            None,
            None,
        )

        assert "## Runtime Context" in report
        assert "thorium-reactor-app:latest" in report
        assert "## Benchmark Residuals" in report
        assert "keff_band" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_transient_sweep_section() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-transient-sweep" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"transient_sweep_peak_power_fraction_p95": 1.14},
                    "transient_sweep": {
                        "status": "completed",
                        "model": "reduced_order_transient_proxy_ensemble",
                        "scenario_name": "partial_heat_sink_loss",
                        "backend": "numpy",
                        "samples": 512,
                        "seed": 42,
                        "duration_s": 120.0,
                        "time_step_s": 1.0,
                        "event_count": 1,
                        "peak_power_fraction_p95": 1.14,
                        "peak_power_fraction_max": 1.28,
                        "peak_fuel_temperature_c_p95": 715.0,
                        "peak_fuel_temperature_c_max": 732.0,
                        "final_power_fraction_p50": 1.02,
                        "final_power_fraction_p95": 1.09,
                        "final_total_reactivity_pcm_p50": 18.4,
                        "final_total_reactivity_pcm_p95": 44.1,
                        "peak_corrosion_index_p95": 1.31,
                        "history_path": "transient_sweep.json",
                    },
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "immersed_pool_reference",
            {
                "reactor": {
                    "name": "Immersed Pool MSR Reference",
                    "family": "reference-inspired immersed pool MSR demonstrator",
                    "stage": "full-core",
                    "design_power_mwth": 8.0,
                    "benchmark": "benchmarks/tmsr_lf1/benchmark.yaml",
                }
            },
            summary_path,
            None,
            None,
        )

        assert "## Transient Sweep" in report
        assert "partial_heat_sink_loss" in report
        assert "512" in report
        assert "1.14" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_surfaces_model_validity_and_validation_maturity() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-validity" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "case": "tmsr_lf1_core",
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"benchmark_traceability_score": 100.0, "validation_maturity_score": 8.8},
                    "model_representation": {
                        "materials": "isotopic_explicit",
                        "fuel_cycle": "proxy_breeding",
                    },
                    "model_validity": {
                        "status": "invalid",
                        "failed_count": 2,
                        "checks": [],
                    },
                    "validation_maturity": {
                        "validation_maturity_score": 8.8,
                        "validation_maturity_stage": "surrogate_only",
                        "operating_point_source": {"status": "surrogate"},
                        "uncertainty_coverage": {"status": "missing"},
                        "cross_code_checks": [],
                        "gaps": ["Benchmark uncertainty coverage is missing."],
                    },
                    "benchmark_traceability": {
                        "traceability_score": 100.0,
                        "maturity_stage": "traceable_surrogate",
                        "coverage": {
                            "evidence_records_complete": {"linked": 1, "total": 1},
                            "assumptions_structured": {"linked": 1, "total": 1},
                            "assumptions_with_evidence": {"linked": 1, "total": 1},
                            "targets_structured": {"linked": 1, "total": 1},
                            "targets_with_evidence": {"linked": 1, "total": 1},
                            "reactor_parameters_linked": {"linked": 1, "total": 1},
                            "physics_validation_targets_linked": {"linked": 1, "total": 1},
                        },
                        "confidence_summary": {"high": 0, "medium": 1, "low": 0, "unspecified": 0},
                        "status_summary": {"surrogate_targets": 1, "literature_backed_targets": 0},
                        "gaps": ["1 benchmark target(s) are still marked surrogate."],
                    },
                }
            ),
            encoding="utf-8",
        )
        validation_path = scratch_root / "validation.json"
        validation_path.write_text(
            json.dumps({"checks": [{"name": "physics::active_channel_velocity_reasonable", "status": "fail", "message": "Too high."}]}),
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
            validation_path,
            None,
        )

        assert "> Model validity" in report
        assert "## Model Representation" in report
        assert "Validation maturity score" in report
        assert "Validation gap" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
