from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path

from thorium_reactor.benchmark_evidence import (
    materialize_benchmark_evidence,
    merge_benchmark_evidence_into_quality,
)
from thorium_reactor.benchmarking import get_docker_runtime_status, run_solver_backed_benchmark
from thorium_reactor.bundle_inputs import ensure_bundle_inputs, load_bundle_inputs
from thorium_reactor.capabilities import get_case_capabilities
from thorium_reactor.config import load_case_config
from thorium_reactor.economics import run_economics_case
from thorium_reactor.evidence import build_evidence_status, load_canonical_artifact_status
from thorium_reactor.geometry.exporters import export_geometry
from thorium_reactor.integrations import (
    persist_integration_result,
    run_moltres_integration,
    run_moose_integration,
    run_saltproc_integration,
    run_scale_integration,
    run_thermochimica_integration,
)
from thorium_reactor.neutronics.openmc_compat import missing_openmc_runtime_message, openmc
from thorium_reactor.neutronics.workflows import _build_visualization_state, build_case, run_case, validate_case
from thorium_reactor.paths import (
    ResultBundle,
    append_stage_manifest,
    case_config_path,
    changed_bundle_artifacts,
    create_result_bundle,
    discover_repo_root,
    existing_result_bundle,
    latest_result_bundle,
    refresh_bundle_artifact_statuses,
    snapshot_bundle_artifacts,
)
from thorium_reactor.qa import build_requirements_summary
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot, load_plot_manifest
from thorium_reactor.reporting.reports import build_presentation_qa, generate_report
from thorium_reactor.sidecar_schemas import validate_bundle_sidecars
from thorium_reactor.transient_sweep import DEFAULT_TRANSIENT_SWEEP_SAMPLES
from thorium_reactor.uncertainty import DEFAULT_UNCERTAINTY_SWEEP_SAMPLES

