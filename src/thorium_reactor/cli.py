from __future__ import annotations

import argparse
import json
from json import JSONDecodeError
from pathlib import Path

from thorium_reactor.config import load_case_config
from thorium_reactor.geometry.exporters import export_geometry
from thorium_reactor.neutronics.workflows import build_case, run_case, validate_case
from thorium_reactor.paths import ResultBundle, case_config_path, create_result_bundle, discover_repo_root, latest_result_bundle
from thorium_reactor.reporting.reports import generate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reactor", description="Thorium reactor platform CLI")
    parser.add_argument("--repo-root", type=Path, default=None, help="Override the repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("build", "run", "validate", "report", "render"):
        command = subparsers.add_parser(command_name, help=f"{command_name.capitalize()} a reactor case")
        command.add_argument("case", help="Case name under configs/cases")
        command.add_argument("--run-id", default=None, help="Reuse or create a specific results run id")
        if command_name == "run":
            command.add_argument("--no-solver", action="store_true", help="Skip calling the OpenMC solver")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve() if args.repo_root else discover_repo_root()
    config = load_case_config(case_config_path(repo_root, args.case))

    if args.command in {"build", "run"}:
        bundle = create_result_bundle(repo_root, config.name, args.run_id)
    else:
        bundle = latest_result_bundle(repo_root, config.name) if args.run_id is None else _load_existing_bundle(repo_root, config.name, args.run_id)

    if args.command == "build":
        built = build_case(config, bundle.openmc_dir)
        geometry_assets = export_geometry(built.geometry_description, bundle.geometry_exports_dir)
        build_manifest = dict(built.manifest)
        build_manifest["geometry_assets"] = geometry_assets
        bundle.write_json("build_manifest.json", build_manifest)
        if built.model is not None:
            built.model.export_to_xml(directory=str(bundle.openmc_dir))
        print(bundle.root)
        return 0

    if args.command == "run":
        summary = run_case(config, bundle, solver_enabled=not args.no_solver)
        print(bundle.root)
        print(summary["neutronics"]["status"])
        return 0

    if args.command == "validate":
        result = validate_case(config, bundle)
        print(result["passed"])
        return 0

    if args.command == "render":
        built = build_case(config, bundle.openmc_dir)
        assets = export_geometry(built.geometry_description, bundle.geometry_exports_dir)
        bundle.write_json("render_assets.json", assets)
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
        validation_path = bundle.root / "validation.json"
        needs_validation = not validation_path.exists()
        if validation_path.exists():
            try:
                json.loads(validation_path.read_text(encoding="utf-8"))
            except JSONDecodeError:
                needs_validation = True
        if needs_validation:
            validate_case(config, bundle, summary=summary)
        geometry_assets = None
        render_assets_path = bundle.root / "render_assets.json"
        if render_assets_path.exists():
            geometry_assets = json.loads(render_assets_path.read_text(encoding="utf-8"))
        else:
            build_manifest_path = bundle.root / "build_manifest.json"
            if build_manifest_path.exists():
                build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
                geometry_assets = build_manifest.get("geometry_assets")
        report = generate_report(config.name, config.data, summary_path, validation_path, geometry_assets)
        report_path = bundle.write_text("report.md", report)
        print(report_path)
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
