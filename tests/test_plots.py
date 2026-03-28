from pathlib import Path

from thorium_reactor.paths import create_result_bundle
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot, load_plot_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_generate_summary_plots_populates_plots_dir() -> None:
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "plot-test", "plot_case", "run")
    summary = {
        "case": "plot_case",
        "metrics": {
            "keff": 0.987,
            "channel_count": 91,
        },
        "bop": {
            "thermal_power_mw": 250.0,
            "electric_power_mw": 95.0,
        },
        "neutronics": {
            "status": "completed",
        },
    }

    assets = generate_summary_plots(bundle, summary)

    assert "metrics_overview" in assets
    assert "bop_balance" in assets
    assert Path(assets["metrics_overview"]).exists()
    assert Path(assets["bop_balance"]).exists()


def test_generate_summary_plots_emits_flow_interface_plot_when_available() -> None:
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "plot-flow-test", "plot_case", "run")
    summary = {
        "case": "plot_case",
        "metrics": {
            "channel_count": 91,
        },
        "flow": {
            "interface_metrics": {
                "plenum_connected_channels": 37,
                "reflector_backed_channels": 54,
                "plenum_connected_salt_area_cm2": 9.813587,
                "reflector_backed_salt_area_cm2": 13.50382,
            }
        },
        "neutronics": {
            "status": "completed",
        },
    }

    assets = generate_summary_plots(bundle, summary)

    assert "flow_interfaces" in assets
    assert Path(assets["flow_interfaces"]).exists()


def test_generate_summary_plots_emits_active_flow_allocation_plot() -> None:
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "plot-active-flow-test", "plot_case", "run")
    summary = {
        "case": "plot_case",
        "metrics": {
            "channel_count": 91,
        },
        "flow": {
            "reduced_order": {
                "variant_summary": [
                    {"variant": "fuel", "allocated_mass_flow_kg_s": 991.839099},
                    {"variant": "control_guides", "allocated_mass_flow_kg_s": 124.232329},
                ]
            }
        },
        "neutronics": {
            "status": "completed",
        },
    }

    assets = generate_summary_plots(bundle, summary)

    assert "active_flow_allocation" in assets
    assert Path(assets["active_flow_allocation"]).exists()


def test_generate_validation_plot_updates_manifest() -> None:
    bundle = create_result_bundle(REPO_ROOT / ".tmp" / "plot-validation-test", "plot_case", "run")
    validation = {
        "case": "plot_case",
        "checks": [
            {"status": "pass"},
            {"status": "fail"},
            {"status": "pending"},
        ],
    }

    assets = generate_validation_plot(bundle, validation)
    manifest = load_plot_manifest(bundle.root / "plots_manifest.json")

    assert "validation_summary" in assets
    assert "validation_summary" in manifest
    assert Path(assets["validation_summary"]).exists()
