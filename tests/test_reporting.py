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
                }
            },
            summary_path,
            validation_path,
            {"render_png": "geometry/exports/core.png"},
            {
                "title": "TMSR-LF1-inspired surrogate benchmark",
                "references": ["Current values are surrogate acceptance bands."],
                "assumptions": ["The model is openly reproducible rather than proprietary."],
                "evidence": [
                    {
                        "topic": "Transient modeling bridge",
                        "source": "https://github.com/openmsr/msrDynamics",
                        "claim": "msrDynamics provides a nodal dynamics API for flowing-fuel systems.",
                        "relevance": "This repo can grow from steady-state BOP into transients.",
                    }
                ],
                "novelty_tracks": [
                    {
                        "name": "Evidence-linked reactor twin",
                        "summary": "Tie assumptions to source-backed evidence and confidence.",
                    }
                ],
            },
        )

        assert "## Benchmark Context" in report
        assert "Current values are surrogate acceptance bands." in report
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
