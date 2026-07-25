from __future__ import annotations

import copy
from pathlib import Path

from thorium_reactor.benchmark_evidence import (
    build_cross_code_comparison,
    materialize_benchmark_evidence,
    merge_benchmark_evidence_into_quality,
)
from thorium_reactor.config import load_case_config, load_yaml
from thorium_reactor.paths import create_result_bundle, refresh_bundle_artifact_statuses
from thorium_reactor.reporting.plots import _resolve_statepoint_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _config_data() -> dict:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "msre_first_criticality" / "case.yaml")
    return config.data


def _benchmark() -> dict:
    return load_yaml(REPO_ROOT / "benchmarks" / "msre_first_criticality" / "benchmark.yaml")


def test_evidence_contract_blocks_static_ready_claim_without_statepoint(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "msre_first_criticality", "static-ready")
    config = copy.deepcopy(_config_data())
    config["benchmark_model"] = {
        "readiness": "benchmark_ready",
        "geometry_fidelity": "benchmark_reconstruction",
        "material_fidelity": "source_indexed_isotopic",
        "solver_statistics": "published_solver_bundle",
        "promotion_blockers": [],
    }
    quality = {
        "quality_score": 100.0,
        "quality_stage": "benchmark_ready",
        "benchmark_ready": True,
        "passed_gate_count": 1,
        "failed_gate_count": 0,
        "gates": [{"id": "static_claim", "status": "pass", "message": "Static metadata claims ready."}],
        "promotion_blockers": [],
    }
    summary = {
        "case": "msre_first_criticality",
        "result_dir": str(bundle.root),
        "neutronics": {"status": "completed", "simulation": config["simulation"]},
        "metrics": {"keff": 1.0, "keff_std_dev": 0.00001},
        "benchmark_quality": quality,
        "runtime_context": {"git_commit": "abc123", "dependency_hash": "deps"},
    }
    bundle.write_json("summary.json", summary)
    bundle.write_json("validation.json", {"passed": True, "checks": []})
    bundle.write_json("benchmark_residuals.json", {"status": "completed"})
    bundle.write_text("report.md", "# report\n")

    evidence = materialize_benchmark_evidence(
        bundle,
        config,
        summary,
        _benchmark(),
        provenance={"run_id": bundle.run_id, "git": {"available": True}},
        openmc_module=None,
    )
    merged = merge_benchmark_evidence_into_quality(quality, evidence)

    assert evidence["benchmark_ready_evidence"] is False
    assert merged["benchmark_ready"] is False
    failed_gate_ids = {gate["id"] for gate in merged["gates"] if gate["status"] == "fail"}
    assert "evidence::openmc_statepoint" in failed_gate_ids
    assert "evidence::nuclear_data_provenance" in failed_gate_ids
    assert any("statepoint" in blocker.lower() for blocker in merged["promotion_blockers"])


def test_materialize_preserves_complete_openmc_sidecars_when_refresh_runtime_lacks_openmc(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name in ("OPENMC_CROSS_SECTIONS", "OPENMC_MG_CROSS_SECTIONS", "OPENMC_CHAIN_FILE"):
        monkeypatch.delenv(name, raising=False)
    bundle = create_result_bundle(tmp_path, "msre_first_criticality", "host-refresh")
    config = copy.deepcopy(_config_data())
    summary = {
        "case": "msre_first_criticality",
        "result_dir": str(bundle.root),
        "neutronics": {"status": "completed", "simulation": config["simulation"]},
        "metrics": {"keff": 1.02032, "keff_std_dev": 0.00002},
        "runtime_context": {"git_commit": "abc123", "dependency_hash": "deps"},
    }
    for name in ("geometry.xml", "materials.xml", "settings.xml", "tallies.xml"):
        (bundle.openmc_dir / name).write_text("<xml />\n", encoding="utf-8")
    (bundle.openmc_dir / "statepoint.220.h5").write_bytes(b"solver-statepoint")
    bundle.write_json("summary.json", summary)
    bundle.write_json("validation.json", {"passed": True, "checks": []})
    bundle.write_json("benchmark_residuals.json", {"status": "completed"})
    bundle.write_text("report.md", "# report\n")
    existing_nuclear = {
        "schema_version": 1,
        "status": "complete",
        "paths": {"OPENMC_CROSS_SECTIONS": {"path": "/solver/cross_sections.xml", "sha256": "abc"}},
        "blockers": [],
    }
    existing_convergence = {
        "schema_version": 1,
        "status": "complete",
        "statepoint": str(bundle.openmc_dir / "statepoint.220.h5"),
        "blockers": [],
    }
    bundle.write_json("nuclear_data_provenance.json", existing_nuclear)
    bundle.write_json("source_convergence_diagnostics.json", existing_convergence)

    evidence = materialize_benchmark_evidence(
        bundle,
        config,
        summary,
        _benchmark(),
        provenance={"run_id": bundle.run_id, "git": {"available": True}},
        openmc_module=None,
    )

    nuclear = load_yaml(bundle.root / "nuclear_data_provenance.json")
    convergence = load_yaml(bundle.root / "source_convergence_diagnostics.json")
    assert nuclear == existing_nuclear
    assert convergence == existing_convergence
    gate_statuses = {gate["id"]: gate["status"] for gate in evidence["gates"]}
    assert gate_statuses["nuclear_data_provenance"] == "pass"
    assert gate_statuses["source_convergence_diagnostics"] == "pass"


def test_cross_code_comparison_computes_openmc_vs_serpent_residual() -> None:
    summary = {
        "neutronics": {"status": "completed"},
        "metrics": {"keff": 1.02032, "keff_std_dev": 0.00002},
    }

    comparison = build_cross_code_comparison(summary, _benchmark())

    assert comparison["status"] == "completed"
    serpent = next(item for item in comparison["comparisons"] if item["code"] == "Serpent")
    assert serpent["residual_pcm"] == -100.0
    assert serpent["combined_uncertainty_pcm"] > 0.0


def test_artifact_status_surfaces_benchmark_evidence_blockers(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "msre_first_criticality", "blocked")
    summary = {
        "benchmark_quality": {"benchmark_ready": False},
        "benchmark_evidence": {"blockers": ["No OpenMC statepoint.*.h5 artifact is present."]},
        "neutronics": {"status": "dry-run"},
    }

    status = refresh_bundle_artifact_statuses(bundle, summary=summary)

    assert status["groups"]["benchmark_evidence"]["state"] == "blocked"
    assert any("statepoint" in blocker.lower() for blocker in status["blockers"])
    assert any("uncertainty_budget.json" in blocker for blocker in status["blockers"])


def test_statepoint_plot_resolution_rejects_external_stale_statepoint(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "msre_first_criticality", "plot-statepoint")
    external = tmp_path / "old" / "statepoint.50.h5"
    external.parent.mkdir()
    external.write_bytes(b"stale")

    resolved = _resolve_statepoint_path(bundle, {"neutronics": {"statepoint": str(external)}})

    assert resolved is None

    local = bundle.openmc_dir / external.name
    local.write_bytes(b"current")

    resolved = _resolve_statepoint_path(bundle, {"neutronics": {"statepoint": str(external)}})

    assert resolved == local
