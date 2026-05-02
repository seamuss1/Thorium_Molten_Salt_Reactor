import shutil
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from thorium_reactor.paths import create_result_bundle
from thorium_reactor.web.app import create_app


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_web_case_discovery_and_docs() -> None:
    client = TestClient(create_app(REPO_ROOT))

    cases = client.get("/api/cases")
    assert cases.status_code == 200
    payload = cases.json()
    names = {item["name"] for item in payload}
    assert "example_pin" in names
    example = next(item for item in payload if item["name"] == "example_pin")
    assert example["editable_parameters"]

    docs = client.get("/api/docs")
    assert docs.status_code == 200
    slugs = {item["slug"] for item in docs.json()}
    assert "readme" in slugs


def test_web_draft_validation_does_not_modify_source_case() -> None:
    client = TestClient(create_app(REPO_ROOT))
    case_path = REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml"
    original = case_path.read_text(encoding="utf-8")

    response = client.post(
        "/api/cases/example_pin/validate-draft",
        json={"patch": {"simulation": {"particles": 4321, "source": {"parameters": [1.0]}}}},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert "particles: 4321" in response.json()["normalized_yaml"]
    assert "- 1.0" in response.json()["normalized_yaml"]
    assert case_path.read_text(encoding="utf-8") == original


def test_web_artifact_serving_rejects_path_traversal() -> None:
    client = TestClient(create_app(REPO_ROOT))
    run_id = f"artifact-test-{uuid.uuid4().hex}"
    bundle = create_result_bundle(REPO_ROOT, "example_pin", run_id)
    try:
        (bundle.root / "report.md").write_text("# Artifact test\n", encoding="utf-8")

        ok = client.get(f"/api/runs/example_pin/{run_id}/artifacts/report.md")
        assert ok.status_code == 200

        repo_file = client.get(f"/api/runs/example_pin/{run_id}/artifacts/README.md")
        assert repo_file.status_code == 404

        blocked = client.get(f"/api/runs/example_pin/{run_id}/artifacts/..%2Fpyproject.toml")
        assert blocked.status_code == 400
    finally:
        shutil.rmtree(bundle.root, ignore_errors=True)


def test_web_run_rejects_unsafe_draft_case_before_creating_results(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    client = TestClient(create_app(REPO_ROOT))
    escape_name = f"escape-{uuid.uuid4().hex}"
    escaped_root = REPO_ROOT / escape_name
    draft_yaml = (REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml").read_text(encoding="utf-8")

    response = client.post(
        "/api/runs",
        json={
            "case_name": f"../{escape_name}",
            "run_id": f"unsafe-{uuid.uuid4().hex}",
            "draft_yaml": draft_yaml,
            "phases": ["run"],
        },
    )

    assert response.status_code == 400
    assert not escaped_root.exists()


def test_web_transient_sweep_samples_are_bounded() -> None:
    client = TestClient(create_app(REPO_ROOT))

    response = client.post(
        "/api/runs",
        json={
            "case_name": "example_pin",
            "phases": ["transient-sweep"],
            "sweep_samples": 65_537,
        },
    )

    assert response.status_code == 422


def test_web_fake_run_records_status_and_streams_events(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    client = TestClient(create_app(REPO_ROOT))
    run_id = f"web-test-{uuid.uuid4().hex}"
    run_root = REPO_ROOT / "results" / "example_pin" / run_id
    try:
        response = client.post(
            "/api/runs",
            json={
                "case_name": "example_pin",
                "run_id": run_id,
                "patch": {"simulation": {"particles": 1000}},
                "phases": ["run", "validate", "report"],
                "sweep_samples": 8,
                "sweep_seed": 1,
                "prefer_gpu": False,
            },
        )
        assert response.status_code == 202

        final_payload = None
        for _ in range(100):
            final_payload = client.get(f"/api/runs/example_pin/{run_id}").json()
            if final_payload["status"] == "completed":
                break
            time.sleep(0.02)

        assert final_payload is not None
        assert final_payload["status"] == "completed"
        assert (run_root / "case_snapshot.yaml").exists()
        assert (run_root / "job_status.json").exists()
        assert (run_root / "job_events.ndjson").exists()

        with client.stream("GET", f"/api/runs/example_pin/{run_id}/events") as events:
            body = "".join(events.iter_text())
        assert "Run completed" in body
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