INTEGRATION_COMMANDS = ("moose", "scale", "thermochimica", "saltproc", "moltres")
NATIVE_ADVANCED_COMMANDS = ("transport", "deplete")
UNCERTAINTY_COMMANDS = ("uncertainty-sweep",)
EXTEND_EXISTING_RUN_COMMANDS = (
    "transient",
    "transient-sweep",
    "economics",
    *UNCERTAINTY_COMMANDS,
    *NATIVE_ADVANCED_COMMANDS,
    *INTEGRATION_COMMANDS,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reactor", description="Thorium reactor platform CLI")
    parser.add_argument("--repo-root", type=Path, default=None, help="Override the repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    qa_command = subparsers.add_parser("qa", help="Validate QA requirements traceability artifacts")
    qa_command.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")

    for command_name in (
        "build",
        "run",
        "validate",
        "report",
        "render",
        "benchmark",
        "verify-bundle",
        "transient",
        "transient-sweep",
        *UNCERTAINTY_COMMANDS,
        *NATIVE_ADVANCED_COMMANDS,
        "economics",
        *INTEGRATION_COMMANDS,
    ):
        command = subparsers.add_parser(command_name, help=f"{command_name.capitalize()} a reactor case")
        command.add_argument("case", help="Case name under configs/cases")
        command.add_argument("--run-id", default=None, help="Create or select a specific results run id")
        command.add_argument("--reuse-run-id", action="store_true", help=argparse.SUPPRESS)
        if command_name == "run":
            command.add_argument("--no-solver", action="store_true", help="Skip calling the OpenMC solver")
        if command_name == "transient":
            command.add_argument("--scenario", default=None, help="Named transient scenario from the case config.")
        if command_name == "transient-sweep":
            command.add_argument("--scenario", default=None, help="Named transient scenario from the case config.")
            command.add_argument(
                "--samples",
                type=int,
                default=DEFAULT_TRANSIENT_SWEEP_SAMPLES,
                help="Number of ensemble trajectories to evaluate.",
            )
            command.add_argument("--seed", type=int, default=42, help="Random seed for the ensemble perturbations.")
            command.add_argument("--prefer-gpu", action="store_true", help="Deprecated alias for --backend auto.")
            command.add_argument(
                "--backend",
                default="auto",
                choices=["auto", "python", "numpy", "torch-cpu", "torch-xpu"],
                help="Array backend for transient ensemble integration.",
            )
            command.add_argument(
                "--dtype", default="float32", choices=["float32", "float64"], help="Array dtype for vector backends."
            )
        if command_name == "uncertainty-sweep":
            command.add_argument(
                "--samples",
                type=int,
                default=DEFAULT_UNCERTAINTY_SWEEP_SAMPLES,
                help="Number of Sobol geometry/material samples to run, excluding nominal and OAT samples.",
            )
            command.add_argument("--seed", type=int, default=42, help="Random seed for the Sobol scramble.")
            command.add_argument("--sampler", default="sobol", choices=["sobol"], help="Uncertainty sample design.")
            command.add_argument("--max-parallel", type=int, default=1, help="Maximum concurrent child OpenMC samples.")
            command.add_argument("--resume", action="store_true", help="Reuse completed child sample bundles.")
            command.add_argument(
                "--require-source-backed",
                action="store_true",
                help="Fail if enabled uncertainty parameters are not source-backed.",
            )
            command.add_argument(
                "--docker-openmc",
                action="store_true",
                help="Run the full uncertainty sweep inside the Docker Compose openmc service.",
            )
        if command_name == "economics":
            command.add_argument("--scenario", default=None, help="Named economics scenario from the case config.")
            command.add_argument("--project-start", default=None, help="Project start date in YYYY-MM-DD format.")
            command.add_argument(
                "--force",
                action="store_true",
                help="Calculate commercial economics even when the case is not marked reactor.mode=commercial_grid.",
            )
        if command_name in INTEGRATION_COMMANDS:
            command.add_argument(
                "--run-external",
                action="store_true",
                help="Attempt to execute the external code after exporting the input deck.",
            )
        if command_name == "benchmark":
            command.add_argument(
                "--docker-openmc",
                action="store_true",
                help="Run the benchmark case through the Docker Compose openmc service instead of the local runtime",
            )
    return parser


def resolve_benchmark_runtime(
    *,
    docker_requested: bool,
    local_openmc_available: bool,
    docker_status: dict[str, object] | None = None,
) -> tuple[str, str | None]:
    if docker_requested:
        return "docker", None
    if local_openmc_available:
        return "local", None

    docker_status = docker_status or {}
    if docker_status.get("daemon_available"):
        return "docker", None

    message = missing_openmc_runtime_message(command_name="benchmark")
    docker_message = docker_status.get("message")
    if isinstance(docker_message, str) and docker_message:
        message = f"{message} {docker_message}"
    return "error", message


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(effective_argv)

    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root()
    if args.command == "qa":
        summary = build_requirements_summary(repo_root)
        if args.format == "json":
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            status = "PASS" if summary["passed"] else "FAIL"
            print(f"QA requirements traceability: {status}")
            print(f"Requirements: {summary['requirements']['total']}")
            print(f"Matrix rows: {summary['matrix']['rows']}")
            for check in summary["checks"]:
                print(f"- {check['name']}: {check['status']}")
            for error in summary["errors"]:
                print(f"ERROR: {error}")
        return 0 if summary["passed"] else 1

    config = load_case_config(case_config_path(repo_root, args.case))

    if args.command in {
        "build",
        "run",
        "benchmark",
        "transient",
        "transient-sweep",
        "economics",
        *UNCERTAINTY_COMMANDS,
        *NATIVE_ADVANCED_COMMANDS,
        *INTEGRATION_COMMANDS,
    }:
        allow_existing = bool(
            args.reuse_run_id or (args.run_id is not None and args.command in EXTEND_EXISTING_RUN_COMMANDS)
        )
        bundle = _load_or_create_bundle(repo_root, config.name, args.run_id, allow_existing=allow_existing)
        inputs = ensure_bundle_inputs(repo_root, bundle, config)
    else:
        bundle = (
            latest_result_bundle(repo_root, config.name)
            if args.run_id is None
            else _load_existing_bundle(repo_root, config.name, args.run_id)
        )
        inputs = load_bundle_inputs(repo_root, bundle, config)

    config = inputs.config
    benchmark = inputs.benchmark
    provenance = inputs.provenance
    stage_started_utc = _utc_now()
    stage_artifacts_before = snapshot_bundle_artifacts(bundle)
    stage_command = _stage_command_from_argv(effective_argv, args.command)

    try:
        if args.command == "build":
            built = build_case(config, bundle.openmc_dir, benchmark=benchmark)
            bundle.write_json("geometry_description.json", built.geometry_description)
            build_manifest = dict(built.manifest)
            build_manifest["workflow_capabilities"] = sorted(get_case_capabilities(config))
            build_manifest["visualization_state"] = _build_visualization_state(bundle)
            build_manifest["input_provenance"] = provenance
            bundle.write_json("build_manifest.json", build_manifest)
            if built.model is not None:
                built.model.export_to_xml(directory=str(bundle.openmc_dir))
            artifact_status = refresh_bundle_artifact_statuses(bundle, summary={"neutronics": {"status": "dry-run"}})
            build_manifest["artifact_status"] = artifact_status
            bundle.write_json("build_manifest.json", build_manifest)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=build_manifest,
            )
            print(bundle.root)
            return 0

        if args.command == "run":
            summary = run_case(
                config,
                bundle,
                benchmark=benchmark,
                solver_enabled=not args.no_solver,
                provenance=provenance,
                repo_root=repo_root,
            )
            print(bundle.root)
            print(summary["neutronics"]["status"])
            if summary["neutronics"].get("message"):
                print(summary["neutronics"]["message"])
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
            )
            return 0

        if args.command == "transient":
            from thorium_reactor.transient import run_transient_case

            summary_path = bundle.root / "summary.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                summary = run_case(
                    config,
                    bundle,
                    benchmark=benchmark,
                    solver_enabled=False,
                    provenance=provenance,
                    repo_root=repo_root,
                )
            transient = run_transient_case(
                config,
                bundle,
                summary,
                scenario_name=args.scenario,
                provenance=provenance,
            )
            bundle.write_json("summary.json", summary)
            generate_summary_plots(bundle, summary)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
            )
            print(bundle.root)
            print(transient["metrics"]["peak_power_fraction"])
            return 0

        if args.command == "transient-sweep":
            from thorium_reactor.transient_sweep import run_transient_sweep_case

            summary_path = bundle.root / "summary.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                summary = run_case(
                    config,
                    bundle,
                    benchmark=benchmark,
                    solver_enabled=False,
                    provenance=provenance,
                    repo_root=repo_root,
                )
            transient_sweep = run_transient_sweep_case(
                config,
                bundle,
                summary,
                scenario_name=args.scenario,
                samples=args.samples,
                seed=args.seed,
                prefer_gpu=args.prefer_gpu,
                backend=args.backend,
                dtype=args.dtype,
                provenance=provenance,
            )
            bundle.write_json("summary.json", summary)
            generate_summary_plots(bundle, summary)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
            )
            print(bundle.root)
            print(transient_sweep["backend"])
            print(transient_sweep["metrics"]["peak_power_fraction_p95"])
            return 0

        if args.command == "uncertainty-sweep":
            from thorium_reactor.uncertainty import run_docker_uncertainty_sweep, run_uncertainty_sweep_case

            if args.docker_openmc and os.environ.get("THORIUM_REACTOR_RUNTIME_SERVICE") != "openmc":
                execution = run_docker_uncertainty_sweep(
                    repo_root,
                    config.name,
                    bundle.run_id,
                    samples=args.samples,
                    seed=args.seed,
                    sampler=args.sampler,
                    max_parallel=args.max_parallel,
                    resume=args.resume,
                    require_source_backed=args.require_source_backed,
                )
                bundle.write_json("uncertainty_execution.json", execution)
                if execution["returncode"] != 0:
                    raise RuntimeError(execution.get("stderr") or "Docker OpenMC uncertainty sweep failed.")
                summary_path = bundle.root / "summary.json"
                if not summary_path.exists():
                    raise FileNotFoundError(
                        f"No summary found for uncertainty sweep case '{config.name}' in {bundle.root}."
                    )
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                summary = run_uncertainty_sweep_case(
                    repo_root,
                    config,
                    bundle,
                    benchmark,
                    samples=args.samples,
                    seed=args.seed,
                    sampler=args.sampler,
                    max_parallel=args.max_parallel,
                    resume=args.resume,
                    require_source_backed=args.require_source_backed,
                    provenance=provenance,
                )
            validation = validate_case(config, bundle, summary=summary, benchmark=benchmark, provenance=provenance)
            generate_validation_plot(bundle, validation)
            plot_assets = load_plot_manifest(bundle.root / "plots_manifest.json")
            geometry_assets = None
            summary["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
            bundle.write_json("summary.json", summary)
            report = generate_report(
                config.name,
                config.data,
                bundle.root / "summary.json",
                bundle.root / "validation.json",
                None,
                benchmark,
                plot_assets,
                provenance=provenance,
            )
            bundle.write_text("report.md", report)
            summary = _refresh_benchmark_evidence(bundle, config.data, benchmark, provenance)
            report = generate_report(
                config.name,
                config.data,
                bundle.root / "summary.json",
                bundle.root / "validation.json",
                geometry_assets,
                benchmark,
                plot_assets,
                provenance=provenance,
            )
            bundle.write_text("report.md", report)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                repo_root=repo_root,
            )
            print(bundle.root)
            print(summary.get("uncertainty_sweep", {}).get("coverage_status", "missing"))
            return 0

        if args.command == "transport":
            from thorium_reactor.transport import run_transport_case

            summary_path = bundle.root / "summary.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                summary = run_case(
                    config,
                    bundle,
                    benchmark=benchmark,
                    solver_enabled=False,
                    provenance=provenance,
                    repo_root=repo_root,
                )
            transport = run_transport_case(config, bundle, summary)
            generate_summary_plots(bundle, summary)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
            )
            print(bundle.root)
            print(transport["status"])
            print(transport["conservation_residual"])
            return 0

        if args.command == "deplete":
            from thorium_reactor.depletion import run_depletion_case

            summary_path = bundle.root / "summary.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                summary = run_case(
                    config,
                    bundle,
                    benchmark=benchmark,
                    solver_enabled=False,
                    provenance=provenance,
                    repo_root=repo_root,
                )
            depletion = run_depletion_case(config, bundle, summary)
            generate_summary_plots(bundle, summary)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
            )
            print(bundle.root)
            print(depletion["status"])
            print(depletion["atom_balance_residual"])
            return 0

        if args.command == "economics":
            plan = run_economics_case(
                config,
                bundle,
                scenario_name=args.scenario,
                project_start=args.project_start,
                force=args.force,
            )
            summary = json.loads((bundle.root / "summary.json").read_text(encoding="utf-8"))
            generate_summary_plots(bundle, summary)
            report_path = bundle.root / "report.md"
            if report_path.exists():
                validation_path = bundle.root / "validation.json"
                geometry_assets = None
                render_assets_path = bundle.root / "render_assets.json"
                if render_assets_path.exists():
                    geometry_assets = json.loads(render_assets_path.read_text(encoding="utf-8"))
                plot_assets = load_plot_manifest(bundle.root / "plots_manifest.json")
                summary["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
                bundle.write_json("summary.json", summary)
                report = generate_report(
                    config.name,
                    config.data,
                    bundle.root / "summary.json",
                    validation_path if validation_path.exists() else None,
                    geometry_assets,
                    benchmark,
                    plot_assets,
                    provenance=provenance,
                )
                bundle.write_text("report.md", report)
            else:
                summary["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
                bundle.write_json("summary.json", summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                repo_root=repo_root,
            )
            print(bundle.root)
            print(plan["status"])
            if plan["finance"].get("status") == "completed":
                print(plan["finance"]["outputs"]["lcoe_usd_per_mwh"])
                print(plan["schedule"]["commercial_operation_date"])
            return 0

        if args.command == "moose":
            result = run_moose_integration(
                config,
                bundle,
                benchmark=benchmark,
                provenance=provenance,
                execute=args.run_external,
                repo_root=repo_root,
            )
            summary_path = bundle.root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            persist_integration_result(bundle, summary, "moose", result)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                status=result.get("status", "completed"),
            )
            print(bundle.root)
            print(result["status"])
            return 0

        if args.command == "scale":
            result = run_scale_integration(
                config,
                bundle,
                benchmark=benchmark,
                provenance=provenance,
                execute=args.run_external,
                repo_root=repo_root,
            )
            summary_path = bundle.root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            persist_integration_result(bundle, summary, "scale", result)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                status=result.get("status", "completed"),
            )
            print(bundle.root)
            print(result["status"])
            return 0

        if args.command == "thermochimica":
            result = run_thermochimica_integration(
                config,
                bundle,
                benchmark=benchmark,
                provenance=provenance,
                execute=args.run_external,
                repo_root=repo_root,
            )
            summary_path = bundle.root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            persist_integration_result(bundle, summary, "thermochimica", result)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                status=result.get("status", "completed"),
            )
            print(bundle.root)
            print(result["status"])
            return 0

        if args.command == "saltproc":
            result = run_saltproc_integration(
                config,
                bundle,
                benchmark=benchmark,
                provenance=provenance,
                execute=args.run_external,
                repo_root=repo_root,
            )
            summary_path = bundle.root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            persist_integration_result(bundle, summary, "saltproc", result)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                status=result.get("status", "completed"),
            )
            print(bundle.root)
            print(result["status"])
            return 0

        if args.command == "moltres":
            result = run_moltres_integration(
                config,
                bundle,
                benchmark=benchmark,
                provenance=provenance,
                execute=args.run_external,
                repo_root=repo_root,
            )
            summary_path = bundle.root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            persist_integration_result(bundle, summary, "moltres", result)
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                status=result.get("status", "completed"),
            )
            print(bundle.root)
            print(result["status"])
            return 0

        if args.command == "validate":
            result = validate_case(config, bundle, benchmark=benchmark, provenance=provenance)
            summary_path = bundle.root / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
            refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                status="completed" if result.get("passed") else "failed",
            )
            print(result["passed"])
            return 0

        if args.command == "render":
            summary_path = bundle.root / "summary.json"
            if not summary_path.exists():
                raise FileNotFoundError(
                    f"No summary found for case '{config.name}' in {bundle.root}. "
                    "Run `reactor run <case>` first or specify an existing run id."
                )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            validation_path = bundle.root / "validation.json"
            if validation_path.exists():
                validation = json.loads(validation_path.read_text(encoding="utf-8"))
            else:
                validation = validate_case(config, bundle, summary=summary, benchmark=benchmark, provenance=provenance)

            geometry_description_path = bundle.root / "geometry_description.json"
            if geometry_description_path.exists():
                geometry_description = json.loads(geometry_description_path.read_text(encoding="utf-8"))
            else:
                built = build_case(config, bundle.openmc_dir, benchmark=benchmark)
                geometry_description = built.geometry_description
                bundle.write_json("geometry_description.json", geometry_description)

            assets = export_geometry(
                geometry_description,
                bundle.geometry_exports_dir,
                summary=summary,
                validation=validation,
            )
            bundle.write_json("render_assets.json", assets)
            summary["visualization_state"] = _build_visualization_state(bundle, assets=assets)
            bundle.write_json("summary.json", summary)
            build_manifest_path = bundle.root / "build_manifest.json"
            if build_manifest_path.exists():
                build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
                build_manifest["geometry_assets"] = assets
                build_manifest["visualization_state"] = _build_visualization_state(bundle, assets=assets)
                build_manifest["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
                bundle.write_json("build_manifest.json", build_manifest)
            else:
                refresh_bundle_artifact_statuses(bundle, summary=summary)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
            )
            print(json.dumps(assets, indent=2))
            return 0

        if args.command == "report":
            summary_path = bundle.root / "summary.json"
            if not summary_path.exists():
                raise FileNotFoundError(
                    f"No summary found for case '{config.name}' in {bundle.root}. "
                    "Run `reactor run <case>` first or specify an existing run id."
                )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            generate_summary_plots(bundle, summary)
            validation_path = bundle.root / "validation.json"
            needs_validation = not validation_path.exists()
            if validation_path.exists():
                try:
                    json.loads(validation_path.read_text(encoding="utf-8"))
                except JSONDecodeError:
                    needs_validation = True
            if needs_validation:
                validate_case(config, bundle, summary=summary, benchmark=benchmark, provenance=provenance)
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            generate_validation_plot(bundle, validation)
            geometry_assets = None
            render_assets_path = bundle.root / "render_assets.json"
            if render_assets_path.exists():
                geometry_assets = json.loads(render_assets_path.read_text(encoding="utf-8"))
            else:
                build_manifest_path = bundle.root / "build_manifest.json"
                if build_manifest_path.exists():
                    build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
                    geometry_assets = build_manifest.get("geometry_assets")
            plot_assets = load_plot_manifest(bundle.root / "plots_manifest.json")
            summary["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
            bundle.write_json("summary.json", summary)
            report = generate_report(
                config.name,
                config.data,
                summary_path,
                validation_path,
                geometry_assets,
                benchmark,
                plot_assets,
                provenance=provenance,
            )
            report_path = bundle.write_text("report.md", report)
            summary = _refresh_benchmark_evidence(bundle, config.data, benchmark, provenance)
            report = generate_report(
                config.name,
                config.data,
                summary_path,
                validation_path,
                geometry_assets,
                benchmark,
                plot_assets,
                provenance=provenance,
            )
            report_path = bundle.write_text("report.md", report)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                repo_root=repo_root,
            )
            print(report_path)
            return 0

        if args.command == "benchmark":
            docker_status = get_docker_runtime_status() if (args.docker_openmc or openmc is None) else None
            runtime, error_message = resolve_benchmark_runtime(
                docker_requested=args.docker_openmc,
                local_openmc_available=openmc is not None,
                docker_status=docker_status,
            )
            if runtime == "docker":
                execution = run_solver_backed_benchmark(repo_root, config.name, bundle.run_id)
                bundle.write_json("benchmark_execution.json", execution)
            elif runtime == "local":
                summary = run_case(
                    config,
                    bundle,
                    benchmark=benchmark,
                    solver_enabled=True,
                    provenance=provenance,
                    repo_root=repo_root,
                )
                bundle.write_json(
                    "benchmark_execution.json",
                    {
                        "runtime": "local-openmc",
                        "summary_status": summary.get("neutronics", {}).get("status"),
                    },
                )
            else:
                raise RuntimeError(error_message or missing_openmc_runtime_message(command_name="benchmark"))

            summary_path = bundle.root / "summary.json"
            if not summary_path.exists():
                raise FileNotFoundError(
                    f"No summary found for benchmark case '{config.name}' in {bundle.root}. "
                    "The solver-backed benchmark run did not produce a summary bundle."
                )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            generate_summary_plots(bundle, summary)
            validation = validate_case(config, bundle, summary=summary, benchmark=benchmark, provenance=provenance)
            generate_validation_plot(bundle, validation)
            geometry_assets = None
            render_assets_path = bundle.root / "render_assets.json"
            if render_assets_path.exists():
                geometry_assets = json.loads(render_assets_path.read_text(encoding="utf-8"))
            else:
                build_manifest_path = bundle.root / "build_manifest.json"
                if build_manifest_path.exists():
                    build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
                    geometry_assets = build_manifest.get("geometry_assets")
            plot_assets = load_plot_manifest(bundle.root / "plots_manifest.json")
            summary["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
            bundle.write_json("summary.json", summary)
            report = generate_report(
                config.name,
                config.data,
                summary_path,
                bundle.root / "validation.json",
                geometry_assets,
                benchmark,
                plot_assets,
                provenance=provenance,
            )
            bundle.write_text("report.md", report)
            summary = _refresh_benchmark_evidence(bundle, config.data, benchmark, provenance)
            report = generate_report(
                config.name,
                config.data,
                bundle.root / "summary.json",
                bundle.root / "validation.json",
                geometry_assets,
                benchmark,
                plot_assets,
                provenance=provenance,
            )
            bundle.write_text("report.md", report)
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                summary=summary,
                repo_root=repo_root,
            )
            print(bundle.root)
            return 0

        if args.command == "verify-bundle":
            failures = _verify_bundle_evidence_contract(bundle)
            if failures:
                for failure in failures:
                    print(f"FAIL: {failure}", file=sys.stderr)
                raise SystemExit(1)
            print(f"Bundle evidence contract OK: {bundle.root}")
            return 0

        return 1
    except SystemExit:
        raise
    except Exception as exc:
        try:
            _finish_cli_stage(
                bundle,
                args.command,
                stage_command,
                stage_started_utc,
                stage_artifacts_before,
                provenance,
                status="failed",
                repo_root=repo_root,
                message=str(exc),
            )
        except Exception:
            pass
        raise


