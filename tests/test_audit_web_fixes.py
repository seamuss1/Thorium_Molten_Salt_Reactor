import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from thorium_reactor.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
HEADERS = {
    "cf-access-authenticated-user-email": "audit@example.com",
    "x-thorium-proxy-secret": "test-only-proxy-secret",
}


@pytest.fixture
def isolated_web_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    case_dir = repo / "configs/cases/example_pin"
    case_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "configs/cases/example_pin/case.yaml", case_dir / "case.yaml")
    shutil.copytree(REPO_ROOT / "benchmarks", repo / "benchmarks")
    (repo / "pyproject.toml").write_text("[project]\nname = 'web-audit-test'\n", encoding="utf-8")
    (tmp_path / "private-marker.txt").write_text("benign-private-marker", encoding="utf-8")
    (repo / "benchmarks/tmsr_lf1/private.yaml").write_text("benign-private-marker", encoding="utf-8")
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    monkeypatch.setenv("THORIUM_REACTOR_PROXY_SHARED_SECRET", HEADERS["x-thorium-proxy-secret"])
    monkeypatch.setenv("THORIUM_REACTOR_ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("THORIUM_REACTOR_RATE_LIMIT_PATH", str(tmp_path / "rates.json"))
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    return repo


@pytest.mark.parametrize("input_kind", ["patch", "draft_yaml"])
@pytest.mark.parametrize(
    "benchmark",
    [
        "../private-marker.txt",
        "benchmarks/tmsr_lf1/../../private-marker.txt",
        "configs/cases/example_pin/case.yaml",
        "benchmarks/tmsr_lf1/private.yaml",
        "benchmarks/missing/benchmark.yaml",
        "absolute_private",
        "absolute_benchmark",
    ],
)
def test_web_rejects_unapproved_benchmarks_before_bundle_and_quota(isolated_web_repo, input_kind, benchmark):
    repo = isolated_web_repo
    if benchmark == "absolute_private":
        benchmark = str(repo.parent / "private-marker.txt")
    elif benchmark == "absolute_benchmark":
        benchmark = str(repo / "benchmarks/tmsr_lf1/benchmark.yaml")
    if input_kind == "draft_yaml":
        config = yaml.safe_load((repo / "configs/cases/example_pin/case.yaml").read_text(encoding="utf-8"))
        config["reactor"]["benchmark"] = benchmark
        draft_input = {"draft_yaml": yaml.safe_dump(config)}
    else:
        draft_input = {"patch": {"reactor": {"benchmark": benchmark}}}

    with TestClient(create_app(repo), client=("203.0.113.10", 1234)) as client:
        validation = client.post("/api/cases/example_pin/validate-draft", headers=HEADERS, json=draft_input)
        assert validation.status_code == 200
        assert validation.json()["valid"] is False
        response = client.post(
            "/api/runs",
            headers=HEADERS,
            json={"case_name": "example_pin", "run_id": "rejected", "phases": ["build"], **draft_input},
        )
        assert response.status_code == 400, response.text
        assert not (repo / "results/example_pin/rejected").exists()
        session = client.get("/api/me", headers=HEADERS)
        assert session.json()["runs_started_today"] == 0
        assert client.get("/api/runs", headers=HEADERS).json() == []


@pytest.mark.parametrize("benchmark", [None, "benchmarks/msre_first_criticality/benchmark.yaml"])
def test_web_copies_approved_benchmark_records(isolated_web_repo, benchmark):
    repo = isolated_web_repo
    patch = {"reactor": {"benchmark": benchmark}} if benchmark else {}
    selected_path = repo / (benchmark or "benchmarks/tmsr_lf1/benchmark.yaml")
    with TestClient(create_app(repo), client=("203.0.113.10", 1234)) as client:
        response = client.post(
            "/api/runs",
            headers=HEADERS,
            json={"case_name": "example_pin", "run_id": "approved", "phases": ["build"], "patch": patch},
        )
        assert response.status_code == 202, response.text
        artifact = client.get("/api/runs/example_pin/approved/artifacts/benchmark_snapshot.yaml", headers=HEADERS)
        assert artifact.status_code == 200
        assert artifact.content == selected_path.read_bytes()


def test_web_rejects_benchmark_symlink_escape(isolated_web_repo):
    repo = isolated_web_repo
    escaped = repo / "benchmarks/escaped/benchmark.yaml"
    escaped.parent.mkdir()
    try:
        escaped.symlink_to(repo.parent / "private-marker.txt")
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable on this host: {exc}")
    with TestClient(create_app(repo), client=("203.0.113.10", 1234)) as client:
        response = client.post(
            "/api/runs",
            headers=HEADERS,
            json={
                "case_name": "example_pin",
                "run_id": "escaped",
                "phases": ["build"],
                "patch": {"reactor": {"benchmark": "benchmarks/escaped/benchmark.yaml"}},
            },
        )
        assert response.status_code == 400
        assert not (repo / "results/example_pin/escaped").exists()


@pytest.mark.parametrize("final_status", ["failed", "completed"])
def test_web_run_detail_and_list_agree_on_cli_stage_status(isolated_web_repo, final_status):
    repo = isolated_web_repo
    run_dir = repo / "results/example_pin/cli-stages"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"neutronics": {"status": "dry-run"}}), encoding="utf-8")
    (run_dir / "stage_manifest.json").write_text(
        json.dumps(
            {"stages": [{"name": "run", "status": "completed"}, {"name": "transient-sweep", "status": final_status}]}
        ),
        encoding="utf-8",
    )
    with TestClient(create_app(repo), client=("203.0.113.10", 1234)) as client:
        listing = client.get("/api/runs", headers=HEADERS)
        detail = client.get("/api/runs/example_pin/cli-stages", headers=HEADERS)
        assert listing.status_code == detail.status_code == 200
        assert listing.json()[0]["status"] == detail.json()["status"] == final_status
        assert client.app.state.repository.run_status("example_pin", "cli-stages") == final_status
