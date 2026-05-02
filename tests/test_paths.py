from pathlib import Path

import pytest

from thorium_reactor.paths import create_result_bundle

def test_result_bundle_creates_dedicated_geometry_exports_dir(tmp_path: Path) -> None:
    repo_root = tmp_path / "bundle-layout-test"
    bundle = create_result_bundle(repo_root, "layout_case", "unit-test")

    assert bundle.geometry_exports_dir == repo_root / "results" / "layout_case" / "unit-test" / "geometry" / "exports"
    assert bundle.geometry_exports_dir.exists()
    assert bundle.plots_dir.exists()


def test_default_result_bundle_ids_do_not_collide(tmp_path: Path) -> None:
    first = create_result_bundle(tmp_path, "layout_case")
    second = create_result_bundle(tmp_path, "layout_case")

    assert first.run_id != second.run_id
    assert first.root != second.root
    assert first.root.exists()
    assert second.root.exists()


def test_explicit_result_bundle_id_collision_is_rejected(tmp_path: Path) -> None:
    create_result_bundle(tmp_path, "layout_case", "fixed")

    with pytest.raises(FileExistsError):
        create_result_bundle(tmp_path, "layout_case", "fixed")