def _refresh_benchmark_evidence(
    bundle: ResultBundle,
    config_data: dict[str, object],
    benchmark: dict[str, object] | None,
    provenance: dict[str, object],
) -> dict[str, object]:
    summary_path = bundle.root / "summary.json"
    if not summary_path.exists():
        return {}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not (summary.get("benchmark_quality") or benchmark):
        return summary
    evidence = materialize_benchmark_evidence(
        bundle,
        config_data,
        summary,
        benchmark or {},
        provenance=provenance,
        openmc_module=openmc,
    )
    summary["benchmark_evidence"] = evidence
    if summary.get("benchmark_quality"):
        summary["benchmark_quality"] = merge_benchmark_evidence_into_quality(
            summary.get("benchmark_quality"),
            evidence,
        )
        quality_checks = _benchmark_quality_status_checks(summary["benchmark_quality"])
        summary["model_validity"] = _replace_model_validity_benchmark_quality_checks(
            summary.get("model_validity", {}),
            quality_checks,
        )
        _replace_validation_benchmark_quality_checks(bundle, quality_checks)
        metrics = summary.get("metrics")
        if isinstance(metrics, dict):
            metrics["benchmark_quality_score"] = summary["benchmark_quality"].get("quality_score", 0.0)
    summary["artifact_status"] = refresh_bundle_artifact_statuses(bundle, summary=summary)
    bundle.write_json("summary.json", summary)
    return summary


