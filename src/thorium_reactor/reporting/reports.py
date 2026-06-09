from __future__ import annotations

import json
import math
import re
from csv import DictWriter
from io import StringIO
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from thorium_reactor.benchmarking import assess_benchmark_traceability
from thorium_reactor.reporting.plots import load_figure_catalog


def generate_report(
    case_name: str,
    config: dict[str, Any],
    summary_path: Path,
    validation_path: Path | None,
    geometry_assets: dict[str, str] | None,
    benchmark: dict[str, Any] | None = None,
    plot_assets: dict[str, str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> str:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    artifact_status_path = summary_path.parent / "artifact_status.json"
    if "artifact_status" not in summary and artifact_status_path.exists():
        try:
            summary["artifact_status"] = json.loads(artifact_status_path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            pass
    validation = {}
    benchmark = benchmark or {}
    effective_provenance = provenance if provenance is not None else summary.get("input_provenance")
    if not isinstance(effective_provenance, dict):
        effective_provenance = None
    benchmark_traceability = summary.get("benchmark_traceability") or (assess_benchmark_traceability(config, benchmark) if benchmark else {})
    if validation_path and validation_path.exists():
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            validation = {"checks": [], "passed": False}
    model_representation = summary.get("model_representation", config.get("model_representation", {}))
    model_validity = summary.get("model_validity", {})
    validation_maturity = summary.get("validation_maturity") or benchmark_traceability.get("validation_maturity", {})
    benchmark_quality = summary.get("benchmark_quality") or benchmark_traceability.get("benchmark_quality", {})
    validation_summary = _materialize_validation_summary_artifacts(summary_path.parent, validation, benchmark_quality)
    limitations_matrix = _ensure_limitations_matrix(summary, validation, benchmark_quality, validation_maturity)
    design_readiness = _classify_design_readiness(summary, validation, benchmark_quality)
    result_claims = _materialize_result_claims(summary_path.parent, config, summary, validation_summary, limitations_matrix, design_readiness)
    summary["limitations_matrix"] = limitations_matrix
    summary["design_readiness"] = design_readiness
    summary["result_claims"] = result_claims
    try:
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass

    lines = [
        f"# {config['reactor']['name']}",
        "",
        f"- Case: `{case_name}`",
        f"- Family: `{config['reactor']['family']}`",
        f"- Stage: `{config['reactor']['stage']}`",
        f"- Mode: `{config['reactor'].get('mode', 'modern_test_reactor')}`",
        f"- Result bundle: `{summary.get('result_dir', '')}`",
        f"- Neutronics status: `{summary.get('neutronics', {}).get('status', 'unknown')}`",
        "",
    ]
    lines.extend(_build_evidence_status_lines(config, summary, benchmark_traceability, benchmark_quality))
    lines.extend(
        _build_artifact_index_lines(
            summary_path=summary_path,
            validation_path=validation_path,
            geometry_assets=geometry_assets,
            benchmark=benchmark,
            plot_assets=plot_assets,
            provenance=effective_provenance,
            summary=summary,
        )
    )
    lines.extend(_build_stage_manifest_lines(summary_path.parent / "stage_manifest.json"))
    lines.extend(["## Reactor Summary", ""])
    _append_value_line(lines, "Design thermal power", config["reactor"].get("design_power_mwth"), "MWth")
    _append_value_line(lines, "Benchmark source", config["reactor"].get("benchmark"))
    lines.append("")

    classification = _classify_reactor_case(case_name, config, summary=summary, benchmark_quality=benchmark_quality)
    lines.extend(
        [
            "## Reactor Classification",
            "",
            f"- Taxonomy role: `{classification['role']}`",
            f"- Build candidate: `{classification['build_candidate']}`",
            f"- Commercial finance subject: `{classification['commercial_finance_subject']}`",
            f"- Description: {classification['description']}",
            "",
        ]
    )
    characteristics = config.get("reactor", {}).get("characteristics", {})
    if isinstance(characteristics, dict) and characteristics:
        lines.append("## Flagship Characteristics")
        lines.append("")
        for key in (
            "reactor_class",
            "licensing_basis",
            "grid_role",
            "module_count",
            "net_electric_power_mwe",
            "thermal_power_mwth",
            "fuel_coolant_family",
            "fuel_cycle",
            "cleanup_strategy",
        ):
            if key in characteristics:
                _append_value_line(lines, _humanize_label(key), characteristics[key])
        if characteristics.get("end_goal"):
            lines.append(f"- End goal: {characteristics['end_goal']}")
        lines.append("")

    if model_validity:
        lines.extend(
            [
                f"> Model validity: `{model_validity.get('status', 'unknown')}`",
                f"> Failed checks: `{model_validity.get('failed_count', 0)}`",
                "",
            ]
        )

    if effective_provenance:
        case_provenance = effective_provenance.get("case", {})
        benchmark_provenance = effective_provenance.get("benchmark", {})
        if not isinstance(case_provenance, dict):
            case_provenance = {}
        if not isinstance(benchmark_provenance, dict):
            benchmark_provenance = {}
        git = effective_provenance.get("git", {})
        lines.extend(
            [
                "## Input Provenance",
                "",
                f"- Case definition: `{case_provenance.get('source', 'unknown')}`",
                f"- Case origin path: `{case_provenance.get('origin_path', 'n/a')}`",
                f"- Benchmark metadata: `{benchmark_provenance.get('source', 'unknown')}`",
                f"- Benchmark origin path: `{benchmark_provenance.get('origin_path', 'n/a')}`",
            ]
        )
        if isinstance(git, dict) and git.get("dirty") is True:
            dirty_count = len(git.get("modified", []) or []) + len(git.get("untracked", []) or [])
            lines.append(
                f"- Reproducibility warning: git worktree was dirty when this bundle was created "
                f"(`{dirty_count}` changed/untracked path(s), diff hash `{git.get('diff_hash', 'n/a')}`)."
            )
        if effective_provenance.get("dependency_hash"):
            lines.append(f"- Runtime/dependency hash: `{effective_provenance.get('dependency_hash')}`")
        if effective_provenance.get("generator"):
            lines.append(
                f"- Provenance generator: `{effective_provenance.get('generator')} "
                f"v{effective_provenance.get('generator_version', 'n/a')}`"
            )
        lines.append("")

    runtime_context = summary.get("runtime_context", {})
    if runtime_context:
        lines.extend(
            [
                "## Runtime Context",
                "",
            ]
        )
        _append_value_line(lines, "Service", runtime_context.get("service", "host"))
        _append_value_line(lines, "Image", runtime_context.get("image"))
        _append_value_line(lines, "Tool runtime", runtime_context.get("tool_runtime"))
        _append_value_line(lines, "Git branch", runtime_context.get("git_branch"))
        _append_value_line(lines, "Git commit", runtime_context.get("git_commit"))
        lines.append("")

    if model_representation:
        lines.extend(
            [
                "## Model Representation",
                "",
                f"- Materials mode: `{model_representation.get('materials', 'n/a')}`",
                f"- Fuel-cycle mode: `{model_representation.get('fuel_cycle', 'n/a')}`",
                "",
            ]
        )

    if benchmark:
        lines.extend(
            [
                "## Benchmark Context",
                "",
                f"- Benchmark title: `{benchmark.get('title', 'n/a')}`",
            ]
        )
        for reference in benchmark.get("references", []):
            lines.append(f"- Reference note: {reference}")
        for assumption in benchmark_traceability.get("assumptions", []):
            lines.append(f"- Assumption `{assumption['id']}`: {assumption.get('text', 'n/a')}")
            if assumption.get("basis"):
                lines.append(f"- Basis: `{assumption['basis']}`")
            if assumption.get("confidence"):
                lines.append(f"- Confidence: `{assumption['confidence']}`")
            if assumption.get("evidence_refs"):
                lines.append(f"- Evidence refs: `{', '.join(assumption['evidence_refs'])}`")
        lines.append("")

    if benchmark_traceability:
        coverage = benchmark_traceability["coverage"]
        confidence = benchmark_traceability["confidence_summary"]
        status_summary = benchmark_traceability["status_summary"]
        lines.extend(
            [
                "## Benchmark Traceability",
                "",
                f"- Traceability score: `{benchmark_traceability['traceability_score']}`",
                f"- Maturity stage: `{benchmark_traceability['maturity_stage']}`",
                f"- Evidence records complete: `{coverage['evidence_records_complete']['linked']}/{coverage['evidence_records_complete']['total']}`",
                f"- Assumptions with evidence links: `{coverage['assumptions_with_evidence']['linked']}/{coverage['assumptions_with_evidence']['total']}`",
                f"- Targets with evidence links: `{coverage['targets_with_evidence']['linked']}/{coverage['targets_with_evidence']['total']}`",
                f"- Reactor parameters linked to benchmark targets: `{coverage['reactor_parameters_linked']['linked']}/{coverage['reactor_parameters_linked']['total']}`",
                f"- Physics validation targets linked: `{coverage['physics_validation_targets_linked']['linked']}/{coverage['physics_validation_targets_linked']['total']}`",
                f"- Confidence coverage: `high={confidence['high']}, medium={confidence['medium']}, low={confidence['low']}, unspecified={confidence['unspecified']}`",
                f"- Surrogate targets remaining: `{status_summary['surrogate_targets']}`",
                f"- Literature-backed targets: `{status_summary['literature_backed_targets']}`",
            ]
        )
        scale_alignment = benchmark_traceability.get("scale_alignment", {})
        if scale_alignment:
            lines.append(f"- Scale alignment: `{scale_alignment.get('status', 'not_assessed')}`")
            if scale_alignment.get("message"):
                lines.append(f"- Scale note: {scale_alignment['message']}")
        datasets = benchmark_traceability.get("datasets", [])
        if datasets:
            lines.append(f"- Dataset count: `{len(datasets)}`")
            for dataset in datasets:
                lines.append(
                    f"- Dataset `{dataset.get('id', 'dataset')}`: "
                    f"status=`{dataset.get('status', 'planned')}`, "
                    f"phenomenon=`{dataset.get('phenomenon', 'n/a')}`, "
                    f"observables=`{dataset.get('observable_count', 0)}`"
                )
        for gap in benchmark_traceability.get("gaps", []):
            lines.append(f"- Traceability gap: {gap}")
        if validation_maturity:
            lines.append(f"- Validation maturity score: `{validation_maturity.get('validation_maturity_score', 'n/a')}`")
            lines.append(f"- Validation maturity stage: `{validation_maturity.get('validation_maturity_stage', 'n/a')}`")
            lines.append(
                f"- Operating-point source: `{validation_maturity.get('operating_point_source', {}).get('status', 'n/a')}`"
            )
            lines.append(
                f"- Uncertainty coverage: `{validation_maturity.get('uncertainty_coverage', {}).get('status', 'n/a')}`"
            )
            cross_code_checks = validation_maturity.get("cross_code_checks", [])
            lines.append(f"- Cross-code checks declared: `{len(cross_code_checks)}`")
            for gap in validation_maturity.get("gaps", []):
                lines.append(f"- Validation gap: {gap}")
        if benchmark_quality:
            lines.extend(["", "## Benchmark Quality Gates", ""])
            lines.append(f"- Quality score: `{benchmark_quality.get('quality_score', 'n/a')}`")
            lines.append(f"- Quality stage: `{benchmark_quality.get('quality_stage', 'n/a')}`")
            lines.append(f"- Benchmark ready: `{benchmark_quality.get('benchmark_ready', 'n/a')}`")
            lines.append(f"- Failed gates: `{benchmark_quality.get('failed_gate_count', 'n/a')}`")
            for gate in benchmark_quality.get("gates", []):
                lines.append(
                    f"- Gate `{gate.get('id', 'gate')}`: "
                    f"`{gate.get('status', 'pending')}`"
                    + (f" ({gate.get('message')})" if gate.get("message") else "")
                )

    lines.extend(_build_key_metrics_lines(config, summary, benchmark_traceability, validation_maturity))
    lines.extend(_build_additional_metrics_lines(summary))
    lines.extend(_build_results_generated_lines(summary_path.parent, summary))
    lines.extend(_build_benchmark_evidence_lines(summary_path.parent, summary))
    lines.extend(_build_design_readiness_lines(design_readiness))
    lines.extend(_build_validation_and_blocker_lines(validation_summary, benchmark_quality))
    lines.extend(_build_interpretation_lines(summary, validation_summary, design_readiness, benchmark_quality))
    lines.extend(_build_limitations_matrix_lines(limitations_matrix))
    lines.extend(_build_result_claims_lines(result_claims))
    lines.extend(_build_method_card_lines(summary))

    neutronics = summary.get("neutronics", {})
    simulation = neutronics.get("simulation", {})
    if simulation:
        lines.extend(["", "## Neutronics Inputs", ""])
        lines.append(f"- OpenMC availability: `{neutronics.get('openmc_available', 'n/a')}`")
        lines.append(f"- Run mode: `{simulation.get('mode', 'n/a')}`")
        lines.append(f"- Particles per generation: `{simulation.get('particles', 'n/a')}`")
        lines.append(f"- Total batches: `{simulation.get('batches', 'n/a')}`")
        lines.append(f"- Inactive batches: `{simulation.get('inactive', 'n/a')}`")
        lines.append(f"- Active batches: `{simulation.get('active_batches', 'n/a')}`")
        lines.append(f"- Radial boundary: `{simulation.get('geometry_boundary', 'n/a')}`")
        if simulation.get("axial_boundary") is not None:
            lines.append(f"- Axial boundary: `{simulation.get('axial_boundary', 'n/a')}`")
        source = simulation.get("source", {})
        lines.append(f"- Source type: `{source.get('type', 'n/a')}`")
        if source.get("parameters") is not None:
            lines.append(f"- Source parameters: `{source.get('parameters')}`")
        tallies = simulation.get("tallies", [])
        lines.append(f"- Tally count: `{len(tallies)}`")
        for tally in tallies:
            lines.append(
                f"- Tally `{tally.get('name', 'unnamed')}`: "
                f"cell=`{tally.get('cell', 'n/a')}`, "
                f"scores=`{', '.join(tally.get('scores', [])) or 'n/a'}`, "
                f"nuclides=`{', '.join(tally.get('nuclides', [])) or 'all'}`"
            )

    finance = summary.get("finance", {})
    if finance:
        lines.extend(["", "## Commercial Finance", ""])
        lines.append(f"- Status: `{finance.get('status', 'n/a')}`")
        if classification.get("commercial_finance_subject") == "planning_only":
            lines.append(
                "- Evidence caveat: Planning-only finance; core physics remains dry-run/proxy or benchmark gates are not ready."
            )
        if finance.get("status") == "completed":
            lines.append(f"- Scenario: `{finance.get('scenario', 'n/a')}`")
            lines.append(f"- Planning basis: `{finance.get('planning_basis', 'n/a')}`")
            lines.append(f"- Currency basis: `{finance.get('source_year_usd', 'n/a')} USD`")
            inputs = finance.get("inputs", {})
            outputs = finance.get("outputs", {})
            costs = finance.get("cost_breakdown_usd", {})
            annual_costs = finance.get("annual_costs_usd_per_year", {})
            lines.append(f"- Net capacity (MWe): `{inputs.get('net_capacity_mwe', 'n/a')}`")
            lines.append(f"- Capacity factor: `{inputs.get('capacity_factor', 'n/a')}`")
            lines.append(f"- Source OCC ($/kWe): `{inputs.get('source_occ_usd_per_kwe', 'n/a')}`")
            lines.append(f"- FOAK overnight uplift: `{inputs.get('overnight_cost_uplift', 'n/a')}`")
            lines.append(f"- Real WACC: `{inputs.get('real_wacc', 'n/a')}`")
            lines.append(f"- Net overnight cost (USD): `{costs.get('net_overnight_cost', 'n/a')}`")
            lines.append(f"- Interest during construction (USD): `{costs.get('interest_during_construction', 'n/a')}`")
            lines.append(f"- Total capitalized cost (USD): `{costs.get('total_capitalized_cost', 'n/a')}`")
            lines.append(f"- Annual generation (MWh): `{outputs.get('annual_generation_mwh', 'n/a')}`")
            lines.append(f"- Annualized capital (USD/yr): `{annual_costs.get('annualized_capital', 'n/a')}`")
            lines.append(f"- Annual total cost (USD/yr): `{annual_costs.get('total', 'n/a')}`")
            lines.append(f"- LCOE ($/MWh): `{outputs.get('lcoe_usd_per_mwh', 'n/a')}`")
            lines.append(f"- LCOE (cents/kWh): `{outputs.get('lcoe_cents_per_kwh', 'n/a')}`")
            lines.append("- Caveat: Planning-grade estimate, not a vendor quote, EPC bid, or investment recommendation.")
        elif finance.get("reason"):
            lines.append(f"- Reason: {finance.get('reason')}")

    schedule = summary.get("schedule", {})
    if schedule:
        lines.extend(["", "## Build Schedule", ""])
        lines.append(f"- Status: `{schedule.get('status', 'n/a')}`")
        if classification.get("build_candidate") == "blocked_by_evidence":
            lines.append(
                "- Evidence caveat: Schedule is a planning scenario, not a build-ready commitment while solver-backed physics or benchmark gates are missing."
            )
        if schedule.get("status") == "completed":
            lines.append(f"- Planning basis: `{schedule.get('planning_basis', 'n/a')}`")
            lines.append(f"- Project start: `{schedule.get('project_start_date', 'n/a')}`")
            lines.append(f"- Construction start: `{schedule.get('construction_start_date', 'n/a')}`")
            lines.append(f"- Commercial operation date: `{schedule.get('commercial_operation_date', 'n/a')}`")
            lines.append(
                f"- Total duration: `{schedule.get('total_months_to_commercial_operation', 'n/a')}` months "
                f"(`{schedule.get('total_years_to_commercial_operation', 'n/a')}` years)"
            )
            for phase in schedule.get("phases", []):
                lines.append(
                    f"- `{phase.get('id', 'phase')}`: "
                    f"{phase.get('start_date', 'n/a')} to {phase.get('end_date', 'n/a')} "
                    f"({phase.get('duration_months', 'n/a')} months)"
                )
        elif schedule.get("reason"):
            lines.append(f"- Reason: {schedule.get('reason')}")

    if "bop" in summary:
        lines.extend(["", "## Balance Of Plant", ""])
        for key, value in summary["bop"].items():
            lines.append(f"- {key}: `{value}`")

    property_uncertainty = summary.get("property_uncertainty", {})
    if property_uncertainty:
        lines.extend(["", "## Property Uncertainty", ""])
        lines.append(f"- Model: `{property_uncertainty.get('model', 'n/a')}`")
        lines.append(
            "- Density uncertainty 95% fraction: "
            f"`{property_uncertainty.get('density_uncertainty_95_fraction', 'n/a')}`"
        )
        lines.append(
            "- Heat capacity uncertainty 95% fraction: "
            f"`{property_uncertainty.get('cp_uncertainty_95_fraction', 'n/a')}`"
        )
        lines.append(
            "- Conductivity uncertainty 95% fraction: "
            f"`{property_uncertainty.get('thermal_conductivity_uncertainty_95_fraction', 'n/a')}`"
        )
        lines.append(
            "- Viscosity uncertainty 95% fraction: "
            f"`{property_uncertainty.get('dynamic_viscosity_uncertainty_95_fraction', 'n/a')}`"
        )
        lines.append(
            "- Core outlet temperature uncertainty 95% (C): "
            f"`{property_uncertainty.get('core_outlet_temperature_uncertainty_95_c', 'n/a')}`"
        )
        lines.append(f"- Source backing: `{property_uncertainty.get('property_source_backing', 'n/a')}`")
        basis_by_property = property_uncertainty.get("basis_by_property", {})
        if isinstance(basis_by_property, dict) and basis_by_property:
            basis_parts = [f"{key}={value}" for key, value in sorted(basis_by_property.items())]
            lines.append(f"- Basis by property: `{', '.join(basis_parts)}`")
        source_applicability = property_uncertainty.get("property_source_applicability", {})
        if isinstance(source_applicability, dict):
            for key, source in sorted(source_applicability.items()):
                if not isinstance(source, dict):
                    continue
                source_parts = [
                    f"provider={source.get('provider') or 'unavailable'}",
                    f"backing={source.get('source_kind') or 'unavailable'}",
                    f"range={source.get('range_status') or 'unavailable'}",
                ]
                if source.get("formula"):
                    source_parts.append(f"formula={source.get('formula')}")
                lines.append(
                    f"- {key} source: `{', '.join(source_parts)}`"
                )

    property_audit = summary.get("property_audit", {})
    if property_audit:
        lines.extend(["", "## Property Provenance", ""])
        lines.append(f"- Provider declaration: `{property_audit.get('provider', 'n/a')}`")
        lines.append(f"- Source backing: `{property_audit.get('source_backing', 'n/a')}`")
        counts = property_audit.get("source_backing_counts", {})
        if isinstance(counts, dict):
            lines.append(f"- Backing counts: `{', '.join(f'{key}={value}' for key, value in sorted(counts.items()))}`")
        for record in property_audit.get("records", []):
            if not isinstance(record, dict):
                continue
            lines.append(
                f"- `{record.get('path', 'property')}`: provider `{record.get('provider', 'n/a')}`, "
                f"backing `{record.get('source_backing', record.get('model', 'configured'))}`, "
                f"validity `{record.get('validity', 'n/a')}`"
            )

    reduced_order_flow = summary.get("flow", {}).get("reduced_order", {})
    if reduced_order_flow:
        active_flow = reduced_order_flow.get("active_flow", {})
        disconnected_inventory = reduced_order_flow.get("disconnected_inventory", {})
        lines.extend(["", "## Reduced-Order Flow", ""])
        lines.append(f"- Allocation rule: `{reduced_order_flow.get('allocation_rule', 'n/a')}`")
        lines.append(f"- Salt bulk temperature (C): `{reduced_order_flow.get('salt_bulk_temperature_c', 'n/a')}`")
        lines.append(f"- Salt density (kg/m3): `{reduced_order_flow.get('salt_density_kg_m3', 'n/a')}`")
        lines.append(f"- Salt property source backing: `{reduced_order_flow.get('salt_source_backing', 'n/a')}`")
        lines.append(f"- Active through-flow channels: `{active_flow.get('channel_count', 'n/a')}`")
        lines.append(f"- Active flow area (cm2): `{active_flow.get('total_flow_area_cm2', 'n/a')}`")
        lines.append(f"- Representative velocity (m/s): `{active_flow.get('representative_velocity_m_s', 'n/a')}`")
        lines.append(f"- Representative residence time (s): `{active_flow.get('representative_residence_time_s', 'n/a')}`")
        lines.append(f"- Disconnected salt inventory channels: `{disconnected_inventory.get('channel_count', 'n/a')}`")

    msre_pump_transient = summary.get("msre_pump_transient_benchmark", {})
    if msre_pump_transient:
        startup_error = msre_pump_transient.get("benchmark_mean_error_startup_pcm", {})
        coastdown_error = msre_pump_transient.get("benchmark_mean_error_coastdown_pcm", {})
        lines.extend(["", "## MSRE Pump Transient Validation", ""])
        lines.append(f"- Model: `{msre_pump_transient.get('model', 'n/a')}`")
        lines.append(f"- Screening status: `{msre_pump_transient.get('screening_status', 'n/a')}`")
        lines.append(f"- Source: `{msre_pump_transient.get('source', 'n/a')}`")
        lines.append(
            "- Benchmark startup mean error range (pcm): "
            f"`{startup_error.get('min', 'n/a')} to {startup_error.get('max', 'n/a')}`"
        )
        lines.append(
            "- Benchmark coastdown mean error range (pcm): "
            f"`{coastdown_error.get('min', 'n/a')} to {coastdown_error.get('max', 'n/a')}`"
        )
        lines.append(
            "- Non-active salt inventory fraction: "
            f"`{msre_pump_transient.get('non_active_salt_inventory_fraction', 'n/a')}`"
        )
        lines.append(
            "- Stagnant salt inventory fraction: "
            f"`{msre_pump_transient.get('stagnant_salt_inventory_fraction', 'n/a')}`"
        )
        lines.append(f"- Interpretation: {msre_pump_transient.get('interpretation', 'n/a')}")

    physics_core = summary.get("physics_core", {})
    if physics_core:
        precursor_transport = physics_core.get("precursor_transport", {})
        decay_heat = precursor_transport.get("decay_heat_precursors", {}) if isinstance(precursor_transport, dict) else {}
        if precursor_transport or decay_heat:
            lines.extend(["", "## Physics Core Transport", ""])
            lines.append(f"- Precursor model: `{precursor_transport.get('model', 'n/a')}`")
            lines.append(f"- Loop residence time (s): `{precursor_transport.get('loop_residence_time_s', 'n/a')}`")
            lines.append(f"- Loop residence basis: `{precursor_transport.get('loop_residence_basis', 'n/a')}`")
            lines.append(
                "- Delayed-neutron transport loss fraction: "
                f"`{precursor_transport.get('transport_loss_fraction', 'n/a')}`"
            )
            if decay_heat:
                dominant_loop_segment = decay_heat.get("dominant_loop_segment", {})
                lines.append(f"- Decay-heat precursor model: `{decay_heat.get('model', 'n/a')}`")
                lines.append(
                    "- Core decay-heat source fraction: "
                    f"`{decay_heat.get('core_decay_heat_source_fraction', 'n/a')}`"
                )
                lines.append(
                    "- External-loop decay-heat source fraction: "
                    f"`{decay_heat.get('loop_decay_heat_source_fraction', 'n/a')}`"
                )
                if dominant_loop_segment:
                    lines.append(
                        "- Dominant loop decay-heat segment: "
                        f"`{dominant_loop_segment.get('segment_id', 'n/a')}` "
                        f"(`{dominant_loop_segment.get('decay_heat_source_fraction', 'n/a')}`)"
                    )
                if decay_heat.get("source"):
                    lines.append(f"- Decay-heat transport source: `{decay_heat.get('source')}`")

    transport_solver = summary.get("transport_solver", {})
    if transport_solver:
        mesh = transport_solver.get("mesh", {})
        source_fractions = transport_solver.get("source_fractions", {})
        lines.extend(["", "## Native RKDG Transport", ""])
        lines.append(f"- Model: `{transport_solver.get('model', 'n/a')}`")
        lines.append(
            "- Mesh/order: "
            f"`{mesh.get('type', 'n/a')}` "
            f"({mesh.get('radial_cells', 'n/a')} x {mesh.get('axial_cells', 'n/a')}), "
            f"p=`{transport_solver.get('polynomial_order', 'n/a')}`"
        )
        lines.append(f"- Time integration: `{transport_solver.get('time_integration', 'n/a')}`")
        lines.append(f"- Time step/CFL: `{transport_solver.get('time_step_s', 'n/a')}` s / `{transport_solver.get('cfl', 'n/a')}`")
        lines.append(f"- Conservation residual: `{transport_solver.get('conservation_residual', 'n/a')}`")
        lines.append(f"- Minimum field value: `{transport_solver.get('minimum_field_value', 'n/a')}`")
        fraction_items = source_fractions.items() if isinstance(source_fractions, dict) else []
        for group_name, fractions in fraction_items:
            if isinstance(fractions, dict):
                lines.append(
                    f"- {group_name} outlet source fraction: "
                    f"`{fractions.get('outlet_source_fraction', 'n/a')}`"
                )
        artifacts = transport_solver.get("artifacts", {})
        if artifacts:
            lines.append(f"- Solution artifact: `{artifacts.get('solution_path', 'n/a')}`")

    depletion_matrix = summary.get("depletion_matrix", {})
    if depletion_matrix:
        lines.extend(["", "## Native Sparse Depletion", ""])
        lines.append(f"- Model: `{depletion_matrix.get('model', 'n/a')}`")
        lines.append(f"- Backend: `{depletion_matrix.get('backend', 'n/a')}`")
        lines.append(f"- Chain: `{depletion_matrix.get('chain_name', 'n/a')}` from `{depletion_matrix.get('chain_format', 'n/a')}`")
        lines.append(f"- Isotopes/zones: `{depletion_matrix.get('isotope_count', 'n/a')}` / `{depletion_matrix.get('zone_count', 'n/a')}`")
        lines.append(f"- Matrix shape/nonzeros: `{depletion_matrix.get('matrix_shape', 'n/a')}` / `{depletion_matrix.get('matrix_nonzero_entries', 'n/a')}`")
        lines.append(f"- Step count/time step days: `{depletion_matrix.get('steps', 'n/a')}` / `{depletion_matrix.get('time_step_days', 'n/a')}`")
        lines.append(f"- Inventory delta fraction: `{depletion_matrix.get('inventory_delta_fraction', 'n/a')}`")
        lines.append(f"- Feed total atoms: `{depletion_matrix.get('feed_total_atoms', 'n/a')}`")
        lines.append(f"- Atom-balance residual: `{depletion_matrix.get('atom_balance_residual', 'n/a')}`")
        artifacts = depletion_matrix.get("artifacts", {})
        if artifacts:
            lines.append(f"- Matrix/history artifacts: `{artifacts.get('matrix_path', 'n/a')}`, `{artifacts.get('history_path', 'n/a')}`")

    primary_system = summary.get("primary_system", {})
    if primary_system:
        loop_hydraulics = primary_system.get("loop_hydraulics", {})
        heat_exchanger = primary_system.get("heat_exchanger", {})
        thermal_profile = primary_system.get("thermal_profile", {})
        inventory = primary_system.get("inventory", {})
        fuel_salt = inventory.get("fuel_salt", {})
        coolant_salt = inventory.get("coolant_salt", {})
        lines.extend(["", "## Primary System", ""])
        lines.append(f"- Bulk salt temperature (C): `{primary_system.get('bulk_temperature_c', 'n/a')}`")
        lines.append(f"- Salt density (kg/m3): `{primary_system.get('salt_density_kg_m3', 'n/a')}`")
        lines.append(f"- Hot-leg density (kg/m3): `{primary_system.get('hot_leg_density_kg_m3', 'n/a')}`")
        lines.append(f"- Cold-leg density (kg/m3): `{primary_system.get('cold_leg_density_kg_m3', 'n/a')}`")
        lines.append(f"- Salt dynamic viscosity (Pa-s): `{primary_system.get('dynamic_viscosity_pa_s', 'n/a')}`")
        lines.append(f"- Required pump pressure (kPa): `{loop_hydraulics.get('required_pump_pressure_kpa', loop_hydraulics.get('total_pressure_drop_kpa', 'n/a'))}`")
        lines.append(f"- Net resistive pressure (kPa): `{loop_hydraulics.get('net_resistive_pressure_kpa', 'n/a')}`")
        lines.append(f"- Loop pressure drop (kPa): `{loop_hydraulics.get('total_pressure_drop_kpa', 'n/a')}`")
        lines.append(f"- Hydrostatic pressure change (kPa): `{loop_hydraulics.get('hydrostatic_pressure_change_kpa', 'n/a')}`")
        lines.append(f"- Buoyancy driving pressure (kPa): `{loop_hydraulics.get('buoyancy_driving_pressure_kpa', 'n/a')}`")
        lines.append(f"- Thermal expansion head (m): `{loop_hydraulics.get('thermal_expansion_head_m', 'n/a')}`")
        lines.append(f"- Pump head (m): `{loop_hydraulics.get('pump_head_m', 'n/a')}`")
        lines.append(f"- Pump shaft power (kW): `{loop_hydraulics.get('pump_shaft_power_kw', 'n/a')}`")
        lines.append(f"- Max Reynolds number: `{loop_hydraulics.get('max_reynolds_number', 'n/a')}`")
        lines.append(f"- Heat exchanger duty (MW): `{heat_exchanger.get('duty_mw', 'n/a')}`")
        lines.append(f"- Heat exchanger area (m2): `{heat_exchanger.get('required_area_m2', 'n/a')}`")
        lines.append(f"- Heat exchanger LMTD (C): `{heat_exchanger.get('lmtd_c', 'n/a')}`")
        lines.append(f"- Heat exchanger configured U (W/m2-K): `{heat_exchanger.get('overall_u_w_m2k', 'n/a')}`")
        lines.append(f"- Heat exchanger estimated clean U (W/m2-K): `{heat_exchanger.get('estimated_clean_u_w_m2k', 'n/a')}`")
        lines.append(f"- Estimated hot leg temperature (C): `{thermal_profile.get('estimated_hot_leg_temp_c', 'n/a')}`")
        lines.append(f"- Estimated cold leg temperature (C): `{thermal_profile.get('estimated_cold_leg_temp_c', 'n/a')}`")
        lines.append(f"- Estimated pipe heat loss (kW): `{thermal_profile.get('total_pipe_heat_loss_kw', 'n/a')}`")
        lines.append(f"- Fuel salt inventory (m3): `{fuel_salt.get('total_m3', 'n/a')}`")
        lines.append(f"- Coolant salt inventory (m3): `{coolant_salt.get('net_pool_inventory_m3', 'n/a')}`")

    chemistry = summary.get("chemistry", {})
    if chemistry:
        lines.extend(["", "## Salt Chemistry", ""])
        lines.append(f"- Model: `{chemistry.get('model', 'n/a')}`")
        lines.append(f"- Redox state (eV): `{chemistry.get('redox_state_ev', 'n/a')}`")
        lines.append(f"- Target redox state (eV): `{chemistry.get('target_redox_state_ev', 'n/a')}`")
        lines.append(f"- Redox deviation (eV): `{chemistry.get('redox_deviation_ev', 'n/a')}`")
        lines.append(f"- Impurity fraction: `{chemistry.get('impurity_fraction', 'n/a')}`")
        lines.append(f"- Corrosion index: `{chemistry.get('corrosion_index', 'n/a')}`")
        lines.append(f"- Corrosion risk: `{chemistry.get('corrosion_risk', 'n/a')}`")
        lines.append(f"- Gas stripping efficiency: `{chemistry.get('gas_stripping_efficiency', 'n/a')}`")
        lines.append(f"- Tritium release fraction: `{chemistry.get('tritium_release_fraction', 'n/a')}`")

    tritium = summary.get("tritium", {})
    if tritium:
        lines.extend(["", "## Tritium Transport", ""])
        lines.append(f"- Model: `{tritium.get('model', 'n/a')}`")
        lines.append(f"- Relative production rate: `{tritium.get('relative_production_rate', 'n/a')}`")
        lines.append(f"- Environmental release fraction: `{tritium.get('environmental_release_fraction', 'n/a')}`")
        lines.append(f"- Removal fraction: `{tritium.get('removal_fraction', 'n/a')}`")
        lines.append(f"- Graphite retention fraction: `{tritium.get('graphite_retention_fraction', 'n/a')}`")
        lines.append(f"- Circulating inventory fraction: `{tritium.get('circulating_inventory_fraction', 'n/a')}`")
        lines.append(f"- Control effect: `{tritium.get('control_effect', 'n/a')}`")

    volatile_species = summary.get("volatile_species", {})
    if volatile_species:
        lines.extend(["", "## Volatile Species Transport", ""])
        lines.append(f"- Model: `{volatile_species.get('model', 'n/a')}`")
        lines.append(f"- Loop residence time (s): `{volatile_species.get('loop_residence_time_s', 'n/a')}`")
        lines.append(f"- Contact factor: `{volatile_species.get('contact_factor', 'n/a')}`")
        lines.append(f"- Bubble contact efficiency: `{volatile_species.get('bubble_contact_efficiency', 'n/a')}`")
        lines.append(f"- Cleanup polish fraction: `{volatile_species.get('cleanup_polish_fraction', 'n/a')}`")
        lines.append(f"- Effective removal fraction: `{volatile_species.get('effective_removal_fraction', 'n/a')}`")
        lines.append(
            "- Xe-135 equilibrium inventory multiplier: "
            f"`{volatile_species.get('equilibrium_xenon_inventory_multiplier', 'n/a')}`"
        )
        lines.append(f"- Screening status: `{volatile_species.get('screening_status', 'n/a')}`")

    graphite_lifetime = summary.get("graphite_lifetime", {})
    if graphite_lifetime:
        lines.extend(["", "## Graphite Lifetime", ""])
        lines.append(f"- Model: `{graphite_lifetime.get('model', 'n/a')}`")
        lines.append(f"- Fuel volume fraction: `{graphite_lifetime.get('fuel_volume_fraction', 'n/a')}`")
        lines.append(f"- Fast-flux peaking factor: `{graphite_lifetime.get('fast_flux_peaking_factor', 'n/a')}`")
        lines.append(
            "- Nominal maximum fast flux (n/cm2-s): "
            f"`{graphite_lifetime.get('nominal_max_fast_flux_n_cm2_s', 'n/a')}`"
        )
        lines.append(f"- Estimated lifespan (years): `{graphite_lifetime.get('estimated_lifespan_years', 'n/a')}`")
        lines.append(f"- Lifetime margin: `{graphite_lifetime.get('lifetime_margin', 'n/a')}`")
        lines.append(f"- Screening status: `{graphite_lifetime.get('screening_status', 'n/a')}`")

    fuel_cycle = summary.get("fuel_cycle", {})
    if fuel_cycle:
        lines.extend(["", "## Fuel Cycle Assumptions", ""])
        lines.append(f"- Depletion chain: `{fuel_cycle.get('depletion_chain', 'n/a')}`")
        lines.append(f"- Cleanup scenario: `{fuel_cycle.get('cleanup_scenario', 'n/a')}`")
        lines.append(f"- Heavy metal inventory (kg): `{fuel_cycle.get('heavy_metal_inventory_kg', 'n/a')}`")
        lines.append(f"- Fissile inventory (kg): `{fuel_cycle.get('fissile_inventory_kg', 'n/a')}`")
        lines.append(f"- Specific power (MW/tHM): `{fuel_cycle.get('specific_power_mw_per_t_hm', 'n/a')}`")
        lines.append(f"- Cleanup turnover (days): `{fuel_cycle.get('cleanup_turnover_days', 'n/a')}`")
        lines.append(f"- Cleanup removal efficiency: `{fuel_cycle.get('cleanup_removal_efficiency', 'n/a')}`")
        lines.append(f"- Xenon generation rate (atoms/s): `{fuel_cycle.get('xenon_generation_rate_atoms_s', 'n/a')}`")
        lines.append(f"- Xenon removal fraction: `{fuel_cycle.get('xenon_removal_fraction', 'n/a')}`")
        lines.append(f"- Protactinium holdup (days): `{fuel_cycle.get('protactinium_holdup_days', 'n/a')}`")
        lines.append(f"- Fissile burn fraction per day: `{fuel_cycle.get('fissile_burn_fraction_per_day_full_power', 'n/a')}`")
        lines.append(f"- Breeding gain fraction per day: `{fuel_cycle.get('breeding_gain_fraction_per_day', 'n/a')}`")
        lines.append(f"- Net fissile change fraction per day: `{fuel_cycle.get('net_fissile_change_fraction_per_day', 'n/a')}`")
        lines.append(f"- Equilibrium protactinium inventory fraction: `{fuel_cycle.get('equilibrium_protactinium_inventory_fraction', 'n/a')}`")
        depletion_assumptions = fuel_cycle.get("depletion_assumptions", {})
        if depletion_assumptions:
            lines.append(f"- Volatile removal efficiency: `{depletion_assumptions.get('volatile_removal_efficiency', 'n/a')}`")

    transient = summary.get("transient", {})
    if transient:
        lines.extend(["", "## Transient Scenario", ""])
        lines.append(f"- Model: `{transient.get('model', 'n/a')}`")
        lines.append(f"- Status: `{transient.get('status', 'n/a')}`")
        lines.append(f"- Scenario: `{transient.get('scenario_name', 'n/a')}`")
        lines.append(f"- Duration (s): `{transient.get('duration_s', 'n/a')}`")
        lines.append(f"- Time step (s): `{transient.get('time_step_s', 'n/a')}`")
        lines.append(f"- Event count: `{transient.get('event_count', 'n/a')}`")
        lines.append(f"- Peak power fraction: `{transient.get('peak_power_fraction', 'n/a')}`")
        lines.append(f"- Final power fraction: `{transient.get('final_power_fraction', 'n/a')}`")
        lines.append(f"- Peak fuel temperature (C): `{transient.get('peak_fuel_temperature_c', 'n/a')}`")
        lines.append(f"- Peak graphite temperature (C): `{transient.get('peak_graphite_temperature_c', 'n/a')}`")
        lines.append(f"- Peak coolant temperature (C): `{transient.get('peak_coolant_temperature_c', 'n/a')}`")
        lines.append(f"- Minimum precursor core fraction: `{transient.get('minimum_precursor_core_fraction', 'n/a')}`")
        lines.append(
            "- Minimum core delayed neutron source fraction: "
            f"`{transient.get('minimum_core_delayed_neutron_source_fraction', 'n/a')}`"
        )
        lines.append(
            "- Final precursor transport loss fraction: "
            f"`{transient.get('final_precursor_transport_loss_fraction', 'n/a')}`"
        )
        lines.append(f"- Final total reactivity (pcm): `{transient.get('final_total_reactivity_pcm', 'n/a')}`")
        lines.append(f"- Depletion chain: `{transient.get('depletion_chain', 'n/a')}`")
        lines.append(f"- Cleanup scenario: `{transient.get('cleanup_scenario', 'n/a')}`")
        lines.append(f"- Final fissile inventory fraction: `{transient.get('final_fissile_inventory_fraction', 'n/a')}`")
        lines.append(f"- Peak protactinium inventory fraction: `{transient.get('peak_protactinium_inventory_fraction', 'n/a')}`")
        lines.append(f"- Final redox state (eV): `{transient.get('final_redox_state_ev', 'n/a')}`")
        lines.append(f"- Peak corrosion index: `{transient.get('peak_corrosion_index', 'n/a')}`")
        lines.append(f"- Transient history: `{transient.get('history_path', 'n/a')}`")

    transient_sweep = summary.get("transient_sweep", {})
    if transient_sweep:
        lines.extend(["", "## Transient Sweep", ""])
        lines.append(f"- Model: `{transient_sweep.get('model', 'n/a')}`")
        lines.append(f"- Status: `{transient_sweep.get('status', 'n/a')}`")
        lines.append(f"- Scenario: `{transient_sweep.get('scenario_name', 'n/a')}`")
        lines.append(f"- Backend: `{transient_sweep.get('backend', 'n/a')}`")
        lines.append(f"- Samples: `{transient_sweep.get('samples', 'n/a')}`")
        lines.append(f"- Seed: `{transient_sweep.get('seed', 'n/a')}`")
        ensemble_definition = transient_sweep.get("ensemble_definition", {})
        if isinstance(ensemble_definition, dict):
            lines.append(f"- Ensemble meaning: `{ensemble_definition.get('ensemble_meaning', 'stress_test_envelope')}`")
            lines.append(f"- Sampler: `{ensemble_definition.get('sampler', 'independent_normal_perturbations')}`")
            lines.append(f"- Correlation assumptions: `{ensemble_definition.get('correlation_assumptions', 'independent perturbations')}`")
            lines.append(f"- Percentile definitions: `{ensemble_definition.get('percentile_definitions', 'sample percentiles by time step')}`")
            for parameter in ensemble_definition.get("varied_parameters", [])[:8]:
                if isinstance(parameter, dict):
                    lines.append(
                        f"- Varied `{parameter.get('parameter')}`: unit=`{parameter.get('units')}`, "
                        f"distribution=`{parameter.get('distribution')}`, bounds=`{parameter.get('bounds')}`, "
                        f"basis={parameter.get('physical_basis')}"
                    )
        lines.append(f"- Duration (s): `{transient_sweep.get('duration_s', 'n/a')}`")
        lines.append(f"- Time step (s): `{transient_sweep.get('time_step_s', 'n/a')}`")
        lines.append(f"- Event count: `{transient_sweep.get('event_count', 'n/a')}`")
        lines.append(f"- Peak power fraction p95: `{transient_sweep.get('peak_power_fraction_p95', 'n/a')}`")
        lines.append(f"- Peak power fraction max: `{transient_sweep.get('peak_power_fraction_max', 'n/a')}`")
        lines.append(f"- Peak fuel temperature p95 (C): `{transient_sweep.get('peak_fuel_temperature_c_p95', 'n/a')}`")
        lines.append(f"- Peak fuel temperature max (C): `{transient_sweep.get('peak_fuel_temperature_c_max', 'n/a')}`")
        lines.append(f"- Final power fraction p50: `{transient_sweep.get('final_power_fraction_p50', 'n/a')}`")
        lines.append(f"- Final power fraction p95: `{transient_sweep.get('final_power_fraction_p95', 'n/a')}`")
        lines.append(f"- Final total reactivity p50 (pcm): `{transient_sweep.get('final_total_reactivity_pcm_p50', 'n/a')}`")
        lines.append(f"- Final total reactivity p95 (pcm): `{transient_sweep.get('final_total_reactivity_pcm_p95', 'n/a')}`")
        lines.append(
            "- Final core delayed neutron source fraction p50: "
            f"`{transient_sweep.get('final_core_delayed_neutron_source_fraction_p50', 'n/a')}`"
        )
        lines.append(
            "- Minimum core delayed neutron source fraction p05: "
            f"`{transient_sweep.get('minimum_core_delayed_neutron_source_fraction_p05', 'n/a')}`"
        )
        lines.append(f"- Peak corrosion index p95: `{transient_sweep.get('peak_corrosion_index_p95', 'n/a')}`")
        lines.append(f"- Sweep history: `{transient_sweep.get('history_path', 'n/a')}`")

    uncertainty_sweep = summary.get("uncertainty_sweep", {})
    uncertainty_budget = summary.get("uncertainty_budget", {})
    if uncertainty_sweep:
        lines.extend(["", "## Benchmark Uncertainty Sweep", ""])
        lines.append(f"- Status: `{uncertainty_sweep.get('status', 'n/a')}`")
        lines.append(f"- Model: `{uncertainty_sweep.get('model', 'n/a')}`")
        lines.append(f"- Samples: `{uncertainty_sweep.get('sample_count', 'n/a')}`")
        lines.append(f"- Completed samples: `{uncertainty_sweep.get('completed_sample_count', 'n/a')}`")
        lines.append(f"- Failed samples: `{uncertainty_sweep.get('failed_sample_count', 'n/a')}`")
        lines.append(f"- Coverage status: `{uncertainty_sweep.get('coverage_status', 'n/a')}`")
        lines.append(f"- Nominal keff: `{uncertainty_sweep.get('nominal_keff', 'n/a')}`")
        lines.append(f"- Nominal residual (pcm): `{uncertainty_sweep.get('nominal_residual_pcm', 'n/a')}`")
        lines.append(f"- Input interval width (pcm): `{uncertainty_sweep.get('input_interval_width_pcm', 'n/a')}`")
        lines.append(f"- Input sigma (pcm): `{uncertainty_sweep.get('input_sigma_pcm', 'n/a')}`")
        lines.append(f"- Statistical sigma (pcm): `{uncertainty_sweep.get('statistical_sigma_pcm', 'n/a')}`")
        lines.append(f"- Combined uncertainty (pcm): `{uncertainty_sweep.get('combined_uncertainty_pcm', 'n/a')}`")
        lines.append(
            f"- Normalized residual with input uncertainty: "
            f"`{uncertainty_sweep.get('normalized_residual_with_input', 'n/a')}`"
        )
        lines.append(f"- Budget artifact: `{uncertainty_sweep.get('budget_path', 'n/a')}`")
        for contributor in uncertainty_sweep.get("dominant_contributors", []):
            lines.append(
                f"- Contributor `{contributor.get('parameter_id', 'parameter')}`: "
                f"ranking_score_pcm=`{contributor.get('ranking_score_pcm', 'n/a')}`, "
                f"source_backed=`{contributor.get('source_backed', 'n/a')}`"
            )
        categories = uncertainty_budget.get("uncertainty_categories", {})
        if categories:
            lines.append("- Uncertainty categories:")
            for name, payload in categories.items():
                if isinstance(payload, dict):
                    lines.append(
                        f"- `{name}`: status=`{payload.get('status', 'n/a')}`, "
                        f"sigma_pcm=`{payload.get('sigma_pcm', 'n/a')}`"
                    )

    benchmark_residuals = summary.get("benchmark_residuals", {})
    if benchmark_residuals:
        validation_by_name = {
            str(check.get("name")): check
            for check in validation.get("checks", [])
            if isinstance(check, dict) and check.get("name")
        }
        lines.extend(["", "## Benchmark Residuals", ""])
        lines.append(f"- Status: `{benchmark_residuals.get('status', 'unknown')}`")
        lines.append(f"- Residual item count: `{benchmark_residuals.get('item_count', 0)}`")
        lines.append(f"- Dataset count: `{benchmark_residuals.get('dataset_count', 0)}`")
        lines.append(
            f"- Completed physics residuals: "
            f"`{benchmark_residuals.get('completed_physics_item_count', 0)}/"
            f"{benchmark_residuals.get('physics_item_count', 0)}`"
        )
        lines.append(
            f"- Construction/diagnostic checks: "
            f"`{benchmark_residuals.get('construction_check_count', 0)}/"
            f"{benchmark_residuals.get('diagnostic_check_count', 0)}`"
        )
        lines.append(f"- Residual plot status: `{benchmark_residuals.get('residual_plot_status', 'unknown')}`")
        for blocker in benchmark_residuals.get("blockers", []):
            lines.append(f"- Benchmark blocker: {blocker}")
        for item in benchmark_residuals.get("items", []):
            parts = [
                f"metric=`{item.get('metric', 'n/a')}`",
                f"category=`{item.get('evidence_category', 'diagnostic_check')}`",
                f"status=`{item.get('status', 'pending')}`",
                f"residual=`{item.get('residual', 'n/a')}`",
            ]
            validation_check = validation_by_name.get(str(item.get("name")))
            if validation_check and validation_check.get("status") != item.get("status"):
                parts.append(f"validation_status=`{validation_check.get('status')}`")
            if item.get("target_value") is not None:
                parts.append(f"target=`{item.get('target_value')}`")
            if item.get("residual_pcm") is not None:
                parts.append(f"residual_pcm=`{item.get('residual_pcm')}`")
            if item.get("combined_uncertainty_pcm") is not None:
                parts.append(f"combined_uncertainty_pcm=`{item.get('combined_uncertainty_pcm')}`")
            if item.get("normalized_residual") is not None:
                parts.append(f"normalized_residual=`{item.get('normalized_residual')}`")
            if item.get("message"):
                parts.append(f"note={item.get('message')}")
            lines.append(f"- `{item.get('name', 'target')}`: " + ", ".join(parts))

    if validation:
        lines.extend(["", "## Validation Appendix", ""])
        details_path = validation_summary.get("details_json")
        csv_path = validation_summary.get("details_csv")
        if details_path:
            lines.append(f"- Full validation detail JSON: `{details_path}`")
        if csv_path:
            lines.append(f"- Full validation detail CSV: `{csv_path}`")
        if model_validity:
            lines.append(f"- Model validity: `{model_validity.get('status', 'unknown')}`")
        for group in validation_summary.get("groups", []):
            lines.append(
                f"- `{group.get('group', 'validation')}`: "
                f"pass=`{group.get('pass', 0)}`, fail=`{group.get('fail', 0)}`, "
                f"pending=`{group.get('pending', 0)}`, blocked=`{group.get('blocked', 0)}`"
            )

    integrations = summary.get("integrations", {})
    if integrations:
        lines.extend(["", "## External Integrations", ""])
        for name in sorted(integrations):
            item = integrations[name]
            lines.append(f"- `{name}` status: `{item.get('status', 'n/a')}`")
            if item.get("execution_mode"):
                lines.append(f"- `{name}` execution mode: `{item.get('execution_mode', 'n/a')}`")
            lines.append(f"- `{name}` input path: `{item.get('input_path', 'n/a')}`")
            if item.get("handoff_path"):
                lines.append(f"- `{name}` handoff path: `{item.get('handoff_path', 'n/a')}`")
            if item.get("application"):
                lines.append(f"- `{name}` application: `{item.get('application', 'n/a')}`")
            if item.get("sequence"):
                lines.append(f"- `{name}` sequence: `{item.get('sequence', 'n/a')}`")
            if item.get("command"):
                lines.append(f"- `{name}` command: `{item.get('command')}`")
            if item.get("error"):
                lines.append(f"- `{name}` note: {item.get('error')}")

    lines.extend(["", "## Evidence Sources", ""])
    if benchmark.get("evidence"):
        for item in benchmark_traceability.get("evidence", []):
            lines.append(f"- {item.get('topic', 'evidence')}: {item.get('claim', 'n/a')}")
            if item.get("source"):
                lines.append(f"- Source: `{item['source']}`")
            if item.get("confidence"):
                lines.append(f"- Confidence: `{item['confidence']}`")
            if item.get("relevance"):
                lines.append(f"- Why it matters here: {item['relevance']}")
    else:
        lines.append("- No benchmark evidence records were attached to this bundle.")
    lines.extend(["", "## Evidence Trail", "", "- See Evidence Sources for source-backed claim context."])

    lines.extend(["", "## Future Work / Novelty Tracks", ""])
    if benchmark.get("novelty_tracks"):
        for track in benchmark["novelty_tracks"]:
            lines.append(f"- {track.get('name', 'untitled')}: {track.get('summary', '')}")
    else:
        lines.append("- No novelty-track claims were completed in this run; future-work language remains separated from generated results.")
    lines.extend(["", "## Novelty Tracks", "", "- See Future Work / Novelty Tracks for proposed or future work items."])

    if geometry_assets:
        lines.extend(["", "## Geometry Outputs", ""])
        for name, path in geometry_assets.items():
            lines.append(f"- {name}: `{path}`")

    if plot_assets:
        catalog = load_figure_catalog(summary_path.parent / "plots_manifest.json")
        primary_plots: list[tuple[str, str]] = []
        appendix_plots: list[tuple[str, str]] = []
        for name, path in sorted(plot_assets.items()):
            entry = catalog.get(name, {})
            if _is_primary_plot(name, entry):
                primary_plots.append((name, path))
            else:
                appendix_plots.append((name, path))
        plot_lines = primary_plots or appendix_plots
        lines.extend(["", "## Plot Outputs", ""])
        for name, path in plot_lines:
            lines.append(f"- {name}: `{path}`")
        if not primary_plots and appendix_plots:
            lines.append("- Plot status: primary figures were unavailable; listed plots are appendix diagnostics.")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This report is generated from the config-driven reactor workflow.",
            "- Validation targets can mix literature-derived bounds with explicitly labeled surrogate assumptions.",
        ]
    )

    report_text = _render_report_lines(lines)
    _materialize_presentation_qa(summary_path.parent, report_text)
    return report_text


_NUMERIC_LITERAL_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_CODE_LITERAL_RE = re.compile(r"`([^`]*)`")
_NUMERIC_CODE_RE = re.compile(rf"`({_NUMERIC_LITERAL_PATTERN})`")
_NUMERIC_TOKEN_RE = re.compile(_NUMERIC_LITERAL_PATTERN)
_NUMERIC_COMPOSITE_RE = re.compile(
    rf"^\s*{_NUMERIC_LITERAL_PATTERN}"
    rf"(?:(?:\s+to\s+|\s+through\s+|\s+-\s+|\s+/\s+|\s+x\s+){_NUMERIC_LITERAL_PATTERN})+"
    rf"(?:\s+[A-Za-z][A-Za-z0-9/%.\-]*)?\s*$",
    re.IGNORECASE,
)
_MISSING_CODE_LITERAL_RE = re.compile(r"`\s*(?:none|n/a|na|null|unknown)?\s*`", re.IGNORECASE)
_MISSING_CODE_VALUES = {"", "none", "n/a", "na", "null", "unknown"}
_CURATED_SUMMARY_METRIC_KEYS = {
    "keff",
    "benchmark_traceability_score",
    "validation_maturity_score",
    "channel_count",
    "active_flow_channel_count",
    "transient_sweep_peak_power_fraction_p95",
    "finance_lcoe_usd_per_mwh",
}
_LABEL_ACRONYMS = {
    "bop": "BOP",
    "csv": "CSV",
    "foak": "FOAK",
    "json": "JSON",
    "lcoe": "LCOE",
    "msre": "MSRE",
    "mw": "MW",
    "mwe": "MWe",
    "mwth": "MWth",
    "occ": "OCC",
    "pcm": "pcm",
    "png": "PNG",
    "rkdg": "RKDG",
    "tmsr": "TMSR",
    "uq": "UQ",
    "usd": "USD",
    "wacc": "WACC",
}
_METRIC_UNIT_SUFFIXES = (
    ("_usd_per_mwh", "USD/MWh"),
    ("_cents_per_kwh", "cents/kWh"),
    ("_mw_per_t_hm", "MW/tHM"),
    ("_usd_per_year", "USD/yr"),
    ("_n_cm2_s", "n/cm2-s"),
    ("_w_m2k", "W/m2-K"),
    ("_kg_s", "kg/s"),
    ("_m_s", "m/s"),
    ("_atoms_s", "atoms/s"),
    ("_pa_s", "Pa-s"),
    ("_mwth", "MWth"),
    ("_mwe", "MWe"),
    ("_mwh", "MWh"),
    ("_kpa", "kPa"),
    ("_pcm", "pcm"),
    ("_cm2", "cm2"),
    ("_m2", "m2"),
    ("_m3", "m3"),
    ("_ev", "eV"),
    ("_kw", "kW"),
    ("_mw", "MW"),
    ("_kg", "kg"),
    ("_days", "days"),
    ("_years", "years"),
    ("_s", "s"),
    ("_c", "C"),
)


def _materialize_validation_summary_artifacts(
    bundle_dir: Path,
    validation: dict[str, Any],
    benchmark_quality: dict[str, Any],
) -> dict[str, Any]:
    checks = validation.get("checks", []) if isinstance(validation, dict) else []
    if not isinstance(checks, list):
        checks = []
    rows: list[dict[str, Any]] = []
    counts = {"pass": 0, "fail": 0, "pending": 0, "blocked": 0, "other": 0}
    group_counts: dict[str, dict[str, int]] = {}
    blockers: list[dict[str, Any]] = []
    failed_gate_ids = {
        str(gate.get("id"))
        for gate in benchmark_quality.get("gates", [])
        if isinstance(gate, dict) and gate.get("status") == "fail"
    }
    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            continue
        name = str(check.get("name", f"check_{index}"))
        status = str(check.get("status", "pending")).lower()
        if status not in counts:
            status = "other"
        group = _validation_group(name)
        benchmark_critical = _is_benchmark_critical_check(name, check, failed_gate_ids)
        row = {
            "name": name,
            "group": group,
            "status": status,
            "benchmark_critical": benchmark_critical,
            "blocker_importance": "high" if benchmark_critical and status in {"fail", "pending", "blocked"} else "normal",
            "message": check.get("message", ""),
        }
        rows.append(row)
        counts[status] += 1
        grouped = group_counts.setdefault(group, {"pass": 0, "fail": 0, "pending": 0, "blocked": 0, "other": 0})
        grouped[status] += 1
        if benchmark_critical and status in {"fail", "pending", "blocked"}:
            blockers.append(row)

    groups = [{"group": group, **payload} for group, payload in sorted(group_counts.items())]
    summary = {
        "status_counts": counts,
        "groups": groups,
        "blockers": blockers,
        "details": rows,
        "details_json": None,
        "details_csv": None,
    }
    if rows or validation:
        details_json = bundle_dir / "validation_summary.json"
        details_csv = bundle_dir / "validation_details.csv"
        summary["details_json"] = details_json.name
        summary["details_csv"] = details_csv.name
        details_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        details_csv.write_text(_validation_rows_csv(rows), encoding="utf-8")
    return summary


def _validation_rows_csv(rows: list[dict[str, Any]]) -> str:
    output = StringIO()
    fieldnames = ["name", "group", "status", "benchmark_critical", "blocker_importance", "message"]
    writer = DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output.getvalue()


def _validation_group(name: str) -> str:
    if "::" in name:
        return name.split("::", 1)[0]
    if "_" in name:
        return name.split("_", 1)[0]
    return "validation"


def _is_benchmark_critical_check(name: str, check: dict[str, Any], failed_gate_ids: set[str]) -> bool:
    lowered = name.lower()
    if name in failed_gate_ids or lowered in failed_gate_ids:
        return True
    if any(token in lowered for token in ("keff", "criticality", "benchmark", "cross_code")):
        return True
    return str(check.get("evidence_category", "")).lower() == "physics_benchmark"


def _ensure_limitations_matrix(
    summary: dict[str, Any],
    validation: dict[str, Any],
    benchmark_quality: dict[str, Any],
    validation_maturity: dict[str, Any],
) -> list[dict[str, Any]]:
    existing = summary.get("limitations_matrix")
    if isinstance(existing, list) and existing:
        return existing
    neutronics_status = _neutronics_status(summary)
    benchmark_ready = benchmark_quality.get("benchmark_ready") is True if benchmark_quality else False
    validation_checks = validation.get("checks", []) if isinstance(validation, dict) else []
    has_keff_pending = any(
        isinstance(check, dict)
        and "keff" in str(check.get("name", "")).lower()
        and str(check.get("status", "")).lower() in {"pending", "blocked", "fail"}
        for check in validation_checks
    )
    rows = [
        _limitation_row(
            "neutronics_status",
            "major_concern" if neutronics_status != "completed" else "nominal",
            "summary.json",
            f"Neutronics status is {neutronics_status}; solver-backed OpenMC claims are blocked."
            if neutronics_status != "completed"
            else "Solver-backed neutronics result is recorded.",
            "Run solver-backed benchmark workflow and publish the statepoint bundle."
            if neutronics_status != "completed"
            else "Maintain artifact provenance.",
        ),
        _limitation_row(
            "benchmark_observable_availability",
            "major_concern" if has_keff_pending or not benchmark_ready else "nominal",
            "validation_summary.json",
            "Benchmark-critical observables are failed, blocked, or pending."
            if has_keff_pending or not benchmark_ready
            else "Benchmark-critical observables are complete for this bundle.",
            "Resolve pending/failed benchmark checks, including keff_core_band when present."
            if has_keff_pending or not benchmark_ready
            else "Retain benchmark quality evidence.",
        ),
        _limitation_row(
            "property_uncertainty_source_backing",
            "watch" if summary.get("property_uncertainty") else "major_concern",
            "summary.json",
            "Property uncertainty is present but remains a screening literature envelope."
            if summary.get("property_uncertainty")
            else "No property uncertainty summary is available in this run.",
            "Replace proxy envelopes with source-indexed salt property covariance where available.",
        ),
        _limitation_row(
            "surrogate_targets",
            "watch" if validation_maturity.get("validation_maturity_stage") in {"screening_backed", "benchmark_ready"} else "major_concern",
            "benchmark metadata",
            "Some targets may remain surrogate or context-only.",
            "Retire surrogate targets with literature-backed or solver-backed observables.",
        ),
        _limitation_row(
            "depletion_balance",
            _severity_from_residual(summary.get("depletion_matrix", {}).get("atom_balance_residual")),
            "depletion_history.json",
            "Native depletion balance is absent or screening-grade unless an atom-balance residual is reported.",
            "Run native depletion and review atom-balance residuals before inventory claims.",
        ),
        _limitation_row(
            "transport_coupling",
            "watch" if summary.get("transport_solver") else "major_concern",
            "transport_solution.npz",
            "Transport coupling is reduced-order/native-screening, not full multiphysics validation.",
            "Cross-check transport fields against an independent solver and couple back to thermal hydraulics.",
        ),
        _limitation_row(
            "uncertainty_propagation",
            "watch" if summary.get("uncertainty_sweep") else "major_concern",
            "uncertainty_budget.json",
            "Uncertainty propagation is missing or partial for benchmark claims.",
            "Run source-backed benchmark uncertainty sweep with propagated keff uncertainty.",
        ),
        _limitation_row(
            "cross_code_validation",
            "nominal" if validation_maturity.get("cross_code_checks") and not validation_maturity.get("gaps") else "major_concern",
            "benchmark metadata",
            "Cross-code validation is incomplete or not declared.",
            "Complete declared cross-code checks and record source artifacts.",
        ),
        _limitation_row(
            "benchmark_quality",
            "nominal" if benchmark_quality else "not_applicable",
            "benchmark metadata",
            "Benchmark quality gates are populated." if benchmark_quality else "No benchmark quality gates apply or were declared for this non-benchmark bundle.",
            "Declare benchmark quality gates when promoting benchmark or validation claims." if not benchmark_quality else "Keep quality gates synchronized with evidence.",
        ),
    ]
    return rows


def _limitation_row(area: str, severity: str, evidence_artifact: str, consequence: str, next_action: str) -> dict[str, Any]:
    return {
        "area": area,
        "severity": severity,
        "evidence_artifact": evidence_artifact,
        "consequence": consequence,
        "next_action": next_action,
    }


def _severity_from_residual(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "major_concern"
    return "nominal" if abs(float(value)) < 1.0e-6 else "watch"


def _classify_design_readiness(
    summary: dict[str, Any],
    validation: dict[str, Any],
    benchmark_quality: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    _add_readiness_finding(
        findings,
        "neutronics_evidence",
        "nominal" if _is_solver_backed_neutronics(summary) else "major_concern",
        "Solver-backed neutronics is required before design or benchmark claims.",
        "summary.json",
    )
    if benchmark_quality:
        _add_readiness_finding(
            findings,
            "benchmark_quality",
            "nominal" if benchmark_quality.get("benchmark_ready") is True else "major_concern",
            "Benchmark gates are not ready." if benchmark_quality.get("benchmark_ready") is not True else "Benchmark gates are ready.",
            "benchmark metadata",
        )
    model_validity = summary.get("model_validity", {})
    if isinstance(model_validity, dict) and model_validity.get("status") == "invalid":
        _add_readiness_finding(findings, "model_validity", "major_concern", "Model validity checks failed.", "summary.json")
    for check in validation.get("checks", []) if isinstance(validation, dict) else []:
        if isinstance(check, dict) and str(check.get("status", "")).lower() == "fail":
            _add_readiness_finding(findings, str(check.get("name", "validation")), "major_concern", str(check.get("message", "Validation check failed.")), "validation.json")
    chemistry = summary.get("chemistry", {})
    if isinstance(chemistry, dict):
        risk = str(chemistry.get("corrosion_risk", "")).lower()
        _add_readiness_finding(
            findings,
            "chemistry_corrosion",
            {"low": "nominal", "moderate": "watch", "high": "major_concern"}.get(risk, "not_applicable"),
            "Chemistry corrosion proxy is screening-only.",
            "summary.json",
        )
    graphite = summary.get("graphite_lifetime", {})
    if isinstance(graphite, dict) and graphite:
        estimated_years = _as_float(graphite.get("estimated_lifespan_years"))
        lifetime_margin = _as_float(graphite.get("lifetime_margin"))
        graphite_severity = "nominal"
        if estimated_years is not None and estimated_years < 1.0:
            graphite_severity = "disqualifying_for_claimed_use"
        elif lifetime_margin is not None and lifetime_margin < 0.25:
            graphite_severity = "disqualifying_for_claimed_use"
        elif estimated_years is not None and estimated_years < 8.0:
            graphite_severity = "major_concern"
        elif lifetime_margin is not None and lifetime_margin < 1.0:
            graphite_severity = "major_concern"
        elif graphite.get("screening_status") == "watch":
            graphite_severity = "watch"
        _add_readiness_finding(
            findings,
            "graphite_lifetime",
            graphite_severity,
            "Graphite lifetime is a screening literature model; short replacement intervals block commercial-readiness claims.",
            "summary.json",
        )
    flow = summary.get("flow", {}).get("reduced_order", {}) if isinstance(summary.get("flow"), dict) else {}
    active_flow = flow.get("active_flow", {}) if isinstance(flow, dict) else {}
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    channel_count = _first_available(metrics.get("channel_count"), flow.get("channel_count") if isinstance(flow, dict) else None)
    active_channel_count = active_flow.get("channel_count") if isinstance(active_flow, dict) else None
    if isinstance(active_channel_count, (int, float)) and isinstance(channel_count, (int, float)):
        if int(active_channel_count) <= 1 and int(channel_count) > 1:
            _add_readiness_finding(
                findings,
                "active_flow_channel_count",
                "major_concern",
                "Only one active through-flow channel is modeled in a multi-channel geometry.",
                "summary.json",
            )
    velocity = active_flow.get("representative_velocity_m_s") if isinstance(active_flow, dict) else None
    if isinstance(velocity, (int, float)):
        severity = "major_concern" if float(velocity) > 12.0 else "nominal"
        _add_readiness_finding(findings, "active_channel_velocity", severity, "Representative velocity screening envelope.", "summary.json")
    primary_system = summary.get("primary_system", {}) if isinstance(summary.get("primary_system"), dict) else {}
    inventory = primary_system.get("inventory", {}) if isinstance(primary_system.get("inventory"), dict) else {}
    coolant_salt = inventory.get("coolant_salt", {}) if isinstance(inventory.get("coolant_salt"), dict) else {}
    coolant_inventory = _as_float(coolant_salt.get("net_pool_inventory_m3"))
    if coolant_inventory is not None and coolant_inventory <= 0.0:
        _add_readiness_finding(
            findings,
            "coolant_salt_inventory",
            "major_concern",
            "Coolant salt inventory is zero or negative while plant-loop claims require a physical inventory explanation.",
            "summary.json",
        )
    fuel_cycle = summary.get("fuel_cycle", {}) if isinstance(summary.get("fuel_cycle"), dict) else {}
    specific_power = _as_float(fuel_cycle.get("specific_power_mw_per_t_hm"))
    if specific_power is not None:
        if specific_power >= 1000.0:
            severity = "disqualifying_for_claimed_use"
        elif specific_power >= 500.0:
            severity = "major_concern"
        else:
            severity = "nominal"
        _add_readiness_finding(
            findings,
            "specific_power",
            severity,
            "Specific power is screened against heavy-metal inventory plausibility for design-readiness claims.",
            "summary.json",
        )
    order = {severity: index for index, severity in enumerate(("not_applicable", "nominal", "watch", "major_concern", "disqualifying_for_claimed_use"))}
    top = max((item["severity"] for item in findings), key=lambda value: order.get(value, 0), default="not_applicable")
    severe = [item for item in findings if item["severity"] in {"major_concern", "disqualifying_for_claimed_use"}]
    return {
        "status": top,
        "severe_finding_count": len(severe),
        "findings": findings,
        "commercial_or_build_candidate_language_allowed": len(severe) == 0,
    }


def _add_readiness_finding(findings: list[dict[str, Any]], metric: str, severity: str, basis: str, evidence_artifact: str) -> None:
    if severity == "not_applicable":
        return
    findings.append(
        {
            "metric": metric,
            "severity": severity,
            "basis": basis,
            "evidence_artifact": evidence_artifact,
        }
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _materialize_result_claims(
    bundle_dir: Path,
    config: dict[str, Any],
    summary: dict[str, Any],
    validation_summary: dict[str, Any],
    limitations_matrix: list[dict[str, Any]],
    design_readiness: dict[str, Any],
) -> list[dict[str, Any]]:
    claims = [
        {
            "claim": "This report summarizes artifacts generated in the current result bundle.",
            "status": "completed_result",
            "evidence_artifact": "summary.json",
            "evidence_tier": "generated_bundle",
        },
        {
            "claim": "Validation and benchmark blockers are explicitly separated from raw validation details.",
            "status": "completed_result" if validation_summary.get("details_json") else "not_applicable",
            "evidence_artifact": validation_summary.get("details_json") or "validation.json",
            "evidence_tier": "curated_validation_summary",
        },
        {
            "claim": "Design/build readiness language is gated by severe screening findings.",
            "status": "completed_result",
            "evidence_artifact": "summary.json",
            "evidence_tier": "screening_qa",
        },
        {
            "claim": "Commercial or build-candidate claims are not supported while severe findings remain.",
            "status": "blocked" if design_readiness.get("severe_finding_count", 0) else "completed_result",
            "evidence_artifact": "summary.json",
            "evidence_tier": "screening_qa",
        },
    ]
    if summary.get("transient_sweep"):
        claims.append(
            {
                "claim": "Transient sweep percentiles describe an ensemble envelope, not deterministic UQ unless source-backed distributions are declared.",
                "status": "completed_result",
                "evidence_artifact": "transient_sweep.json",
                "evidence_tier": "proxy_ensemble",
            }
        )
    if any(row["severity"] in {"major_concern", "disqualifying_for_claimed_use"} for row in limitations_matrix):
        claims.append(
            {
                "claim": "Major limitations remain before benchmark-ready or design-ready claims.",
                "status": "blocked",
                "evidence_artifact": "limitations_matrix.json",
                "evidence_tier": "limitations_matrix",
            }
        )
    path = bundle_dir / "result_claims.json"
    path.write_text(json.dumps(claims, indent=2, sort_keys=True), encoding="utf-8")
    (bundle_dir / "limitations_matrix.json").write_text(json.dumps(limitations_matrix, indent=2, sort_keys=True), encoding="utf-8")
    (bundle_dir / "design_readiness.json").write_text(json.dumps(design_readiness, indent=2, sort_keys=True), encoding="utf-8")
    return claims


def _build_results_generated_lines(bundle_dir: Path, summary: dict[str, Any]) -> list[str]:
    lines = ["## Results Generated In This Run", ""]
    artifacts = [
        "summary.json",
        "validation.json",
        "report.md",
        "benchmark_evidence.json",
        "nuclear_data_provenance.json",
        "source_convergence_diagnostics.json",
        "cross_code_comparison.json",
        "uncertainty_budget.json",
        "validation_summary.json",
        "validation_details.csv",
        "limitations_matrix.json",
        "result_claims.json",
        "design_readiness.json",
    ]
    present = [name for name in artifacts if (bundle_dir / name).exists()]
    if not present:
        lines.append("- No report-side artifacts were materialized yet.")
    else:
        for name in present:
            lines.append(f"- `{name}`")
    if summary.get("neutronics"):
        lines.append(f"- Neutronics status for this run: `{_neutronics_status(summary)}`")
    lines.append("")
    return lines


def _build_benchmark_evidence_lines(bundle_dir: Path, summary: dict[str, Any]) -> list[str]:
    evidence = summary.get("benchmark_evidence", {})
    if not isinstance(evidence, dict) or not evidence:
        evidence_path = bundle_dir / "benchmark_evidence.json"
        if evidence_path.exists():
            try:
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            except JSONDecodeError:
                evidence = {}
    if not evidence:
        return []

    lines = ["## Benchmark Evidence Contract", ""]
    lines.append(f"- Evidence status: `{evidence.get('status', 'unknown')}`")
    lines.append(f"- Evidence benchmark-ready: `{evidence.get('benchmark_ready_evidence', False)}`")
    lines.append(f"- Failed evidence gates: `{evidence.get('failed_gate_count', 0)}`")
    for gate in evidence.get("gates", []):
        if not isinstance(gate, dict):
            continue
        if gate.get("status") == "pass":
            continue
        lines.append(
            f"- Gate `{gate.get('id', 'gate')}`: `{gate.get('status', 'fail')}`"
            + (f" ({gate.get('message')})" if gate.get("message") else "")
        )
    for artifact in (
        "benchmark_evidence.json",
        "nuclear_data_provenance.json",
        "source_convergence_diagnostics.json",
        "cross_code_comparison.json",
        "uncertainty_budget.json",
    ):
        if (bundle_dir / artifact).exists():
            lines.append(f"- Evidence artifact: `{artifact}`")
    lines.append("")
    return lines


def _build_design_readiness_lines(design_readiness: dict[str, Any]) -> list[str]:
    lines = ["## Design Readiness", ""]
    lines.append(f"- Overall screening severity: `{design_readiness.get('status', 'not_applicable')}`")
    lines.append(f"- Severe finding count: `{design_readiness.get('severe_finding_count', 0)}`")
    if not design_readiness.get("commercial_or_build_candidate_language_allowed", False):
        lines.append("- Claim gate: commercial/build-candidate language is blocked by severe screening findings.")
    severe = [
        item
        for item in design_readiness.get("findings", [])
        if isinstance(item, dict) and item.get("severity") in {"major_concern", "disqualifying_for_claimed_use"}
    ]
    for item in severe[:8]:
        lines.append(
            f"- `{item.get('metric', 'metric')}`: severity=`{item.get('severity')}`, "
            f"evidence=`{item.get('evidence_artifact')}`, basis={item.get('basis')}"
        )
    lines.append("")
    return lines


def _build_validation_and_blocker_lines(
    validation_summary: dict[str, Any],
    benchmark_quality: dict[str, Any],
) -> list[str]:
    lines = ["## Validation And Blockers", ""]
    counts = validation_summary.get("status_counts", {})
    if counts:
        lines.append(
            f"- Validation summary: pass=`{counts.get('pass', 0)}`, fail=`{counts.get('fail', 0)}`, "
            f"pending=`{counts.get('pending', 0)}`, blocked=`{counts.get('blocked', 0)}`"
        )
    if benchmark_quality:
        lines.append(f"- Benchmark ready: `{benchmark_quality.get('benchmark_ready', False)}`")
        lines.append(f"- Benchmark quality stage: `{benchmark_quality.get('quality_stage', 'not_assessed')}`")
    else:
        lines.append("- Benchmark quality: `not_applicable` for this bundle, or no quality gates were declared.")
    blockers = validation_summary.get("blockers", [])
    if blockers:
        lines.extend(["", "| Check | Group | Status | Importance | Message |", "| --- | --- | --- | --- | --- |"])
        for blocker in blockers:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(blocker.get("name", "")),
                        str(blocker.get("group", "")),
                        str(blocker.get("status", "")),
                        str(blocker.get("blocker_importance", "")),
                        str(blocker.get("message", "")).replace("|", "/"),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- No failed, pending, or blocked benchmark-critical validation checks were found.")
    if validation_summary.get("details_json"):
        lines.append(f"- Full detail artifacts: `{validation_summary.get('details_json')}`, `{validation_summary.get('details_csv')}`")
    lines.append("")
    return lines


def _build_interpretation_lines(
    summary: dict[str, Any],
    validation_summary: dict[str, Any],
    design_readiness: dict[str, Any],
    benchmark_quality: dict[str, Any],
) -> list[str]:
    lines = ["## Interpretation", ""]
    if _is_solver_backed_neutronics(summary):
        lines.append("- Neutronics interpretation may use solver-backed result language for artifacts present in this bundle.")
    else:
        lines.append("- Neutronics interpretation is limited to dry-run/proxy diagnostics; no solver-backed OpenMC physics result is present.")
    if validation_summary.get("blockers"):
        lines.append("- Benchmark-critical blockers take precedence over pass counts from construction or diagnostic checks.")
    if benchmark_quality and benchmark_quality.get("benchmark_ready") is not True:
        lines.append("- Benchmark quality gates prevent benchmark-ready interpretation.")
    if design_readiness.get("severe_finding_count", 0):
        lines.append("- Severe engineering-screening findings prevent commercial/build-candidate interpretation.")
    lines.append("")
    return lines


def _build_limitations_matrix_lines(limitations_matrix: list[dict[str, Any]]) -> list[str]:
    lines = ["## Limitations", ""]
    lines.extend(["| Area | Severity | Evidence | Consequence | Next action |", "| --- | --- | --- | --- | --- |"])
    for row in limitations_matrix:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("area", "")),
                    str(row.get("severity", "")),
                    str(row.get("evidence_artifact", "")),
                    str(row.get("consequence", "")).replace("|", "/"),
                    str(row.get("next_action", "")).replace("|", "/"),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _build_result_claims_lines(result_claims: list[dict[str, Any]]) -> list[str]:
    lines = ["## Result Claims", ""]
    lines.append("- Machine-readable claim map: `result_claims.json`")
    for claim in result_claims:
        lines.append(
            f"- {claim.get('claim')}: status=`{claim.get('status')}`, "
            f"tier=`{claim.get('evidence_tier')}`, artifact=`{claim.get('evidence_artifact')}`"
        )
    lines.append("")
    return lines


def _build_method_card_lines(summary: dict[str, Any]) -> list[str]:
    cards: list[dict[str, str]] = []
    if summary.get("chemistry"):
        cards.append(
            {
                "name": "Chemistry proxy",
                "model": str(summary["chemistry"].get("model", "salt_redox_cleanup_proxy")),
                "equation": "corrosion_index = 1 + max(redox - target, 0) * acceleration + impurity_fraction",
                "inputs": "redox state (eV), target redox (eV), impurity fraction, gas stripping fraction",
                "basis": "configured case parameters and literature-screening assumptions",
                "validity": "screening comparisons within configured molten-salt operating envelope",
                "uncertainty": "sensitive to redox setpoint, impurity ingress, and cleanup efficiency",
                "screening_reason": "not a thermochemical equilibrium or corrosion kinetics solver",
                "artifact": "summary.json",
            }
        )
    if summary.get("tritium"):
        cards.append(
            {
                "name": "Tritium screen",
                "model": str(summary["tritium"].get("model", summary["tritium"].get("basis", "tritium_distribution_screen"))),
                "equation": "release = production * environmental_release_fraction after removal and retention partitions",
                "inputs": "relative production, removal fraction, graphite retention fraction, release fraction",
                "basis": "literature-backed distribution screen and configured chemistry removal assumptions",
                "validity": "first-pass source-term ranking, not isotope transport",
                "uncertainty": "sensitive to gas stripping, graphite retention, and permeation assumptions",
                "screening_reason": "does not solve isotope generation, permeation, or plant release pathways",
                "artifact": "summary.json",
            }
        )
    if summary.get("fuel_cycle"):
        cards.append(
            {
                "name": "Fuel-cycle proxy",
                "model": str(summary["fuel_cycle"].get("depletion_chain", summary["fuel_cycle"].get("depletion_model", "thorium_cleanup_proxy"))),
                "equation": "net fissile change = breeding gain - burnup - cleanup/removal penalties",
                "inputs": "heavy-metal inventory (kg), fissile inventory (kg), cleanup turnover (days), removal efficiency",
                "basis": "configured depletion/cleanup proxy or native sparse depletion artifact when present",
                "validity": "screening balances and trend checks only",
                "uncertainty": "sensitive to cleanup efficiency, protactinium holdup, and burn/breed coefficients",
                "screening_reason": "not a full burnup, online-processing, or safeguards model",
                "artifact": "summary.json",
            }
        )
    if not cards:
        return []
    lines = ["## Method Cards", ""]
    for card in cards:
        lines.extend(
            [
                f"### {card['name']}",
                "",
                f"- Model: `{card['model']}`",
                f"- Equation form: {card['equation']}",
                f"- Inputs and units: {card['inputs']}",
                f"- Calibration/source basis: {card['basis']}",
                f"- Validity range: {card['validity']}",
                f"- Uncertainty/sensitivity notes: {card['uncertainty']}",
                f"- Screening-only reason: {card['screening_reason']}",
                f"- Source artifacts: `{card['artifact']}`",
                "",
            ]
        )
    return lines


def build_presentation_qa(bundle_dir: Path, report_text: str | None = None) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    if report_text is None:
        report_path = bundle_dir / "report.md"
        report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    checks: list[dict[str, Any]] = []
    _qa_required_sections(checks, report_text)
    _qa_no_raw_none(checks, bundle_dir, report_text)
    _qa_figure_manifest(checks, bundle_dir)
    _qa_dry_run_warning(checks, bundle_dir, report_text)
    _qa_status_contradictions(checks, bundle_dir, report_text)
    passed = all(check["status"] == "pass" for check in checks)
    return {"passed": passed, "checks": checks}


def _materialize_presentation_qa(bundle_dir: Path, report_text: str) -> None:
    qa = build_presentation_qa(bundle_dir, report_text=report_text)
    try:
        (bundle_dir / "presentation_qa.json").write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _qa_required_sections(checks: list[dict[str, Any]], report_text: str) -> None:
    required = [
        "## Results Generated In This Run",
        "## Validation And Blockers",
        "## Interpretation",
        "## Limitations",
        "## Future Work / Novelty Tracks",
        "## Evidence Sources",
    ]
    missing_or_empty = []
    for heading in required:
        if heading not in report_text:
            missing_or_empty.append(heading)
            continue
        section = _extract_section_text(report_text, heading)
        if not any(line.strip() and not line.startswith("#") for line in section.splitlines()[1:]):
            missing_or_empty.append(heading)
    checks.append(_qa_check("report::required_sections_nonempty", not missing_or_empty, ", ".join(missing_or_empty)))


def _qa_no_raw_none(checks: list[dict[str, Any]], bundle_dir: Path, report_text: str) -> None:
    offenders = []
    if "`None`" in report_text or ": None" in report_text:
        offenders.append("report.md")
    for path in bundle_dir.glob("*.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if '"None"' in text:
            offenders.append(path.name)
    checks.append(_qa_check("report::no_raw_python_none", not offenders, ", ".join(sorted(set(offenders)))))


def _qa_figure_manifest(checks: list[dict[str, Any]], bundle_dir: Path) -> None:
    manifest_path = bundle_dir / "plots_manifest.json"
    if not manifest_path.exists():
        plot_files_present = (bundle_dir / "plots").exists() and any((bundle_dir / "plots").iterdir())
        passed = not plot_files_present
        detail = "No plot manifest present." if passed else "Plot files exist without plots_manifest.json."
        checks.append(_qa_check("figures::manifest_metadata", passed, detail))
        checks.append(_qa_check("figures::no_mixed_unit_primary_charts", passed, detail))
        checks.append(_qa_check("figures::portable_paths", passed, detail))
        return
    catalog = load_figure_catalog(manifest_path)
    missing_metadata = [
        plot_id
        for plot_id, entry in catalog.items()
        if not entry.get("caption") or not entry.get("quality_status") or not entry.get("status")
    ]
    mixed_primary = [
        plot_id
        for plot_id, entry in catalog.items()
        if entry.get("report_section") == "primary" and any("mixed" in str(value).lower() for value in entry.get("units", {}).values())
    ]
    nonportable = [
        plot_id
        for plot_id, entry in catalog.items()
        if _is_nonportable_manifest_path(str(entry.get("path", "")), bundle_dir)
    ]
    checks.append(_qa_check("figures::manifest_metadata", not missing_metadata, ", ".join(missing_metadata)))
    checks.append(_qa_check("figures::no_mixed_unit_primary_charts", not mixed_primary, ", ".join(mixed_primary)))
    checks.append(_qa_check("figures::portable_paths", not nonportable, ", ".join(nonportable)))


def _qa_dry_run_warning(checks: list[dict[str, Any]], bundle_dir: Path, report_text: str) -> None:
    summary = _read_json(bundle_dir / "summary.json")
    status = str(summary.get("neutronics", {}).get("status", "")).lower() if isinstance(summary, dict) else ""
    needs_warning = status and status != "completed"
    has_warning = "dry-run/proxy" in report_text or "not a solver-backed OpenMC physics result" in report_text
    checks.append(_qa_check("report::dry_run_proxy_warning", (not needs_warning) or has_warning, status))


def _qa_status_contradictions(checks: list[dict[str, Any]], bundle_dir: Path, report_text: str) -> None:
    summary = _read_json(bundle_dir / "summary.json")
    status = str(summary.get("neutronics", {}).get("status", "")).lower() if isinstance(summary, dict) else ""
    contradictions = []
    if status and status != "completed" and "solver-backed OpenMC result" in report_text and "not a solver-backed OpenMC physics result" not in report_text:
        contradictions.append("dry-run report claims solver-backed evidence")
    artifact_status = summary.get("artifact_status", {}) if isinstance(summary, dict) else {}
    groups = artifact_status.get("groups", {}) if isinstance(artifact_status, dict) else {}
    openmc = groups.get("openmc", {}) if isinstance(groups, dict) else {}
    if isinstance(openmc, dict) and openmc.get("state") in {"missing", "dry_run"} and "Build candidate: `true`" in report_text:
        contradictions.append("build candidate true with missing/dry-run OpenMC artifacts")
    checks.append(_qa_check("report::status_contradictions", not contradictions, "; ".join(contradictions)))


def _qa_check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def _extract_section_text(report_text: str, heading: str) -> str:
    start = report_text.find(heading)
    if start < 0:
        return ""
    next_heading = report_text.find("\n## ", start + len(heading))
    return report_text[start:] if next_heading < 0 else report_text[start:next_heading]


def _is_nonportable_manifest_path(path_text: str, bundle_dir: Path) -> bool:
    if not path_text:
        return True
    if re.match(r"^[A-Za-z]:[\\/]", path_text) or path_text.startswith("\\\\"):
        return True
    path = Path(path_text)
    if not path.is_absolute():
        return False
    return True


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_artifact_index_lines(
    *,
    summary_path: Path,
    validation_path: Path | None,
    geometry_assets: dict[str, str] | None,
    benchmark: dict[str, Any],
    plot_assets: dict[str, str] | None,
    provenance: dict[str, Any] | None,
    summary: dict[str, Any],
) -> list[str]:
    primary: list[str] = []
    appendix: list[str] = []

    _append_artifact(
        primary,
        "Run summary JSON",
        summary_path,
        "curated run status, metrics, and generated artifact paths.",
    )
    if validation_path:
        _append_artifact(
            primary,
            "Validation checks",
            validation_path,
            "pass/fail evidence for configured validation targets.",
        )
    if plot_assets:
        catalog = load_figure_catalog(summary_path.parent / "plots_manifest.json")
        for name, path in sorted(plot_assets.items()):
            entry = catalog.get(name, {})
            target = primary if _is_primary_plot(name, entry) else appendix
            description = str(
                _first_available(
                    entry.get("caption") if isinstance(entry, dict) else None,
                    entry.get("conclusion") if isinstance(entry, dict) else None,
                    "visual evidence generated from the run summary.",
                )
            )
            _append_artifact(target, f"{_humanize_label(name)} plot", path, description)
    if geometry_assets:
        for name, path in sorted(geometry_assets.items()):
            _append_artifact(primary, f"{_humanize_label(name)} geometry", path, "rendered geometry evidence for reader inspection.")
    for artifact, description in (
        ("benchmark_evidence.json", "compiled fail-closed benchmark evidence gates for this bundle."),
        ("nuclear_data_provenance.json", "OpenMC nuclear-data library provenance and path/hash evidence."),
        ("source_convergence_diagnostics.json", "OpenMC batch, particle, statepoint, and source convergence diagnostics."),
        ("cross_code_comparison.json", "OpenMC residuals against declared Serpent/SCALE-style references."),
        ("uncertainty_budget.json", "geometry/material uncertainty propagation budget."),
    ):
        path = summary_path.parent / artifact
        if path.exists():
            _append_artifact(primary, _humanize_label(artifact), path, description)
    if benchmark:
        primary.append(
            "- Benchmark context: this report - source assumptions, references, traceability, and evidence trail."
        )
        reference_count = len(benchmark.get("references", []))
        target_count = len(benchmark.get("targets", {}))
        if reference_count or target_count:
            appendix.append(
                "- Benchmark metadata: embedded benchmark input - "
                f"{reference_count} reference note(s), {target_count} target definition(s)."
            )

    if provenance:
        for label, payload in (
            ("Case input provenance", provenance.get("case", {})),
            ("Benchmark input provenance", provenance.get("benchmark", {})),
        ):
            if isinstance(payload, dict):
                origin = _first_available(payload.get("origin_path"), payload.get("source"))
                _append_artifact(appendix, label, origin, "source input used to assemble this result bundle.")

    seen_paths = {str(summary_path)}
    if validation_path:
        seen_paths.add(str(validation_path))
    for _, path in _iter_summary_artifact_paths(summary):
        path_text = str(path)
        if path_text in seen_paths:
            continue
        seen_paths.add(path_text)
        _append_artifact(
            appendix,
            _humanize_label(path_text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]),
            path,
            "raw generated artifact referenced by the run summary.",
        )
        if len(appendix) >= 10:
            appendix.append("- Additional raw artifacts: see the run summary JSON for the complete path list.")
            break

    lines = [
        "## Start Here",
        "",
        "- Open first: this report, then the run summary JSON; use the primary evidence below before digging into raw appendices.",
        "",
        "### Primary Evidence",
        "",
        *primary,
    ]
    if appendix:
        lines.extend(["", "### Appendix / Raw Artifacts", "", *appendix])
    lines.append("")
    return lines


def _is_primary_plot(plot_id: str, entry: dict[str, Any]) -> bool:
    if isinstance(entry, dict) and entry:
        if entry.get("report_section") != "primary":
            return False
        units = entry.get("units", {})
        if isinstance(units, dict) and any("mixed" in str(value).lower() for value in units.values()):
            return False
        return True
    return plot_id not in {"metrics_overview", "bop_balance", "transient_redox_state", "transient_fissile_inventory"}


def _build_stage_manifest_lines(manifest_path: Path) -> list[str]:
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except JSONDecodeError:
        return []
    stages = manifest.get("stages", [])
    if not isinstance(stages, list) or not stages:
        return []
    lines = ["## Stage Manifest", ""]
    lines.append("- Bundle provenance: ordered suite-level stage manifest; artifacts may come from multiple commands.")
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        command = " ".join([*(stage.get("command") or []), *(stage.get("args") or [])]).strip()
        output_count = len(stage.get("output_artifacts", []) or [])
        lines.append(
            f"- Stage `{stage.get('sequence', '?')}` `{stage.get('stage', 'stage')}`: "
            f"status=`{stage.get('status', 'unknown')}`, method=`{stage.get('method_tier', 'unspecified')}`, "
            f"artifacts=`{output_count}`, command=`{command}`"
        )
    lines.append("")
    return lines


def _build_evidence_status_lines(
    config: dict[str, Any],
    summary: dict[str, Any],
    benchmark_traceability: dict[str, Any],
    benchmark_quality: dict[str, Any],
) -> list[str]:
    neutronics_status = _neutronics_status(summary)
    solver_backed = _is_solver_backed_neutronics(summary)
    benchmark_residuals = summary.get("benchmark_residuals", {})
    model_representation = summary.get("model_representation", config.get("model_representation", {}))
    proxy_modes = [
        f"{key}={value}"
        for key, value in (model_representation.items() if isinstance(model_representation, dict) else [])
        if "proxy" in str(value).lower() or "dry" in str(value).lower()
    ]

    lines = ["## Evidence Status", ""]
    if solver_backed:
        lines.append("- Neutronics evidence: `solver-backed OpenMC result`")
    else:
        lines.append(
            f"- Neutronics evidence: `{neutronics_status}` dry-run/proxy; "
            "not a solver-backed OpenMC physics result."
        )
    artifact_status = summary.get("artifact_status", {})
    artifact_blocker_count = 0
    if isinstance(artifact_status, dict):
        groups = artifact_status.get("groups", {})
        openmc_status = groups.get("openmc", {}) if isinstance(groups, dict) else {}
        if isinstance(openmc_status, dict):
            for blocker in openmc_status.get("blockers", [])[:3]:
                lines.append(f"- OpenMC artifact blocker: {blocker}")
                artifact_blocker_count += 1
    if not solver_backed and artifact_blocker_count == 0:
        lines.append("- OpenMC artifact blocker: no solver-backed OpenMC statepoint artifact is recorded.")
    if proxy_modes:
        lines.append(f"- Proxy model modes: `{', '.join(proxy_modes)}`")
    if isinstance(benchmark_residuals, dict) and benchmark_residuals:
        lines.append(f"- Benchmark residual status: `{benchmark_residuals.get('status', 'unknown')}`")
        for blocker in benchmark_residuals.get("blockers", [])[:3]:
            lines.append(f"- Benchmark blocker: {blocker}")
    if benchmark_quality:
        readiness = "ready" if benchmark_quality.get("benchmark_ready") is True else "not ready"
        lines.append(f"- Benchmark readiness: `{readiness}`")
        if benchmark_quality.get("quality_stage"):
            lines.append(f"- Benchmark quality stage: `{benchmark_quality.get('quality_stage')}`")
    scale_alignment = benchmark_traceability.get("scale_alignment", {})
    if isinstance(scale_alignment, dict) and scale_alignment.get("status") == "scale_mismatch":
        lines.append(f"- Scale/surrogate mismatch: {scale_alignment.get('message')}")
    lines.append("")
    return lines


def _build_key_metrics_lines(
    config: dict[str, Any],
    summary: dict[str, Any],
    benchmark_traceability: dict[str, Any],
    validation_maturity: dict[str, Any],
) -> list[str]:
    reactor = config.get("reactor", {})
    metrics = summary.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    neutronics = summary.get("neutronics", {})
    bop = summary.get("bop", {})
    flow = summary.get("flow", {}).get("reduced_order", {})
    active_flow = flow.get("active_flow", {}) if isinstance(flow, dict) else {}
    primary_system = summary.get("primary_system", {})
    heat_exchanger = primary_system.get("heat_exchanger", {}) if isinstance(primary_system, dict) else {}
    fuel_cycle = summary.get("fuel_cycle", {})
    uncertainty_sweep = summary.get("uncertainty_sweep", {})
    transient = summary.get("transient", {})
    transient_sweep = summary.get("transient_sweep", {})
    finance = summary.get("finance", {})
    finance_outputs = finance.get("outputs", {}) if isinstance(finance, dict) else {}
    schedule = summary.get("schedule", {})

    metric_lines: list[str] = []
    _append_value_line(metric_lines, "Design thermal power", reactor.get("design_power_mwth"), "MWth")
    _append_value_line(metric_lines, "Neutronics status", neutronics.get("status") if isinstance(neutronics, dict) else None)
    if not _is_solver_backed_neutronics(summary):
        metric_lines.append(
            "- Neutronics metric evidence scope: `dry-run/proxy diagnostics; no solver-backed OpenMC result`"
        )
    _append_value_line(
        metric_lines,
        "Effective multiplication factor",
        _first_available(metrics.get("keff"), uncertainty_sweep.get("nominal_keff") if isinstance(uncertainty_sweep, dict) else None),
        sig_digits=6,
    )
    _append_value_line(
        metric_lines,
        "Benchmark residual",
        uncertainty_sweep.get("nominal_residual_pcm") if isinstance(uncertainty_sweep, dict) else None,
        "pcm",
    )
    _append_value_line(
        metric_lines,
        "Combined benchmark uncertainty",
        uncertainty_sweep.get("combined_uncertainty_pcm") if isinstance(uncertainty_sweep, dict) else None,
        "pcm",
    )
    _append_value_line(
        metric_lines,
        "Traceability score",
        _first_available(metrics.get("benchmark_traceability_score"), benchmark_traceability.get("traceability_score")),
    )
    _append_value_line(
        metric_lines,
        "Validation maturity score",
        _first_available(metrics.get("validation_maturity_score"), validation_maturity.get("validation_maturity_score")),
    )
    _append_value_line(metric_lines, "Channel count", metrics.get("channel_count"))
    _append_value_line(
        metric_lines,
        "Active flow channels",
        _first_available(metrics.get("active_flow_channel_count"), active_flow.get("channel_count") if isinstance(active_flow, dict) else None),
    )
    _append_value_line(metric_lines, "Thermal power", bop.get("thermal_power_mw") if isinstance(bop, dict) else None, "MW")
    _append_value_line(metric_lines, "Electric power", bop.get("electric_power_mw") if isinstance(bop, dict) else None, "MW")
    _append_value_line(
        metric_lines,
        "Primary mass flow",
        bop.get("primary_mass_flow_kg_s") if isinstance(bop, dict) else None,
        "kg/s",
    )
    _append_value_line(
        metric_lines,
        "Representative salt velocity",
        active_flow.get("representative_velocity_m_s") if isinstance(active_flow, dict) else None,
        "m/s",
    )
    _append_value_line(
        metric_lines,
        "Representative residence time",
        active_flow.get("representative_residence_time_s") if isinstance(active_flow, dict) else None,
        "s",
    )
    _append_value_line(
        metric_lines,
        "Heat exchanger duty",
        heat_exchanger.get("duty_mw") if isinstance(heat_exchanger, dict) else None,
        "MW",
    )
    _append_value_line(
        metric_lines,
        "Heat exchanger area",
        heat_exchanger.get("required_area_m2") if isinstance(heat_exchanger, dict) else None,
        "m2",
    )
    _append_value_line(
        metric_lines,
        "Heavy metal inventory",
        fuel_cycle.get("heavy_metal_inventory_kg") if isinstance(fuel_cycle, dict) else None,
        "kg",
    )
    _append_value_line(
        metric_lines,
        "Fissile inventory",
        fuel_cycle.get("fissile_inventory_kg") if isinstance(fuel_cycle, dict) else None,
        "kg",
    )
    _append_value_line(
        metric_lines,
        "Specific power",
        fuel_cycle.get("specific_power_mw_per_t_hm") if isinstance(fuel_cycle, dict) else None,
        "MW/tHM",
    )
    _append_value_line(
        metric_lines,
        "Peak power fraction",
        transient.get("peak_power_fraction") if isinstance(transient, dict) else None,
    )
    _append_value_line(
        metric_lines,
        "Peak fuel temperature",
        transient.get("peak_fuel_temperature_c") if isinstance(transient, dict) else None,
        "C",
    )
    _append_value_line(
        metric_lines,
        "Transient peak power p95",
        _first_available(
            metrics.get("transient_sweep_peak_power_fraction_p95"),
            transient_sweep.get("peak_power_fraction_p95") if isinstance(transient_sweep, dict) else None,
        ),
    )
    _append_value_line(
        metric_lines,
        "Transient peak fuel temperature p95",
        transient_sweep.get("peak_fuel_temperature_c_p95") if isinstance(transient_sweep, dict) else None,
        "C",
    )
    _append_value_line(
        metric_lines,
        "LCOE",
        _first_available(metrics.get("finance_lcoe_usd_per_mwh"), finance_outputs.get("lcoe_usd_per_mwh")),
        "USD/MWh",
    )
    _append_value_line(
        metric_lines,
        "Commercial operation date",
        schedule.get("commercial_operation_date") if isinstance(schedule, dict) else None,
    )

    lines = ["## Key Metrics", ""]
    if metric_lines:
        lines.extend(metric_lines)
    else:
        lines.append("- No curated metrics were reported for this run.")
    lines.append("")
    return lines


def _build_additional_metrics_lines(summary: dict[str, Any]) -> list[str]:
    metrics = summary.get("metrics", {})
    if not isinstance(metrics, dict):
        return []

    metric_lines: list[str] = []
    for key in sorted(metrics):
        if key in _CURATED_SUMMARY_METRIC_KEYS:
            continue
        value = metrics[key]
        if not _has_reader_value(value):
            continue
        label, unit = _metric_label_and_unit(key)
        formatted = _format_metric_value(value, unit)
        if formatted:
            metric_lines.append(f"- {label}: `{formatted}`")

    if not metric_lines:
        return []
    return ["## Additional Metrics", "", *metric_lines, ""]


def _metric_label_and_unit(key: str) -> tuple[str, str | None]:
    label_key = key
    unit = None
    for suffix, candidate_unit in _METRIC_UNIT_SUFFIXES:
        if key.endswith(suffix):
            label_key = key.removesuffix(suffix)
            unit = candidate_unit
            break
    return _humanize_label(label_key), unit


def _format_metric_value(value: Any, unit: str | None) -> str:
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(_has_reader_value(item) for item in value):
        formatted = f"{_format_report_value(value[0])} to {_format_report_value(value[1])}"
        return f"{formatted} {unit}" if unit else formatted
    return _format_report_value(value, unit)


def _append_artifact(lines: list[str], label: str, path: Any, description: str) -> None:
    if _has_reader_value(path):
        lines.append(f"- {label}: `{path}` - {description}")


def _append_value_line(
    lines: list[str],
    label: str,
    value: Any,
    unit: str | None = None,
    *,
    sig_digits: int = 5,
) -> None:
    if _has_reader_value(value):
        lines.append(f"- {label}: `{_format_report_value(value, unit, sig_digits=sig_digits)}`")


def _format_report_value(value: Any, unit: str | None = None, *, sig_digits: int = 5) -> str:
    if isinstance(value, bool):
        formatted = "true" if value else "false"
    elif isinstance(value, int):
        formatted = str(value)
    elif isinstance(value, float):
        formatted = _format_float(value, sig_digits=sig_digits)
    elif isinstance(value, (list, tuple)):
        formatted = ", ".join(_format_report_value(item, sig_digits=sig_digits) for item in value if _has_reader_value(item))
    elif isinstance(value, dict):
        formatted = ", ".join(
            f"{_humanize_label(str(key))}={_format_report_value(item, sig_digits=sig_digits)}"
            for key, item in value.items()
            if _has_reader_value(item)
        )
    else:
        formatted = str(value)
    return f"{formatted} {unit}" if unit else formatted


def _format_float(value: float, *, sig_digits: int = 5) -> str:
    if not math.isfinite(value):
        return str(value)
    if value == 0:
        return "0"
    abs_value = abs(value)
    if abs_value >= 1_000_000 or abs_value < 1e-6:
        exponent = int(math.floor(math.log10(abs_value) / 3) * 3)
        mantissa = value / (10**exponent)
        return f"{mantissa:.{sig_digits}g}e{exponent:+d}"
    return f"{value:.{sig_digits}g}"


def _has_reader_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _MISSING_CODE_VALUES
    if isinstance(value, (list, tuple, set)):
        return any(_has_reader_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_reader_value(item) for item in value.values())
    return True


def _first_available(*values: Any) -> Any:
    for value in values:
        if _has_reader_value(value):
            return value
    return None


def _neutronics_status(summary: dict[str, Any]) -> str:
    neutronics = summary.get("neutronics", {})
    if isinstance(neutronics, dict) and _has_reader_value(neutronics.get("status")):
        return str(neutronics["status"])
    return "unknown"


def _is_solver_backed_neutronics(summary: dict[str, Any]) -> bool:
    return _neutronics_status(summary).lower() == "completed"


def _humanize_label(name: str) -> str:
    words = re.sub(r"[_\-]+", " ", str(name)).strip().split()
    if not words:
        return "Artifact"
    rendered = [_LABEL_ACRONYMS.get(word.lower(), word) for word in words]
    label = " ".join(rendered)
    return label[:1].upper() + label[1:]


def _iter_summary_artifact_paths(summary: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    artifacts: list[tuple[str, Any]] = []
    for key, value in summary.items():
        path = (*prefix, str(key))
        if key.endswith("_path") and _has_reader_value(value):
            artifacts.append((" ".join(path), value))
        elif isinstance(value, dict):
            artifacts.extend(_iter_summary_artifact_paths(value, path))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    artifacts.extend(_iter_summary_artifact_paths(item, (*path, str(index))))
    return artifacts


def _render_report_lines(lines: list[str]) -> str:
    rendered: list[str] = []
    previous_blank = False
    for line in lines:
        cleaned = _clean_missing_line(line)
        if cleaned is None:
            continue
        if _should_omit_missing_line(cleaned):
            continue
        formatted = _format_numeric_code_literals(cleaned)
        is_blank = formatted == ""
        if is_blank and previous_blank:
            continue
        rendered.append(formatted)
        previous_blank = is_blank
    return "\n".join(rendered).rstrip() + "\n"


def _should_omit_missing_line(line: str) -> bool:
    if not line.lstrip().startswith("- "):
        return False
    code_literals = _CODE_LITERAL_RE.findall(line)
    return bool(code_literals) and all(_is_missing_code_value(token) for token in code_literals)


def _clean_missing_line(line: str) -> str | None:
    if not line.lstrip().startswith("- ") or not _MISSING_CODE_LITERAL_RE.search(line):
        return line

    cleaned = _remove_missing_assignments(line)
    cleaned = _remove_missing_code_context(cleaned)
    cleaned = _tidy_missing_cleanup(cleaned)
    if (
        not cleaned
        or _should_omit_missing_line(cleaned)
        or _MISSING_CODE_LITERAL_RE.search(cleaned)
        or _is_label_only_bullet(cleaned)
    ):
        return None
    return cleaned


def _is_missing_code_value(value: str) -> bool:
    return value.strip().lower() in _MISSING_CODE_VALUES


def _remove_missing_assignments(line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        return "" if "," in prefix else prefix

    return re.sub(
        rf"(?P<prefix>,\s*|\s+)[A-Za-z][A-Za-z0-9_\- /]*=\s*{_MISSING_CODE_LITERAL_RE.pattern}",
        replace,
        line,
        flags=re.IGNORECASE,
    )


def _remove_missing_code_context(line: str) -> str:
    missing = _MISSING_CODE_LITERAL_RE.pattern
    cleaned = re.sub(rf"\s+from\s+{missing}", "", line, flags=re.IGNORECASE)
    cleaned = re.sub(rf"\s*/\s*{missing}(?:\s+[A-Za-z][A-Za-z0-9/%.\-]*)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf"{missing}(?:\s+[A-Za-z][A-Za-z0-9/%.\-]*)?\s*/\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf"\s*\(\s*{missing}(?:\s+[A-Za-z][A-Za-z0-9/%.\-]*)?\s*\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf"{missing}(?:\s+[A-Za-z][A-Za-z0-9/%.\-]*)?", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _tidy_missing_cleanup(line: str) -> str:
    cleaned = re.sub(r"\(\s*\)", "", line)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    cleaned = re.sub(r":\s*,\s*", ": ", cleaned)
    cleaned = re.sub(r",\s*\)", ")", cleaned)
    cleaned = re.sub(r"\(\s*,\s*", "(", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = cleaned.rstrip(" ,:/")
    return cleaned.strip()


def _is_label_only_bullet(line: str) -> bool:
    if not line.lstrip().startswith("- "):
        return False
    body = line.lstrip()[2:].strip()
    if ":" in body:
        return not body.split(":", 1)[1].strip()
    return "`" not in body and not re.search(r"[\d([]", body)


def _format_numeric_code_literals(line: str) -> str:
    lower_line = line.lower()
    sig_digits = 6 if "keff" in lower_line or "multiplication factor" in lower_line else 5

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if "." not in token and "e" not in token.lower():
            return f"`{int(token)}`"
        return f"`{_format_float(float(token), sig_digits=sig_digits)}`"

    formatted = _NUMERIC_CODE_RE.sub(replace, line)

    def replace_composite(match: re.Match[str]) -> str:
        token = match.group(1)
        if not _NUMERIC_COMPOSITE_RE.fullmatch(token):
            return match.group(0)
        return "`" + _NUMERIC_TOKEN_RE.sub(lambda item: _format_float(float(item.group(0)), sig_digits=sig_digits), token) + "`"

    return _CODE_LITERAL_RE.sub(replace_composite, formatted)


def _classify_reactor_case(
    case_name: str,
    config: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    benchmark_quality: dict[str, Any] | None = None,
) -> dict[str, str]:
    reactor = config.get("reactor", {})
    mode = str(reactor.get("mode", "modern_test_reactor"))
    family = str(reactor.get("family", "")).lower()
    stage = str(reactor.get("stage", "")).lower()
    summary = summary or {}
    benchmark_quality = benchmark_quality or {}
    solver_backed = _is_solver_backed_neutronics(summary)
    benchmark_ready = benchmark_quality.get("benchmark_ready") is True if benchmark_quality else False
    evidence_limited = not solver_backed or not benchmark_ready
    design_readiness = summary.get("design_readiness", {}) if isinstance(summary.get("design_readiness"), dict) else {}
    severe_screening = int(design_readiness.get("severe_finding_count", 0) or 0) > 0
    if case_name == "example_pin":
        return {
            "role": "smoke/regression pin",
            "build_candidate": "false",
            "commercial_finance_subject": "false",
            "description": "PWR-inspired neutronics smoke case, not a molten-salt reactor build target.",
        }
    if case_name == "fuel_channel" or "channel" in stage:
        return {
            "role": "TMSR channel submodel",
            "build_candidate": "false",
            "commercial_finance_subject": "false",
            "description": "Fuel-channel submodel for geometry and reaction-rate plumbing.",
        }
    if case_name.startswith("msre_") or mode == "historic_benchmark":
        return {
            "role": "historic MSRE benchmark harness",
            "build_candidate": "false",
            "commercial_finance_subject": "false",
            "description": "Validation anchor for historical molten-salt reactor behavior, not a build candidate.",
        }
    if case_name == "flagship_grid_msr" or mode == "commercial_grid":
        if evidence_limited or severe_screening:
            blockers: list[str] = []
            if not solver_backed:
                blockers.append("dry-run/proxy neutronics")
            if not benchmark_ready:
                blockers.append("benchmark gates not ready")
            if severe_screening:
                blockers.append("severe engineering screening findings")
            blocker_text = " and ".join(blockers)
            return {
                "role": "commercial flagship grid reactor planning case",
                "build_candidate": "blocked_by_evidence",
                "commercial_finance_subject": "planning_only",
                "description": (
                    "End-goal cost, schedule, grid, and plant-planning case; not build-ready or "
                    f"commercially validated while {blocker_text} remain."
                ),
            }
        return {
            "role": "commercial flagship grid reactor",
            "build_candidate": "true",
            "commercial_finance_subject": "true",
            "description": "End-goal reactor case with solver-backed neutronics and ready benchmark gates.",
        }
    if "immersed" in family:
        return {
            "role": "research/demonstrator primary-system reference",
            "build_candidate": "false",
            "commercial_finance_subject": "false",
            "description": "Research-style primary-system demonstrator with richer loop and render behavior.",
        }
    if "tmsr" in family:
        return {
            "role": "modern test-reactor surrogate",
            "build_candidate": "false",
            "commercial_finance_subject": "false",
            "description": "TMSR-LF1-inspired validation bridge and scale-up surrogate.",
        }
    return {
        "role": "generic reactor model",
        "build_candidate": "false",
        "commercial_finance_subject": "false",
        "description": "General repository case without commercial plant-planning status.",
    }
