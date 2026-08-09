from __future__ import annotations

import copy
import csv
import json
import math
import mimetypes
import re
import shutil
import tempfile
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from thorium_reactor.bundle_inputs import BENCHMARK_SNAPSHOT_NAME, CASE_SNAPSHOT_NAME, PROVENANCE_NAME
from thorium_reactor.capabilities import get_case_capabilities
from thorium_reactor.config import CaseConfig, load_case_config, resolve_benchmark_path
from thorium_reactor.paths import (
    ResultBundle,
    case_config_path,
    create_result_bundle,
    default_run_id,
    discover_repo_root,
    safe_path_segment,
)
from thorium_reactor.web.schemas import (
    ArtifactRef,
    CaseDetail,
    CaseSummary,
    DocRecord,
    DocSummary,
    DraftValidationResponse,
    EditableParameter,
    OutputMetric,
    OutputSection,
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
    "physics_core.json",
    "transport_mesh.json",
    "transport_summary.json",
    "transport_solution.npz",
    "depletion_chain.json",
    "depletion_summary.json",
    "depletion_history.json",
    "depletion_matrix.npz",
    "flow_summary.json",
    "build_manifest.json",
    "geometry_description.json",
    "validation.json",
    "transient.json",
    "transient_sweep.json",
    "uncertainty_manifest.json",
    "uncertainty_samples.json",
    "uncertainty_results.json",
    "uncertainty_budget.json",
    "uncertainty_summary.json",
    "uncertainty_execution.json",
    "benchmark_execution.json",
    "finance.json",
    "schedule.json",
    "project_plan.json",
    "metrics.csv",
    "cash_flow.csv",
    "cost_breakdown.csv",
    "report.md",
    "case_snapshot.yaml",
    "benchmark_snapshot.yaml",
    "provenance.json",
    "job_status.json",
    "job_events.ndjson",
    "plots_manifest.json",
    "render_assets.json",
)
VIEWABLE_GEOMETRY_EXTENSIONS = {".gltf", ".glb"}
TOP_LEVEL_ARTIFACT_EXTENSIONS = {".csv", ".json", ".md", ".ndjson", ".yaml", ".yml"}
VISUAL_ARTIFACT_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".mp4", ".png", ".svg", ".webp"}


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

    def validate_draft(
        self, case_name: str, *, draft_yaml: str | None, patch: Mapping[str, Any]
    ) -> DraftValidationResponse:
        try:
            config, normalized_yaml = self._load_draft_config(case_name, draft_yaml=draft_yaml, patch=patch)
        except Exception as exc:
            return DraftValidationResponse(valid=False, message=str(exc))
        return DraftValidationResponse(
            valid=True,
            message="Draft is valid.",
            normalized_yaml=normalized_yaml,
            editable_parameters=self._editable_parameters(config),
        )

    def prepare_run_bundle(self, draft: SimulationDraft) -> ResultBundle:
        case_name = safe_segment(draft.case_name)
        base_config = load_case_config(case_config_path(self.repo_root, case_name))
        config, normalized_yaml = self._load_draft_config(
            case_name,
            draft_yaml=draft.draft_yaml,
            patch=draft.patch,
        )
        run_id = sanitize_run_id(draft.run_id)
        bundle = create_result_bundle(self.repo_root, config.name, run_id)
        (bundle.root / CASE_SNAPSHOT_NAME).write_text(normalized_yaml, encoding="utf-8")

        benchmark_path = resolve_benchmark_path(self.repo_root, config.data) or resolve_benchmark_path(
            self.repo_root, base_config.data
        )
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
            for run_dir in sorted(
                [path for path in case_dir.iterdir() if path.is_dir()],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            ):
                try:
                    records.append(self._run_summary_record(case_dir.name, run_dir.name, run_dir))
                except ValueError:
                    continue
        return records

    def get_run(self, case_name: str, run_id: str) -> RunRecord:
        run_dir = self._run_dir(case_name, run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run '{run_id}' for case '{case_name}' was not found.")
        return self._run_record(case_name, run_id, run_dir)

    def read_events(self, case_name: str, run_id: str) -> list[RunEvent]:
        events, _offset = self.read_events_from(case_name, run_id, 0)
        return events

    def read_events_from(self, case_name: str, run_id: str, offset: int = 0) -> tuple[list[RunEvent], int]:
        """Read events appended at or after the given byte offset.

        Returns the newly parsed events and the byte offset to resume from, so
        pollers only pay for new data instead of re-parsing the whole log.
        """
        path = self._run_dir(case_name, run_id) / "job_events.ndjson"
        if not path.exists():
            return [], offset
        events: list[RunEvent] = []
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
            new_offset = offset + len(chunk)
        text = chunk.decode("utf-8", errors="replace")
        trailing = 0
        if text and not text.endswith("\n"):
            # A writer may be mid-append; leave the partial line for next poll.
            partial = text.rsplit("\n", 1)[-1]
            trailing = len(partial.encode("utf-8"))
            text = text[: len(text) - len(partial)]
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                events.append(RunEvent(**json.loads(line)))
            except json.JSONDecodeError:
                continue
        return events, new_offset - trailing

    def run_status(self, case_name: str, run_id: str) -> str:
        """Resolve the run status without building a full RunRecord."""
        run_dir = self._run_dir(case_name, run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run '{run_id}' for case '{case_name}' was not found.")
        status_payload = read_status_payload(run_dir)
        status = status_payload.get("status") if isinstance(status_payload, Mapping) else None
        if status:
            return str(status)
        return infer_status_from_files(run_dir)

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

    def resolve_artifact_path(self, case_name: str, run_id: str, artifact_path: str) -> Path:
        run_dir = self._run_dir(case_name, run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"Run '{run_id}' for case '{case_name}' was not found.")
        run_relative_path = normalize_artifact_path(artifact_path)
        path = (run_dir / run_relative_path).resolve()
        if not path.is_relative_to(run_dir.resolve()):
            raise ValueError("Artifact path must stay inside the run artifact directory.")
        if not path.is_file():
            raise FileNotFoundError(f"Artifact '{artifact_path}' was not found.")
        allowed_paths = {
            (self.repo_root / ref.path).resolve()
            for ref in self._artifacts_for_run(safe_segment(case_name), safe_segment(run_id), run_dir)
        }
        if path not in allowed_paths:
            raise FileNotFoundError(f"Artifact '{artifact_path}' is not available for this run.")
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
        safe_case_name = safe_segment(case_name)
        case_results = self.repo_root / "results" / safe_case_name
        if not case_results.exists():
            return None
        candidates = [path for path in case_results.iterdir() if path.is_dir()]
        if not candidates:
            return None
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        return self._run_summary_record(safe_case_name, latest.name, latest)

    def _run_record(self, case_name: str, run_id: str, run_dir: Path) -> RunRecord:
        safe_case_name = safe_segment(case_name)
        safe_run_id = safe_segment(run_id)
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
            case_name=safe_case_name,
            run_id=safe_run_id,
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
            artifacts=self._artifacts_for_run(safe_case_name, safe_run_id, run_dir),
            output_sections=self._output_sections(summary, validation),
            latest_event=events[-1] if events else None,
            progress=_coerce_progress(status_payload.get("progress")),
        )

    def _run_summary_record(self, case_name: str, run_id: str, run_dir: Path) -> RunRecord:
        safe_case_name = safe_segment(case_name)
        safe_run_id = safe_segment(run_id)
        status_payload = read_json(run_dir / "job_status.json", {})
        metrics = read_metrics_csv(run_dir / "metrics.csv")
        status = status_payload.get("status") or infer_status_from_files(run_dir)
        return RunRecord(
            case_name=safe_case_name,
            run_id=safe_run_id,
            status=str(status),
            phase=status_payload.get("phase"),
            command_plan=[str(item) for item in status_payload.get("command_plan", [])],
            created_at=status_payload.get("created_at") or timestamp_from_path(run_dir),
            started_at=status_payload.get("started_at"),
            finished_at=status_payload.get("finished_at"),
            metrics=metrics if isinstance(metrics, dict) else {},
            artifacts=self._summary_artifacts_for_run(safe_case_name, safe_run_id, run_dir),
            progress=_coerce_progress(status_payload.get("progress")),
        )

    def _output_sections(self, summary: Any, validation: Any) -> list[OutputSection]:
        if not isinstance(summary, Mapping):
            return []

        sections: list[OutputSection] = []
        self._add_neutronics_section(sections, summary)
        self._add_heat_balance_section(sections, summary)
        self._add_flow_section(sections, summary)
        self._add_advanced_physics_section(sections, summary)
        self._add_transient_section(sections, summary)
        self._add_transient_sweep_section(sections, summary)
        self._add_uncertainty_sweep_section(sections, summary)
        self._add_fuel_chemistry_section(sections, summary)
        self._add_validation_section(sections, summary, validation if isinstance(validation, Mapping) else {})
        self._add_commercial_section(sections, summary)
        self._add_visualization_section(sections, summary)
        return sections

    def _add_neutronics_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        physics_core = as_mapping(summary.get("physics_core"))
        physics_neutronics = as_mapping(physics_core.get("neutronics"))
        neutronics = as_mapping(summary.get("neutronics"))
        if not physics_neutronics and not neutronics:
            return

        simulation = as_mapping(neutronics.get("simulation"))
        feedback = as_mapping(physics_neutronics.get("feedback_coefficients"))
        metrics = output_metrics(
            (
                "k-effective",
                first_present(physics_neutronics.get("k_eff"), neutronics.get("k_eff")),
                "delta-k/k",
                "number",
            ),
            ("Beta effective", physics_neutronics.get("beta_eff"), "fraction", "number"),
            ("Energy groups", physics_neutronics.get("group_count"), "count", "number"),
            ("Particles", simulation.get("particles"), "histories", "number"),
            ("Batches", simulation.get("batches"), "count", "number"),
            ("Inactive batches", simulation.get("inactive"), "count", "number"),
            ("Fuel feedback", feedback.get("fuel_temperature_pcm_per_c"), "pcm/C", "number"),
            ("Uniform feedback", feedback.get("uniform_temperature_pcm_per_c"), "pcm/C", "number"),
        )
        methods = physics_neutronics.get("methods")
        if isinstance(methods, list) and methods:
            metrics.append(OutputMetric(label="Methods", value=", ".join(str(item) for item in methods)))

        notes = []
        message = neutronics.get("message")
        if message:
            notes.append(str(message))
        coupling = as_mapping(physics_core.get("coupling"))
        for key, value in list(coupling.items())[:3]:
            notes.append(f"{humanize_key(str(key))}: {value}")

        status = first_present(get_path(physics_core, "integrity_checks.status"), neutronics.get("status"))
        self._append_section(
            sections,
            "neutronics",
            "Neutronics and physics core",
            metrics=metrics,
            status=status,
            summary=make_sentence(
                [
                    f"k-effective {format_value(first_present(physics_neutronics.get('k_eff'), neutronics.get('k_eff')))}"
                    if first_present(physics_neutronics.get("k_eff"), neutronics.get("k_eff")) is not None
                    else None,
                    f"{physics_neutronics.get('group_count')} groups"
                    if physics_neutronics.get("group_count") is not None
                    else None,
                    f"OpenMC {neutronics.get('status')}" if neutronics.get("status") else None,
                ]
            ),
            notes=notes,
        )

    def _add_heat_balance_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        bop = as_mapping(summary.get("bop"))
        plant = as_mapping(summary.get("plant_system"))
        if not bop and not plant:
            return

        metrics = output_metrics(
            (
                "Thermal power",
                first_present(bop.get("thermal_power_mw"), get_path(plant, "design_basis.thermal_power_mw")),
                "MWth",
                "number",
            ),
            (
                "Electric power",
                first_present(bop.get("electric_power_mw"), get_path(plant, "design_basis.net_electric_power_mwe")),
                "MWe",
                "number",
            ),
            (
                "Steam generator duty",
                first_present(
                    bop.get("steam_generator_duty_mw"), get_path(plant, "design_basis.steam_generator_duty_mw")
                ),
                "MW",
                "number",
            ),
            ("Steam cycle usable duty", bop.get("steam_cycle_usable_duty_mw"), "MW", "number"),
            ("Condenser duty", bop.get("condenser_duty_mw"), "MW", "number"),
            ("Primary mass flow", bop.get("primary_mass_flow_kg_s"), "kg/s", "number"),
            ("Primary delta T", bop.get("primary_delta_t_c"), "C", "number"),
            ("Thermal efficiency", get_path(plant, "design_basis.overall_thermal_efficiency"), "fraction", "number"),
            ("Closure error", bop.get("closure_error_mw"), "MW", "number"),
        )
        notes = []
        if plant.get("scope"):
            notes.append(f"Scope: {str(plant['scope']).replace('_', ' ')}")
        if plant.get("model"):
            notes.append(f"Model: {plant['model']}")
        self._append_section(
            sections,
            "plant_balance",
            "Plant heat balance",
            metrics=metrics,
            summary=make_sentence(
                [
                    f"{format_value(first_present(bop.get('electric_power_mw'), get_path(plant, 'design_basis.net_electric_power_mwe')))} MWe",
                    f"from {format_value(first_present(bop.get('thermal_power_mw'), get_path(plant, 'design_basis.thermal_power_mw')))} MWth",
                    f"across {format_value(bop.get('primary_delta_t_c'))} C primary delta T"
                    if bop.get("primary_delta_t_c") is not None
                    else None,
                ]
            ),
            notes=notes,
        )

    def _add_flow_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        flow = as_mapping(summary.get("flow"))
        primary = as_mapping(summary.get("primary_system"))
        thermal = as_mapping(get_path(summary, "physics_core.thermal_hydraulics"))
        if not flow and not primary and not thermal:
            return

        reduced = as_mapping(flow.get("reduced_order"))
        active_flow = as_mapping(reduced.get("active_flow"))
        loop = as_mapping(primary.get("loop_hydraulics"))
        heat_exchanger = as_mapping(primary.get("heat_exchanger"))
        thermal_profile = as_mapping(primary.get("thermal_profile"))
        thermal_hx = as_mapping(thermal.get("heat_exchanger"))
        thermal_momentum = as_mapping(thermal.get("momentum_balance"))
        metrics = output_metrics(
            (
                "Primary mass flow",
                first_present(
                    primary.get("primary_mass_flow_kg_s"),
                    reduced.get("primary_mass_flow_kg_s"),
                    get_path(thermal, "boundary_conditions.mass_flow_kg_s"),
                ),
                "kg/s",
                "number",
            ),
            (
                "Volumetric flow",
                first_present(
                    primary.get("primary_volumetric_flow_m3_s"), active_flow.get("total_volumetric_flow_m3_s")
                ),
                "m3/s",
                "number",
            ),
            (
                "Representative velocity",
                first_present(active_flow.get("representative_velocity_m_s"), loop.get("limiting_velocity_m_s")),
                "m/s",
                "number",
            ),
            ("Core residence time", active_flow.get("representative_residence_time_s"), "s", "number"),
            ("Fuel salt inventory", get_path(primary, "inventory.fuel_salt.total_m3"), "m3", "number"),
            (
                "Pump pressure",
                first_present(
                    loop.get("required_pump_pressure_kpa"), get_path(thermal, "pump_curve.nominal_pressure_kpa")
                ),
                "kPa",
                "number",
            ),
            ("Pump shaft power", loop.get("pump_shaft_power_kw"), "kW", "number"),
            (
                "Max Reynolds number",
                first_present(loop.get("max_reynolds_number"), thermal_momentum.get("reynolds_number")),
                "Re",
                "number",
            ),
            (
                "Heat exchanger area",
                first_present(heat_exchanger.get("required_area_m2"), thermal_hx.get("area_m2")),
                "m2",
                "number",
            ),
            (
                "Heat exchanger duty",
                first_present(heat_exchanger.get("duty_mw"), thermal_profile.get("required_heat_exchanger_duty_mw")),
                "MW",
                "number",
            ),
            ("Secondary mass flow", heat_exchanger.get("secondary_mass_flow_kg_s"), "kg/s", "number"),
            ("Pipe heat loss", thermal_profile.get("total_pipe_heat_loss_kw"), "kW", "number"),
            ("Flow reversal margin", thermal_momentum.get("flow_reversal_margin_kpa"), "kPa", "number"),
        )
        notes = []
        for model in (
            primary.get("model"),
            thermal.get("model"),
            reduced.get("core_model", {}).get("kind") if isinstance(reduced.get("core_model"), Mapping) else None,
        ):
            if model:
                notes.append(f"Model: {model}")
        if thermal_momentum.get("flow_reversal_predicted") is True:
            notes.append("Nominal transient screen predicts a flow-reversal condition.")
        elif thermal_momentum.get("flow_reversal_predicted") is False:
            notes.append("Nominal transient screen does not predict flow reversal.")
        self._append_section(
            sections,
            "primary_flow",
            "Primary flow and heat transport",
            metrics=metrics,
            status=first_present(primary.get("status"), reduced.get("status"), thermal.get("status")),
            summary=make_sentence(
                [
                    f"{format_value(first_present(primary.get('primary_mass_flow_kg_s'), reduced.get('primary_mass_flow_kg_s'), get_path(thermal, 'boundary_conditions.mass_flow_kg_s')))} kg/s primary flow",
                    f"{format_value(first_present(heat_exchanger.get('required_area_m2'), thermal_hx.get('area_m2')))} m2 exchanger area",
                    f"{format_value(first_present(loop.get('max_reynolds_number'), thermal_momentum.get('reynolds_number')))} peak Reynolds",
                ]
            ),
            notes=dedupe(notes),
        )

    def _add_advanced_physics_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        transport = as_mapping(summary.get("transport_solver"))
        depletion = as_mapping(summary.get("depletion_matrix"))
        if not transport and not depletion:
            return

        mesh = as_mapping(transport.get("mesh"))
        metrics = output_metrics(
            ("RKDG radial cells", mesh.get("radial_cells"), "cells", "number"),
            ("RKDG axial cells", mesh.get("axial_cells"), "cells", "number"),
            ("Polynomial order", transport.get("polynomial_order"), "p", "number"),
            ("CFL", transport.get("cfl"), None, "number"),
            ("Transport residual", transport.get("conservation_residual"), "fraction", "number"),
            ("Minimum field", transport.get("minimum_field_value"), None, "number"),
            ("Depletion isotopes", depletion.get("isotope_count"), "count", "number"),
            ("Depletion zones", depletion.get("zone_count"), "count", "number"),
            ("Depletion nonzeros", depletion.get("matrix_nonzero_entries"), "entries", "number"),
            ("Atom-balance residual", depletion.get("atom_balance_residual"), "fraction", "number"),
            ("Inventory delta", depletion.get("inventory_delta_fraction"), "fraction", "number"),
        )
        notes = []
        for model in (transport.get("model"), depletion.get("model"), depletion.get("backend")):
            if model:
                notes.append(str(model))
        self._append_section(
            sections,
            "advanced_physics",
            "Native transport and depletion",
            metrics=metrics,
            status=first_present(transport.get("status"), depletion.get("status")),
            summary=make_sentence(
                [
                    f"R-Z RKDG {mesh.get('radial_cells')} x {mesh.get('axial_cells')}"
                    if mesh.get("radial_cells") and mesh.get("axial_cells")
                    else None,
                    f"{depletion.get('isotope_count')} isotope depletion matrix"
                    if depletion.get("isotope_count") is not None
                    else None,
                    f"atom residual {format_value(depletion.get('atom_balance_residual'))}"
                    if depletion.get("atom_balance_residual") is not None
                    else None,
                ]
            ),
            notes=dedupe(notes),
        )

    def _add_transient_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        transient = as_mapping(summary.get("transient"))
        if not transient:
            return

        metrics = output_metrics(
            ("Duration", transient.get("duration_s"), "s", "number"),
            ("Time step", transient.get("time_step_s"), "s", "number"),
            ("Peak power fraction", transient.get("peak_power_fraction"), "fraction", "number"),
            ("Final power fraction", transient.get("final_power_fraction"), "fraction", "number"),
            ("Final reactivity", transient.get("final_total_reactivity_pcm"), "pcm", "number"),
            ("Peak fuel temperature", transient.get("peak_fuel_temperature_c"), "C", "number"),
            ("Peak coolant temperature", transient.get("peak_coolant_temperature_c"), "C", "number"),
            ("Peak graphite temperature", transient.get("peak_graphite_temperature_c"), "C", "number"),
            (
                "Precursor transport loss",
                transient.get("final_precursor_transport_loss_fraction"),
                "fraction",
                "number",
            ),
            ("Peak corrosion index", transient.get("peak_corrosion_index"), "index", "number"),
            ("Final redox state", transient.get("final_redox_state_ev"), "eV", "number"),
        )
        self._append_section(
            sections,
            "transient_response",
            "Transient response",
            metrics=metrics,
            status=transient.get("status"),
            summary=make_sentence(
                [
                    f"{transient.get('scenario_name')} scenario" if transient.get("scenario_name") else None,
                    f"peaks at {format_value(transient.get('peak_power_fraction'))} power fraction",
                    f"{format_value(transient.get('peak_fuel_temperature_c'))} C fuel peak",
                ]
            ),
            notes=[f"Model: {transient['model']}"] if transient.get("model") else [],
        )

    def _add_transient_sweep_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        sweep = as_mapping(summary.get("transient_sweep"))
        if not sweep:
            return

        performance = as_mapping(sweep.get("runtime_performance"))
        checks = as_mapping(sweep.get("numerical_checks"))
        backend_report = as_mapping(sweep.get("backend_report"))
        details = as_mapping(backend_report.get("details"))
        metrics = output_metrics(
            ("Samples", sweep.get("samples"), "count", "number"),
            ("Backend", first_present(sweep.get("backend"), backend_report.get("selected")), None, "text"),
            # Which device did the work, and at what precision. Without these a
            # reader cannot tell a GPU run from a CPU run in the browser.
            ("Device", first_present(sweep.get("device"), details.get("device")), None, "text"),
            ("Precision", first_present(sweep.get("dtype"), details.get("dtype")), None, "text"),
            ("Peak power p95", sweep.get("peak_power_fraction_p95"), "fraction", "number"),
            ("Peak power max", sweep.get("peak_power_fraction_max"), "fraction", "number"),
            ("Final power p50", sweep.get("final_power_fraction_p50"), "fraction", "number"),
            ("Final power p95", sweep.get("final_power_fraction_p95"), "fraction", "number"),
            ("Fuel temp p95", sweep.get("peak_fuel_temperature_c_p95"), "C", "number"),
            ("Fuel temp max", sweep.get("peak_fuel_temperature_c_max"), "C", "number"),
            ("Final reactivity p50", sweep.get("final_total_reactivity_pcm_p50"), "pcm", "number"),
            ("Final reactivity p95", sweep.get("final_total_reactivity_pcm_p95"), "pcm", "number"),
            ("Sample steps per second", performance.get("sample_steps_per_s"), "steps/s", "number"),
        )
        notes = []
        if checks.get("status"):
            failures = checks.get("failures") or []
            notes.append(
                f"Numerical checks: {checks['status']}"
                + (f" ({', '.join(str(item) for item in failures)})" if failures else "")
            )
        requested = sweep.get("requested_backend") or backend_report.get("requested")
        selected = first_present(sweep.get("backend"), backend_report.get("selected"))
        if requested and selected and requested != selected and requested != "auto":
            # A run that asked for one backend and got another must say so
            # rather than presenting the substitute as what was requested.
            notes.append(f"Requested {requested}, ran on {selected}.")
        if backend_report.get("reason"):
            notes.append(str(backend_report["reason"]))
        self._append_section(
            sections,
            "transient_uncertainty",
            "Transient uncertainty sweep",
            metrics=metrics,
            status=sweep.get("status"),
            summary=make_sentence(
                [
                    f"{sweep.get('samples')} samples" if sweep.get("samples") is not None else None,
                    f"{first_present(sweep.get('backend'), backend_report.get('selected'))} backend"
                    if first_present(sweep.get("backend"), backend_report.get("selected"))
                    else None,
                    f"p95 peak power {format_value(sweep.get('peak_power_fraction_p95'))}",
                ]
            ),
            notes=notes,
        )

    def _add_uncertainty_sweep_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        sweep = as_mapping(summary.get("uncertainty_sweep"))
        if not sweep:
            return
        metrics = output_metrics(
            ("Samples", sweep.get("sample_count"), "count", "number"),
            ("Completed", sweep.get("completed_sample_count"), "count", "number"),
            ("Failed", sweep.get("failed_sample_count"), "count", "number"),
            ("Coverage", sweep.get("coverage_status"), None, "text"),
            ("Nominal keff", sweep.get("nominal_keff"), "k-effective", "number"),
            ("Input interval width", sweep.get("input_interval_width_pcm"), "pcm", "number"),
            ("Input sigma", sweep.get("input_sigma_pcm"), "pcm", "number"),
            ("Statistical sigma", sweep.get("statistical_sigma_pcm"), "pcm", "number"),
            ("Combined uncertainty", sweep.get("combined_uncertainty_pcm"), "pcm", "number"),
        )
        notes = []
        contributors = sweep.get("dominant_contributors", [])
        if isinstance(contributors, list) and contributors:
            notes.append("Top contributor: " + str(contributors[0].get("parameter_id", "n/a")))
        self._append_section(
            sections,
            "benchmark_uncertainty",
            "Benchmark uncertainty sweep",
            metrics=metrics,
            status=sweep.get("status"),
            summary=make_sentence(
                [
                    f"{sweep.get('completed_sample_count')} completed samples"
                    if sweep.get("completed_sample_count") is not None
                    else None,
                    f"coverage {sweep.get('coverage_status')}" if sweep.get("coverage_status") else None,
                    f"input width {format_value(sweep.get('input_interval_width_pcm'))} pcm",
                ]
            ),
            notes=notes,
        )

    def _add_fuel_chemistry_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        fuel_cycle = as_mapping(summary.get("fuel_cycle"))
        graphite = as_mapping(summary.get("graphite_lifetime"))
        chemistry = as_mapping(summary.get("chemistry"))
        tritium = as_mapping(summary.get("tritium"))
        if not fuel_cycle and not graphite and not chemistry and not tritium:
            return

        metrics = output_metrics(
            ("Fissile inventory", fuel_cycle.get("fissile_inventory_kg"), "kg", "number"),
            ("Heavy metal inventory", fuel_cycle.get("heavy_metal_inventory_kg"), "kg", "number"),
            ("Specific power", fuel_cycle.get("specific_power_mw_per_t_hm"), "MW/tHM", "number"),
            ("Net fissile change", fuel_cycle.get("net_fissile_change_fraction_per_day"), "fraction/day", "number"),
            ("Cleanup turnover", fuel_cycle.get("cleanup_turnover_days"), "days", "number"),
            ("Cleanup removal", fuel_cycle.get("cleanup_removal_efficiency"), "fraction", "number"),
            ("Corrosion risk", chemistry.get("corrosion_risk"), None, "text"),
            ("Corrosion index", chemistry.get("corrosion_index"), "index", "number"),
            ("Redox state", chemistry.get("redox_state_ev"), "eV", "number"),
            ("Tritium removal", tritium.get("removal_fraction"), "fraction", "number"),
            ("Environmental release", tritium.get("environmental_release_fraction"), "fraction", "number"),
            ("Graphite lifespan", graphite.get("estimated_lifespan_years"), "years", "number"),
            ("Graphite margin", graphite.get("lifetime_margin"), "fraction", "number"),
        )
        notes = []
        for source in (
            fuel_cycle.get("depletion_model"),
            chemistry.get("model"),
            tritium.get("basis"),
            graphite.get("basis"),
        ):
            if source:
                notes.append(str(source))
        self._append_section(
            sections,
            "fuel_chemistry",
            "Fuel cycle, chemistry, and tritium",
            metrics=metrics,
            status=first_present(graphite.get("screening_status"), chemistry.get("corrosion_risk")),
            summary=make_sentence(
                [
                    f"{format_value(fuel_cycle.get('fissile_inventory_kg'))} kg fissile inventory"
                    if fuel_cycle.get("fissile_inventory_kg") is not None
                    else None,
                    f"{chemistry.get('corrosion_risk')} corrosion risk" if chemistry.get("corrosion_risk") else None,
                    f"{format_value(graphite.get('estimated_lifespan_years'))} year graphite screen"
                    if graphite.get("estimated_lifespan_years") is not None
                    else None,
                ]
            ),
            notes=dedupe(notes[:4]),
        )

    def _add_validation_section(
        self, sections: list[OutputSection], summary: Mapping[str, Any], validation: Mapping[str, Any]
    ) -> None:
        traceability = as_mapping(summary.get("benchmark_traceability"))
        maturity = as_mapping(
            first_present(summary.get("validation_maturity"), traceability.get("validation_maturity"))
        )
        quality = as_mapping(first_present(summary.get("benchmark_quality"), traceability.get("benchmark_quality")))
        checks = validation.get("checks") if isinstance(validation.get("checks"), list) else []
        if not traceability and not maturity and not checks:
            return

        status_counts: dict[str, int] = {}
        for check in checks:
            if isinstance(check, Mapping):
                status = str(check.get("status", "unknown"))
                status_counts[status] = status_counts.get(status, 0) + 1

        confidence = as_mapping(traceability.get("confidence_summary"))
        coverage = as_mapping(traceability.get("coverage"))
        target_coverage = as_mapping(coverage.get("targets_structured"))
        evidence_coverage = as_mapping(coverage.get("targets_with_evidence"))
        metrics = output_metrics(
            ("Traceability score", traceability.get("traceability_score"), "score", "number"),
            ("Validation maturity", maturity.get("validation_maturity_score"), "score", "number"),
            ("Benchmark quality", quality.get("quality_score"), "score", "number"),
            ("Validation checks", len(checks) if checks else None, "count", "number"),
            ("Checks passed", status_counts.get("pass"), "count", "number"),
            ("Checks pending", status_counts.get("pending"), "count", "number"),
            ("Checks failed", status_counts.get("fail"), "count", "number"),
            (
                "Datasets",
                len(traceability.get("datasets")) if isinstance(traceability.get("datasets"), list) else None,
                "count",
                "number",
            ),
            ("High confidence items", confidence.get("high"), "count", "number"),
            ("Medium confidence items", confidence.get("medium"), "count", "number"),
            ("Structured targets", target_coverage.get("linked"), "linked", "number"),
            ("Targets with evidence", evidence_coverage.get("linked"), "linked", "number"),
        )
        notes = []
        for gap in (
            maturity.get("gaps")
            if isinstance(maturity.get("gaps"), list)
            else traceability.get("gaps")
            if isinstance(traceability.get("gaps"), list)
            else []
        ):
            notes.append(str(gap))
        quality_blockers = quality.get("promotion_blockers")
        if isinstance(quality_blockers, list):
            for blocker in quality_blockers:
                notes.append(str(blocker))
        self._append_section(
            sections,
            "validation_maturity",
            "Validation and benchmark maturity",
            metrics=metrics,
            status=first_present(
                quality.get("quality_stage"),
                maturity.get("validation_maturity_stage"),
                traceability.get("maturity_stage"),
                "validation",
            ),
            summary=make_sentence(
                [
                    f"{format_value(traceability.get('traceability_score'))} traceability score"
                    if traceability.get("traceability_score") is not None
                    else None,
                    f"{format_value(maturity.get('validation_maturity_score'))} maturity score"
                    if maturity.get("validation_maturity_score") is not None
                    else None,
                    f"{format_value(quality.get('quality_score'))} benchmark quality score"
                    if quality.get("quality_score") is not None
                    else None,
                    f"{status_counts.get('pass', 0)} checks passing" if checks else None,
                ]
            ),
            notes=notes[:4],
        )

    def _add_commercial_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        finance = as_mapping(summary.get("finance"))
        schedule = as_mapping(summary.get("schedule"))
        if not finance and not schedule:
            return

        outputs = as_mapping(finance.get("outputs"))
        inputs = as_mapping(finance.get("inputs"))
        costs = as_mapping(finance.get("cost_breakdown_usd"))
        annual_costs = as_mapping(finance.get("annual_costs_usd_per_year"))
        metrics = output_metrics(
            ("LCOE", outputs.get("lcoe_usd_per_mwh"), "USD/MWh", "currency"),
            ("LCOE", outputs.get("lcoe_cents_per_kwh"), "cents/kWh", "number"),
            ("Net capacity", inputs.get("net_capacity_mwe"), "MWe", "number"),
            ("Capacity factor", inputs.get("capacity_factor"), "fraction", "number"),
            ("Annual generation", outputs.get("annual_generation_mwh"), "MWh", "number"),
            ("Capitalized cost", costs.get("total_capitalized_cost"), "USD", "currency"),
            ("Cost per kWe", outputs.get("capitalized_cost_usd_per_kwe"), "USD/kWe", "currency"),
            ("Annual cost", annual_costs.get("total"), "USD/year", "currency"),
            ("Construction duration", inputs.get("construction_months"), "months", "number"),
            ("Years to operation", schedule.get("total_years_to_commercial_operation"), "years", "number"),
            ("Commercial operation", schedule.get("commercial_operation_date"), None, "text"),
        )
        notes = []
        for value in (
            finance.get("planning_basis"),
            get_path(finance, "provenance.caveat"),
            schedule.get("planning_basis"),
        ):
            if value:
                notes.append(str(value))
        self._append_section(
            sections,
            "commercial_planning",
            "Commercial planning and cost",
            metrics=metrics,
            status=first_present(finance.get("status"), schedule.get("status")),
            summary=make_sentence(
                [
                    f"{format_value(outputs.get('lcoe_usd_per_mwh'))} USD/MWh LCOE"
                    if outputs.get("lcoe_usd_per_mwh") is not None
                    else None,
                    f"{format_value(schedule.get('total_years_to_commercial_operation'))} years to commercial operation"
                    if schedule.get("total_years_to_commercial_operation") is not None
                    else None,
                    str(schedule.get("commercial_operation_date"))
                    if schedule.get("commercial_operation_date")
                    else None,
                ]
            ),
            notes=dedupe(notes),
        )

    def _add_visualization_section(self, sections: list[OutputSection], summary: Mapping[str, Any]) -> None:
        visualization = as_mapping(summary.get("visualization_state"))
        if not visualization:
            return

        assets = as_mapping(visualization.get("assets"))
        available_views = (
            visualization.get("available_views") if isinstance(visualization.get("available_views"), list) else []
        )
        metrics = output_metrics(
            ("Geometry description", visualization.get("has_geometry_description"), None, "boolean"),
            ("Render assets", visualization.get("has_render_assets"), None, "boolean"),
            ("Available views", len(available_views), "count", "number"),
            ("Assets", len(assets), "count", "number"),
            ("glTF asset", "yes" if assets.get("gltf") else "no", None, "text"),
            ("Video asset", "yes" if assets.get("mp4") or assets.get("gif") else "no", None, "text"),
        )
        self._append_section(
            sections,
            "visualization",
            "Visualization and geometry",
            metrics=metrics,
            status="ready" if visualization.get("has_render_assets") else "missing assets",
            summary=make_sentence(
                [
                    f"{len(available_views)} rendered views" if available_views else None,
                    "viewable 3D geometry" if assets.get("gltf") or assets.get("glb") else "no viewable 3D geometry",
                ]
            ),
            notes=[f"Views: {', '.join(str(item) for item in available_views)}"] if available_views else [],
        )

    def _append_section(
        self,
        sections: list[OutputSection],
        section_id: str,
        title: str,
        *,
        metrics: list[OutputMetric],
        status: Any = None,
        summary: str | None = None,
        notes: list[str] | None = None,
    ) -> None:
        clean_notes = [note for note in (notes or []) if note]
        if not metrics and not summary and not clean_notes:
            return
        sections.append(
            OutputSection(
                id=section_id,
                title=title,
                status=str(status) if status is not None else None,
                summary=summary,
                metrics=metrics,
                notes=clean_notes,
            )
        )

    def _summary_artifacts_for_run(self, case_name: str, run_id: str, run_dir: Path) -> list[ArtifactRef]:
        refs: dict[str, ArtifactRef] = {}
        for path in self._viewable_geometry_paths(run_dir):
            ref = self._artifact_ref(
                path, run_dir=run_dir, case_name=case_name, run_id=run_id, label=path.name, kind="geometry"
            )
            refs[ref.path] = ref
        return sorted(refs.values(), key=lambda ref: ref.label)

    def _viewable_geometry_paths(self, run_dir: Path) -> list[Path]:
        paths: dict[str, Path] = {}

        exports_dir = run_dir / "geometry" / "exports"
        if exports_dir.exists():
            for extension in VIEWABLE_GEOMETRY_EXTENSIONS:
                for path in sorted(exports_dir.glob(f"*{extension}")):
                    if self._is_viewable_geometry_file(path):
                        paths[path.as_posix()] = path

        manifest = read_json(run_dir / "render_assets.json", {})
        if isinstance(manifest, dict):
            for raw_path in manifest.values():
                if not is_viewable_geometry_artifact_path(raw_path):
                    continue
                path = self._resolve_recorded_path(raw_path, run_dir=run_dir)
                if path and self._is_viewable_geometry_file(path):
                    paths[path.as_posix()] = path

        return sorted(paths.values(), key=lambda path: path.name)

    def _is_viewable_geometry_file(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in VIEWABLE_GEOMETRY_EXTENSIONS and path.stat().st_size > 0

    def _artifacts_for_run(self, case_name: str, run_id: str, run_dir: Path) -> list[ArtifactRef]:
        refs: dict[str, ArtifactRef] = {}
        for name in RAW_ARTIFACTS:
            path = run_dir / name
            if path.exists() and path.is_file():
                ref = self._artifact_ref(
                    path, run_dir=run_dir, case_name=case_name, run_id=run_id, label=name, kind=artifact_kind(path)
                )
                refs[ref.path] = ref

        for path in sorted(run_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in TOP_LEVEL_ARTIFACT_EXTENSIONS:
                ref = self._artifact_ref(
                    path, run_dir=run_dir, case_name=case_name, run_id=run_id, label=path.name, kind=artifact_kind(path)
                )
                refs[ref.path] = ref

        for manifest_name, kind in (("plots_manifest.json", "plot"), ("render_assets.json", "geometry")):
            manifest = read_json(run_dir / manifest_name, {})
            if isinstance(manifest, dict):
                for label, raw_path in manifest.items():
                    path = self._resolve_recorded_path(raw_path, run_dir=run_dir)
                    if path and path.exists() and path.is_file():
                        ref = self._artifact_ref(
                            path, run_dir=run_dir, case_name=case_name, run_id=run_id, label=str(label), kind=kind
                        )
                        refs[ref.path] = ref

        exports_dir = run_dir / "geometry" / "exports"
        if exports_dir.exists():
            for path in sorted(exports_dir.glob("*")):
                if path.is_file() and path.suffix.lower() in {
                    ".gltf",
                    ".bin",
                    ".obj",
                    ".stl",
                    ".png",
                    ".svg",
                    ".json",
                    ".mp4",
                    ".gif",
                }:
                    ref = self._artifact_ref(
                        path,
                        run_dir=run_dir,
                        case_name=case_name,
                        run_id=run_id,
                        label=path.name,
                        kind=artifact_kind(path),
                    )
                    refs[ref.path] = ref

        for asset_dir_name in ("plots", "images"):
            asset_dir = run_dir / asset_dir_name
            if not asset_dir.exists():
                continue
            for path in sorted(asset_dir.glob("*")):
                if path.is_file() and path.suffix.lower() in VISUAL_ARTIFACT_EXTENSIONS:
                    ref = self._artifact_ref(
                        path,
                        run_dir=run_dir,
                        case_name=case_name,
                        run_id=run_id,
                        label=path.name,
                        kind=artifact_kind(path),
                    )
                    refs[ref.path] = ref
        return sorted(refs.values(), key=lambda ref: (ref.kind, ref.label))

    def _artifact_ref(
        self, path: Path, *, run_dir: Path, case_name: str, run_id: str, label: str, kind: str
    ) -> ArtifactRef:
        resolved = path.resolve()
        run_root = run_dir.resolve()
        if not resolved.is_relative_to(run_root):
            raise ValueError("Run artifact references must stay inside the run directory.")
        rel = self._display_path(resolved)
        run_rel = resolved.relative_to(run_root).as_posix()
        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return ArtifactRef(
            label=label,
            kind=kind,
            mime_type=mime_type,
            size=path.stat().st_size,
            path=rel,
            url=f"/api/runs/{case_name}/{run_id}/artifacts/{run_rel}",
        )

    def _resolve_recorded_path(self, value: Any, *, run_dir: Path) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        normalized = value.replace("\\", "/")
        if normalized.startswith("/workspace/"):
            return self._artifact_candidate_within_run(self.repo_root / normalized.removeprefix("/workspace/"), run_dir)
        path = Path(value)
        if path.is_absolute():
            try:
                resolved = path.resolve()
            except OSError:
                return None
            return resolved if resolved.is_relative_to(run_dir.resolve()) else None
        return self._artifact_candidate_within_run(self.repo_root / normalized, run_dir)

    def _load_draft_config(
        self, case_name: str, *, draft_yaml: str | None, patch: Mapping[str, Any]
    ) -> tuple[CaseConfig, str]:
        safe_case_name = safe_segment(case_name)
        base_path = case_config_path(self.repo_root, safe_case_name)
        if not base_path.exists():
            raise FileNotFoundError(f"Case '{safe_case_name}' was not found.")
        if draft_yaml:
            payload = yaml.safe_load(draft_yaml) or {}
        else:
            payload = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
            deep_merge(payload, patch)
        payload["name"] = safe_case_name
        normalized_yaml = yaml.safe_dump(payload, sort_keys=False)
        tmp_parent = self.repo_root / ".tmp" / "web-validation"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(tmp_parent)) as tmp_name:
            case_dir = Path(tmp_name) / "configs" / "cases" / safe_case_name
            case_dir.mkdir(parents=True, exist_ok=True)
            draft_path = case_dir / "case.yaml"
            draft_path.write_text(normalized_yaml, encoding="utf-8")
            config = load_case_config(draft_path)
        return config, normalized_yaml

    def _run_dir(self, case_name: str, run_id: str) -> Path:
        return self.repo_root / "results" / safe_segment(case_name) / safe_segment(run_id)

    def _artifact_candidate_within_run(self, path: Path, run_dir: Path) -> Path | None:
        try:
            resolved = path.resolve()
        except OSError:
            return None
        return resolved if resolved.is_relative_to(run_dir.resolve()) else None

    def _editable_parameters(self, config: CaseConfig) -> list[EditableParameter]:
        parameters: list[EditableParameter] = []

        def add(
            path: str,
            label: str,
            group: str,
            kind: str,
            *,
            unit: str | None = None,
            minimum: float | None = None,
            maximum: float | None = None,
            step: float | None = None,
            options: list[str] | None = None,
        ) -> None:
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

        add(
            "reactor.design_power_mwth",
            "Design thermal power",
            "Reactor",
            "number",
            unit="MWth",
            minimum=0.001,
            step=1.0,
        )
        add("reactor.hot_leg_temp_c", "Hot leg temperature", "Reactor", "number", unit="C", step=1.0)
        add("reactor.cold_leg_temp_c", "Cold leg temperature", "Reactor", "number", unit="C", step=1.0)
        add(
            "reactor.primary_cp_kj_kgk",
            "Primary heat capacity",
            "Reactor",
            "number",
            unit="kJ/kg-K",
            minimum=0.001,
            step=0.01,
        )
        add(
            "reactor.steam_generator_effectiveness",
            "Steam generator effectiveness",
            "Balance of plant",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add(
            "reactor.turbine_efficiency",
            "Turbine efficiency",
            "Balance of plant",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add(
            "reactor.generator_efficiency",
            "Generator efficiency",
            "Balance of plant",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add("simulation.particles", "Particles per generation", "Neutronics", "integer", minimum=1, step=1000)
        add("simulation.batches", "Total batches", "Neutronics", "integer", minimum=1, step=1)
        add("simulation.inactive", "Inactive batches", "Neutronics", "integer", minimum=0, step=1)
        add("simulation.source.parameters.0", "Source X", "Neutronics", "number", unit="cm", step=0.1)
        add("simulation.source.parameters.1", "Source Y", "Neutronics", "number", unit="cm", step=0.1)
        add("simulation.source.parameters.2", "Source Z", "Neutronics", "number", unit="cm", step=0.1)
        add("transient.duration_s", "Transient duration", "Transient", "number", unit="s", minimum=0.1, step=1.0)
        add("transient.time_step_s", "Transient time step", "Transient", "number", unit="s", minimum=0.001, step=0.1)
        add(
            "transient.fuel_temperature_feedback_pcm_per_c",
            "Fuel temperature feedback",
            "Transient",
            "number",
            unit="pcm/C",
            step=0.1,
        )
        add(
            "transient.graphite_temperature_feedback_pcm_per_c",
            "Graphite temperature feedback",
            "Transient",
            "number",
            unit="pcm/C",
            step=0.1,
        )
        add(
            "transient.coolant_temperature_feedback_pcm_per_c",
            "Coolant temperature feedback",
            "Transient",
            "number",
            unit="pcm/C",
            step=0.1,
        )
        add(
            "property_uncertainty.density_uncertainty_95_fraction",
            "Density uncertainty 95%",
            "Uncertainty",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add(
            "property_uncertainty.cp_uncertainty_95_fraction",
            "Heat capacity uncertainty 95%",
            "Uncertainty",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add(
            "property_uncertainty.thermal_conductivity_uncertainty_95_fraction",
            "Conductivity uncertainty 95%",
            "Uncertainty",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add(
            "property_uncertainty.dynamic_viscosity_uncertainty_95_fraction",
            "Viscosity uncertainty 95%",
            "Uncertainty",
            "number",
            minimum=0.0,
            maximum=1.0,
            step=0.01,
        )
        add(
            "property_uncertainty.core_outlet_temperature_uncertainty_95_c",
            "Outlet temperature uncertainty 95%",
            "Uncertainty",
            "number",
            unit="C",
            minimum=0.0,
            step=1.0,
        )

        for material_name, spec in config.materials.items():
            if not isinstance(spec, Mapping):
                continue
            for property_name in ("density", "cp", "dynamic_viscosity", "thermal_conductivity"):
                property_spec = spec.get(property_name)
                if not isinstance(property_spec, Mapping):
                    continue
                units = str(property_spec.get("units", "")) or None
                for field_name, label_suffix in (
                    ("value", "value"),
                    ("reference_value", "reference value"),
                    ("slope_per_c", "slope"),
                ):
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
        needles = {
            config.name.lower(),
            str(config.reactor.get("family", "")).lower(),
            str(config.reactor.get("mode", "")).lower(),
        }
        selected: list[dict[str, str]] = []
        for doc in docs:
            haystack = f"{doc.slug} {doc.title}".lower()
            if any(needle and needle in haystack for needle in needles) or doc.slug in {
                "readme",
                "current-model-equations",
                "thermal-hydraulics-modeling-strategy",
            }:
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
        return f"web-{default_run_id()}"
    sanitized = RUN_ID_RE.sub("-", run_id).strip(".-_")
    if not sanitized:
        raise ValueError("Run id must contain at least one letter or number.")
    return sanitized[:80]


def safe_segment(value: str) -> str:
    return safe_path_segment(value)


def normalize_artifact_path(artifact_path: str) -> Path:
    normalized = artifact_path.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("Artifact path must be relative to the run directory.")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Artifact path must stay inside the run directory.")
    return Path(*candidate.parts)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read_status_payload(run_dir: Path) -> Any:
    """Read job_status.json, tolerating a write that is in flight.

    Writes are atomic, but a reader on a filesystem without atomic rename
    semantics can still catch a partial file. One retry absorbs that; a file
    that stays unparseable is genuinely corrupt, so we fall through to
    inference rather than reporting the run live forever and leaving the event
    stream open.
    """
    path = run_dir / "job_status.json"
    if not path.exists():
        return {}
    payload = read_json(path, None)
    if payload is None:
        time.sleep(0.05)
        payload = read_json(path, None)
    return payload if payload is not None else {}


def _coerce_progress(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


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


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def output_metrics(*items: tuple[str, Any, str | None, str | None]) -> list[OutputMetric]:
    metrics: list[OutputMetric] = []
    for label, value, unit, kind in items:
        if value is None:
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, (dict, list)):
            continue
        metrics.append(OutputMetric(label=label, value=value, unit=unit, kind=kind))
    return metrics


def make_sentence(parts: Iterable[str | None]) -> str | None:
    clean = [part.strip(" .") for part in parts if isinstance(part, str) and part.strip(" .") and "n/a" not in part]
    if not clean:
        return None
    return ", ".join(clean) + "."


def format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "n/a"
        magnitude = abs(value)
        if magnitude >= 1000:
            return f"{value:,.1f}"
        if magnitude >= 10:
            return f"{value:,.2f}".rstrip("0").rstrip(".")
        if magnitude >= 0.01:
            return f"{value:,.4f}".rstrip("0").rstrip(".")
        return f"{value:.3g}"
    return str(value)


def humanize_key(value: str) -> str:
    return value.replace("_", " ").strip().capitalize()


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def timestamp_from_path(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")


def infer_status(run_dir: Path, summary: Mapping[str, Any], validation: Mapping[str, Any]) -> str:
    if summary.get("neutronics", {}).get("status") or validation or (run_dir / "report.md").exists():
        return "completed"
    if (run_dir / "build_manifest.json").exists():
        return "built"
    return "unknown"


def infer_status_from_files(run_dir: Path) -> str:
    # The stage manifest is the record of what actually happened. A CLI stage
    # that raised (a sweep failing its numerical checks, say) leaves the
    # summary.json an earlier stage wrote, which would otherwise read as a
    # clean success.
    if stage_manifest_last_status(run_dir) == "failed":
        return "failed"
    if (
        (run_dir / "summary.json").exists()
        or (run_dir / "validation.json").exists()
        or (run_dir / "report.md").exists()
    ):
        return "completed"
    if (run_dir / "build_manifest.json").exists():
        return "built"
    return "unknown"


def stage_manifest_last_status(run_dir: Path) -> str | None:
    """Status of the most recent recorded stage, or None if there is no manifest."""
    manifest = read_json(run_dir / "stage_manifest.json", {})
    stages = manifest.get("stages") if isinstance(manifest, Mapping) else None
    if not isinstance(stages, list) or not stages:
        return None
    last = stages[-1]
    return str(last.get("status")) if isinstance(last, Mapping) and last.get("status") else None


def is_viewable_geometry_artifact_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/").lower()
    return any(normalized.endswith(extension) for extension in VIEWABLE_GEOMETRY_EXTENSIONS)


def artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".gltf", ".glb", ".obj", ".stl", ".bin"}:
        return "geometry"
    if suffix in {".gif", ".jpeg", ".jpg", ".mp4", ".png", ".svg", ".webp"}:
        return "media"
    if suffix in {".json", ".csv", ".yaml", ".yml", ".ndjson"}:
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
