import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thorium_reactor.accelerators import GPU_EFFICIENT_SAMPLE_FLOOR
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.transient_sweep import MAX_TRANSIENT_SWEEP_SAMPLES
from thorium_reactor.web.app import create_app
from thorium_reactor.web.jobs import append_event, build_cli_command
from thorium_reactor.web.repository import WebRepository
from thorium_reactor.web.schemas import SimulationDraft

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_HEADERS = {"cf-access-authenticated-user-email": "seamusdgallagher@gmail.com"}


def access_headers(email: str) -> dict[str, str]:
    return {"cf-access-authenticated-user-email": email}


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


def test_web_run_summaries_only_advertise_viewable_geometry() -> None:
    client = TestClient(create_app(REPO_ROOT))
    run_id = f"geometry-summary-{uuid.uuid4().hex}"
    bundle = create_result_bundle(REPO_ROOT, "example_pin", run_id)
    try:
        exports_dir = bundle.root / "geometry" / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / "core.gltf").write_text('{"asset":{"version":"2.0"},"scenes":[]}', encoding="utf-8")
        (exports_dir / "core.obj").write_text("o core\n", encoding="utf-8")
        (exports_dir / "core.png").write_bytes(b"not really a png")
        (bundle.root / "report.md").write_text("# Geometry summary test\n", encoding="utf-8")

        listed = client.get("/api/runs")
        assert listed.status_code == 200
        listed_run = next(
            item for item in listed.json() if item["case_name"] == "example_pin" and item["run_id"] == run_id
        )
        assert [artifact["label"] for artifact in listed_run["artifacts"]] == ["core.gltf"]

        detail = client.get(f"/api/runs/example_pin/{run_id}")
        detail_labels = {artifact["label"] for artifact in detail.json()["artifacts"]}
        assert {"core.gltf", "core.obj", "core.png", "report.md"}.issubset(detail_labels)
    finally:
        shutil.rmtree(bundle.root, ignore_errors=True)


