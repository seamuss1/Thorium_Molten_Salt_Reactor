from pathlib import Path

from thorium_reactor.config import load_case_config
from thorium_reactor.geometry.exporters import export_geometry, render_svg
from thorium_reactor.neutronics.workflows import build_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_render_svg_emits_svg_markup() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "fuel_channel" / "case.yaml")
    built = build_case(config)
    svg = render_svg(built.geometry_description)

    assert svg.startswith("<svg")
    assert "circle" in svg


def test_detailed_core_export_emits_png_asset() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "tmsr_lf1_core" / "case.yaml")
    built = build_case(config)
    output_dir = REPO_ROOT / ".tmp" / "geometry-export-test"
    output_dir.mkdir(parents=True, exist_ok=True)

    assets = export_geometry(built.geometry_description, output_dir)

    assert "png" in assets
    assert "stl" in assets
    assert Path(assets["png"]).exists()
    assert Path(assets["stl"]).exists()
    assert Path(assets["stl"]).read_text(encoding="utf-8").startswith("solid procedural_reactor_geometry")
