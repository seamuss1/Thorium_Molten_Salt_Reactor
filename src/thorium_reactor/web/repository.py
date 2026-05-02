from __future__ import annotations

import copy
import csv
import json
import mimetypes
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from thorium_reactor.bundle_inputs import BENCHMARK_SNAPSHOT_NAME, CASE_SNAPSHOT_NAME, PROVENANCE_NAME
from thorium_reactor.capabilities import get_case_capabilities
from thorium_reactor.config import CaseConfig, load_case_config, resolve_benchmark_path
from thorium_reactor.paths import ResultBundle, case_config_path, create_result_bundle, discover_repo_root
from thorium_reactor.web.schemas import (
    ArtifactRef,
    CaseDetail,
    CaseSummary,
    DocRecord,
    DocSummary,
    DraftValidationResponse,
    EditableParameter,
    RunEvent,
    RunRecord,
    SimulationDraft,
    model_to_dict,
)


RUN_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
TERMINAL_STATUSES = {"completed", "failed", "canceled"}
RAW_ARTIFACTS = (
    "summary.json",
    "state_store.json",
    "runtime_context.json",
    "property_audit.json",
    "benchmark_residuals.json",
    "build_manifest.json",
    "geometry_description.json",
    "validation.json",
    "transient.json",
    "transient_sweep.json",
    "metrics.csv",
    "report.md",
    "case_snapshot.yaml",
    "benchmark_snapshot.yaml",
    "provenance.json",
    "job_status.json",
    "job_events.ndjson",
)