def test_web_run_detail_exposes_curated_output_sections() -> None:
    client = TestClient(create_app(REPO_ROOT))
    run_id = f"output-sections-{uuid.uuid4().hex}"
    bundle = create_result_bundle(REPO_ROOT, "example_pin", run_id)
    try:
        bundle.write_json(
            "summary.json",
            {
                "bop": {
                    "thermal_power_mw": 100.0,
                    "electric_power_mw": 38.0,
                    "primary_delta_t_c": 90.0,
                    "primary_mass_flow_kg_s": 500.0,
                    "steam_generator_duty_mw": 100.0,
                },
                "physics_core": {
                    "integrity_checks": {"status": "ok"},
                    "neutronics": {
                        "k_eff": 1.012,
                        "beta_eff": 0.0061,
                        "group_count": 6,
                        "methods": ["diffusion", "sp3"],
                        "feedback_coefficients": {"fuel_temperature_pcm_per_c": -12.5},
                    },
                },
                "primary_system": {
                    "primary_mass_flow_kg_s": 500.0,
                    "primary_volumetric_flow_m3_s": 0.2,
                    "loop_hydraulics": {"pump_shaft_power_kw": 42.0, "max_reynolds_number": 120000.0},
                    "heat_exchanger": {"required_area_m2": 450.0, "duty_mw": 99.8},
                },
                "transient": {
                    "status": "completed",
                    "scenario_name": "load_follow",
                    "peak_power_fraction": 1.07,
                    "final_power_fraction": 1.0,
                    "peak_fuel_temperature_c": 704.0,
                },
                "transient_sweep": {
                    "status": "completed",
                    "backend": "numpy",
                    "samples": 32,
                    "peak_power_fraction_p95": 1.08,
                    "peak_fuel_temperature_c_p95": 712.0,
                    "runtime_performance": {"sample_steps_per_s": 9000.0},
                    "numerical_checks": {"status": "ok"},
                },
                "transport_solver": {
                    "status": "completed",
                    "model": "native_rz_rkdg_scalar_transport_v1",
                    "mesh": {"radial_cells": 4, "axial_cells": 8},
                    "polynomial_order": 1,
                    "cfl": 0.35,
                    "conservation_residual": 1.0e-8,
                    "minimum_field_value": 0.0,
                },
                "depletion_matrix": {
                    "status": "completed",
                    "model": "native_sparse_bateman_depletion_matrix_v1",
                    "backend": "numpy_dense_expm_fallback",
                    "isotope_count": 6,
                    "zone_count": 1,
                    "matrix_nonzero_entries": 9,
                    "atom_balance_residual": 1.0e-10,
                    "inventory_delta_fraction": -0.001,
                },
                "fuel_cycle": {"fissile_inventory_kg": 80.0, "specific_power_mw_per_t_hm": 500.0},
                "chemistry": {"corrosion_risk": "low", "corrosion_index": 1.01, "redox_state_ev": -0.02},
                "tritium": {"removal_fraction": 0.66, "environmental_release_fraction": 0.18},
                "benchmark_traceability": {
                    "traceability_score": 88.0,
                    "confidence_summary": {"high": 2, "medium": 1},
                    "coverage": {
                        "targets_structured": {"linked": 5, "total": 5},
                        "targets_with_evidence": {"linked": 4, "total": 5},
                    },
                    "datasets": [{"id": "sample"}],
                    "validation_maturity": {
                        "validation_maturity_score": 70.0,
                        "validation_maturity_stage": "screening_backed",
                        "gaps": ["Cross-code check pending."],
                    },
                },
                "finance": {
                    "status": "completed",
                    "outputs": {"lcoe_usd_per_mwh": 120.0, "annual_generation_mwh": 800000.0},
                    "inputs": {"net_capacity_mwe": 100.0, "construction_months": 48},
                    "cost_breakdown_usd": {"total_capitalized_cost": 500000000.0},
                },
                "schedule": {
                    "status": "completed",
                    "total_years_to_commercial_operation": 8.5,
                    "commercial_operation_date": "2035-01-01",
                },
                "visualization_state": {
                    "has_geometry_description": True,
                    "has_render_assets": True,
                    "available_views": ["hero_cutaway"],
                    "assets": {"gltf": "/workspace/results/example_pin/run/geometry/exports/core.gltf"},
                },
            },
        )
        bundle.write_json(
            "validation.json",
            {"checks": [{"status": "pass"}, {"status": "pending"}, {"status": "pass"}]},
        )

        response = client.get(f"/api/runs/example_pin/{run_id}")

        assert response.status_code == 200
        sections = {section["id"]: section for section in response.json()["output_sections"]}
        assert {
            "neutronics",
            "plant_balance",
            "primary_flow",
            "transient_response",
            "transient_uncertainty",
            "advanced_physics",
            "fuel_chemistry",
            "validation_maturity",
            "commercial_planning",
            "visualization",
        }.issubset(sections)
        assert sections["plant_balance"]["metrics"][0]["label"] == "Thermal power"
        assert any(metric["label"] == "k-effective" for metric in sections["neutronics"]["metrics"])
        assert any(metric["label"] == "Atom-balance residual" for metric in sections["advanced_physics"]["metrics"])
        assert sections["validation_maturity"]["status"] == "screening_backed"
        assert any(metric["label"] == "LCOE" for metric in sections["commercial_planning"]["metrics"])
    finally:
        shutil.rmtree(bundle.root, ignore_errors=True)


