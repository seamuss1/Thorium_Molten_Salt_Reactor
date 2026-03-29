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
    benchmark_traceability = assess_benchmark_traceability(config, benchmark) if benchmark else {}
    if validation_path and validation_path.exists():
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            validation = {"checks": [], "passed": False}

    lines = [
        f"# {config['reactor']['name']}",
        "",
        f"- Case: `{case_name}`",
        f"- Family: `{config['reactor']['family']}`",
        f"- Stage: `{config['reactor']['stage']}`",
        f"- Result bundle: `{summary.get('result_dir', '')}`",
        f"- Neutronics status: `{summary.get('neutronics', {}).get('status', 'unknown')}`",
        "",
        "## Reactor Summary",
        "",
        f"- Design thermal power (MWth): `{config['reactor'].get('design_power_mwth', 'n/a')}`",
        f"- Benchmark source: `{config['reactor'].get('benchmark', 'n/a')}`",
        "",
    ]

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
        for gap in benchmark_traceability.get("gaps", []):
            lines.append(f"- Gap: {gap}")

    lines.extend(
        [
            "## Key Metrics",
            "",
        ]
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

    fuel_cycle = summary.get("fuel_cycle", {})
    if fuel_cycle:
        lines.extend(["", "## Fuel Cycle Assumptions", ""])
        lines.append(f"- Heavy metal inventory (kg): `{fuel_cycle.get('heavy_metal_inventory_kg', 'n/a')}`")
        lines.append(f"- Fissile inventory (kg): `{fuel_cycle.get('fissile_inventory_kg', 'n/a')}`")
        lines.append(f"- Specific power (MW/tHM): `{fuel_cycle.get('specific_power_mw_per_t_hm', 'n/a')}`")
        lines.append(f"- Cleanup turnover (days): `{fuel_cycle.get('cleanup_turnover_days', 'n/a')}`")
        lines.append(f"- Cleanup removal efficiency: `{fuel_cycle.get('cleanup_removal_efficiency', 'n/a')}`")
        lines.append(f"- Xenon generation rate (atoms/s): `{fuel_cycle.get('xenon_generation_rate_atoms_s', 'n/a')}`")
        lines.append(f"- Xenon removal fraction: `{fuel_cycle.get('xenon_removal_fraction', 'n/a')}`")
        lines.append(f"- Protactinium holdup (days): `{fuel_cycle.get('protactinium_holdup_days', 'n/a')}`")

    if validation:
        lines.extend(["", "## Validation", ""])
        for check in validation.get("checks", []):
            lines.append(
                f"- {check['name']}: `{check['status']}`"
                + (f" ({check['message']})" if check.get("message") else "")
            )

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
