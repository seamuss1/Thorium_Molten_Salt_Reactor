import json
import shutil
import sys
from pathlib import Path

import pytest

from thorium_reactor.cli import (
    _finish_cli_stage,
    _load_or_create_bundle,
    _stage_command_from_argv,
    build_parser,
    main,
    resolve_benchmark_runtime,
)
from thorium_reactor.paths import create_result_bundle, snapshot_bundle_artifacts
from thorium_reactor.transient_sweep import DEFAULT_TRANSIENT_SWEEP_SAMPLES
from thorium_reactor.uncertainty import DEFAULT_UNCERTAINTY_SWEEP_SAMPLES

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_registers_all_commands() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["run", "example_pin", "--no-solver"])

    assert namespace.command == "run"
    assert namespace.case == "example_pin"
    assert namespace.no_solver is True


def test_cli_registers_benchmark_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["benchmark", "tmsr_lf1_core", "--docker-openmc"])

    assert namespace.command == "benchmark"
    assert namespace.case == "tmsr_lf1_core"
    assert namespace.docker_openmc is True


def test_benchmark_runtime_falls_back_to_docker_when_local_openmc_is_missing() -> None:
    runtime, message = resolve_benchmark_runtime(
        docker_requested=False,
        local_openmc_available=False,
        docker_status={"daemon_available": True, "message": None},
    )

    assert runtime == "docker"
    assert message is None


def test_benchmark_runtime_returns_guidance_when_no_solver_runtime_is_available() -> None:
    runtime, message = resolve_benchmark_runtime(
        docker_requested=False,
        local_openmc_available=False,
        docker_status={"daemon_available": False, "message": "Docker daemon unavailable."},
    )

    assert runtime == "error"
    assert message is not None
    assert "docker compose run --rm openmc" in message
    assert "Docker daemon unavailable." in message


def test_cli_registers_transient_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["transient", "immersed_pool_reference", "--scenario", "load_follow_step"])

    assert namespace.command == "transient"
    assert namespace.case == "immersed_pool_reference"
    assert namespace.scenario == "load_follow_step"


def test_cli_registers_external_integration_commands() -> None:
    parser = build_parser()
    moose = parser.parse_args(["moose", "immersed_pool_reference", "--run-external"])
    scale = parser.parse_args(["scale", "tmsr_lf1_core"])
    thermochimica = parser.parse_args(["thermochimica", "tmsr_lf1_core"])
    saltproc = parser.parse_args(["saltproc", "tmsr_lf1_core"])
    moltres = parser.parse_args(["moltres", "immersed_pool_reference", "--run-external"])

    assert moose.command == "moose"
    assert moose.run_external is True
    assert scale.command == "scale"
    assert scale.run_external is False
    assert thermochimica.command == "thermochimica"
    assert saltproc.command == "saltproc"
    assert moltres.command == "moltres"
    assert moltres.run_external is True


def test_cli_registers_transient_sweep_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(
        [
            "transient-sweep",
            "immersed_pool_reference",
            "--scenario",
            "partial_heat_sink_loss",
            "--samples",
            "1024",
            "--seed",
            "7",
            "--prefer-gpu",
            "--backend",
            "numpy",
        ]
    )

    assert namespace.command == "transient-sweep"
    assert namespace.case == "immersed_pool_reference"
    assert namespace.scenario == "partial_heat_sink_loss"
    assert namespace.samples == 1024
    assert namespace.seed == 7
    assert namespace.prefer_gpu is True
    assert namespace.backend == "numpy"


def test_cli_transient_sweep_defaults_to_gpu_sized_ensemble() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["transient-sweep", "immersed_pool_reference"])

    assert namespace.samples == DEFAULT_TRANSIENT_SWEEP_SAMPLES
    assert namespace.backend == "auto"


def test_cli_registers_runtime_benchmark_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(
        [
            "runtime-benchmark",
            "immersed_pool_reference",
            "--samples",
            "128",
            "--backends",
            "python,numpy",
            "--fail-on-gpu-fallback",
        ]
    )

    assert namespace.command == "runtime-benchmark"
    assert namespace.case == "immersed_pool_reference"
    assert namespace.samples == 128
    assert namespace.backends == "python,numpy"
    assert namespace.fail_on_gpu_fallback is True


def test_cli_registers_uncertainty_sweep_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(
        [
            "uncertainty-sweep",
            "msre_first_criticality",
            "--samples",
            "64",
            "--seed",
            "19",
            "--docker-openmc",
            "--resume",
            "--require-source-backed",
        ]
    )

    assert namespace.command == "uncertainty-sweep"
    assert namespace.case == "msre_first_criticality"
    assert namespace.samples == 64
    assert namespace.seed == 19
    assert namespace.docker_openmc is True
    assert namespace.resume is True
    assert namespace.require_source_backed is True


