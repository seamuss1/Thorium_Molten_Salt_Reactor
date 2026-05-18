import json
import xml.etree.ElementTree as ET
from pathlib import Path

from thorium_reactor.paths import create_result_bundle
from thorium_reactor.reporting.plots import (
    generate_summary_plots,
    generate_validation_plot,
    load_figure_catalog,
    load_plot_manifest,
)

SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def _svg_root(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))


def _axis_tick_labels(path: Path, axis: str) -> list[str]:
    root = _svg_root(path)
    return [
        element.text or ""
        for element in root.findall(".//svg:text", SVG_NS)
        if element.attrib.get("data-axis") == axis
    ]


def _axis_tick_marks(path: Path, axis: str) -> list[ET.Element]:
    root = _svg_root(path)
    return [
        element
        for element in root.findall(".//svg:line", SVG_NS)
        if element.attrib.get("data-axis") == axis
    ]


def test_generate_summary_plots_populates_plots_dir(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
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

    manifest_path = bundle.root / "plots_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy_manifest = load_plot_manifest(manifest_path)
    catalog = load_figure_catalog(manifest_path)
    metrics_figure = payload["figures"]["metrics_overview"]

    assert payload["schema_version"] == 2
    assert legacy_manifest["metrics_overview"] == assets["metrics_overview"]
    assert catalog["bop_balance"]["path"] == assets["bop_balance"]
    assert metrics_figure["plot_id"] == "metrics_overview"
    assert metrics_figure["path"] == assets["metrics_overview"]
    assert metrics_figure["title"] == "Metrics overview"
    assert metrics_figure["caption"]
    assert metrics_figure["quality_status"] == "publication_ready"
    assert metrics_figure["report_section"] == "primary"
    assert metrics_figure["axes"]["x"] == "Metric"
    assert metrics_figure["units"]["y"] == "mixed"
    assert metrics_figure["conclusion"]


def test_generate_summary_plots_emits_flow_interface_plot_when_available(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
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


def test_generate_summary_plots_emits_active_flow_allocation_plot(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
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


def test_generate_validation_plot_updates_manifest(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
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

    figure = load_figure_catalog(bundle.root / "plots_manifest.json")["validation_summary"]
    assert figure["plot_id"] == "validation_summary"
    assert figure["quality_status"] == "publication_ready"
    assert figure["units"]["y"] == "checks"
    assert "failures" in figure["conclusion"]


def test_load_plot_manifest_reads_legacy_flat_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "plots_manifest.json"
    plot_path = tmp_path / "plots" / "legacy.svg"
    manifest_path.write_text(json.dumps({"legacy_plot": str(plot_path)}), encoding="utf-8")

    manifest = load_plot_manifest(manifest_path)
    catalog = load_figure_catalog(manifest_path)

    assert manifest == {"legacy_plot": str(plot_path)}
    assert catalog["legacy_plot"]["plot_id"] == "legacy_plot"
    assert catalog["legacy_plot"]["path"] == str(plot_path)
    assert catalog["legacy_plot"]["quality_status"] == "diagnostic_only"
    assert catalog["legacy_plot"]["caption"]


def test_mixed_v2_manifest_preserves_legacy_top_level_entries(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
    manifest_path = bundle.root / "plots_manifest.json"
    v2_path = bundle.plots_dir / "metrics_overview.svg"
    legacy_path = bundle.plots_dir / "uncertainty_tornado.svg"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "figures": {
                    "metrics_overview": {
                        "plot_id": "metrics_overview",
                        "path": str(v2_path),
                        "title": "Custom metrics title",
                    }
                },
                "uncertainty_tornado": str(legacy_path),
            }
        ),
        encoding="utf-8",
    )

    manifest = load_plot_manifest(manifest_path)
    catalog = load_figure_catalog(manifest_path)

    assert manifest == {
        "metrics_overview": str(v2_path),
        "uncertainty_tornado": str(legacy_path),
    }
    assert catalog["metrics_overview"]["title"] == "Custom metrics title"
    assert catalog["uncertainty_tornado"]["plot_id"] == "uncertainty_tornado"
    assert catalog["uncertainty_tornado"]["path"] == str(legacy_path)

    generate_validation_plot(bundle, {"case": "plot_case", "checks": [{"status": "pass"}]})
    updated_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    updated_manifest = load_plot_manifest(manifest_path)

    assert "uncertainty_tornado" in updated_payload["figures"]
    assert updated_manifest["uncertainty_tornado"] == str(legacy_path)
    assert updated_manifest["validation_summary"].endswith("validation_summary.svg")


def test_generate_summary_plots_emits_transient_plots_when_history_exists(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
    transient_path = bundle.write_json(
        "transient.json",
        {
            "history": [
                {"time_s": 0.0, "power_fraction": 1.0, "fuel_temp_c": 690.0},
                {"time_s": 10.0, "power_fraction": 1.08, "fuel_temp_c": 702.0},
                {"time_s": 20.0, "power_fraction": 1.03, "fuel_temp_c": 698.0},
            ]
        },
    )
    summary = {
        "case": "plot_case",
        "metrics": {
            "channel_count": 91,
        },
        "transient": {
            "history_path": str(transient_path),
        },
        "neutronics": {
            "status": "completed",
        },
    }

    assets = generate_summary_plots(bundle, summary)

    assert "transient_power" in assets
    assert "transient_fuel_temperature" in assets
    assert Path(assets["transient_power"]).exists()
    assert Path(assets["transient_fuel_temperature"]).exists()
    assert _axis_tick_labels(Path(assets["transient_power"]), "x") == ["0", "10", "20"]
    assert len(_axis_tick_marks(Path(assets["transient_power"]), "x")) == 3


def test_generate_summary_plots_emits_transient_sweep_envelopes_when_history_exists(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
    transient_sweep_path = bundle.write_json(
        "transient_sweep.json",
        {
            "history": [
                {
                    "time_s": 0.0,
                    "power_fraction_p05": 0.98,
                    "power_fraction_p50": 1.0,
                    "power_fraction_p95": 1.03,
                    "fuel_temp_c_p05": 685.0,
                    "fuel_temp_c_p50": 690.0,
                    "fuel_temp_c_p95": 696.0,
                },
                {
                    "time_s": 10.0,
                    "power_fraction_p05": 1.02,
                    "power_fraction_p50": 1.08,
                    "power_fraction_p95": 1.16,
                    "fuel_temp_c_p05": 692.0,
                    "fuel_temp_c_p50": 701.0,
                    "fuel_temp_c_p95": 714.0,
                },
            ]
        },
    )
    summary = {
        "case": "plot_case",
        "metrics": {
            "channel_count": 91,
        },
        "transient_sweep": {
            "history_path": str(transient_sweep_path),
        },
        "neutronics": {
            "status": "completed",
        },
    }

    assets = generate_summary_plots(bundle, summary)

    assert "transient_sweep_power_envelope" in assets
    assert "transient_sweep_fuel_temperature_envelope" in assets
    assert Path(assets["transient_sweep_power_envelope"]).exists()
    assert Path(assets["transient_sweep_fuel_temperature_envelope"]).exists()
    assert _axis_tick_labels(Path(assets["transient_sweep_power_envelope"]), "x") == ["0", "5", "10"]
    assert len(_axis_tick_marks(Path(assets["transient_sweep_power_envelope"]), "x")) == 3


def test_line_plot_y_tick_labels_do_not_collapse_for_nonzero_range(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
    transient_path = bundle.write_json(
        "transient.json",
        {
            "history": [
                {"time_s": 0.0, "power_fraction": 1.000001},
                {"time_s": 5.0, "power_fraction": 1.000003},
                {"time_s": 10.0, "power_fraction": 1.000005},
            ]
        },
    )
    summary = {
        "case": "plot_case",
        "metrics": {
            "channel_count": 91,
        },
        "transient": {
            "history_path": str(transient_path),
        },
        "neutronics": {
            "status": "completed",
        },
    }

    assets = generate_summary_plots(bundle, summary)
    y_tick_labels = _axis_tick_labels(Path(assets["transient_power"]), "y")

    assert y_tick_labels
    assert len(set(y_tick_labels)) > 1


def test_zero_valued_bar_entries_do_not_render_filled_bar_rects(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "plot_case", "run")
    validation = {
        "case": "plot_case",
        "checks": [
            {"status": "pass"},
        ],
    }

    assets = generate_validation_plot(bundle, validation)
    root = _svg_root(Path(assets["validation_summary"]))
    bar_rects = [
        element
        for element in root.findall(".//svg:rect", SVG_NS)
        if element.attrib.get("rx") == "6"
    ]
    text_values = [element.text or "" for element in root.findall(".//svg:text", SVG_NS)]

    assert len(bar_rects) == 1
    assert "fail" in text_values
    assert "pending" in text_values