def _benchmark_quality_status_checks(quality: dict[str, object]) -> list[dict[str, str]]:
    checks = []
    for gate in quality.get("gates", []):
        if not isinstance(gate, dict):
            continue
        checks.append(
            {
                "name": f"benchmark_quality::{gate.get('id', 'gate')}",
                "status": str(gate.get("status", "pending")),
                "message": str(gate.get("message", "")),
            }
        )
    return checks


def _replace_model_validity_benchmark_quality_checks(
    model_validity: object,
    quality_checks: list[dict[str, str]],
) -> dict[str, object]:
    existing = model_validity if isinstance(model_validity, dict) else {}
    checks = [
        check
        for check in existing.get("checks", [])
        if isinstance(check, dict) and not str(check.get("name", "")).startswith("benchmark_quality::")
    ]
    checks.extend(quality_checks)
    passed_count = sum(1 for check in checks if check.get("status") == "pass")
    failed = [check for check in checks if check.get("status") == "fail"]
    pending_count = sum(1 for check in checks if check.get("status") == "pending")
    return {
        "status": "valid" if not failed else "invalid",
        "passed": not failed,
        "check_count": len(checks),
        "passed_count": passed_count,
        "failed_count": len(failed),
        "pending_count": pending_count,
        "failed_check_names": [str(check.get("name")) for check in failed],
        "failed_messages": [str(check.get("message")) for check in failed if check.get("message")],
        "checks": checks,
    }