def test_cli_uncertainty_sweep_defaults_to_publication_sized_ensemble() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["uncertainty-sweep", "msre_first_criticality"])

    assert namespace.samples == DEFAULT_UNCERTAINTY_SWEEP_SAMPLES
    assert namespace.sampler == "sobol"


def test_cli_registers_native_transport_and_depletion_commands() -> None:
    parser = build_parser()
    transport = parser.parse_args(["transport", "immersed_pool_reference", "--run-id", "native"])
    deplete = parser.parse_args(["deplete", "immersed_pool_reference", "--run-id", "native", "--reuse-run-id"])

    assert transport.command == "transport"
    assert transport.case == "immersed_pool_reference"
    assert transport.run_id == "native"
    assert deplete.command == "deplete"
    assert deplete.reuse_run_id is True


def test_cli_explicit_run_id_creation_rejects_collision(tmp_path: Path) -> None:
    existing = create_result_bundle(tmp_path, "example_pin", "fixed")

    with pytest.raises(FileExistsError):
        _load_or_create_bundle(tmp_path, "example_pin", "fixed")

    reused = _load_or_create_bundle(tmp_path, "example_pin", "fixed", allow_existing=True)
    assert reused.root == existing.root


def test_cli_run_writes_stage_manifest_and_artifact_status(tmp_path: Path) -> None:
    scratch = tmp_path / "repo"
    (scratch / "configs" / "cases" / "example_pin").mkdir(parents=True)
    (scratch / "benchmarks" / "tmsr_lf1").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml",
        scratch / "configs" / "cases" / "example_pin" / "case.yaml",
    )
    shutil.copy2(
        REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", scratch / "benchmarks" / "tmsr_lf1" / "benchmark.yaml"
    )

    assert main(["--repo-root", str(scratch), "run", "example_pin", "--run-id", "cli-stage", "--no-solver"]) == 0

    bundle_root = scratch / "results" / "example_pin" / "cli-stage"
    stage_manifest = json.loads((bundle_root / "stage_manifest.json").read_text(encoding="utf-8"))
    artifact_status = json.loads((bundle_root / "artifact_status.json").read_text(encoding="utf-8"))
    summary = json.loads((bundle_root / "summary.json").read_text(encoding="utf-8"))

    assert stage_manifest["stages"][0]["stage"] == "run"
    assert stage_manifest["stages"][0]["command"] == ["run"]
    assert stage_manifest["stages"][0]["args"] == ["example_pin", "--run-id", "cli-stage", "--no-solver"]
    assert stage_manifest["stages"][0]["method_tier"] == "dry_run_proxy"
    assert "summary.json" in stage_manifest["stages"][0]["output_artifacts"]
    assert artifact_status["groups"]["openmc"]["state"] == "dry_run"
    assert summary["artifact_status"]["groups"]["openmc"]["blockers"]


def test_cli_stage_manifest_records_modified_existing_artifact(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "example_pin", "modified-summary")
    bundle.write_json("summary.json", {"neutronics": {"status": "dry-run"}, "version": 1})
    before = snapshot_bundle_artifacts(bundle)

    bundle.write_json("summary.json", {"neutronics": {"status": "dry-run"}, "version": 2})
    _finish_cli_stage(
        bundle,
        "run",
        ["run", "example_pin", "--no-solver"],
        "2026-05-18T00:00:00Z",
        before,
        {"input_snapshots": {}},
        summary={"neutronics": {"status": "dry-run"}},
        repo_root=tmp_path,
    )

    manifest = json.loads((bundle.root / "stage_manifest.json").read_text(encoding="utf-8"))
    assert "summary.json" in manifest["stages"][0]["output_artifacts"]


def test_finish_cli_stage_syncs_summary_with_refreshed_sidecar(tmp_path: Path) -> None:
    bundle = create_result_bundle(tmp_path, "example_pin", "stale-summary")
    # Summary embeds a stale artifact_status claiming the folder was never generated.
    bundle.write_json(
        "summary.json",
        {
            "neutronics": {"status": "completed"},
            "artifact_status": {"groups": {"openmc": {"state": "not_generated", "artifacts": []}}},
        },
    )
    (bundle.openmc_dir / "statepoint.42.h5").write_bytes(b"\x89HDF\r\n\x1a\n")
    before = snapshot_bundle_artifacts(bundle)

    _finish_cli_stage(
        bundle,
        "render",
        ["render", "example_pin"],
        "2026-07-14T00:00:00Z",
        before,
        {"input_snapshots": {}},
        summary={"neutronics": {"status": "completed"}},
        repo_root=tmp_path,
    )

    sidecar = json.loads((bundle.root / "artifact_status.json").read_text(encoding="utf-8"))
    summary = json.loads((bundle.root / "summary.json").read_text(encoding="utf-8"))
    assert sidecar["groups"]["openmc"]["state"] == "completed"
    # summary.json must be re-synced with the fresh sidecar, not left stale.
    assert summary["artifact_status"]["groups"]["openmc"]["state"] == "completed"


