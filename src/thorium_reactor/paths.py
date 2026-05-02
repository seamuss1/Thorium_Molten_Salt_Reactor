from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import uuid


PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


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
    return repo_root / "configs" / "cases" / safe_path_segment(case_name, "case name") / "case.yaml"


def create_result_bundle(repo_root: Path, case_name: str, run_id: str | None = None) -> ResultBundle:
    resolved_case_name = safe_path_segment(case_name, "case name")
    explicit_run_id = run_id is not None
    results_root = (repo_root / "results").resolve()
    case_root = (results_root / resolved_case_name).resolve()

    if explicit_run_id:
        resolved_run_id = safe_path_segment(run_id, "run id")
        root = (case_root / resolved_run_id).resolve()
        if not root.is_relative_to(results_root):
            raise ValueError("Result bundle path must stay inside the results directory.")
        if root.exists():
            raise FileExistsError(f"Run '{resolved_run_id}' for case '{resolved_case_name}' already exists.")
        root.mkdir(parents=True, exist_ok=False)
    else:
        root = None
        resolved_run_id = ""
        for _ in range(8):
            resolved_run_id = default_run_id()
            candidate = (case_root / resolved_run_id).resolve()
            if not candidate.is_relative_to(results_root):
                raise ValueError("Result bundle path must stay inside the results directory.")
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            root = candidate
            break
        if root is None:
            raise FileExistsError(f"Could not allocate a unique run id for case '{resolved_case_name}'.")

    openmc_dir = root / "openmc"
    plots_dir = root / "plots"
    images_dir = root / "images"
    geometry_dir = root / "geometry"
    geometry_exports_dir = geometry_dir / "exports"
    for path in (openmc_dir, plots_dir, images_dir, geometry_dir, geometry_exports_dir):
        path.mkdir(parents=True, exist_ok=True)
    return ResultBundle(
        case_name=resolved_case_name,
        run_id=resolved_run_id,
        root=root,
        openmc_dir=openmc_dir,
        plots_dir=plots_dir,
        images_dir=images_dir,
        geometry_dir=geometry_dir,
    )


def latest_result_bundle(repo_root: Path, case_name: str) -> ResultBundle:
    resolved_case_name = safe_path_segment(case_name, "case name")
    case_root = repo_root / "results" / resolved_case_name
    if not case_root.exists():
        raise FileNotFoundError(f"No results found for case '{resolved_case_name}'.")
    candidates = sorted([path for path in case_root.iterdir() if path.is_dir()])
    if not candidates:
        raise FileNotFoundError(f"No runs found for case '{resolved_case_name}'.")
    latest = candidates[-1]
    return ResultBundle(
        case_name=resolved_case_name,
        run_id=latest.name,
        root=latest,
        openmc_dir=latest / "openmc",
        plots_dir=latest / "plots",
        images_dir=latest / "images",
        geometry_dir=latest / "geometry",
    )


def default_run_id() -> str:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def safe_path_segment(value: str | None, label: str = "path segment") -> str:
    if value is None:
        raise ValueError(f"{label.capitalize()} is required.")
    segment = str(value)
    if segment in {"", ".", ".."}:
        raise ValueError(f"{label.capitalize()} must contain at least one safe character.")
    if any(separator in segment for separator in ("/", "\\")):
        raise ValueError(f"{label.capitalize()} must not contain path separators.")
    if ":" in segment:
        raise ValueError(f"{label.capitalize()} must not contain a drive or URI separator.")
    if not PATH_SEGMENT_RE.fullmatch(segment):
        raise ValueError(f"{label.capitalize()} contains unsupported characters.")
    return segment


def existing_result_bundle(repo_root: Path, case_name: str, run_id: str) -> ResultBundle:
    resolved_case_name = safe_path_segment(case_name, "case name")
    resolved_run_id = safe_path_segment(run_id, "run id")
    root = (repo_root / "results" / resolved_case_name / resolved_run_id).resolve()
    results_root = (repo_root / "results").resolve()
    if not root.is_relative_to(results_root):
        raise ValueError("Result bundle path must stay inside the results directory.")
    if not root.exists():
        raise FileNotFoundError(f"Run '{resolved_run_id}' for case '{resolved_case_name}' does not exist.")
    return ResultBundle(
        case_name=resolved_case_name,
        run_id=resolved_run_id,
        root=root,
        openmc_dir=root / "openmc",
        plots_dir=root / "plots",
        images_dir=root / "images",
        geometry_dir=root / "geometry",
    )