def test_web_run_detail_exposes_generated_data_and_plot_artifacts() -> None:
    client = TestClient(create_app(REPO_ROOT))
    run_id = f"artifact-inventory-{uuid.uuid4().hex}"
    bundle = create_result_bundle(REPO_ROOT, "example_pin", run_id)
    try:
        bundle.write_json("summary.json", {"case": "example_pin", "metrics": {}})
        bundle.write_json("flow_summary.json", {"reduced_order": {"primary_mass_flow_kg_s": 12.0}})
        bundle.write_json("finance.json", {"status": "completed"})
        bundle.write_text("cash_flow.csv", "month,cumulative_capitalized_cost_usd\n0,100\n")
        bundle.write_json("saltproc_integration.json", {"status": "exported"})
        plot_path = bundle.plots_dir / "summary.svg"
        plot_path.write_text("<svg></svg>", encoding="utf-8")

        response = client.get(f"/api/runs/example_pin/{run_id}")

        assert response.status_code == 200
        labels = {artifact["label"] for artifact in response.json()["artifacts"]}
        assert {
            "cash_flow.csv",
            "finance.json",
            "flow_summary.json",
            "saltproc_integration.json",
            "summary.svg",
        }.issubset(labels)
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
        headers=ADMIN_HEADERS,
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
            "sweep_samples": MAX_TRANSIENT_SWEEP_SAMPLES + 1,
        },
    )

    assert response.status_code == 422


def test_web_transient_sweep_allows_gpu_efficient_ensembles() -> None:
    """The browser must be able to reach the regime the accelerator exists for.

    The old ceiling was 65,536 -- exactly the size at which GPU offload has not
    yet amortized its per-step overhead -- so a browser sweep could never ask
    for an ensemble where the device is worth using.
    """
    assert MAX_TRANSIENT_SWEEP_SAMPLES > GPU_EFFICIENT_SAMPLE_FLOOR
    draft = SimulationDraft(case_name="example_pin", sweep_samples=GPU_EFFICIENT_SAMPLE_FLOOR)

    assert draft.sweep_samples == GPU_EFFICIENT_SAMPLE_FLOOR


def test_web_sweep_backend_selection_reaches_the_cli() -> None:
    """The browser's compute-backend control must actually select a backend."""
    for requested in ("auto", "numpy", "torch-cpu", "torch-xpu"):
        draft = SimulationDraft(case_name="example_pin", phases=["transient-sweep"], sweep_backend=requested)
        command = build_cli_command(draft, "transient-sweep")

        assert "--backend" in command
        assert command[command.index("--backend") + 1] == requested
        # The dead alias must not be forwarded alongside the real control.
        assert "--prefer-gpu" not in command


def test_web_sweep_rejects_unknown_backend() -> None:
    client = TestClient(create_app(REPO_ROOT))

    response = client.post(
        "/api/runs",
        json={"case_name": "example_pin", "phases": ["transient-sweep"], "sweep_backend": "cuda"},
    )

    assert response.status_code == 422


def test_web_legacy_prefer_gpu_false_selects_a_cpu_backend() -> None:
    """An old client that unchecks "use GPU" must get a CPU backend.

    Previously ``prefer_gpu`` mapped to ``--prefer-gpu``, an alias for the
    ``auto`` that ``--backend`` already defaulted to, so unchecking it changed
    nothing and the sweep still ran on the GPU.
    """
    draft = SimulationDraft(case_name="example_pin", phases=["transient-sweep"], prefer_gpu=False)
    command = build_cli_command(draft, "transient-sweep")

    assert command[command.index("--backend") + 1] == "numpy"