def test_cli_records_failed_stage_when_command_raises(tmp_path: Path) -> None:
    scratch = tmp_path / "repo"
    (scratch / "configs" / "cases" / "example_pin").mkdir(parents=True)
    (scratch / "benchmarks" / "tmsr_lf1").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml",
        scratch / "configs" / "cases" / "example_pin" / "case.yaml",
    )
    shutil.copy2(
        REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", scratch / "benchmarks" / "tmsr_lf1" / "benchmark.yaml"
    )

    # report on a bundle with no summary.json raises FileNotFoundError.
    create_result_bundle(scratch, "example_pin", "no-summary")
    with pytest.raises(FileNotFoundError):
        main(["--repo-root", str(scratch), "report", "example_pin", "--run-id", "no-summary"])

    manifest = json.loads(
        (scratch / "results" / "example_pin" / "no-summary" / "stage_manifest.json").read_text(encoding="utf-8")
    )
    failed = [stage for stage in manifest["stages"] if stage["status"] == "failed"]
    assert failed and failed[-1]["stage"] == "report"
    assert failed[-1].get("message")


def test_cli_verify_bundle_passes_for_dry_run_report(tmp_path: Path) -> None:
    scratch = tmp_path / "repo"
    (scratch / "configs" / "cases" / "example_pin").mkdir(parents=True)
    (scratch / "benchmarks" / "tmsr_lf1").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml",
        scratch / "configs" / "cases" / "example_pin" / "case.yaml",
    )
    shutil.copy2(
        REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", scratch / "benchmarks" / "tmsr_lf1" / "benchmark.yaml"
    )

    assert main(["--repo-root", str(scratch), "run", "example_pin", "--run-id", "vb", "--no-solver"]) == 0
    assert main(["--repo-root", str(scratch), "report", "example_pin", "--run-id", "vb"]) == 0
    assert main(["--repo-root", str(scratch), "verify-bundle", "example_pin", "--run-id", "vb"]) == 0


def test_cli_verify_bundle_fails_on_stale_summary_status(tmp_path: Path) -> None:
    scratch = tmp_path / "repo"
    (scratch / "configs" / "cases" / "example_pin").mkdir(parents=True)
    (scratch / "benchmarks" / "tmsr_lf1").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml",
        scratch / "configs" / "cases" / "example_pin" / "case.yaml",
    )
    shutil.copy2(
        REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml", scratch / "benchmarks" / "tmsr_lf1" / "benchmark.yaml"
    )

    assert main(["--repo-root", str(scratch), "run", "example_pin", "--run-id", "vb", "--no-solver"]) == 0
    assert main(["--repo-root", str(scratch), "report", "example_pin", "--run-id", "vb"]) == 0

    # Corrupt summary.json's embedded artifact_status so it disagrees with the sidecar.
    bundle_root = scratch / "results" / "example_pin" / "vb"
    summary = json.loads((bundle_root / "summary.json").read_text(encoding="utf-8"))
    summary["artifact_status"] = {"groups": {"openmc": {"state": "completed", "artifacts": ["statepoint.fake.h5"]}}}
    (bundle_root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["--repo-root", str(scratch), "verify-bundle", "example_pin", "--run-id", "vb"])
    assert exc.value.code == 1


def test_stage_command_from_sys_argv_preserves_subcommand_options(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reactor",
            "--repo-root",
            "C:\\repo",
            "transient-sweep",
            "immersed_pool_reference",
            "--scenario",
            "load_follow_step",
            "--samples",
            "16",
        ],
    )

    assert _stage_command_from_argv(None, "transient-sweep") == [
        "transient-sweep",
        "immersed_pool_reference",
        "--scenario",
        "load_follow_step",
        "--samples",
        "16",
    ]


def test_cli_registers_economics_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(
        [
            "economics",
            "flagship_grid_msr",
            "--scenario",
            "conservative_foak",
            "--project-start",
            "2026-05-02",
        ]
    )

    assert namespace.command == "economics"
    assert namespace.case == "flagship_grid_msr"
    assert namespace.scenario == "conservative_foak"
    assert namespace.project_start == "2026-05-02"
    assert namespace.force is False
