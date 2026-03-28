from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class ResultBundle:
    case_name: str
    run_id: str
    root: Path
    openmc_dir: Path
    plots_dir: Path
    images_dir: Path
    geometry_dir: Path

    @property
    def geometry_exports_dir(self) -> Path:
        return self.geometry_dir / "exports"

    def write_text(self, name: str, contents: str) -> Path:
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def write_json(self, name: str, payload: object) -> Path:
        import json

        path = self.root / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def write_metrics(self, metrics: dict[str, object]) -> Path:
        path = self.root / "metrics.csv"
        lines = ["metric,value"]
        for key, value in metrics.items():
            lines.append(f"{key},{value}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def discover_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "configs" / "cases").exists():
            return candidate
    raise FileNotFoundError("Could not locate repository root from the current working directory.")


def case_config_path(repo_root: Path, case_name: str) -> Path:
    return repo_root / "configs" / "cases" / case_name / "case.yaml"


def create_result_bundle(repo_root: Path, case_name: str, run_id: str | None = None) -> ResultBundle:
    resolved_run_id = run_id or datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    root = repo_root / "results" / case_name / resolved_run_id
    openmc_dir = root / "openmc"
    plots_dir = root / "plots"
    images_dir = root / "images"
    geometry_dir = root / "geometry"
    geometry_exports_dir = geometry_dir / "exports"
    for path in (root, openmc_dir, plots_dir, images_dir, geometry_dir, geometry_exports_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ResultBundle(
        case_name=case_name,
        run_id=resolved_run_id,
        root=root,
        openmc_dir=openmc_dir,
        plots_dir=plots_dir,
        images_dir=images_dir,
        geometry_dir=geometry_dir,
    )


def latest_result_bundle(repo_root: Path, case_name: str) -> ResultBundle:
    case_root = repo_root / "results" / case_name
    if not case_root.exists():
        raise FileNotFoundError(f"No results found for case '{case_name}'.")
    candidates = sorted([path for path in case_root.iterdir() if path.is_dir()])
    if not candidates:
        raise FileNotFoundError(f"No runs found for case '{case_name}'.")
    latest = candidates[-1]
    return ResultBundle(
        case_name=case_name,
        run_id=latest.name,
        root=latest,
        openmc_dir=latest / "openmc",
        plots_dir=latest / "plots",
        images_dir=latest / "images",
        geometry_dir=latest / "geometry",
    )
