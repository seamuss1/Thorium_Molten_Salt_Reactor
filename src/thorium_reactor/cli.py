from __future__ import annotations

import argparse
import json
from json import JSONDecodeError
from pathlib import Path

from thorium_reactor.capabilities import get_case_capabilities
from thorium_reactor.benchmarking import get_docker_runtime_status, run_solver_backed_benchmark
from thorium_reactor.bundle_inputs import ensure_bundle_inputs, load_bundle_inputs
from thorium_reactor.config import load_case_config
from thorium_reactor.economics import run_economics_case
from thorium_reactor.geometry.exporters import export_geometry
from thorium_reactor.integrations import (
    persist_integration_result,
    run_moose_integration,
    run_moltres_integration,
    run_named_integration,
    run_saltproc_integration,
    run_scale_integration,
    run_thermochimica_integration,
)
from thorium_reactor.neutronics.openmc_compat import missing_openmc_runtime_message, openmc
from thorium_reactor.neutronics.workflows import _build_visualization_state, build_case, run_case, validate_case
from thorium_reactor.paths import ResultBundle, case_config_path, create_result_bundle, discover_repo_root, existing_result_bundle, latest_result_bundle
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot, load_plot_manifest
from thorium_reactor.reporting.reports import generate_report


INTEGRATION_COMMANDS = ("moose", "scale", "thermochimica", "saltproc", "moltres")
EXTEND_EXISTING_RUN_COMMANDS = ("transient", "transient-sweep", "runtime-benchmark", "economics", *INTEGRATION_COMMANDS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reactor", description="Thorium reactor platform CLI")
    parser.add_argument("--repo-root", type=Path, default=None, help="Override the repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in (
        "build",
        "run",
        "validate",
        "report",
        "render",
        "benchmark",
        "transient",
        "transient-sweep",
        "runtime-benchmark",
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
            command.add_argument("--samples", type=int, default=512, help="Number of ensemble trajectories to evaluate.")
            command.add_argument("--seed", type=int, default=42, help="Random seed for the ensemble perturbations.")
            command.add_argument("--prefer-gpu", action="store_true", help="Deprecated alias for --backend auto.")
            command.add_argument("--backend", default="auto", choices=["auto", "python", "numpy", "torch-cpu", "torch-xpu"], help="Array backend for transient ensemble integration.")
            command.add_argument("--dtype", default="float32", choices=["float32", "float64"], help="Array dtype for vector backends.")
        if command_name == "runtime-benchmark":
            command.add_argument("--scenario", default=None, help="Named transient scenario from the case config.")
            command.add_argument("--samples", type=int, default=1048576, help="Number of ensemble trajectories per backend.")
            command.add_argument("--seed", type=int, default=42, help="Random seed for identical backend ensembles.")
            command.add_argument("--backends", default="python,numpy,torch-xpu", help="Comma-separated backends to benchmark.")
            command.add_argument("--dtype", default="float32", choices=["float32", "float64"], help="Array dtype for vector backends.")
            command.add_argument("--fail-on-gpu-fallback", action="store_true", help="Fail if a GPU backend reports PyTorch XPU fallback enabled.")
        if command_name == "economics":
            command.add_argument("--scenario", default=None, help="Named economics scenario from the case config.")
            command.add_argument("--project-start", default=None, help="Project start date in YYYY-MM-DD format.")
            command.add_argument(
                "--force",
                action="store_true",
                help="Calculate commercial economics even when the case is not marked reactor.mode=commercial_grid.",
            )
        if command_name in INTEGRATION_COMMANDS:
            command.add_argument("--run-external", action="store_true", help="Attempt to execute the external code after exporting the input deck.")
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
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root()
    config = load_case_config(case_config_path(repo_root, args.case))

    if args.command in {"build", "run", "benchmark", "transient", "transient-sweep", "runtime-benchmark", "economics", *INTEGRATION_COMMANDS}:
        allow_existing = bool(args.reuse_run_id or (args.run_id is not None and args.command in EXTEND_EXISTING_RUN_COMMANDS))
        bundle = _load_or_create_bundle(repo_root, config.name, args.run_id, allow_existing=allow_existing)
        inputs = ensure_bundle_inputs(repo_root, bundle, config)
    else:
        bundle = latest_result_bundle(repo_root, config.name) if args.run_id is None else _load_existing_bundle(repo_root, config.name, args.run_id)
        inputs = load_bundle_inputs(repo_root, bundle, config)

    config = inputs.config
    benchmark = inputs.benchmark
    provenance = inputs.provenance

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
        print(bundle.root)
        return 0

    if args.command == "run":
        summary = run_case(
            config,
            bundle,
            benchmark=benchmark,
            solver_enabled=not args.no_solver,
            provenance=provenance,
        )
        print(bundle.root)
        print(summary["neutronics"]["status"])
        if summary["neutronics"].get("message"):
            print(summary["neutronics"]["message"])
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
        print(bundle.root)
        print(transient_sweep["backend"])
        print(transient_sweep["metrics"]["peak_power_fraction_p95"])
        return 0

    if args.command == "runtime-benchmark":
        from thorium_reactor.runtime_benchmark import parse_backend_list, run_runtime_benchmark_case

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
            )
        runtime_benchmark = run_runtime_benchmark_case(
            config,
            bundle,
            summary,
            scenario_name=args.scenario,
            samples=args.samples,
            seed=args.seed,
            backends=parse_backend_list(args.backends),
            dtype=args.dtype,
            fail_on_gpu_fallback=args.fail_on_gpu_fallback,
            provenance=provenance,
        )
        print(bundle.root)
        print(runtime_benchmark["recommendation"].get("backend"))
        print(runtime_benchmark["recommendation"].get("speedup_vs_reference"))
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
        )
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        persist_integration_result(bundle, summary, "moose", result)
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
        )
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        persist_integration_result(bundle, summary, "scale", result)
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
        )
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        persist_integration_result(bundle, summary, "thermochimica", result)
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
        )
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        persist_integration_result(bundle, summary, "saltproc", result)
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
        )
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        persist_integration_result(bundle, summary, "moltres", result)
        print(bundle.root)
        print(result["status"])
        return 0

    if args.command == "validate":
        result = validate_case(config, bundle, benchmark=benchmark, provenance=provenance)
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
            bundle.write_json("build_manifest.json", build_manifest)
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
        print(bundle.root)
        return 0

    return 1

def _load_or_create_bundle(repo_root: Path, case_name: str, run_id: str | None, *, allow_existing: bool = False) -> ResultBundle:
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


if __name__ == "__main__":
    raise SystemExit(main())
