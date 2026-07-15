"""Adversarial regression fixtures for the evidence/readiness trust contract.

These prove the repo refuses to overclaim solver-backed, benchmark-ready, or
build-candidate status when evidence is stale, missing, failed, or contradictory.
Fixtures are small and deterministic (no solver-backed OpenMC runs).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from thorium_reactor.evidence import build_evidence_status, load_canonical_artifact_status
from thorium_reactor.paths import create_result_bundle, refresh_bundle_artifact_statuses
from thorium_reactor.reporting.reports import build_presentation_qa
from thorium_reactor.sidecar_schemas import (
    SidecarValidationError,
    validate_artifact_status,
    validate_bundle_sidecars,
    validate_stage_manifest,
)


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_completed_status_without_statepoint_is_not_solver_backed(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "no-statepoint")
    summary = {"neutronics": {"status": "completed"}}
    refresh_bundle_artifact_statuses(bundle, summary=summary)

    evidence = build_evidence_status(bundle.root, summary)

    assert evidence["has_solver_backed_statepoint"] is False
    assert evidence["can_use_solver_backed_language"] is False
    assert any("statepoint" in blocker.lower() for blocker in evidence["blockers"])


def test_completed_status_with_statepoint_is_solver_backed(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "with-statepoint")
    (bundle.openmc_dir / "statepoint.100.h5").write_bytes(b"\x89HDF\r\n\x1a\n")
    summary = {"neutronics": {"status": "completed"}}
    refresh_bundle_artifact_statuses(bundle, summary=summary)

    evidence = build_evidence_status(bundle.root, summary)

    assert evidence["has_solver_backed_statepoint"] is True
    assert evidence["can_use_solver_backed_language"] is True
    assert not any("statepoint" in blocker.lower() for blocker in evidence["blockers"])


def test_stale_embedded_artifact_status_is_ignored_for_fresh_sidecar(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "stale-embedded")
    (bundle.openmc_dir / "statepoint.50.h5").write_bytes(b"\x89HDF\r\n\x1a\n")
    refresh_bundle_artifact_statuses(bundle, summary={"neutronics": {"status": "completed"}})
    summary = {
        "neutronics": {"status": "completed"},
        # Stale copy claims the folder was never generated.
        "artifact_status": {"groups": {"openmc": {"state": "not_generated", "artifacts": []}}},
    }

    canonical = load_canonical_artifact_status(bundle.root, summary)
    assert canonical["groups"]["openmc"]["state"] == "completed"

    evidence = build_evidence_status(bundle.root, summary)
    assert evidence["can_use_solver_backed_language"] is True


def test_failed_state_blocks_solver_backed_language(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "failed-openmc")
    # No statepoint and a completed status forces the sidecar to a failed state.
    (bundle.openmc_dir / "openmc_error.log").write_text("solver crashed", encoding="utf-8")
    summary = {"neutronics": {"status": "completed"}}
    status = refresh_bundle_artifact_statuses(bundle, summary=summary)

    assert status["groups"]["openmc"]["state"] in {"failed", "skipped"}
    assert build_evidence_status(bundle.root, summary)["can_use_solver_backed_language"] is False


def test_report_build_candidate_true_with_dry_run_evidence_fails_qa(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "build-candidate-dryrun")
    summary = {"neutronics": {"status": "dry-run"}}
    _write(bundle.root / "summary.json", summary)
    refresh_bundle_artifact_statuses(bundle, summary=summary)
    report_text = (
        "## Reactor Classification\n\n- Build candidate: `true`\n\n"
        "## Results Generated In This Run\n\n- `summary.json`\n\n"
        "## Validation And Blockers\n\n- none\n\n## Interpretation\n\n- x\n\n"
        "## Limitations\n\n- x\n\n## Future Work / Novelty Tracks\n\n- x\n\n"
        "## Evidence Sources\n\n- x\n\n"
    )

    qa = build_presentation_qa(bundle.root, report_text=report_text)

    failed = {check["name"] for check in qa["checks"] if check["status"] == "fail"}
    assert "report::status_contradictions" in failed
    assert qa["passed"] is False


@pytest.mark.parametrize("bad_state", ["failed", "skipped", "not_generated"])
def test_qa_flags_build_candidate_against_any_non_completed_openmc_state(tmp_path: Path, bad_state: str) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", f"state-{bad_state}")
    summary = {"neutronics": {"status": "dry-run"}}
    _write(bundle.root / "summary.json", summary)
    status = refresh_bundle_artifact_statuses(bundle, summary=summary)
    status["groups"]["openmc"]["state"] = bad_state
    _write(bundle.root / "artifact_status.json", status)
    report_text = (
        "## Reactor Classification\n\n- Build candidate: `true`\n\n"
        "## Results Generated In This Run\n\n- `summary.json`\n\n"
        "## Validation And Blockers\n\n- none\n\n## Interpretation\n\n- x\n\n"
        "## Limitations\n\n- x\n\n## Future Work / Novelty Tracks\n\n- x\n\n"
        "## Evidence Sources\n\n- x\n\n"
    )

    qa = build_presentation_qa(bundle.root, report_text=report_text)

    failed = {check["name"] for check in qa["checks"] if check["status"] == "fail"}
    assert "report::status_contradictions" in failed


def test_failed_stage_manifest_contradicts_benchmark_ready_claim(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "failed-stage")
    (bundle.openmc_dir / "statepoint.10.h5").write_bytes(b"\x89HDF\r\n\x1a\n")
    summary = {"neutronics": {"status": "completed"}, "benchmark_quality": {"benchmark_ready": True}}
    _write(bundle.root / "summary.json", summary)
    refresh_bundle_artifact_statuses(bundle, summary=summary)
    _write(
        bundle.root / "stage_manifest.json",
        {
            "schema_version": 1,
            "case_name": "adv_case",
            "run_id": "failed-stage",
            "stages": [
                {
                    "sequence": 1,
                    "stage": "report",
                    "status": "failed",
                    "started_utc": "2026-07-14T00:00:00Z",
                    "ended_utc": "2026-07-14T00:00:01Z",
                    "command": ["report"],
                }
            ],
        },
    )

    evidence = build_evidence_status(bundle.root, summary)

    assert evidence["failed_stages"] == ["report"]
    assert evidence["can_use_build_candidate_language"] is False


def test_corrupted_sidecar_is_reported_by_schema_validation(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "corrupt-sidecar")
    (bundle.root / "artifact_status.json").write_text("{not valid json", encoding="utf-8")

    errors = validate_bundle_sidecars(bundle.root)

    assert any("artifact_status.json" in error for error in errors)


def test_partial_sidecar_missing_required_field_fails_validation() -> None:
    with pytest.raises(SidecarValidationError) as exc:
        validate_artifact_status({"schema_version": 1, "case_name": "c", "run_id": "r"})
    assert exc.value.field in {"updated_utc", "groups"}

    with pytest.raises(SidecarValidationError):
        validate_stage_manifest({"schema_version": 99, "case_name": "c", "run_id": "r", "stages": []})


def test_rerun_on_existing_bundle_refreshes_sidecar(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "adv_case", "rerun")
    first = refresh_bundle_artifact_statuses(bundle, summary={"neutronics": {"status": "dry-run"}})
    assert first["groups"]["openmc"]["state"] == "dry_run"

    # A later stage adds a statepoint; the refreshed sidecar must reflect it.
    (bundle.openmc_dir / "statepoint.5.h5").write_bytes(b"\x89HDF\r\n\x1a\n")
    second = refresh_bundle_artifact_statuses(bundle, summary={"neutronics": {"status": "completed"}})
    assert second["groups"]["openmc"]["state"] == "completed"
    on_disk = json.loads((bundle.root / "artifact_status.json").read_text(encoding="utf-8"))
    assert on_disk["groups"]["openmc"]["state"] == "completed"