class WebRepository:
    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = discover_repo_root(repo_root)

    def list_cases(self) -> list[CaseSummary]:
        cases: list[CaseSummary] = []
        for path in sorted((self.repo_root / "configs" / "cases").glob("*/case.yaml")):
            config = load_case_config(path)
            cases.append(self._case_summary(config))
        return cases

    def get_case(self, case_name: str) -> CaseDetail:
        config = load_case_config(case_config_path(self.repo_root, case_name))
        summary = self._case_summary(config)
        benchmark_path = resolve_benchmark_path(self.repo_root, config.data)
        return CaseDetail(
            **model_to_dict(summary),
            config=config.data,
            validation_targets=config.validation_targets,
            benchmark_path=self._display_path(benchmark_path) if benchmark_path else None,
        )

    def validate_draft(self, case_name: str, *, draft_yaml: str | None, patch: Mapping[str, Any]) -> DraftValidationResponse:
        try:
            config, normalized_yaml = self._load_draft_config(case_name, draft_yaml=draft_yaml, patch=patch)
        except Exception as exc:  # noqa: BLE001 - surfaced as validation feedback for the UI.
            return DraftValidationResponse(valid=False, message=str(exc))
        return DraftValidationResponse(
            valid=True,
            message="Draft is valid.",
            normalized_yaml=normalized_yaml,
            editable_parameters=self._editable_parameters(config),
        )

    def prepare_run_bundle(self, draft: SimulationDraft) -> ResultBundle:
        config, normalized_yaml = self._load_draft_config(
            draft.case_name,
            draft_yaml=draft.draft_yaml,
            patch=draft.patch,
        )
        run_id = sanitize_run_id(draft.run_id)
        bundle = create_result_bundle(self.repo_root, config.name, run_id)
        (bundle.root / CASE_SNAPSHOT_NAME).write_text(normalized_yaml, encoding="utf-8")

        base_config = load_case_config(case_config_path(self.repo_root, draft.case_name))
        benchmark_path = resolve_benchmark_path(self.repo_root, config.data) or resolve_benchmark_path(self.repo_root, base_config.data)
        if benchmark_path and benchmark_path.exists():
            shutil.copy2(benchmark_path, bundle.root / BENCHMARK_SNAPSHOT_NAME)

        bundle.write_json(
            PROVENANCE_NAME,
            {
                "case_name": config.name,
                "created_utc": utc_now(),
                "run_id": bundle.run_id,
                "schema_version": 1,
                "source_benchmark_path": self._display_path(benchmark_path) if benchmark_path else None,
                "source_case_path": self._display_path(base_config.path),
                "used_snapshot": True,
                "web_draft": True,
            },
        )
        return bundle

    def list_runs(self) -> list[RunRecord]:
        records: list[RunRecord] = []
        results_root = self.repo_root / "results"
        if not results_root.exists():
            return records
        for case_dir in sorted([path for path in results_root.iterdir() if path.is_dir()]):
            for run_dir in sorted([path for path in case_dir.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True):
                records.append(self._run_record(case_dir.name, run_dir.name, run_dir))
        return records

    def get_run(self, case_name: str, run_id: str) -> RunRecord:
        run_dir = self.repo_root / "results" / safe_segment(case_name) / safe_segment(run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run '{run_id}' for case '{case_name}' was not found.")
        return self._run_record(case_name, run_id, run_dir)

    def read_events(self, case_name: str, run_id: str) -> list[RunEvent]:
        path = self.repo_root / "results" / safe_segment(case_name) / safe_segment(run_id) / "job_events.ndjson"
        if not path.exists():
            return []
        events: list[RunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(RunEvent(**json.loads(line)))
            except json.JSONDecodeError:
                continue
        return events

    def list_docs(self) -> list[DocSummary]:
        docs: list[DocSummary] = []
        candidates = [self.repo_root / "README.md", *sorted((self.repo_root / "docs").glob("*.md"))]
        for path in candidates:
            if path.exists():
                docs.append(self._doc_summary(path))
        return docs

    def get_doc(self, slug: str) -> DocRecord:
        for summary in self.list_docs():
            if summary.slug == slug:
                path = self.repo_root / summary.path
                return DocRecord(**model_to_dict(summary), content=path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Document '{slug}' was not found.")

    def resolve_artifact_path(self, artifact_path: str) -> Path:
        normalized = artifact_path.replace("\\", "/").lstrip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized or normalized == ".." or re.match(r"^[A-Za-z]:/", normalized):
            raise ValueError("Artifact path must stay inside the repository.")
        path = (self.repo_root / normalized).resolve()
        if not path.is_relative_to(self.repo_root.resolve()):
            raise ValueError("Artifact path must stay inside the repository.")
        if not path.is_file():
            raise FileNotFoundError(f"Artifact '{artifact_path}' was not found.")
        return path

    def _case_summary(self, config: CaseConfig) -> CaseSummary:
        latest_run = self._latest_run(config.name)
        return CaseSummary(
            name=config.name,
            reactor=copy.deepcopy(config.reactor),
            capabilities=sorted(get_case_capabilities(config)),
            editable_parameters=self._editable_parameters(config),
            latest_run=latest_run,
            docs=self._docs_for_case(config),
        )

    def _latest_run(self, case_name: str) -> RunRecord | None:
        case_results = self.repo_root / "results" / case_name
        if not case_results.exists():
            return None
        candidates = [path for path in case_results.iterdir() if path.is_dir()]
        if not candidates:
            return None
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        return self._run_record(case_name, latest.name, latest)

    def _run_record(self, case_name: str, run_id: str, run_dir: Path) -> RunRecord:
        status_payload = read_json(run_dir / "job_status.json", {})
        summary = read_json(run_dir / "summary.json", {})
        validation = read_json(run_dir / "validation.json", {})
        provenance = read_json(run_dir / "provenance.json", summary.get("input_provenance", {}))
        build_manifest = read_json(run_dir / "build_manifest.json", {})
        state_store = read_json(run_dir / "state_store.json", {})
        events = self.read_events(case_name, run_id)
        metrics = summary.get("metrics") or read_metrics_csv(run_dir / "metrics.csv")
        status = status_payload.get("status") or infer_status(run_dir, summary, validation)
        reactor = state_store.get("reactor") or summary.get("reactor") or build_manifest.get("reactor") or {}
        capabilities = summary.get("workflow_capabilities") or build_manifest.get("workflow_capabilities") or []
        return RunRecord(
            case_name=case_name,
            run_id=run_id,
            status=str(status),
            phase=status_payload.get("phase"),
            command_plan=[str(item) for item in status_payload.get("command_plan", [])],
            created_at=status_payload.get("created_at") or timestamp_from_path(run_dir),
            started_at=status_payload.get("started_at"),
            finished_at=status_payload.get("finished_at"),
            metrics=metrics if isinstance(metrics, dict) else {},
            validation=validation if isinstance(validation, dict) else {},
            provenance=provenance if isinstance(provenance, dict) else {},
            reactor=reactor if isinstance(reactor, dict) else {},
            capabilities=[str(item) for item in capabilities],
            artifacts=self._artifacts_for_run(run_dir),
            latest_event=events[-1] if events else None,
        )

    def _artifacts_for_run(self, run_dir: Path) -> list[ArtifactRef]:
        refs: dict[str, ArtifactRef] = {}
        for name in RAW_ARTIFACTS:
            path = run_dir / name
            if path.exists() and path.is_file():
                ref = self._artifact_ref(path, label=name, kind=artifact_kind(path))
                refs[ref.path] = ref

        for manifest_name, kind in (("plots_manifest.json", "plot"), ("render_assets.json", "geometry")):
            manifest = read_json(run_dir / manifest_name, {})
            if isinstance(manifest, dict):
                for label, raw_path in manifest.items():
                    path = self._resolve_recorded_path(raw_path)
                    if path and path.exists() and path.is_file():
                        ref = self._artifact_ref(path, label=str(label), kind=kind)
                        refs[ref.path] = ref

        exports_dir = run_dir / "geometry" / "exports"
        if exports_dir.exists():
            for path in sorted(exports_dir.glob("*")):
                if path.is_file() and path.suffix.lower() in {".gltf", ".bin", ".obj", ".stl", ".png", ".svg", ".json", ".mp4", ".gif"}:
                    ref = self._artifact_ref(path, label=path.name, kind=artifact_kind(path))
                    refs[ref.path] = ref
        return sorted(refs.values(), key=lambda ref: (ref.kind, ref.label))

    def _artifact_ref(self, path: Path, *, label: str, kind: str) -> ArtifactRef:
        rel = self._display_path(path)
        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return ArtifactRef(
            label=label,
            kind=kind,
            mime_type=mime_type,
            size=path.stat().st_size,
            path=rel,
            url=f"/api/runs/_/_/artifacts/{rel}",
        )

    def _resolve_recorded_path(self, value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        normalized = value.replace("\\", "/")
        if normalized.startswith("/workspace/"):
            return (self.repo_root / normalized.removeprefix("/workspace/")).resolve()
        path = Path(value)
        if path.is_absolute():
            try:
                resolved = path.resolve()
            except OSError:
                return None
            if resolved.is_relative_to(self.repo_root.resolve()):
                return resolved
            return None
        return (self.repo_root / normalized).resolve()

    def _load_draft_config(self, case_name: str, *, draft_yaml: str | None, patch: Mapping[str, Any]) -> tuple[CaseConfig, str]:
        base_path = case_config_path(self.repo_root, case_name)
        if draft_yaml:
            payload = yaml.safe_load(draft_yaml) or {}
        else:
            payload = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
            deep_merge(payload, patch)
        payload["name"] = case_name
        normalized_yaml = yaml.safe_dump(payload, sort_keys=False)
        tmp_parent = self.repo_root / ".tmp" / "web-validation"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(tmp_parent)) as tmp_name:
            case_dir = Path(tmp_name) / "configs" / "cases" / case_name
            case_dir.mkdir(parents=True, exist_ok=True)
            draft_path = case_dir / "case.yaml"
            draft_path.write_text(normalized_yaml, encoding="utf-8")
            config = load_case_config(draft_path)
        return config, normalized_yaml

    def _editable_parameters(self, config: CaseConfig) -> list[EditableParameter]:
        parameters: list[EditableParameter] = []

        def add(path: str, label: str, group: str, kind: str, *, unit: str | None = None, minimum: float | None = None, maximum: float | None = None, step: float | None = None, options: list[str] | None = None) -> None:
            value = get_path(config.data, path)
            if value is None:
                return
            parameters.append(
                EditableParameter(
                    path=path,
                    label=label,
                    group=group,
                    kind=kind,
                    value=value,
                    unit=unit,
                    minimum=minimum,
                    maximum=maximum,
                    step=step,
                    options=options,
                )
            )

        add("reactor.design_power_mwth", "Design thermal power", "Reactor", "number", unit="MWth", minimum=0.001, step=1.0)
        add("reactor.hot_leg_temp_c", "Hot leg temperature", "Reactor", "number", unit="C", step=1.0)
        add("reactor.cold_leg_temp_c", "Cold leg temperature", "Reactor", "number", unit="C", step=1.0)
        add("reactor.primary_cp_kj_kgk", "Primary heat capacity", "Reactor", "number", unit="kJ/kg-K", minimum=0.001, step=0.01)
        add("reactor.steam_generator_effectiveness", "Steam generator effectiveness", "Balance of plant", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("reactor.turbine_efficiency", "Turbine efficiency", "Balance of plant", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("reactor.generator_efficiency", "Generator efficiency", "Balance of plant", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("simulation.particles", "Particles per generation", "Neutronics", "integer", minimum=1, step=1000)
        add("simulation.batches", "Total batches", "Neutronics", "integer", minimum=1, step=1)
        add("simulation.inactive", "Inactive batches", "Neutronics", "integer", minimum=0, step=1)
        add("simulation.source.parameters.0", "Source X", "Neutronics", "number", unit="cm", step=0.1)
        add("simulation.source.parameters.1", "Source Y", "Neutronics", "number", unit="cm", step=0.1)
        add("simulation.source.parameters.2", "Source Z", "Neutronics", "number", unit="cm", step=0.1)
        add("transient.duration_s", "Transient duration", "Transient", "number", unit="s", minimum=0.1, step=1.0)
        add("transient.time_step_s", "Transient time step", "Transient", "number", unit="s", minimum=0.001, step=0.1)
        add("transient.fuel_temperature_feedback_pcm_per_c", "Fuel temperature feedback", "Transient", "number", unit="pcm/C", step=0.1)
        add("transient.graphite_temperature_feedback_pcm_per_c", "Graphite temperature feedback", "Transient", "number", unit="pcm/C", step=0.1)
        add("transient.coolant_temperature_feedback_pcm_per_c", "Coolant temperature feedback", "Transient", "number", unit="pcm/C", step=0.1)
        add("property_uncertainty.density_uncertainty_95_fraction", "Density uncertainty 95%", "Uncertainty", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("property_uncertainty.cp_uncertainty_95_fraction", "Heat capacity uncertainty 95%", "Uncertainty", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("property_uncertainty.thermal_conductivity_uncertainty_95_fraction", "Conductivity uncertainty 95%", "Uncertainty", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("property_uncertainty.dynamic_viscosity_uncertainty_95_fraction", "Viscosity uncertainty 95%", "Uncertainty", "number", minimum=0.0, maximum=1.0, step=0.01)
        add("property_uncertainty.core_outlet_temperature_uncertainty_95_c", "Outlet temperature uncertainty 95%", "Uncertainty", "number", unit="C", minimum=0.0, step=1.0)

        for material_name, spec in config.materials.items():
            if not isinstance(spec, Mapping):
                continue
            for property_name in ("density", "cp", "dynamic_viscosity", "thermal_conductivity"):
                property_spec = spec.get(property_name)
                if not isinstance(property_spec, Mapping):
                    continue
                units = str(property_spec.get("units", "")) or None
                for field_name, label_suffix in (("value", "value"), ("reference_value", "reference value"), ("slope_per_c", "slope")):
                    path = f"materials.{material_name}.{property_name}.{field_name}"
                    if get_path(config.data, path) is not None:
                        add(
                            path,
                            f"{material_name} {property_name} {label_suffix}",
                            "Materials",
                            "number",
                            unit=units,
                            step=0.01,
                        )
        return parameters

    def _docs_for_case(self, config: CaseConfig) -> list[dict[str, str]]:
        docs = self.list_docs()
        needles = {config.name.lower(), str(config.reactor.get("family", "")).lower(), str(config.reactor.get("mode", "")).lower()}
        selected: list[dict[str, str]] = []
        for doc in docs:
            haystack = f"{doc.slug} {doc.title}".lower()
            if any(needle and needle in haystack for needle in needles) or doc.slug in {"readme", "current-model-equations", "thermal-hydraulics-modeling-strategy"}:
                selected.append({"slug": doc.slug, "title": doc.title})
        return selected

    def _doc_summary(self, path: Path) -> DocSummary:
        content = path.read_text(encoding="utf-8")
        headings = [line.strip("# ").strip() for line in content.splitlines() if line.startswith("#")]
        title = headings[0] if headings else path.stem.replace("-", " ").title()
        slug = "readme" if path.name.lower() == "readme.md" else path.stem.lower()
        return DocSummary(slug=slug, title=title, path=self._display_path(path), headings=headings[:12])

    def _display_path(self, path: Path | None) -> str:
        if path is None:
            return ""
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.repo_root.resolve()).as_posix()
        except ValueError:
            return str(resolved)


def sanitize_run_id(run_id: str | None) -> str | None:
    if not run_id:
        return f"web-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"
    sanitized = RUN_ID_RE.sub("-", run_id).strip(".-_")
    if not sanitized:
        raise ValueError("Run id must contain at least one letter or number.")
    return sanitized[:80]


def safe_segment(value: str) -> str:
    sanitized = sanitize_run_id(value)
    if sanitized != value:
        raise ValueError(f"Unsafe path segment: {value!r}")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def read_metrics_csv(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    values: dict[str, Any] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = row.get("metric")
            value = row.get("value")
            if not key:
                continue
            values[key] = coerce_metric(value)
    return values


def coerce_metric(value: str | None) -> Any:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return value
    if parsed.is_integer():
        return int(parsed)
    return parsed


def timestamp_from_path(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def infer_status(run_dir: Path, summary: Mapping[str, Any], validation: Mapping[str, Any]) -> str:
    if summary.get("neutronics", {}).get("status") or validation or (run_dir / "report.md").exists():
        return "completed"
    if (run_dir / "build_manifest.json").exists():
        return "built"
    return "unknown"


def artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".gltf", ".glb", ".obj", ".stl", ".bin"}:
        return "geometry"
    if suffix in {".png", ".svg", ".gif", ".mp4"}:
        return "media"
    if suffix in {".json", ".csv", ".yaml", ".yml"}:
        return "data"
    if suffix == ".md":
        return "report" if path.name == "report.md" else "document"
    return "artifact"


def deep_merge(target: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            merge_list(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def merge_list(target: list[Any], patch: list[Any]) -> list[Any]:
    for index, value in enumerate(patch):
        if value is None:
            continue
        if index >= len(target):
            target.append(copy.deepcopy(value))
            continue
        if isinstance(value, Mapping) and isinstance(target[index], dict):
            deep_merge(target[index], value)
        else:
            target[index] = copy.deepcopy(value)
    return target


def get_path(payload: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
            continue
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def iter_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    return (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
