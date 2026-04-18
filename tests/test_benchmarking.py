from pathlib import Path

from thorium_reactor.benchmarking import assess_benchmark_traceability, build_docker_openmc_command
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
    assert traceability["validation_maturity"]["validation_maturity_score"] < 40.0
    assert traceability["validation_maturity"]["validation_maturity_stage"] == "surrogate_only"
    assert any("literature-backed" in gap for gap in traceability["validation_maturity"]["gaps"])


def test_build_docker_openmc_command_targets_repo_compose_runtime() -> None:
    command = build_docker_openmc_command("tmsr_lf1_core", "benchmark-run")

    assert command == [
        "docker",
        "compose",
        "-f",
        "docker-compose.openmc.yml",
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
