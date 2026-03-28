import shutil
from pathlib import Path

from PIL import Image

from thorium_reactor.config import load_case_config
from thorium_reactor.geometry.exporters import export_geometry, render_svg, validate_watertight_meshes
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


def test_immersed_pool_reference_export_supports_box_and_horizontal_primitives() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    built = build_case(config)
    output_dir = REPO_ROOT / ".tmp" / "immersed-pool-geometry-export-test"
    output_dir.mkdir(parents=True, exist_ok=True)

    assets = export_geometry(built.geometry_description, output_dir)
    obj_text = Path(assets["obj"]).read_text(encoding="utf-8")

    assert Path(assets["png"]).exists()
    assert Path(assets["obj"]).exists()
    assert Path(assets["gif"]).exists()
    assert Path(assets["mesh_validation"]).exists()
    if shutil.which("ffmpeg") is not None:
        assert Path(assets["mp4"]).exists()
    assert not list(output_dir.glob(".*-frames*"))
    assert "core_box_left_wall" in obj_text
    assert "primary_heat_exchanger" in obj_text

    with Image.open(assets["gif"]) as animation:
        assert getattr(animation, "is_animated", False) is True
        assert animation.n_frames >= 20

    mesh_checks = validate_watertight_meshes(built.geometry_description)
    assert mesh_checks
    assert all(check["watertight"] for check in mesh_checks)
