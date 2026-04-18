from __future__ import annotations

import argparse
import json
from json import JSONDecodeError
from pathlib import Path

from thorium_reactor.capabilities import get_case_capabilities
from thorium_reactor.benchmarking import run_solver_backed_benchmark
from thorium_reactor.bundle_inputs import ensure_bundle_inputs, load_bundle_inputs
from thorium_reactor.config import load_case_config
from thorium_reactor.geometry.exporters import export_geometry
from thorium_reactor.integrations import persist_integration_result, run_moose_integration, run_scale_integration
from thorium_reactor.neutronics.openmc_compat import openmc
from thorium_reactor.neutronics.workflows import _build_visualization_state, build_case, run_case, validate_case
from thorium_reactor.paths import ResultBundle, case_config_path, create_result_bundle, discover_repo_root, latest_result_bundle
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot, load_plot_manifest
from thorium_reactor.reporting.reports import generate_report
from thorium_reactor.transient import run_transient_case
from thorium_reactor.transient_sweep import run_transient_sweep_case


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reactor", description="Thorium reactor platform CLI")
    parser.add_argument("--repo-root", type=Path, default=None, help="Override the repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("build", "run", "validate", "report", "render", "benchmark", "transient", "transient-sweep", "moose", "scale"):
        command = subparsers.add_parser(command_name, help=f"{command_name.capitalize()} a reactor case")
        command.add_argument("case", help="Case name under configs/cases")
        command.add_argument("--run-id", default=None, help="Reuse or create a specific results run id")
        if command_name == "run":
            command.add_argument("--no-solver", action="store_true", help="Skip calling the OpenMC solver")
        if command_name == "transient":
            command.add_argument("--scenario", default=None, help="Named transient scenario from the case config.")
        if command_name == "transient-sweep":
            command.add_argument("--scenario", default=None, help="Named transient scenario from the case config.")
            command.add_argument("--samples", type=int, default=512, help="Number of ensemble trajectories to evaluate.")
            command.add_argument("--seed", type=int, default=42, help="Random seed for the ensemble perturbations.")
            command.add_argument("--prefer-gpu", action="store_true", help="Use CuPy when available for batched transient integration.")
        if command_name in {"moose", "scale"}:
            command.add_argument("--run-external", action="store_true", help="Attempt to execute the external code after exporting the input deck.")
        if command_name == "benchmark":
            command.add_argument(
                "--docker-openmc",
                action="store_true",
                help="Run the benchmark case through docker-compose.openmc.yml instead of the local runtime",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root()
    config = load_case_config(case_config_path(repo_root, args.case))

    if args.command in {"build", "run", "benchmark", "transient", "transient-sweep", "moose", "scale"}:
        bundle = create_result_bundle(repo_root, config.name, args.run_id)
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
        return 0

    if args.command == "transient":
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
            provenance=provenance,
        )
        bundle.write_json("summary.json", summary)
        generate_summary_plots(bundle, summary)
        print(bundle.root)
        print(transient_sweep["backend"])
        print(transient_sweep["metrics"]["peak_power_fraction_p95"])
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
        if args.docker_openmc:
            execution = run_solver_backed_benchmark(repo_root, config.name, bundle.run_id)
            bundle.write_json("benchmark_execution.json", execution)
        else:
            if openmc is None:
                raise RuntimeError(
                    "Benchmark runs require a solver-backed OpenMC runtime. "
                    "Use `reactor benchmark <case> --docker-openmc` or run on a supported host."
                )
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

def _load_existing_bundle(repo_root: Path, case_name: str, run_id: str) -> ResultBundle:
    root = repo_root / "results" / case_name / run_id
    if not root.exists():
        raise FileNotFoundError(f"Run '{run_id}' for case '{case_name}' does not exist.")
    return ResultBundle(
        case_name=case_name,
        run_id=run_id,
        root=root,
        openmc_dir=root / "openmc",
        plots_dir=root / "plots",
        images_dir=root / "images",
        geometry_dir=root / "geometry",
    )


if __name__ == "__main__":
    raise SystemExit(main())
