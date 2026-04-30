from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from thorium_reactor.benchmarking import assess_benchmark_traceability


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
    validation = {}
    benchmark = benchmark or {}
    benchmark_traceability = summary.get("benchmark_traceability") or (assess_benchmark_traceability(config, benchmark) if benchmark else {})
    if validation_path and validation_path.exists():
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            validation = {"checks": [], "passed": False}
    model_representation = summary.get("model_representation", config.get("model_representation", {}))
    model_validity = summary.get("model_validity", {})
    validation_maturity = summary.get("validation_maturity") or benchmark_traceability.get("validation_maturity", {})

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
        "## Reactor Summary",
        "",
        f"- Design thermal power (MWth): `{config['reactor'].get('design_power_mwth', 'n/a')}`",
        f"- Benchmark source: `{config['reactor'].get('benchmark', 'n/a')}`",
        "",
    ]

    if model_validity:
        lines.extend(
            [
                f"> Model validity: `{model_validity.get('status', 'unknown')}`",
                f"> Failed checks: `{model_validity.get('failed_count', 0)}`",
                "",
            ]
        )

    if provenance:
        case_provenance = provenance.get("case", {})
        benchmark_provenance = provenance.get("benchmark", {})
        lines.extend(
            [
                "## Input Provenance",
                "",
                f"- Case definition: `{case_provenance.get('source', 'unknown')}`",
                f"- Case origin path: `{case_provenance.get('origin_path', 'n/a')}`",
                f"- Benchmark metadata: `{benchmark_provenance.get('source', 'unknown')}`",
                f"- Benchmark origin path: `{benchmark_provenance.get('origin_path', 'n/a')}`",
                "",
            ]
        )

    runtime_context = summary.get("runtime_context", {})
    if runtime_context:
        lines.extend(
            [
                "## Runtime Context",
                "",
                f"- Service: `{runtime_context.get('service', 'host')}`",
                f"- Image: `{runtime_context.get('image', 'n/a')}`",
                f"- Tool runtime: `{runtime_context.get('tool_runtime', 'n/a')}`",
                f"- Git branch: `{runtime_context.get('git_branch', 'n/a')}`",
                f"- Git commit: `{runtime_context.get('git_commit', 'n/a')}`",
                "",
            ]
        )

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

    lines.extend(
        [
            "## Key Metrics",
            "",
        ]
    )

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

    for key, value in summary.get("metrics", {}).items():
        lines.append(f"- {key}: `{value}`")

    if "bop" in summary:
        lines.extend(["", "## Balance Of Plant", ""])
        for key, value in summary["bop"].items():
            lines.append(f"- {key}: `{value}`")

    reduced_order_flow = summary.get("flow", {}).get("reduced_order", {})
    if reduced_order_flow:
        active_flow = reduced_order_flow.get("active_flow", {})
        disconnected_inventory = reduced_order_flow.get("disconnected_inventory", {})
        lines.extend(["", "## Reduced-Order Flow", ""])
        lines.append(f"- Allocation rule: `{reduced_order_flow.get('allocation_rule', 'n/a')}`")
        lines.append(f"- Salt bulk temperature (C): `{reduced_order_flow.get('salt_bulk_temperature_c', 'n/a')}`")
        lines.append(f"- Salt density (kg/m3): `{reduced_order_flow.get('salt_density_kg_m3', 'n/a')}`")
        lines.append(f"- Active through-flow channels: `{active_flow.get('channel_count', 'n/a')}`")
        lines.append(f"- Active flow area (cm2): `{active_flow.get('total_flow_area_cm2', 'n/a')}`")
        lines.append(f"- Representative velocity (m/s): `{active_flow.get('representative_velocity_m_s', 'n/a')}`")
        lines.append(f"- Representative residence time (s): `{active_flow.get('representative_residence_time_s', 'n/a')}`")
        lines.append(f"- Disconnected salt inventory channels: `{disconnected_inventory.get('channel_count', 'n/a')}`")

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

    benchmark_residuals = summary.get("benchmark_residuals", {})
    if benchmark_residuals:
        lines.extend(["", "## Benchmark Residuals", ""])
        lines.append(f"- Residual item count: `{benchmark_residuals.get('item_count', 0)}`")
        lines.append(f"- Dataset count: `{benchmark_residuals.get('dataset_count', 0)}`")
        for item in benchmark_residuals.get("items", []):
            lines.append(
                f"- `{item.get('name', 'target')}`: "
                f"metric=`{item.get('metric', 'n/a')}`, "
                f"status=`{item.get('status', 'pending')}`, "
                f"residual=`{item.get('residual', 'n/a')}`"
            )

    if validation:
        lines.extend(["", "## Validation", ""])
        if model_validity:
            lines.append(f"- Model validity: `{model_validity.get('status', 'unknown')}`")
        for check in validation.get("checks", []):
            lines.append(
                f"- {check['name']}: `{check['status']}`"
                + (f" ({check['message']})" if check.get("message") else "")
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

    if benchmark.get("evidence"):
        lines.extend(["", "## Evidence Trail", ""])
        for item in benchmark_traceability.get("evidence", []):
            lines.append(f"- {item.get('topic', 'evidence')}: {item.get('claim', 'n/a')}")
            if item.get("source"):
                lines.append(f"- Source: `{item['source']}`")
            if item.get("confidence"):
                lines.append(f"- Confidence: `{item['confidence']}`")
            if item.get("relevance"):
                lines.append(f"- Why it matters here: {item['relevance']}")

    if benchmark.get("novelty_tracks"):
        lines.extend(["", "## Novelty Tracks", ""])
        for track in benchmark["novelty_tracks"]:
            lines.append(f"- {track.get('name', 'untitled')}: {track.get('summary', '')}")

    if geometry_assets:
        lines.extend(["", "## Geometry Outputs", ""])
        for name, path in geometry_assets.items():
            lines.append(f"- {name}: `{path}`")

    if plot_assets:
        lines.extend(["", "## Plot Outputs", ""])
        for name, path in plot_assets.items():
            lines.append(f"- {name}: `{path}`")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This report is generated from the config-driven reactor workflow.",
            "- Validation targets can mix literature-derived bounds with explicitly labeled surrogate assumptions.",
        ]
    )

    return "\n".join(lines) + "\n"
