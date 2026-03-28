from pathlib import Path

from thorium_reactor.paths import create_result_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_result_bundle_creates_dedicated_geometry_exports_dir() -> None:
    repo_root = REPO_ROOT / ".tmp" / "bundle-layout-test"
    bundle = create_result_bundle(repo_root, "layout_case", "unit-test")

    assert bundle.geometry_exports_dir == repo_root / "results" / "layout_case" / "unit-test" / "geometry" / "exports"
    assert bundle.geometry_exports_dir.exists()
    assert bundle.plots_dir.exists()
