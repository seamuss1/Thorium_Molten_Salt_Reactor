"""Behavioral coverage for the deployment and job-lifecycle audit findings."""

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thorium_reactor.cli import main
from thorium_reactor.config import ConfigError, load_case_config
from thorium_reactor.web.app import create_app
from thorium_reactor.web.job_ownership import start_process, terminate_process_tree
from thorium_reactor.web.jobs import JobManager, JobQueueFull, write_status
from thorium_reactor.web.permissions import configured_admin_emails
from thorium_reactor.web.repository import WebRepository
from thorium_reactor.web.schemas import SimulationDraft

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'audit-test'\n")
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    shutil.copytree(REPO / "benchmarks", tmp_path / "benchmarks")
    return tmp_path


@pytest.mark.parametrize(
    "patch",
    [
        {"geometry": {"pin_pitch_cm": float("nan")}},
        {"transient": {"duration_s": float("inf")}},
        {"transient": {"time_step_s": 0}},
        {"transient": {"events": [{"time_s": -1}]}},
        {"transient": {"events": [{"time_s": 121}]}},
        {"transient": {"events": [{"time_s": 1, "flow_fraction": "fast"}]}},
        {"transient": {"scenarios": [{"name": "a"}, {"name": "a"}]}},
    ],
)
def test_invalid_draft_rejected_before_quota_or_bundle(repo, patch):
    repository = WebRepository(repo)
    manager = JobManager(repository)
    claims = []
    try:
        with pytest.raises(ConfigError):
            manager.submit(SimulationDraft(case_name="example_pin", patch=patch), claim=lambda: claims.append(1))
        assert claims == []
        assert not (repo / "results/example_pin").exists()
    finally:
        manager.shutdown()


@pytest.mark.parametrize("command", ["transient", "transient-sweep"])
def test_unknown_cli_scenario_creates_no_bundle(repo, command, capsys):
    assert main(["--repo-root", str(repo), command, "immersed_pool_reference", "--scenario", "misspelled"]) == 2
    assert "Unknown transient scenario" in capsys.readouterr().err
    assert not (repo / "results").exists()


@pytest.mark.parametrize(
    ("status", "dry", "expected"),
    [
        ("failed", False, 1),
        ("unavailable", False, 1),
        ("dry-run", False, 1),
        ("completed", False, 0),
        ("dry-run", True, 0),
        ("failed", True, 1),
    ],
)
def test_cli_solver_outcome_controls_exit_and_stage(repo, monkeypatch, capsys, status, dry, expected):
    def run(config, bundle, **kwargs):
        payload = {"neutronics": {"status": status, "message": "solver diagnostic"}}
        bundle.write_json("summary.json", payload)
        return payload

    monkeypatch.setattr("thorium_reactor.cli.run_case", run)
    args = ["--repo-root", str(repo), "run", "example_pin", "--run-id", "outcome"]
    assert main(args + (["--no-solver"] if dry else [])) == expected
    manifest = json.loads((repo / "results/example_pin/outcome/stage_manifest.json").read_text())
    assert manifest["stages"][-1]["status"] == ("failed" if expected else "completed")
    if expected:
        assert "solver diagnostic" in capsys.readouterr().err


def test_only_abandoned_jobs_are_reconciled_once(repo):
    repository = WebRepository(repo)
    first = JobManager(repository)
    live = repository.prepare_run_bundle(SimulationDraft(case_name="example_pin", run_id="live"))
    dead = repository.prepare_run_bundle(SimulationDraft(case_name="example_pin", run_id="dead"))
    write_status(live.root, {"status": "running", "owner_id": first.owner_id})
    write_status(dead.root, {"status": "queued", "owner_id": "0" * 32})
    second = JobManager(repository)
    try:
        second.reconcile()
        assert repository.run_status("example_pin", "live") == "running"
        assert repository.run_status("example_pin", "dead") == "interrupted"
        assert len((dead.root / "job_events.ndjson").read_text().splitlines()) == 1
        first.shutdown()
        second.reconcile()
        assert repository.run_status("example_pin", "live") == "interrupted"
    finally:
        first.shutdown()
        second.shutdown()