def _replace_validation_benchmark_quality_checks(
    bundle: ResultBundle,
    quality_checks: list[dict[str, str]],
) -> None:
    validation_path = bundle.root / "validation.json"
    if not validation_path.exists():
        return
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except JSONDecodeError:
        return
    checks = [
        check
        for check in validation.get("checks", [])
        if isinstance(check, dict) and not str(check.get("name", "")).startswith("benchmark_quality::")
    ]
    checks.extend(quality_checks)
    validation["checks"] = checks
    validation["passed"] = all(check.get("status") == "pass" for check in checks)
    bundle.write_json("validation.json", validation)


def _verify_bundle_evidence_contract(bundle: ResultBundle) -> list[str]:
    """Return trust-contract failures for a generated bundle (empty means OK).

    Fails closed when sidecars are invalid, presentation QA fails, the summary's
    embedded artifact status is stale relative to the canonical sidecar, or the
    report overclaims solver-backed/benchmark-ready/build-candidate status.
    """
    failures: list[str] = []
    failures.extend(validate_bundle_sidecars(bundle.root))

    summary_path = bundle.root / "summary.json"
    summary: dict[str, object] = {}
    if summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            summary = loaded if isinstance(loaded, dict) else {}
        except JSONDecodeError:
            failures.append("summary.json is not valid JSON.")

    embedded = summary.get("artifact_status")
    canonical = load_canonical_artifact_status(bundle.root, {})
    if isinstance(embedded, dict) and canonical and embedded != canonical:
        failures.append("summary.json artifact_status is stale relative to artifact_status.json.")

    report_path = bundle.root / "report.md"
    if report_path.exists():
        qa = build_presentation_qa(bundle.root, report_text=report_path.read_text(encoding="utf-8"))
        for check in qa.get("checks", []):
            if check.get("status") != "pass":
                failures.append(f"presentation QA check failed: {check.get('name')} ({check.get('detail', '')})")

    evidence = build_evidence_status(bundle.root, summary)
    if evidence["blockers"] and evidence["claim_tier"] == "solver_backed":
        failures.append("evidence gate reports solver-backed claim tier while blockers remain.")
    return failures