def test_web_fake_run_records_status_and_streams_events(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    client = TestClient(create_app(REPO_ROOT))
    run_id = f"web-test-{uuid.uuid4().hex}"
    run_root = REPO_ROOT / "results" / "example_pin" / run_id
    try:
        response = client.post(
            "/api/runs",
            headers=ADMIN_HEADERS,
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


def test_append_event_assigns_unique_contiguous_sequences_under_concurrency(tmp_path: Path) -> None:
    # The worker thread and the timeout Timer thread both append events during a
    # phase, so sequence assignment must be serialized to stay unique.
    run_dir = tmp_path
    per_thread = 150
    thread_count = 4

    def worker() -> None:
        for _ in range(per_thread):
            append_event(run_dir, "log", "run", "line")

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = [line for line in (run_dir / "job_events.ndjson").read_text(encoding="utf-8").splitlines() if line.strip()]
    sequences = sorted(json.loads(line)["sequence"] for line in lines)
    assert len(lines) == per_thread * thread_count
    assert sequences == list(range(1, per_thread * thread_count + 1))


def test_append_event_recovers_sequence_from_existing_log(tmp_path: Path) -> None:
    run_dir = tmp_path
    (run_dir / "job_events.ndjson").write_text(
        json.dumps({"sequence": 1, "timestamp": "t", "level": "info", "phase": None, "message": "prior"})
        + "\n"
        + json.dumps({"sequence": 2, "timestamp": "t", "level": "info", "phase": None, "message": "prior"})
        + "\n",
        encoding="utf-8",
    )

    event = append_event(run_dir, "info", "run", "resumed")

    assert event.sequence == 3


def test_web_requires_access_identity_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    client = TestClient(create_app(REPO_ROOT))

    response = client.post(
        "/api/runs",
        json={
            "case_name": "example_pin",
            "run_id": f"missing-identity-{uuid.uuid4().hex}",
            "phases": ["run"],
        },
    )

    assert response.status_code == 401


def test_every_api_route_requires_identity_when_access_is_required(monkeypatch) -> None:
    """Auth is opt-out, not opt-in.

    Enumerates the live route table rather than a hand-written URL list, so a
    new /api route added without auth fails here instead of shipping open.
    """
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    app = create_app(REPO_ROOT)
    # Untrusted transport: no verified identity can be established.
    client = TestClient(app, client=("203.0.113.10", 4242))

    public_paths = {"/api/health"}

    # Enumerate from the OpenAPI schema rather than app.routes: it is the
    # documented public surface, and unlike the route objects its shape does
    # not change when Starlette changes how include_router nests routers.
    schema = app.openapi()["paths"]
    assert len(schema) >= 12, f"OpenAPI surface looks wrong, only {len(schema)} paths"

    checked = 0
    for path, operations in sorted(schema.items()):
        if not path.startswith("/api") or path in public_paths:
            continue
        # Placeholders only need to survive routing; auth must reject before
        # any handler validates them.
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(set(operations) & {"get", "post", "put", "patch", "delete"}):
            response = client.request(method.upper(), url, json={})
            assert response.status_code == 401, f"{method.upper()} {path} returned {response.status_code}, expected 401"
            checked += 1

    assert checked >= 12, f"expected the full /api surface to be checked, only saw {checked}"

    health = client.get("/api/health")
    assert health.status_code == 200, "health must stay reachable without an identity"
    # Being public, it must not describe the host: it used to hand the
    # absolute repo path to any unauthenticated caller.
    assert health.json() == {"status": "ok"}
    assert str(REPO_ROOT) not in health.text


def test_web_allows_loopback_transport_dev_identity_when_access_is_required(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    client = TestClient(create_app(REPO_ROOT), client=("127.0.0.1", 50123))

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["email"] == "seamusdgallagher@gmail.com"
    assert response.json()["is_admin"] is True


def test_web_rejects_spoofed_identity_headers_when_access_is_required(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    client = TestClient(create_app(REPO_ROOT), client=("203.0.113.10", 4242))

    for header in ("x-user-email", "x-forwarded-email", "x-authenticated-user-email"):
        response = client.get("/api/me", headers={header: "seamusdgallagher@gmail.com"})
        assert response.status_code == 401, header

    unverified = client.get(
        "/api/me",
        headers={"cf-access-authenticated-user-email": "seamusdgallagher@gmail.com"},
    )
    assert unverified.status_code == 401

    admin_probe = client.get(
        "/api/admin/rate-limits",
        headers={"x-user-email": "seamusdgallagher@gmail.com"},
    )
    assert admin_probe.status_code == 401


def test_web_rejects_host_header_localhost_spoof_when_access_is_required(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    client = TestClient(
        create_app(REPO_ROOT),
        base_url="http://localhost:18488",
        client=("203.0.113.10", 4242),
    )

    response = client.get("/api/me")

    assert response.status_code == 401


def test_web_trusts_identity_header_only_with_proxy_shared_secret(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    monkeypatch.setenv("THORIUM_REACTOR_PROXY_SHARED_SECRET", "tunnel-secret")
    monkeypatch.setenv("THORIUM_REACTOR_RATE_LIMIT_PATH", str(tmp_path / "limits.json"))
    client = TestClient(create_app(REPO_ROOT), client=("203.0.113.10", 4242))
    identity = {"cf-access-authenticated-user-email": "guest@example.com"}

    wrong_secret = client.get("/api/me", headers={**identity, "x-thorium-proxy-secret": "wrong"})
    assert wrong_secret.status_code == 401

    verified = client.get("/api/me", headers={**identity, "x-thorium-proxy-secret": "tunnel-secret"})
    assert verified.status_code == 200
    assert verified.json()["email"] == "guest@example.com"
    assert verified.json()["is_admin"] is False


def test_web_trusted_client_addrs_extend_local_fallback(monkeypatch) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_ACCESS_REQUIRED", "1")
    monkeypatch.setenv("THORIUM_REACTOR_TRUSTED_CLIENT_ADDRS", "172.18.0.0/16")
    client = TestClient(create_app(REPO_ROOT), client=("172.18.0.1", 39100))

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["email"] == "seamusdgallagher@gmail.com"


def test_web_unknown_api_route_returns_json_404() -> None:
    client = TestClient(create_app(REPO_ROOT))

    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_web_spa_deep_links_serve_index_but_api_routes_404(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (repo_root / "configs" / "cases").mkdir(parents=True)
    dist = repo_root / "web" / "ui" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa-shell</html>", encoding="utf-8")
    client = TestClient(create_app(repo_root))

    deep_link = client.get("/runs/example_pin/some-run")
    assert deep_link.status_code == 200
    assert "spa-shell" in deep_link.text

    api_missing = client.get("/api/runz")
    assert api_missing.status_code == 404
    assert api_missing.headers["content-type"].startswith("application/json")


@pytest.mark.slow
def test_rate_limit_store_is_safe_across_processes(tmp_path: Path) -> None:
    store_path = tmp_path / "limits.json"
    worker = tmp_path / "claim_worker.py"
    worker.write_text(
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path

            sys.path.insert(0, {str(REPO_ROOT / "src")!r})

            from fastapi import HTTPException

            from thorium_reactor.web.permissions import RateLimitStore

            store = RateLimitStore(Path({str(REPO_ROOT)!r}), daily_limit=5)
            successes = 0
            for _ in range(25):
                try:
                    store.claim("guest@example.com")
                    successes += 1
                except HTTPException:
                    pass
            print(successes)
            """
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, THORIUM_REACTOR_RATE_LIMIT_PATH=str(store_path))
    processes = [
        subprocess.Popen([sys.executable, str(worker)], env=env, stdout=subprocess.PIPE, text=True) for _ in range(4)
    ]
    totals = []
    for process in processes:
        out, _err = process.communicate(timeout=180)
        assert process.returncode == 0
        totals.append(int(out.strip()))

    assert sum(totals) == 5
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["users"]["guest@example.com"]["count"] == 5


def test_append_event_sequences_without_rereading_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    for expected in (1, 2, 3):
        assert append_event(run_dir, "info", None, f"event {expected}").sequence == expected

    # Truncate the log: the cached counter must keep the sequence monotonic,
    # proving appends no longer re-read the file each time.
    (run_dir / "job_events.ndjson").write_text("", encoding="utf-8")
    assert append_event(run_dir, "info", None, "event 4").sequence == 4


def test_read_events_from_returns_only_new_events(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (repo_root / "configs" / "cases").mkdir(parents=True)
    run_dir = repo_root / "results" / "example_case" / "run-1"
    run_dir.mkdir(parents=True)
    repository = WebRepository(repo_root)

    append_event(run_dir, "info", None, "first")
    append_event(run_dir, "info", None, "second")
    events, offset = repository.read_events_from("example_case", "run-1", 0)
    assert [event.message for event in events] == ["first", "second"]

    append_event(run_dir, "info", None, "third")
    new_events, new_offset = repository.read_events_from("example_case", "run-1", offset)
    assert [event.message for event in new_events] == ["third"]
    assert new_offset > offset
    assert [event.sequence for event in events + new_events] == [1, 2, 3]


def test_web_rate_limits_non_admins_and_admin_can_reset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    monkeypatch.setenv("THORIUM_REACTOR_RATE_LIMIT_PATH", str(tmp_path / "limits.json"))
    client = TestClient(create_app(REPO_ROOT))
    email = "guest@example.com"
    run_ids = [f"limited-{uuid.uuid4().hex}" for _ in range(3)]
    run_roots = [REPO_ROOT / "results" / "example_pin" / run_id for run_id in run_ids]

    try:
        first = client.post(
            "/api/runs",
            headers=access_headers(email),
            json={"case_name": "example_pin", "run_id": run_ids[0], "phases": ["run"]},
        )
        assert first.status_code == 202

        limited = client.post(
            "/api/runs",
            headers=access_headers(email),
            json={"case_name": "example_pin", "run_id": run_ids[1], "phases": ["run"]},
        )
        assert limited.status_code == 429

        reset = client.post(
            f"/api/admin/rate-limits/{email}/reset",
            headers=ADMIN_HEADERS,
        )
        assert reset.status_code == 200
        assert reset.json()["remaining"] == 1

        after_reset = client.post(
            "/api/runs",
            headers=access_headers(email),
            json={"case_name": "example_pin", "run_id": run_ids[2], "phases": ["run"]},
        )
        assert after_reset.status_code == 202
    finally:
        for run_root in run_roots:
            shutil.rmtree(run_root, ignore_errors=True)


def test_web_admins_bypass_daily_run_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("THORIUM_REACTOR_WEB_FAKE_JOBS", "1")
    monkeypatch.setenv("THORIUM_REACTOR_RATE_LIMIT_PATH", str(tmp_path / "limits.json"))
    client = TestClient(create_app(REPO_ROOT))
    run_ids = [f"admin-unlimited-{uuid.uuid4().hex}" for _ in range(2)]
    run_roots = [REPO_ROOT / "results" / "example_pin" / run_id for run_id in run_ids]

    try:
        for run_id in run_ids:
            response = client.post(
                "/api/runs",
                headers=ADMIN_HEADERS,
                json={"case_name": "example_pin", "run_id": run_id, "phases": ["run"]},
            )
            assert response.status_code == 202

        session = client.get("/api/me", headers=ADMIN_HEADERS)
        assert session.status_code == 200
        assert session.json()["runs_remaining_today"] is None
    finally:
        for run_root in run_roots:
            shutil.rmtree(run_root, ignore_errors=True)


def test_rate_limit_timezone_falls_back_when_no_tz_database_exists(monkeypatch) -> None:
    """The fallback must not need the thing that is missing.

    On a system with no tz database -- a plain Windows install without the
    tzdata package -- ZoneInfo(...) raises, and the previous fallback of
    ZoneInfo("UTC") raised the same error it was catching, so constructing
    the app failed outright rather than degrading to UTC.
    """
    import zoneinfo

    from thorium_reactor.web import permissions

    def no_tz_database(key):
        raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(permissions, "ZoneInfo", no_tz_database)

    assert permissions.configured_timezone() is datetime.UTC
