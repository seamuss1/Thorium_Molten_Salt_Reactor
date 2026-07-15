from pathlib import Path
import json
import os
import time

import pytest

from thorium_reactor.paths import (
    append_stage_manifest,
    changed_bundle_artifacts,
    create_result_bundle,
    latest_result_bundle,
    refresh_bundle_artifact_statuses,
    snapshot_bundle_artifacts,
)


def test_latest_result_bundle_prefers_newest_run_regardless_of_run_id_format(tmp_path: Path) -> None:
    old = create_result_bundle(tmp_path, "layout_case", "20260101-000000-000001-aaaaaaaa")
    web = create_result_bundle(tmp_path, "layout_case", "web-demo")
    newest = create_result_bundle(tmp_path, "layout_case", "20260102-000000-000001-bbbbbbbb")

    base = time.time()
    os.utime(old.root, (base - 300, base - 300))
    os.utime(web.root, (base - 200, base - 200))
    os.utime(newest.root, (base - 100, base - 100))
    assert latest_result_bundle(tmp_path, "layout_case").run_id == newest.run_id

    os.utime(web.root, (base, base))
    assert latest_result_bundle(tmp_path, "layout_case").run_id == "web-demo"

def test_result_bundle_creates_dedicated_geometry_exports_dir(tmp_path: Path) -> None:
    repo_root = tmp_path / "bundle-layout-test"
    bundle = create_result_bundle(repo_root, "layout_case", "unit-test")

    assert bundle.geometry_exports_dir == repo_root / "results" / "layout_case" / "unit-test" / "geometry" / "exports"
    assert bundle.geometry_exports_dir.exists()
    assert bundle.plots_dir.exists()
    status = json.loads((bundle.root / "artifact_status.json").read_text(encoding="utf-8"))
    assert status["groups"]["openmc"]["state"] == "not_generated"
    assert status["groups"]["geometry_exports"]["state"] == "not_generated"
    assert (bundle.openmc_dir / "status.json").exists()


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


def test_bundle_artifact_status_flags_absent_openmc_statepoint(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "layout_case", "status-test")
    status = refresh_bundle_artifact_statuses(bundle, summary={"neutronics": {"status": "dry-run"}})

    assert status["groups"]["openmc"]["state"] == "dry_run"
    assert "No solver-backed OpenMC statepoint artifact" in status["blockers"][0]


def test_stage_manifest_records_ordered_outputs(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "layout_case", "stage-test")
    before = snapshot_bundle_artifacts(bundle)
    bundle.write_json("summary.json", {"neutronics": {"status": "dry-run"}})

    manifest = append_stage_manifest(
        bundle,
        stage="run",
        command=["run", "layout_case", "--no-solver"],
        started_utc="2026-05-18T00:00:00Z",
        status="completed",
        inputs={"input_snapshots": {"case": {"sha256": "abc"}}},
        output_artifacts=sorted(changed_bundle_artifacts(before, snapshot_bundle_artifacts(bundle))),
        method_tier="dry_run_proxy",
    )

    assert manifest["stages"][0]["sequence"] == 1
    assert manifest["stages"][0]["args"] == ["layout_case", "--no-solver"]
    assert manifest["stages"][0]["input_snapshot"]["case"]["sha256"] == "abc"
    assert "summary.json" in manifest["stages"][0]["output_artifacts"]