def _load_or_create_bundle(
    repo_root: Path, case_name: str, run_id: str | None, *, allow_existing: bool = False
) -> ResultBundle:
    if run_id is None:
        return create_result_bundle(repo_root, case_name)
    if allow_existing:
        try:
            return existing_result_bundle(repo_root, case_name, run_id)
        except FileNotFoundError:
            pass
    return create_result_bundle(repo_root, case_name, run_id)


def _load_existing_bundle(repo_root: Path, case_name: str, run_id: str) -> ResultBundle:
    return existing_result_bundle(repo_root, case_name, run_id)


def _stage_command_from_argv(argv: list[str] | None, command: str) -> list[str]:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(effective_argv):
        token = effective_argv[index]
        if token == command:
            return effective_argv[index:]
        if token == "--repo-root":
            index += 2
            continue
        if token.startswith("--repo-root="):
            index += 1
            continue
        index += 1
    return [command]


def _finish_cli_stage(
    bundle: ResultBundle,
    stage: str,
    command: list[str],
    started_utc: str,
    artifacts_before: dict[str, str],
    provenance: dict[str, object],
    *,
    summary: dict[str, object] | None = None,
    status: str | None = None,
    repo_root: Path | None = None,
    message: str | None = None,
) -> None:
    summary = summary if isinstance(summary, dict) else {}
    _persist_refreshed_artifact_status(bundle, summary)
    after = snapshot_bundle_artifacts(bundle)
    stage_repo_root = repo_root or _repo_root_from_bundle(bundle)
    append_stage_manifest(
        bundle,
        stage=stage,
        command=command,
        started_utc=started_utc,
        ended_utc=_utc_now(),
        status=status or _stage_status_from_summary(summary),
        inputs=provenance,
        output_artifacts=sorted(changed_bundle_artifacts(artifacts_before, after)),
        method_tier=_method_tier_from_summary(summary),
        repo_root=stage_repo_root,
        message=message,
    )


