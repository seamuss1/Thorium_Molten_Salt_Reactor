import csv
import json
import os
import time
from pathlib import Path

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


def _bundle_in(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "configs" / "cases").mkdir(parents=True, exist_ok=True)
    return create_result_bundle(tmp_path, "case", "run")


def test_bundle_writes_are_atomic_and_leave_no_temp_residue(tmp_path: Path) -> None:
    """A reader must never observe a partially written bundle file.

    The web process polls these files from another process while a CLI run
    writes them, and it turns a JSONDecodeError into an empty run, so a torn
    read surfaces as data loss rather than a retry.
    """
    bundle = _bundle_in(tmp_path)
    target = bundle.root / "summary.json"

    bundle.write_json("summary.json", {"generation": 1})
    bundle.write_json("summary.json", {"generation": 2})

    # Overwrite replaced the content rather than appending or truncating.
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}

    # The temp file used for the swap is not left behind for the artifact
    # walker or the web artifact list to pick up.
    assert [p.name for p in bundle.root.iterdir() if p.name.endswith(".tmp")] == []


def test_bundle_write_publishes_by_rename_not_in_place(tmp_path: Path, monkeypatch) -> None:
    """The new content must become visible in one step.

    Asserts the mechanism, not just the end state: while the replacement is
    still pending, a concurrent reader must still see the *old* file in full.
    An in-place write would fail this because the target is already truncated
    by the time the rename would happen.
    """
    bundle = _bundle_in(tmp_path)
    target = bundle.root / "summary.json"
    bundle.write_json("summary.json", {"generation": 1})

    seen_by_reader: list[str] = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        # Stand in for another process reading the bundle mid-write.
        seen_by_reader.append(Path(dst).read_text(encoding="utf-8"))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    bundle.write_json("summary.json", {"generation": 2})

    assert seen_by_reader, "write did not publish through os.replace"
    assert json.loads(seen_by_reader[0]) == {"generation": 1}
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 2}


def test_write_metrics_quotes_values_that_would_break_the_reader(tmp_path: Path) -> None:
    """metrics.csv is read back with csv.DictReader by the web layer.

    Building the file by joining on commas split a value containing a comma
    across columns, so the reader mapped it to the wrong key.
    """
    bundle = _bundle_in(tmp_path)

    bundle.write_metrics({"plain": 1.5, "with,comma": "a,b"})

    with (bundle.root / "metrics.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = {row["metric"]: row["value"] for row in csv.DictReader(handle)}

    assert rows["plain"] == "1.5"
    assert rows["with,comma"] == "a,b"
