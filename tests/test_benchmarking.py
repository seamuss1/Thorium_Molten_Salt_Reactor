from pathlib import Path

from thorium_reactor.benchmarking import (
    assess_benchmark_traceability,
    build_benchmark_residuals,
    build_docker_openmc_command,
)
from thorium_reactor.config import load_case_config, load_yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_structured_benchmark_metadata_has_traceability_summary() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "tmsr_lf1_core" / "case.yaml")
    benchmark = load_yaml(REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml")

    traceability = assess_benchmark_traceability(config, benchmark)

    assert traceability["traceability_score"] >= 80.0
    assert traceability["maturity_stage"] == "traceable_surrogate"
    assert traceability["coverage"]["reactor_parameters_linked"] == {"linked": 3, "total": 3}
    assert traceability["coverage"]["physics_validation_targets_linked"] == {"linked": 1, "total": 1}
    assert traceability["status_summary"]["surrogate_targets"] >= 1
    assert any("surrogate" in gap for gap in traceability["gaps"])
    assert traceability["validation_maturity"]["validation_maturity_score"] >= 40.0
    assert traceability["validation_maturity"]["validation_maturity_stage"] == "screening_backed"
    assert any("cross-code" in gap for gap in traceability["validation_maturity"]["gaps"])
    assert traceability["datasets"][0]["status"] == "literature_backed"


def test_build_docker_openmc_command_targets_repo_compose_runtime() -> None:
    command = build_docker_openmc_command("tmsr_lf1_core", "benchmark-run")

    assert command == [
        "docker",
        "compose",
        "run",
        "--build",
        "--rm",
        "openmc",
        "python",
        "-m",
        "thorium_reactor.cli",
        "run",
        "tmsr_lf1_core",
        "--run-id",
        "benchmark-run",
    ]


def test_msre_first_criticality_quality_gates_block_illustrative_harness() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "msre_first_criticality" / "case.yaml")
    benchmark = load_yaml(REPO_ROOT / "benchmarks" / "msre_first_criticality" / "benchmark.yaml")

    traceability = assess_benchmark_traceability(config, benchmark)
    quality = traceability["benchmark_quality"]

    assert traceability["maturity_stage"] == "literature_tracked"
    assert traceability["status_summary"]["surrogate_targets"] == 0
    assert quality["benchmark_ready"] is False
    assert quality["quality_stage"] == "benchmark_blocked"
    assert quality["failed_gate_count"] > 0
    assert any(gate["id"] == "benchmark_geometry_reconstructed" for gate in quality["gates"])
    assert any("geometry" in blocker.lower() for blocker in quality["promotion_blockers"])


def test_msre_keff_residuals_are_uncertainty_aware() -> None:
    config = load_case_config(REPO_ROOT / "configs" / "cases" / "msre_first_criticality" / "case.yaml")
    benchmark = load_yaml(REPO_ROOT / "benchmarks" / "msre_first_criticality" / "benchmark.yaml")

    passing = build_benchmark_residuals(
        config,
        {"metrics": {"keff": 1.001, "keff_std_dev": 0.00005}},
        benchmark,
    )
    passing_item = next(item for item in passing["items"] if item["name"] == "keff_experimental_criticality")

    assert passing_item["status"] == "pass"
    assert passing_item["target_value"] == 0.99978
    assert passing_item["benchmark_uncertainty_pcm"] == 420.0
    assert passing_item["calculated_uncertainty_pcm"] == 5.0
    assert passing_item["normalized_residual"] < 1.0

    failing = build_benchmark_residuals(
        config,
        {"metrics": {"keff": 1.02, "keff_std_dev": 0.00005}},
        benchmark,
    )
    failing_item = next(item for item in failing["items"] if item["name"] == "keff_experimental_criticality")

    assert failing_item["status"] == "fail"
    assert failing_item["residual_pcm"] == 2022.0
    assert failing_item["normalized_residual"] > 2.0