def test_queue_backpressure_precedes_quota_and_shutdown_finishes_queue(repo, monkeypatch):
    repository = WebRepository(repo)
    manager = JobManager(repository, max_workers=1, max_queued=1)
    started, finish = threading.Event(), threading.Event()

    def phase(*args):
        started.set()
        finish.wait(5)

    monkeypatch.setattr(manager, "_run_phase", phase)
    monkeypatch.delenv("THORIUM_REACTOR_WEB_FAKE_JOBS", raising=False)
    try:
        first = manager.submit(SimulationDraft(case_name="example_pin", phases=["build"]))
        assert started.wait(5)
        second = manager.submit(SimulationDraft(case_name="example_pin", phases=["build"]))
        claims = []
        with pytest.raises(JobQueueFull):
            manager.submit(SimulationDraft(case_name="example_pin"), claim=lambda: claims.append(1))
        assert not claims
        manager._stopping.set()
        finish.set()
        manager.shutdown()
        assert repository.run_status("example_pin", second.run_id) == "interrupted"
        assert not manager._threads
        assert repository.run_status("example_pin", first.run_id) in {"completed", "interrupted"}
    finally:
        finish.set()
        manager.shutdown()


def test_retry_uses_snapshot_and_preserves_original(repo, monkeypatch):
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "0")
    monkeypatch.delenv("THORIUM_REACTOR_ADMIN_EMAILS", raising=False)
    repository = WebRepository(repo)
    draft = SimulationDraft(
        case_name="example_pin", run_id="stopped", phases=["build"], patch={"simulation": {"particles": 123}}
    )
    bundle = repository.prepare_run_bundle(draft)
    write_status(bundle.root, {"status": "interrupted", "error": "Server stopped"})
    (bundle.root / "web_draft.json").write_text(draft.model_dump_json())
    before = {p.name: p.read_bytes() for p in bundle.root.iterdir() if p.is_file()}
    with TestClient(create_app(repo)) as client:
        response = client.post("/api/runs/example_pin/stopped/retry")
        assert response.status_code == 202, response.text
        new_id = response.json()["run_id"]
        assert new_id != "stopped"
        snapshot = repo / "results/example_pin" / new_id / "case_snapshot.yaml"
        assert load_case_config(snapshot).simulation["particles"] == 123
    assert {p.name: p.read_bytes() for p in bundle.root.iterdir() if p.is_file()} == before


def test_deployment_has_no_implicit_administrator(monkeypatch):
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    monkeypatch.delenv("THORIUM_REACTOR_ADMIN_EMAILS", raising=False)
    assert configured_admin_emails() == set()


def test_invalid_inputs_and_full_queue_do_not_spend_api_quota(repo, monkeypatch):
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "0")
    app = create_app(repo)
    headers = {"cf-access-authenticated-user-email": "guest@example.com"}
    with TestClient(app) as client:
        invalid = client.post("/api/runs", headers=headers, json={"case_name": "example_pin", "scenario": "typo"})
        assert invalid.status_code == 400
        assert "Unknown transient scenario" in invalid.json()["detail"]
        acquired = 0
        try:
            while app.state.jobs._capacity.acquire(blocking=False):
                acquired += 1
            full = client.post("/api/runs", headers=headers, json={"case_name": "example_pin"})
            assert full.status_code == 503
            assert full.headers["retry-after"] == "5"
            assert client.get("/api/me", headers=headers).json()["runs_started_today"] == 0
        finally:
            for _ in range(acquired):
                app.state.jobs._capacity.release()
    assert not (repo / "results/example_pin").exists()


def test_phase_timeout_terminates_tree_and_records_failure(repo, monkeypatch):
    monkeypatch.setenv("THORIUM_REACTOR_WEB_PHASE_TIMEOUT_S", "1")
    monkeypatch.delenv("THORIUM_REACTOR_WEB_FAKE_JOBS", raising=False)
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    monkeypatch.setattr("thorium_reactor.web.jobs.build_cli_command", lambda *args: command)
    manager = JobManager(WebRepository(repo))
    try:
        record = manager.submit(SimulationDraft(case_name="example_pin", phases=["build"]))
        deadline = time.monotonic() + 10
        while manager._threads and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not manager._threads
        assert not manager._processes
        finished = manager.repository.get_run("example_pin", record.run_id)
        assert finished.status == "failed"
        assert "exceeded the 1 second job budget" in finished.error
    finally:
        manager.shutdown()


def test_process_tree_termination_closes_descendant_pipes(tmp_path):
    # The intermediate child exits, leaving its grandchild holding stdout.
    child = "import time; print('descendant-ready',flush=True); time.sleep(60)"
    parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1]])"
    process = start_process(
        [sys.executable, "-c", parent, child], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    output = []
    reader = threading.Thread(target=lambda: output.append(process.stdout.read()), daemon=True)
    try:
        assert process.stdout.readline().strip() == "descendant-ready"
        reader.start()
        terminate_process_tree(process)
        process.wait(timeout=5)
        reader.join(timeout=5)
        assert not reader.is_alive(), "A descendant still holds the phase output pipe open"
    finally:
        terminate_process_tree(process)
        process.stdout.close()