def _persist_refreshed_artifact_status(bundle: ResultBundle, summary: dict[str, object]) -> dict[str, object]:
    """Refresh the canonical artifact-status sidecar and keep summary.json in sync.

    Every CLI stage funnels through here so no branch can leave a stale
    ``summary["artifact_status"]`` behind a fresher ``artifact_status.json``.
    """
    summary_path = bundle.root / "summary.json"
    status_summary = summary
    if not status_summary and summary_path.exists():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            status_summary = loaded
    artifact_status = refresh_bundle_artifact_statuses(bundle, summary=status_summary)
    summary["artifact_status"] = artifact_status
    if summary_path.exists():
        try:
            persisted = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError):
            persisted = None
        if isinstance(persisted, dict) and persisted.get("artifact_status") != artifact_status:
            persisted["artifact_status"] = artifact_status
            bundle.write_json("summary.json", persisted)
    return artifact_status


def _repo_root_from_bundle(bundle: ResultBundle) -> Path | None:
    try:
        results_root = bundle.root.parents[2]
    except IndexError:
        return None
    if bundle.root.parents[1].name == "results":
        return results_root
    return None


def _stage_status_from_summary(summary: dict[str, object]) -> str:
    neutronics = summary.get("neutronics")
    if isinstance(neutronics, dict) and str(neutronics.get("status", "")).lower() == "failed":
        return "failed"
    return "completed"


def _method_tier_from_summary(summary: dict[str, object]) -> str:
    neutronics = summary.get("neutronics")
    if isinstance(neutronics, dict):
        status = str(neutronics.get("status", "")).lower()
        if status == "completed":
            return "solver_backed_openmc"
        if status in {"dry-run", "dry_run"}:
            return "dry_run_proxy"
        if status.startswith("skipped"):
            return "skipped_solver"
        if status:
            return status
    if summary.get("model"):
        return str(summary["model"])
    return "workflow_artifact"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
