import json
import shutil
import uuid
from pathlib import Path

from thorium_reactor.benchmarking import assess_benchmark_traceability, build_benchmark_residuals
from thorium_reactor.config import load_case_config, load_yaml
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot
from thorium_reactor.reporting.reports import build_presentation_qa, generate_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def _section(report: str, heading: str) -> str:
    start = report.index(heading)
    next_heading = report.find("\n## ", start + len(heading))
    if next_heading == -1:
        return report[start:]
    return report[start:next_heading]


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


def test_report_includes_benchmark_uncertainty_sweep_section() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-uq" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "completed"},
                    "metrics": {"keff": 1.0001},
                    "uncertainty_sweep": {
                        "status": "completed",
                        "model": "solver_backed_geometry_material_uq_v1",
                        "sample_count": 19,
                        "completed_sample_count": 19,
                        "failed_sample_count": 0,
                        "coverage_status": "assumption_backed",
                        "nominal_keff": 1.0001,
                        "nominal_residual_pcm": 32.0,
                        "input_interval_width_pcm": 145.0,
                        "input_sigma_pcm": 37.0,
                        "statistical_sigma_pcm": 1.0,
                        "combined_uncertainty_pcm": 422.0,
                        "normalized_residual_with_input": 0.08,
                        "budget_path": "uncertainty_budget.json",
                        "dominant_contributors": [
                            {
                                "parameter_id": "u235_atom_fraction",
                                "ranking_score_pcm": 24.0,
                                "source_backed": False,
                            }
                        ],
                    },
                    "uncertainty_budget": {
                        "uncertainty_categories": {
                            "input": {"status": "assumption_backed", "sigma_pcm": 37.0},
                            "statistical": {"status": "quantified", "sigma_pcm": 1.0},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        validation_path = scratch_root / "validation.json"
        validation_path.write_text(json.dumps({"checks": [], "passed": True}), encoding="utf-8")

        report = generate_report(
            "msre_first_criticality",
            {
                "reactor": {
                    "name": "MSRE First Criticality Harness",
                    "family": "MSRE-inspired historical benchmark",
                    "stage": "benchmark",
                    "mode": "historic_benchmark",
                }
            },
            summary_path,
            validation_path,
            None,
            {},
        )

        assert "## Benchmark Uncertainty Sweep" in report
        assert "assumption_backed" in report
        assert "u235_atom_fraction" in report
        assert "Uncertainty categories" in report
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
        assert "validation_summary" in report
        plot_outputs = _section(report, "## Plot Outputs")
        start_here = _section(report, "## Start Here")
        assert "metrics_overview" not in plot_outputs
        assert "Metrics overview plot" in start_here
        assert "Appendix / Raw Artifacts" in start_here
        manifest_payload = json.loads((bundle.root / "plots_manifest.json").read_text(encoding="utf-8"))
        assert manifest_payload["figures"]["metrics_overview"]["path"] == "plots/metrics_overview.svg"
        qa = json.loads((bundle.root / "presentation_qa.json").read_text(encoding="utf-8"))
        assert qa["passed"] is True
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_adds_front_artifact_index_for_result_bundles() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-artifact-index" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"keff": 1.01},
                    "transient": {"history_path": "transient_history.json"},
                    "benchmark_traceability": {
                        "traceability_score": 100.0,
                        "maturity_stage": "traceable_surrogate",
                        "coverage": {
                            "evidence_records_complete": {"linked": 1, "total": 1},
                            "assumptions_with_evidence": {"linked": 1, "total": 1},
                            "targets_with_evidence": {"linked": 1, "total": 1},
                            "reactor_parameters_linked": {"linked": 1, "total": 1},
                            "physics_validation_targets_linked": {"linked": 1, "total": 1},
                        },
                        "confidence_summary": {"high": 0, "medium": 1, "low": 0, "unspecified": 0},
                        "status_summary": {"surrogate_targets": 1, "literature_backed_targets": 0},
                    },
                }
            ),
            encoding="utf-8",
        )
        validation_path = scratch_root / "validation.json"
        validation_path.write_text(json.dumps({"checks": [], "passed": True}), encoding="utf-8")
        (scratch_root / "stage_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "stages": [
                        {
                            "sequence": 1,
                            "stage": "run",
                            "command": ["run"],
                            "args": ["tmsr_lf1_core", "--no-solver"],
                            "status": "completed",
                            "method_tier": "dry_run_proxy",
                            "output_artifacts": ["summary.json", "validation.json"],
                        },
                        {
                            "sequence": 2,
                            "stage": "transport",
                            "command": ["transport"],
                            "args": ["tmsr_lf1_core"],
                            "status": "completed",
                            "method_tier": "native_rz_rkdg_scalar_transport_v1",
                            "output_artifacts": ["transport_solution.npz"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (scratch_root / "artifact_status.json").write_text(
            json.dumps(
                {
                    "groups": {
                        "openmc": {
                            "state": "dry_run",
                            "blockers": ["No solver-backed OpenMC statepoint artifact is present for this bundle."],
                        }
                    }
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
            validation_path,
            {"render_png": "geometry/exports/core.png"},
            {
                "title": "TMSR-LF1-inspired surrogate benchmark",
                "references": ["Surrogate acceptance bands."],
                "targets": {"expected_keff_band": {"min": 0.98, "max": 1.08}},
            },
            {"metrics_overview": "plots/metrics_overview.png"},
            {
                "case": {"source": "repo", "origin_path": "configs/cases/tmsr_lf1_core/case.yaml"},
                "benchmark": {"source": "repo", "origin_path": "benchmarks/tmsr_lf1/benchmark.yaml"},
                "git": {
                    "dirty": True,
                    "modified": ["src/thorium_reactor/cli.py"],
                    "untracked": ["notes.txt"],
                    "diff_hash": "abc123",
                },
                "dependency_hash": "dep123",
                "generator": "test.generator",
                "generator_version": 2,
            },
        )

        assert "## Start Here" in report
        assert "Open first: this report" in report
        assert "### Primary Evidence" in report
        assert "Run summary JSON" in report
        assert "Validation checks" in report
        assert "Metrics overview plot" in _section(report, "## Start Here")
        assert "Render PNG geometry" in report
        assert "Benchmark context" in report
        assert "### Appendix / Raw Artifacts" in report
        assert "Case input provenance" in report
        assert "Benchmark metadata" in report
        assert "transient_history.json" in report
        assert "## Stage Manifest" in report
        assert "ordered suite-level stage manifest" in report
        assert "native_rz_rkdg_scalar_transport_v1" in report
        assert "Reproducibility warning: git worktree was dirty" in report
        assert "Runtime/dependency hash" in report
        assert "OpenMC artifact blocker" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_surfaces_openmc_blocker_without_artifact_status_file() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-openmc-blocker" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"expected_cells": 4},
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "example_pin",
            {
                "reactor": {
                    "name": "Example Pin Smoke Test",
                    "family": "pin-cell",
                    "stage": "smoke",
                }
            },
            summary_path,
            None,
            None,
        )

        assert not (scratch_root / "artifact_status.json").exists()
        assert "OpenMC artifact blocker: no solver-backed OpenMC statepoint artifact is recorded." in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_key_metrics_are_curated_readable_and_reader_formatted() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-key-metrics" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary = {
            "result_dir": str(scratch_root),
            "neutronics": {"status": "dry-run"},
            "metrics": {
                "keff": 1.000123456789,
                "active_flow_channel_count": 37,
                "finance_lcoe_usd_per_mwh": 190.123456,
                "reactivity_margin_pcm": 125.4321,
                "xenon_generation_rate_atoms_s": 6.99e14,
                "startup_temperature_range_c": [565.123456, 590.987654],
                "raw_missing_metric": None,
            },
            "runtime_context": {
                "service": "app",
                "image": "thorium-reactor-app:latest",
                "tool_runtime": None,
                "git_branch": "codex/reporting",
                "git_commit": "deadbeef",
            },
            "bop": {"primary_mass_flow_kg_s": 1116.071429},
            "flow": {
                "reduced_order": {
                    "active_flow": {
                        "channel_count": 37,
                        "representative_velocity_m_s": 355.397391,
                        "representative_residence_time_s": 0.005402,
                    }
                }
            },
            "fuel_cycle": {
                "xenon_generation_rate_atoms_s": 6.99e14,
            },
        }
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        report = generate_report(
            "tmsr_lf1_core",
            {
                "reactor": {
                    "name": "TMSR-LF1-Inspired Core",
                    "family": "TMSR-LF1-inspired MSR",
                    "stage": "full-core",
                    "design_power_mwth": 250.0000001,
                    "benchmark": "n/a",
                }
            },
            summary_path,
            None,
            None,
        )

        key_metrics = _section(report, "## Key Metrics")
        additional_metrics = _section(report, "## Additional Metrics")
        assert "- Effective multiplication factor: `1.00012`" in key_metrics
        assert "- Active flow channels: `37`" in key_metrics
        assert "- Representative salt velocity: `355.4 m/s`" in key_metrics
        assert "- LCOE: `190.12 USD/MWh`" in key_metrics
        assert "- Reactivity margin: `125.43 pcm`" in additional_metrics
        assert "- Startup temperature range: `565.12 to 590.99 C`" in additional_metrics
        assert "- Xenon generation rate: `699e+12 atoms/s`" in additional_metrics
        assert "active_flow_channel_count" not in key_metrics
        assert "reactivity_margin_pcm" not in additional_metrics
        assert "raw_missing_metric" not in report
        assert "`None`" not in report
        assert "`n/a`" not in report
        assert "355.397391" not in report
        assert "1.000123456789" not in report
        assert "699e+12" in report
        assert json.loads(summary_path.read_text(encoding="utf-8"))["metrics"]["keff"] == 1.000123456789
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_missing_cleanup_preserves_valid_compound_fields() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-missing-cleanup" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "transport_solver": {
                        "model": "native_rkdg",
                        "mesh": {"radial_cells": 12, "axial_cells": 24},
                        "polynomial_order": 3,
                        "time_step_s": 0.0025,
                        "cfl": None,
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
                    "benchmark": "n/a",
                }
            },
            summary_path,
            None,
            None,
        )

        transport = _section(report, "## Native RKDG Transport")
        assert "- Mesh/order: (12 x 24), p=`3`" in transport
        assert "- Time step/CFL: `0.0025` s" in transport
        assert "`None`" not in transport
        assert "`n/a`" not in transport
        assert "`unknown`" not in transport
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_formats_numeric_ranges_inside_code_literals() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-range-format" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "msre_pump_transient_benchmark": {
                        "model": "msre_pump_transient_benchmark_screen",
                        "screening_status": "watch",
                        "benchmark_mean_error_startup_pcm": {"min": 11.000000, "max": 21.000000},
                        "benchmark_mean_error_coastdown_pcm": {"min": 5.123456, "max": 13.987654},
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
                    "benchmark": "n/a",
                }
            },
            summary_path,
            None,
            None,
        )

        transient = _section(report, "## MSRE Pump Transient Validation")
        assert "- Benchmark startup mean error range (pcm): `11 to 21`" in transient
        assert "- Benchmark coastdown mean error range (pcm): `5.1235 to 13.988`" in transient
        assert "11.000000" not in transient
        assert "13.987654" not in transient
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_literature_operating_point_section() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-literature-operating-point" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "literature_operating_point": {
                        "model": "literature_operating_point_screen",
                        "screening_status": "mismatch",
                        "aligned_count": 2,
                        "watch_count": 1,
                        "mismatch_count": 2,
                        "comparisons": [
                            {
                                "id": "nominal_thermal_power_mwth",
                                "label": "Design thermal power",
                                "actual": 250.0,
                                "reference": 2.0,
                                "units": "MWth",
                                "status": "mismatch",
                                "relative_difference_fraction": 124.0,
                            },
                            {
                                "id": "external_core_residence_time_s",
                                "label": "External-loop residence time",
                                "actual": 55.25,
                                "reference": 55.25,
                                "units": "s",
                                "status": "aligned",
                                "relative_difference_fraction": 0.0,
                            },
                        ],
                        "sources": ["https://doi.org/10.3390/en19040964"],
                        "interpretation": "The case should be treated as surrogate context for temperatures and transport.",
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
                    "benchmark": "n/a",
                }
            },
            summary_path,
            None,
            None,
        )

        section = _section(report, "## Literature Operating Point")
        assert "- Screening status: `mismatch`" in section
        assert "Design thermal power: actual `250` vs reference `2` MWth (`mismatch`" in section
        assert "External-loop residence time: actual `55.25` vs reference `55.25` s (`aligned`" in section
        assert "10.3390/en19040964" in section
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
                    "msre_pump_transient_benchmark": {
                        "model": "msre_pump_transient_benchmark_screen",
                        "screening_status": "watch",
                        "source": "https://doi.org/10.1080/00295639.2025.2475650",
                        "benchmark_mean_error_startup_pcm": {"min": 11.0, "max": 21.0},
                        "benchmark_mean_error_coastdown_pcm": {"min": 5.0, "max": 13.0},
                        "non_active_salt_inventory_fraction": 0.12,
                        "stagnant_salt_inventory_fraction": 0.03,
                        "interpretation": "Bypass-like inventory can affect early transient reactivity rates.",
                    },
                    "physics_core": {
                        "precursor_transport": {
                            "model": "finite_volume_advection_diffusion_decay",
                            "loop_residence_time_s": 4.5,
                            "loop_residence_basis": "fuel_salt_inventory_minus_active_core_volume_over_primary_flow",
                            "transport_loss_fraction": 0.31,
                            "decay_heat_precursors": {
                                "model": "finite_volume_decay_heat_precursor_transport",
                                "core_decay_heat_source_fraction": 0.58,
                                "loop_decay_heat_source_fraction": 0.42,
                                "dominant_loop_segment": {
                                    "region": "loop",
                                    "segment_id": "heat_exchanger_and_offgas_contact",
                                    "decay_heat_source_fraction": 0.2,
                                },
                                "source": "https://doi.org/10.1016/j.applthermaleng.2026.129983",
                            },
                        }
                    },
                    "transport_solver": {
                        "status": "completed",
                        "model": "native_rz_rkdg_scalar_transport_v1",
                        "mesh": {"type": "rz_structured", "radial_cells": 4, "axial_cells": 8},
                        "polynomial_order": 1,
                        "time_integration": "ssp_rk3",
                        "time_step_s": 0.01,
                        "cfl": 0.35,
                        "conservation_residual": 2.0e-8,
                        "minimum_field_value": 0.0,
                        "source_fractions": {
                            "delayed_neutron_precursors": {"outlet_source_fraction": 0.12}
                        },
                        "artifacts": {"solution_path": "transport_solution.npz"},
                    },
                    "depletion_matrix": {
                        "status": "completed",
                        "model": "native_sparse_bateman_depletion_matrix_v1",
                        "backend": "scipy_sparse_expm_multiply",
                        "chain_name": "tiny_thorium_test_chain",
                        "chain_format": "yaml",
                        "isotope_count": 6,
                        "zone_count": 1,
                        "matrix_shape": [6, 6],
                        "matrix_nonzero_entries": 9,
                        "steps": 1,
                        "time_step_days": 0.01,
                        "inventory_delta_fraction": -0.001,
                        "feed_total_atoms": 0.0,
                        "atom_balance_residual": 1.0e-10,
                        "artifacts": {
                            "matrix_path": "depletion_matrix.npz",
                            "history_path": "depletion_history.json",
                        },
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
        assert "355.4" in report
        assert "0.005402" in report
        assert "## MSRE Pump Transient Validation" in report
        assert "11 to 21" in report
        assert "0.12" in report
        assert "## Physics Core Transport" in report
        assert "finite_volume_decay_heat_precursor_transport" in report
        assert "heat_exchanger_and_offgas_contact" in report
        assert "## Native RKDG Transport" in report
        assert "native_rz_rkdg_scalar_transport_v1" in report
        assert "transport_solution.npz" in report
        assert "## Native Sparse Depletion" in report
        assert "tiny_thorium_test_chain" in report
        assert "depletion_matrix.npz" in report
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


def test_report_reconciles_construction_checks_with_primary_physics_gap() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-reporting-residual-reconcile" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = load_case_config(REPO_ROOT / "configs" / "cases" / "tmsr_lf1_core" / "case.yaml")
        benchmark = load_yaml(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml")
        summary = {
            "case": config.name,
            "result_dir": str(scratch_root),
            "neutronics": {"status": "dry-run"},
            "metrics": {"expected_cells": 456, "channel_count": 91},
        }
        summary["benchmark_residuals"] = build_benchmark_residuals(config, summary, benchmark)
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        validation_path = scratch_root / "validation.json"
        validation_path.write_text(
            json.dumps(
                {
                    "checks": [
                        {"name": "channel_count", "status": "pass", "message": "91 is within the expected range."}
                    ]
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            config.name,
            config.data,
            summary_path,
            validation_path,
            None,
            benchmark,
        )

        residuals = _section(report, "## Benchmark Residuals")
        assert "Status: `blocked_missing_primary_physics`" in residuals
        assert "Completed physics residuals: `0/1`" in residuals
        assert "Benchmark blocker: Primary physics benchmark metric 'keff'" in residuals
        assert "`channel_count`: metric=`channel_count`, category=`construction_check`, status=`pass`" in residuals
        assert "category=`physics_benchmark`, status=`pending`" in residuals
        assert "`channel_count`: metric=`channel_count`, category=`construction_check`, status=`pending`" not in residuals
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


def test_report_writes_validation_claims_limitations_and_qa_artifacts() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-reporting-ready-issues" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "case": "tmsr_lf1_core",
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "dry-run"},
                    "metrics": {"keff": 1.01, "channel_count": 91},
                    "benchmark_quality": {
                        "quality_stage": "benchmark_blocked",
                        "benchmark_ready": False,
                        "gates": [{"id": "keff_core_band", "status": "fail", "message": "keff is not solver-backed."}],
                    },
                    "chemistry": {
                        "model": "salt_redox_cleanup_proxy",
                        "redox_state_ev": -0.02,
                        "target_redox_state_ev": -0.03,
                        "impurity_fraction": 0.0001,
                        "corrosion_risk": "high",
                    },
                    "tritium": {
                        "model": "tmsr_lithium_salt_tritium_distribution_screen",
                        "environmental_release_fraction": 0.12,
                        "removal_fraction": 0.88,
                    },
                    "fuel_cycle": {
                        "depletion_chain": "thorium_u233_cleanup_proxy",
                        "cleanup_turnover_days": 10.0,
                        "specific_power_mw_per_t_hm": 988.23,
                    },
                    "graphite_lifetime": {
                        "screening_status": "watch",
                        "estimated_lifespan_years": 0.54166,
                        "lifetime_margin": 0.067708,
                    },
                    "flow": {
                        "reduced_order": {
                            "active_flow": {
                                "channel_count": 1,
                                "representative_velocity_m_s": 4.0,
                            }
                        }
                    },
                    "primary_system": {
                        "inventory": {
                            "coolant_salt": {"net_pool_inventory_m3": 0.0},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        validation_path = scratch_root / "validation.json"
        validation_path.write_text(
            json.dumps(
                {
                    "checks": [
                        {"name": "keff_core_band", "status": "pending", "message": "Awaiting solver-backed keff."},
                        {"name": "physics::active_channel_velocity_reasonable", "status": "fail", "message": "Too high."},
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
                }
            },
            summary_path,
            validation_path,
            None,
        )

        assert "## Results Generated In This Run" in report
        assert "## Validation And Blockers" in report
        assert "| keff_core_band | keff | pending | high | Awaiting solver-backed keff. |" in report
        assert "## Interpretation" in report
        assert "## Limitations" in report
        assert "## Result Claims" in report
        assert "## Method Cards" in report
        assert "Chemistry proxy" in report
        assert "Tritium screen" in report
        assert "Fuel-cycle proxy" in report
        assert "keff_core_band" not in _section(report, "## Validation Appendix").splitlines()[4:]
        assert (scratch_root / "validation_summary.json").exists()
        assert (scratch_root / "validation_details.csv").exists()
        assert (scratch_root / "limitations_matrix.json").exists()
        assert (scratch_root / "result_claims.json").exists()
        assert (scratch_root / "design_readiness.json").exists()
        assert (scratch_root / "presentation_qa.json").exists()

        validation_summary = json.loads((scratch_root / "validation_summary.json").read_text(encoding="utf-8"))
        assert validation_summary["status_counts"]["pending"] == 1
        assert validation_summary["details_json"] == "validation_summary.json"
        assert validation_summary["details_csv"] == "validation_details.csv"
        assert validation_summary["blockers"][0]["name"] == "keff_core_band"
        limitations = json.loads((scratch_root / "limitations_matrix.json").read_text(encoding="utf-8"))
        assert {row["area"] for row in limitations} >= {"neutronics_status", "benchmark_quality", "cross_code_validation"}
        readiness = json.loads((scratch_root / "design_readiness.json").read_text(encoding="utf-8"))
        severe_metrics = {item["metric"]: item["severity"] for item in readiness["findings"]}
        assert severe_metrics["graphite_lifetime"] == "disqualifying_for_claimed_use"
        assert severe_metrics["coolant_salt_inventory"] == "major_concern"
        assert severe_metrics["active_flow_channel_count"] == "major_concern"
        assert severe_metrics["specific_power"] == "major_concern"
        assert readiness["commercial_or_build_candidate_language_allowed"] is False
        claims = json.loads((scratch_root / "result_claims.json").read_text(encoding="utf-8"))
        assert any(claim["evidence_tier"] == "curated_validation_summary" for claim in claims)
        qa = json.loads((scratch_root / "presentation_qa.json").read_text(encoding="utf-8"))
        assert qa["passed"] is True
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_presentation_qa_catches_bad_report_bundle() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-reporting-presentation-qa" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        (scratch_root / "summary.json").write_text(json.dumps({"neutronics": {"status": "dry-run"}}), encoding="utf-8")
        (scratch_root / "plots_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "figures": {
                        "bad": {
                            "plot_id": "bad",
                            "path": "C:/outside/bad.svg",
                            "caption": "",
                            "quality_status": "publication_ready",
                            "report_section": "primary",
                            "units": {"y": "mixed"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        report_text = "## Results Generated In This Run\n\n- `summary.json`\n\n## Validation And Blockers\n\n- none\n\n"

        qa = build_presentation_qa(scratch_root, report_text=report_text + "- raw `None`\n")

        failed = {check["name"] for check in qa["checks"] if check["status"] == "fail"}
        assert not qa["passed"]
        assert "report::required_sections_nonempty" in failed
        assert "report::no_raw_python_none" in failed
        assert "figures::manifest_metadata" in failed
        assert "figures::no_mixed_unit_primary_charts" in failed
        assert "figures::portable_paths" in failed
        assert "report::dry_run_proxy_warning" in failed
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
                        "benchmark_quality": {
                            "quality_score": 50.0,
                            "quality_stage": "benchmark_blocked",
                            "benchmark_ready": False,
                            "failed_gate_count": 1,
                            "gates": [
                                {
                                    "id": "benchmark_geometry_reconstructed",
                                    "status": "fail",
                                    "message": "Case geometry is not marked as a benchmark reconstruction.",
                                }
                            ],
                        },
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
        assert "## Benchmark Quality Gates" in report
        assert "benchmark_geometry_reconstructed" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_includes_flagship_finance_schedule_and_taxonomy() -> None:
    scratch_root = Path(__file__).resolve().parents[1] / ".tmp" / "test-reporting-finance" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "case": "flagship_grid_msr",
                    "result_dir": str(scratch_root),
                    "neutronics": {"status": "not_run"},
                    "metrics": {"finance_lcoe_usd_per_mwh": 190.0},
                    "finance": {
                        "status": "completed",
                        "scenario": "conservative_foak",
                        "planning_basis": "planning_grade_not_vendor_quote",
                        "source_year_usd": 2022,
                        "inputs": {
                            "net_capacity_mwe": 300.0,
                            "capacity_factor": 0.93,
                            "source_occ_usd_per_kwe": 10000.0,
                            "overnight_cost_uplift": 1.25,
                            "real_wacc": 0.08,
                        },
                        "cost_breakdown_usd": {
                            "net_overnight_cost": 3_750_000_000.0,
                            "interest_during_construction": 1_000_000_000.0,
                            "total_capitalized_cost": 4_750_000_000.0,
                        },
                        "annual_costs_usd_per_year": {
                            "annualized_capital": 380_000_000.0,
                            "total": 480_000_000.0,
                        },
                        "outputs": {
                            "annual_generation_mwh": 2_444_040.0,
                            "lcoe_usd_per_mwh": 190.0,
                            "lcoe_cents_per_kwh": 19.0,
                        },
                    },
                    "schedule": {
                        "status": "completed",
                        "planning_basis": "U.S. NRC Part 52",
                        "project_start_date": "2026-05-02",
                        "construction_start_date": "2032-11-02",
                        "commercial_operation_date": "2040-11-02",
                        "total_months_to_commercial_operation": 174,
                        "total_years_to_commercial_operation": 14.5,
                        "phases": [
                            {
                                "id": "nuclear_construction",
                                "start_date": "2032-11-02",
                                "end_date": "2040-01-02",
                                "duration_months": 86,
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

        report = generate_report(
            "flagship_grid_msr",
            {
                "reactor": {
                    "name": "Flagship Grid Thorium MSR",
                    "family": "commercial grid-connected thorium molten-salt SMR",
                    "stage": "commercial-planning",
                    "mode": "commercial_grid",
                    "design_power_mwth": 792.242363,
                    "benchmark": "benchmarks/tmsr_lf1/benchmark.yaml",
                    "characteristics": {
                        "reactor_class": "molten_salt_smr",
                        "licensing_basis": "U.S. NRC Part 52 combined license",
                        "grid_role": "firm zero-carbon grid generation",
                        "module_count": 1,
                        "net_electric_power_mwe": 300.0,
                        "thermal_power_mwth": 792.242363,
                    },
                }
            },
            summary_path,
            None,
            None,
        )

        assert "## Reactor Classification" in report
        assert "commercial flagship grid reactor" in report
        assert "## Flagship Characteristics" in report
        assert "## Commercial Finance" in report
        assert "conservative_foak" in report
        assert "## Build Schedule" in report
        assert "2040-11-02" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_flagship_report_caveats_commercial_planning_against_dry_run_surrogate_evidence() -> None:
    scratch_root = REPO_ROOT / ".tmp" / "test-reporting-flagship-evidence" / uuid.uuid4().hex
    scratch_root.mkdir(parents=True, exist_ok=True)
    try:
        config = load_case_config(REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml")
        benchmark = load_yaml(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml")
        traceability = assess_benchmark_traceability(config, benchmark)
        summary = {
            "case": config.name,
            "result_dir": str(scratch_root),
            "neutronics": {"status": "dry-run"},
            "model_representation": config.data["model_representation"],
            "metrics": {"expected_cells": 456, "channel_count": 91},
            "benchmark_traceability": traceability,
            "benchmark_quality": traceability["benchmark_quality"],
            "finance": {
                "status": "completed",
                "scenario": "conservative_foak",
                "planning_basis": "planning_grade_not_vendor_quote",
                "source_year_usd": 2022,
            },
            "schedule": {
                "status": "completed",
                "planning_basis": "U.S. NRC Part 52",
                "commercial_operation_date": "2040-11-02",
            },
        }
        summary["benchmark_residuals"] = build_benchmark_residuals(config, summary, benchmark)
        summary_path = scratch_root / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        report = generate_report(
            config.name,
            config.data,
            summary_path,
            None,
            None,
            benchmark,
        )

        evidence = _section(report, "## Evidence Status")
        classification = _section(report, "## Reactor Classification")
        finance = _section(report, "## Commercial Finance")
        schedule = _section(report, "## Build Schedule")

        assert "dry-run/proxy" in evidence
        assert "not a solver-backed OpenMC physics result" in evidence
        assert "Scale/surrogate mismatch" in evidence
        assert "benchmark nominal thermal power 2 MWth" in evidence
        assert "not same-scale validation" in evidence
        assert "Taxonomy role: `commercial flagship grid reactor planning case`" in classification
        assert "Build candidate: `blocked_by_evidence`" in classification
        assert "Commercial finance subject: `planning_only`" in classification
        assert "not build-ready or commercially validated" in classification
        assert "Build candidate: `true`" not in classification
        assert "Planning-only finance" in finance
        assert "not a build-ready commitment" in schedule
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
