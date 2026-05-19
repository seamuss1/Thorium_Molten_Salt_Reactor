from __future__ import annotations

import json
import math
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from thorium_reactor.neutronics.openmc_compat import openmc

FIGURE_CATALOG_SCHEMA_VERSION = 2

_DEFAULT_FIGURE_METADATA: dict[str, Any] = {
    "title": "Generated plot",
    "caption": "Generated reporting figure.",
    "quality_status": "diagnostic_only",
    "status": "available",
    "method_tier": "summary_derived_visual",
    "report_section": "appendix",
    "axes": {},
    "units": {},
    "conclusion": "Review alongside the source report data before using in presentation material.",
}

_FIGURE_METADATA: dict[str, dict[str, Any]] = {
    "metrics_overview": {
        "title": "Metrics overview",
        "caption": "Bar chart of numeric top-level run metrics from the summary payload.",
        "quality_status": "appendix_only",
        "status": "available_appendix_only",
        "method_tier": "mixed_unit_summary_diagnostic",
        "report_section": "appendix",
        "axes": {"x": "Metric", "y": "Reported value"},
        "units": {"y": "mixed"},
        "conclusion": "Appendix diagnostic only because reported metrics use incompatible units.",
    },
    "bop_balance": {
        "title": "Balance of plant overview",
        "caption": "Numeric balance-of-plant outputs plotted as a compact comparison chart.",
        "quality_status": "appendix_only",
        "status": "available_appendix_only",
        "method_tier": "mixed_unit_summary_diagnostic",
        "report_section": "appendix",
        "axes": {"x": "BOP metric", "y": "Reported value"},
        "units": {"y": "mixed, commonly MW"},
        "conclusion": "Appendix diagnostic only unless replotted as single-unit quantities.",
    },
    "finance_cost_waterfall": {
        "title": "Capital cost breakdown",
        "caption": "Capital cost components scaled from USD into billions of USD.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Cost component", "y": "Capitalized cost"},
        "units": {"y": "B USD"},
        "conclusion": "Use as the primary cost-driver figure for completed finance runs.",
    },
    "finance_annual_cost_stack": {
        "title": "Annualized cost stack",
        "caption": "Annual operating and financing cost components scaled into millions of USD per year.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Annual cost component", "y": "Annualized cost"},
        "units": {"y": "M USD/yr"},
        "conclusion": "Use to compare annual cost contributors after finance completion.",
    },
    "finance_construction_cash_flow": {
        "title": "Construction cash flow",
        "caption": "Cumulative capitalized construction cost by project month.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Construction month", "y": "Capitalized cost"},
        "units": {"x": "month", "y": "B USD"},
        "conclusion": "Use to show when capital is committed across the construction schedule.",
    },
    "project_schedule_gantt": {
        "title": "Project schedule",
        "caption": "Planning-grade Gantt view from project start through commercial operation.",
        "quality_status": "appendix_only",
        "report_section": "appendix",
        "axes": {"x": "Calendar year", "y": "Project phase"},
        "units": {"x": "year", "bar": "months"},
        "conclusion": "Use as appendix context unless the schedule basis has been independently reviewed.",
    },
    "flow_interfaces": {
        "title": "Flow interface channel counts",
        "caption": "Counts channels by plenum and reflector interface categories.",
        "quality_status": "diagnostic_only",
        "report_section": "appendix",
        "axes": {"x": "Interface metric", "y": "Channel count"},
        "units": {"y": "channels"},
        "conclusion": "Use as a geometry connectivity diagnostic rather than a final performance claim.",
    },
    "active_flow_allocation": {
        "title": "Active through-flow allocation",
        "caption": "Reduced-order flow allocation by channel or component variant.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Variant", "y": "Allocated mass flow"},
        "units": {"y": "kg/s"},
        "conclusion": "Use to communicate how active coolant flow is distributed in the reduced-order model.",
    },
    "keff_history": {
        "title": "k-effective history",
        "caption": "Generation-by-generation k-effective values loaded from the OpenMC statepoint.",
        "quality_status": "diagnostic_only",
        "report_section": "appendix",
        "axes": {"x": "Generation", "y": "k-effective"},
        "units": {"y": "dimensionless"},
        "conclusion": "Use to assess convergence behavior before presenting neutronics results.",
    },
    "benchmark_residuals": {
        "title": "Physics benchmark residuals",
        "caption": "Residuals between completed physics benchmark targets and solver-backed computed case values.",
        "quality_status": "publication_ready",
        "status": "available_solver_backed_only",
        "method_tier": "solver_backed_benchmark_residual",
        "report_section": "primary",
        "axes": {"x": "Benchmark target", "y": "Residual"},
        "units": {"y": "pcm"},
        "conclusion": "Use only when at least one completed physics residual is present; construction checks are excluded and blockers remain report-critical.",
        "blocker_importance": "high",
    },
    "transient_power": {
        "title": "Transient power fraction",
        "caption": "Time history of normalized power during the transient run.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Time", "y": "Power fraction"},
        "units": {"x": "s", "y": "dimensionless"},
        "conclusion": "Use as the primary transient response figure for power behavior.",
    },
    "transient_fuel_temperature": {
        "title": "Transient fuel temperature",
        "caption": "Time history of fuel temperature during the transient run.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Time", "y": "Fuel temperature"},
        "units": {"x": "s", "y": "C"},
        "conclusion": "Use as the primary transient thermal response figure.",
    },
    "transient_redox_state": {
        "title": "Transient redox state",
        "caption": "Time history of the modeled redox state during the transient run.",
        "quality_status": "appendix_only",
        "report_section": "appendix",
        "axes": {"x": "Time", "y": "Redox state"},
        "units": {"x": "s", "y": "eV"},
        "conclusion": "Use as supporting chemistry context when redox assumptions are in scope.",
    },
    "transient_fissile_inventory": {
        "title": "Transient fissile inventory",
        "caption": "Time history of normalized fissile inventory during the transient run.",
        "quality_status": "appendix_only",
        "report_section": "appendix",
        "axes": {"x": "Time", "y": "Fissile inventory fraction"},
        "units": {"x": "s", "y": "dimensionless"},
        "conclusion": "Use as appendix context for inventory sensitivity during transients.",
    },
    "transient_sweep_power_envelope": {
        "title": "Transient sweep power envelope",
        "caption": "Empirical stress-test ensemble percentiles showing p05, p50, and p95 power-fraction histories.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Time", "y": "Power fraction"},
        "units": {"x": "s", "y": "dimensionless"},
        "conclusion": "Use to present the median response and scenario-envelope spread; this is not calibrated UQ unless distributions are source-backed.",
    },
    "transient_sweep_fuel_temperature_envelope": {
        "title": "Transient sweep fuel temperature envelope",
        "caption": "Empirical stress-test ensemble percentiles showing p05, p50, and p95 fuel-temperature histories.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Time", "y": "Fuel temperature"},
        "units": {"x": "s", "y": "C"},
        "conclusion": "Use to present the median response and scenario-envelope spread; this is not calibrated UQ unless distributions are source-backed.",
    },
    "validation_summary": {
        "title": "Validation summary",
        "caption": "Counts validation checks by pass, fail, pending, and blocked status.",
        "quality_status": "publication_ready",
        "report_section": "primary",
        "axes": {"x": "Validation status", "y": "Check count"},
        "units": {"y": "checks"},
        "conclusion": "Use to summarize readiness; failures, pending checks, and blocked checks require reviewer attention.",
    },
}


def generate_summary_plots(bundle, summary: dict[str, Any]) -> dict[str, str]:
    bundle.plots_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[str, str] = {}
    remove_from_manifest: set[str] = set()

    numeric_metrics = _coerce_numeric_mapping(summary.get("metrics", {}))
    if numeric_metrics:
        metrics_path = bundle.plots_dir / "metrics_overview.svg"
        _write_bar_chart_svg(numeric_metrics, metrics_path, title=f"{summary['case']} metrics")
        assets["metrics_overview"] = str(metrics_path)

    bop_numeric = _coerce_numeric_mapping(summary.get("bop", {}))
    if bop_numeric:
        bop_path = bundle.plots_dir / "bop_balance.svg"
        _write_bar_chart_svg(bop_numeric, bop_path, title=f"{summary['case']} balance of plant")
        assets["bop_balance"] = str(bop_path)

    finance = summary.get("finance", {})
    if isinstance(finance, dict) and finance.get("status") == "completed":
        cost_breakdown = _coerce_numeric_mapping(finance.get("cost_breakdown_usd", {}))
        if cost_breakdown:
            cost_path = bundle.plots_dir / "finance_cost_waterfall.svg"
            _write_bar_chart_svg(
                {key: value / 1_000_000_000.0 for key, value in cost_breakdown.items()},
                cost_path,
                title=f"{summary['case']} capital cost breakdown (B USD)",
                palette=["#2563eb", "#b45309", "#15803d", "#0f766e", "#7c3aed", "#334155"],
            )
            assets["finance_cost_waterfall"] = str(cost_path)

        annual_costs = _coerce_numeric_mapping(finance.get("annual_costs_usd_per_year", {}))
        if annual_costs:
            annual_path = bundle.plots_dir / "finance_annual_cost_stack.svg"
            _write_bar_chart_svg(
                {key: value / 1_000_000.0 for key, value in annual_costs.items()},
                annual_path,
                title=f"{summary['case']} annualized cost stack (M USD/yr)",
                palette=["#1d4ed8", "#047857", "#b45309", "#7c3aed", "#475569"],
            )
            assets["finance_annual_cost_stack"] = str(annual_path)

        cash_flow_points = [
            (float(item["month"]), float(item["cumulative_capitalized_cost_usd"]) / 1_000_000_000.0)
            for item in finance.get("cash_flow", [])
            if isinstance(item, dict)
            and isinstance(item.get("month"), (int, float))
            and isinstance(item.get("cumulative_capitalized_cost_usd"), (int, float))
        ]
        if cash_flow_points:
            cash_flow_path = bundle.plots_dir / "finance_construction_cash_flow.svg"
            _write_xy_line_chart_svg(
                cash_flow_points,
                cash_flow_path,
                title=f"{summary['case']} construction cash flow (B USD)",
                x_label="Construction month",
                y_label="Capitalized cost (B USD)",
            )
            assets["finance_construction_cash_flow"] = str(cash_flow_path)

    schedule = summary.get("schedule", {})
    if isinstance(schedule, dict) and schedule.get("status") == "completed":
        phases = schedule.get("phases", [])
        if isinstance(phases, list) and phases:
            schedule_path = bundle.plots_dir / "project_schedule_gantt.svg"
            _write_schedule_gantt_svg(phases, schedule_path, title=f"{summary['case']} project schedule")
            assets["project_schedule_gantt"] = str(schedule_path)

    flow_metrics = summary.get("flow", {}).get("interface_metrics", {})
    flow_numeric = _coerce_numeric_mapping(
        {
            "plenum_connected_channels": flow_metrics.get("plenum_connected_channels"),
            "reflector_backed_channels": flow_metrics.get("reflector_backed_channels"),
            "plenum_connected_salt_bearing_channels": flow_metrics.get("plenum_connected_salt_bearing_channels"),
            "reflector_backed_salt_bearing_channels": flow_metrics.get("reflector_backed_salt_bearing_channels"),
        }
    )
    if flow_numeric:
        flow_path = bundle.plots_dir / "flow_interfaces.svg"
        _write_bar_chart_svg(flow_numeric, flow_path, title=f"{summary['case']} flow interface channel counts")
        assets["flow_interfaces"] = str(flow_path)

    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    allocation_metrics = {
        str(item["variant"]): float(item["allocated_mass_flow_kg_s"])
        for item in reduced_order.get("variant_summary", [])
    }
    if allocation_metrics:
        allocation_path = bundle.plots_dir / "active_flow_allocation.svg"
        _write_bar_chart_svg(
            allocation_metrics,
            allocation_path,
            title=f"{summary['case']} active through-flow allocation (kg/s)",
        )
        assets["active_flow_allocation"] = str(allocation_path)

    statepoint_path = _resolve_statepoint_path(bundle, summary)
    if statepoint_path is not None and openmc is not None:
        history_path = bundle.plots_dir / "keff_history.svg"
        if _write_keff_history_svg(statepoint_path, history_path):
            assets["keff_history"] = str(history_path)

    benchmark_residuals = summary.get("benchmark_residuals")
    if isinstance(benchmark_residuals, dict):
        residual_metrics: dict[str, float] = {}
        neutronics_status = _summary_neutronics_status(summary)
        for index, item in enumerate(benchmark_residuals.get("items", []), start=1):
            if not isinstance(item, dict) or not _is_completed_physics_residual(
                item,
                neutronics_status=neutronics_status,
            ):
                continue
            residual_value = item.get("residual_pcm")
            if isinstance(residual_value, (int, float)):
                residual_metrics[str(item.get("name", f"target_{index}"))] = float(residual_value)
        if residual_metrics:
            residual_path = bundle.plots_dir / "benchmark_residuals.svg"
            _write_bar_chart_svg(
                residual_metrics,
                residual_path,
                title=f"{summary['case']} physics benchmark residuals",
                palette=["#0f766e", "#1d4ed8", "#b45309", "#7c3aed"],
            )
            assets["benchmark_residuals"] = str(residual_path)
        else:
            remove_from_manifest.add("benchmark_residuals")
            residual_path = bundle.plots_dir / "benchmark_residuals.svg"
            if residual_path.exists():
                residual_path.unlink()

    transient = summary.get("transient", {})
    transient_path = _resolve_transient_path(bundle, transient)
    if transient_path is not None:
        try:
            transient_payload = json.loads(transient_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            transient_payload = {}
        history = transient_payload.get("history", [])
        if isinstance(history, list) and history:
            power_points = [
                (float(item["time_s"]), float(item["power_fraction"]))
                for item in history
                if isinstance(item, dict) and "time_s" in item and "power_fraction" in item
            ]
            if power_points:
                power_path = bundle.plots_dir / "transient_power.svg"
                _write_xy_line_chart_svg(
                    power_points,
                    power_path,
                    title=f"{summary['case']} transient power fraction",
                    x_label="Time (s)",
                    y_label="Power fraction",
                )
                assets["transient_power"] = str(power_path)
            fuel_points = [
                (float(item["time_s"]), float(item["fuel_temp_c"]))
                for item in history
                if isinstance(item, dict) and "time_s" in item and "fuel_temp_c" in item
            ]
            if fuel_points:
                fuel_path = bundle.plots_dir / "transient_fuel_temperature.svg"
                _write_xy_line_chart_svg(
                    fuel_points,
                    fuel_path,
                    title=f"{summary['case']} transient fuel temperature",
                    x_label="Time (s)",
                    y_label="Fuel temperature (C)",
                )
                assets["transient_fuel_temperature"] = str(fuel_path)
            redox_points = [
                (float(item["time_s"]), float(item["redox_state_ev"]))
                for item in history
                if isinstance(item, dict) and "time_s" in item and "redox_state_ev" in item
            ]
            if redox_points:
                redox_path = bundle.plots_dir / "transient_redox_state.svg"
                _write_xy_line_chart_svg(
                    redox_points,
                    redox_path,
                    title=f"{summary['case']} transient redox state",
                    x_label="Time (s)",
                    y_label="Redox state (eV)",
                )
                assets["transient_redox_state"] = str(redox_path)
            fissile_points = [
                (float(item["time_s"]), float(item["fissile_inventory_fraction"]))
                for item in history
                if isinstance(item, dict) and "time_s" in item and "fissile_inventory_fraction" in item
            ]
            if fissile_points:
                fissile_path = bundle.plots_dir / "transient_fissile_inventory.svg"
                _write_xy_line_chart_svg(
                    fissile_points,
                    fissile_path,
                    title=f"{summary['case']} transient fissile inventory",
                    x_label="Time (s)",
                    y_label="Fissile inventory fraction",
                )
                assets["transient_fissile_inventory"] = str(fissile_path)

    transient_sweep = summary.get("transient_sweep", {})
    transient_sweep_path = _resolve_transient_sweep_path(bundle, transient_sweep)
    if transient_sweep_path is not None:
        try:
            transient_sweep_payload = json.loads(transient_sweep_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            transient_sweep_payload = {}
        sweep_history = transient_sweep_payload.get("history", [])
        if isinstance(sweep_history, list) and sweep_history:
            power_low = [
                (float(item["time_s"]), float(item["power_fraction_p05"]))
                for item in sweep_history
                if isinstance(item, dict) and "time_s" in item and "power_fraction_p05" in item
            ]
            power_mid = [
                (float(item["time_s"]), float(item["power_fraction_p50"]))
                for item in sweep_history
                if isinstance(item, dict) and "time_s" in item and "power_fraction_p50" in item
            ]
            power_high = [
                (float(item["time_s"]), float(item["power_fraction_p95"]))
                for item in sweep_history
                if isinstance(item, dict) and "time_s" in item and "power_fraction_p95" in item
            ]
            if power_low and power_mid and power_high:
                power_envelope_path = bundle.plots_dir / "transient_sweep_power_envelope.svg"
                _write_uncertainty_band_chart_svg(
                    lower_points=power_low,
                    median_points=power_mid,
                    upper_points=power_high,
                    output_path=power_envelope_path,
                    title=f"{summary['case']} transient sweep power envelope",
                    x_label="Time (s)",
                    y_label="Power fraction",
                )
                assets["transient_sweep_power_envelope"] = str(power_envelope_path)

            fuel_low = [
                (float(item["time_s"]), float(item["fuel_temp_c_p05"]))
                for item in sweep_history
                if isinstance(item, dict) and "time_s" in item and "fuel_temp_c_p05" in item
            ]
            fuel_mid = [
                (float(item["time_s"]), float(item["fuel_temp_c_p50"]))
                for item in sweep_history
                if isinstance(item, dict) and "time_s" in item and "fuel_temp_c_p50" in item
            ]
            fuel_high = [
                (float(item["time_s"]), float(item["fuel_temp_c_p95"]))
                for item in sweep_history
                if isinstance(item, dict) and "time_s" in item and "fuel_temp_c_p95" in item
            ]
            if fuel_low and fuel_mid and fuel_high:
                fuel_envelope_path = bundle.plots_dir / "transient_sweep_fuel_temperature_envelope.svg"
                _write_uncertainty_band_chart_svg(
                    lower_points=fuel_low,
                    median_points=fuel_mid,
                    upper_points=fuel_high,
                    output_path=fuel_envelope_path,
                    title=f"{summary['case']} transient sweep fuel temperature envelope",
                    x_label="Time (s)",
                    y_label="Fuel temperature (C)",
                )
                assets["transient_sweep_fuel_temperature_envelope"] = str(fuel_envelope_path)

    return _update_plot_manifest(bundle.root / "plots_manifest.json", assets, remove=remove_from_manifest)


def generate_validation_plot(bundle, validation: dict[str, Any]) -> dict[str, str]:
    bundle.plots_dir.mkdir(parents=True, exist_ok=True)
    counts = {"pass": 0, "fail": 0, "pending": 0, "blocked": 0}
    for check in validation.get("checks", []):
        status = str(check.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1

    path = bundle.plots_dir / "validation_summary.svg"
    _write_bar_chart_svg(
        counts,
        path,
        title=f"{validation.get('case', 'case')} validation summary",
        palette=["#2e8b57", "#c0392b", "#d4ac0d", "#64748b"],
    )
    return _update_plot_manifest(bundle.root / "plots_manifest.json", {"validation_summary": str(path)})


def load_plot_manifest(path: Path) -> dict[str, str]:
    return {
        plot_id: _resolve_manifest_asset_path(path.parent, str(entry["path"]))
        for plot_id, entry in load_figure_catalog(path).items()
        if isinstance(entry.get("path"), str)
    }


def load_figure_catalog(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_plot_manifest_payload(path)
    if not payload:
        return {}

    catalog: dict[str, dict[str, Any]] = {}
    figures = payload.get("figures")
    if isinstance(figures, dict):
        catalog.update(
            {
                str(plot_id): _normalize_figure_entry(str(plot_id), entry)
                for plot_id, entry in figures.items()
                if isinstance(entry, dict) and entry.get("path")
            }
        )
    elif isinstance(figures, list):
        for entry in figures:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            plot_id = str(entry.get("plot_id") or Path(str(entry["path"])).stem)
            catalog[plot_id] = _normalize_figure_entry(plot_id, entry)

    for plot_id, asset_path in _legacy_plot_paths(payload).items():
        catalog.setdefault(plot_id, _build_figure_entry(plot_id, asset_path))
    return catalog


def _update_plot_manifest(path: Path, assets: dict[str, str], *, remove: set[str] | None = None) -> dict[str, str]:
    catalog = load_figure_catalog(path)
    for plot_id in remove or set():
        catalog.pop(plot_id, None)
    for plot_id, asset_path in assets.items():
        catalog[plot_id] = _build_figure_entry(plot_id, _portable_asset_path(path.parent, asset_path))
    for entry in catalog.values():
        entry["path"] = _portable_asset_path(path.parent, str(entry.get("path", "")))
        for artifact in entry.get("source_artifacts", []):
            if isinstance(artifact, dict) and artifact.get("path"):
                artifact["path"] = _portable_asset_path(path.parent, str(artifact["path"]))
    manifest = {
        "schema_version": FIGURE_CATALOG_SCHEMA_VERSION,
        "figures": {plot_id: catalog[plot_id] for plot_id in sorted(catalog)},
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return load_plot_manifest(path)


def _read_plot_manifest_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _legacy_plot_paths(payload: dict[str, Any]) -> dict[str, str]:
    reserved_keys = {"figures", "schema_version"}
    return {
        str(plot_id): str(asset_path)
        for plot_id, asset_path in payload.items()
        if plot_id not in reserved_keys and not isinstance(asset_path, (dict, list))
    }


def _build_figure_entry(plot_id: str, asset_path: str) -> dict[str, Any]:
    metadata = _figure_metadata(plot_id)
    return {
        "plot_id": plot_id,
        "path": str(asset_path),
        "title": str(metadata["title"]),
        "caption": str(metadata["caption"]),
        "quality_status": str(metadata["quality_status"]),
        "status": str(metadata["status"]),
        "method_tier": str(metadata["method_tier"]),
        "report_section": str(metadata["report_section"]),
        "axes": dict(metadata["axes"]),
        "units": dict(metadata["units"]),
        "conclusion": str(metadata["conclusion"]),
        "source_artifacts": [
            {
                "path": str(asset_path),
                "role": "rendered_figure",
                "artifact_type": Path(str(asset_path)).suffix.lstrip(".") or "unknown",
            }
        ],
    }


def _normalize_figure_entry(plot_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    normalized = _build_figure_entry(plot_id, str(entry["path"]))
    for key, value in entry.items():
        if key not in {"plot_id", "path"}:
            normalized[str(key)] = value
    normalized["plot_id"] = plot_id
    normalized["path"] = str(entry["path"])
    return normalized


def _figure_metadata(plot_id: str) -> dict[str, Any]:
    metadata = dict(_DEFAULT_FIGURE_METADATA)
    metadata.update(_FIGURE_METADATA.get(plot_id, {}))
    return metadata


def _portable_asset_path(bundle_root: Path, asset_path: str) -> str:
    if not asset_path:
        return asset_path
    path = Path(asset_path)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(bundle_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _resolve_manifest_asset_path(bundle_root: Path, asset_path: str) -> str:
    path = Path(asset_path)
    if path.is_absolute():
        return str(path)
    return str(bundle_root / path)


def _summary_neutronics_status(summary: dict[str, Any]) -> str | None:
    neutronics = summary.get("neutronics", {})
    if not isinstance(neutronics, dict):
        return None
    status = neutronics.get("status")
    if status is None:
        return None
    return str(status)


def _is_solver_backed_neutronics_status(status: str | None) -> bool:
    return bool(status) and str(status).lower() == "completed"


def _is_completed_physics_residual(item: dict[str, Any], *, neutronics_status: str | None = None) -> bool:
    if item.get("evidence_category") == "physics_benchmark":
        item_neutronics_status = item.get("neutronics_status")
        if item_neutronics_status is not None and not _is_solver_backed_neutronics_status(str(item_neutronics_status)):
            return False
        if neutronics_status is not None and not _is_solver_backed_neutronics_status(neutronics_status):
            return False
        return item.get("physics_residual_completed") is True and item.get("status") in {"pass", "fail"}
    # Legacy summaries did not carry evidence categories, so require a solver-backed run-level status.
    return (
        item.get("metric") == "keff"
        and item.get("status") in {"pass", "fail"}
        and _is_solver_backed_neutronics_status(neutronics_status)
    )


def _coerce_numeric_mapping(values: dict[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in values.items():
        if isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)):
            continue
        coerced = float(value)
        if math.isfinite(coerced):
            numeric[str(key)] = coerced
    return numeric


def _resolve_statepoint_path(bundle, summary: dict[str, Any]) -> Path | None:
    statepoint = summary.get("neutronics", {}).get("statepoint")
    if not isinstance(statepoint, str):
        return None

    statepoint_path = Path(statepoint)
    candidate = bundle.openmc_dir / statepoint_path.name
    if candidate.exists():
        return candidate
    try:
        resolved_statepoint = statepoint_path.resolve()
        resolved_openmc_dir = bundle.openmc_dir.resolve()
        if resolved_statepoint.exists() and resolved_statepoint.parent == resolved_openmc_dir:
            return resolved_statepoint
    except OSError:
        return None
    return None


def _resolve_transient_path(bundle, transient: dict[str, Any]) -> Path | None:
    history_path = transient.get("history_path")
    if not isinstance(history_path, str):
        candidate = bundle.root / "transient.json"
        return candidate if candidate.exists() else None
    path = Path(history_path)
    if path.exists():
        return path
    candidate = bundle.root / path.name
    return candidate if candidate.exists() else None


def _resolve_transient_sweep_path(bundle, transient_sweep: dict[str, Any]) -> Path | None:
    history_path = transient_sweep.get("history_path")
    if not isinstance(history_path, str):
        candidate = bundle.root / "transient_sweep.json"
        return candidate if candidate.exists() else None
    path = Path(history_path)
    if path.exists():
        return path
    candidate = bundle.root / path.name
    return candidate if candidate.exists() else None


def _write_bar_chart_svg(
    metrics: dict[str, float],
    output_path: Path,
    title: str,
    palette: list[str] | None = None,
) -> None:
    items = list(metrics.items())
    values = [value for _, value in items]
    if not values:
        return

    width = 960
    height = 540
    left = 90
    right = 30
    top = 70
    bottom = 130
    chart_width = width - left - right
    chart_height = height - top - bottom

    min_value = min(0.0, min(values))
    max_value = max(0.0, max(values))
    if math.isclose(min_value, max_value):
        if math.isclose(max_value, 0.0):
            max_value = 1.0
        else:
            min_value = min(0.0, max_value * 0.9)
            max_value = max_value * 1.1
    span = max_value - min_value

    def value_to_y(value: float) -> float:
        return top + ((max_value - value) / span) * chart_height

    baseline_y = value_to_y(0.0)
    step = chart_width / max(len(items), 1)
    bar_width = min(72.0, step * 0.6)
    colors = palette or ["#1f77b4", "#2c3e50", "#d35400", "#16a085", "#8e44ad"]

    grid_values = _axis_tick_values(min_value, max_value, 5)
    grid_labels = _format_tick_labels(grid_values)
    grid_lines: list[str] = []
    for grid_value, grid_label in zip(grid_values, grid_labels):
        grid_y = value_to_y(grid_value)
        grid_lines.append(
            f'<line x1="{left}" y1="{grid_y:.2f}" x2="{width - right}" y2="{grid_y:.2f}" stroke="#d7dce2" stroke-width="1" />'
        )
        grid_lines.append(
            f'<text x="{left - 12}" y="{grid_y + 4:.2f}" text-anchor="end" font-size="12" fill="#5c6670" data-axis="y">{escape(grid_label)}</text>'
        )

    bars: list[str] = []
    for index, (label, value) in enumerate(items):
        center_x = left + step * (index + 0.5)
        value_y = value_to_y(value)
        rect_y = min(value_y, baseline_y)
        rect_height = max(abs(value_y - baseline_y), 1.5)
        color = colors[index % len(colors)]
        if value != 0.0:
            bars.append(
                f'<rect x="{center_x - bar_width / 2:.2f}" y="{rect_y:.2f}" width="{bar_width:.2f}" height="{rect_height:.2f}" rx="6" fill="{color}" data-series="bar" />'
            )
        value_label_y = rect_y - 10 if value >= 0 else rect_y + rect_height + 16
        bars.append(
            f'<text x="{center_x:.2f}" y="{value_label_y:.2f}" text-anchor="middle" font-size="12" fill="#334155">{escape(_format_value(value))}</text>'
        )
        label_x = center_x - 6
        label_y = height - bottom + 42
        bars.append(
            f'<text x="{label_x:.2f}" y="{label_y:.2f}" transform="rotate(28 {label_x:.2f} {label_y:.2f})" '
            f'font-size="12" fill="#334155">{escape(label)}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#f8fafc" />
  <text x="{left}" y="38" font-size="24" font-weight="700" fill="#0f172a">{escape(title)}</text>
  <line x1="{left}" y1="{baseline_y:.2f}" x2="{width - right}" y2="{baseline_y:.2f}" stroke="#475569" stroke-width="1.5" />
  <line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  {''.join(grid_lines)}
  {''.join(bars)}
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def _write_line_chart_svg(values: list[float], output_path: Path, title: str) -> None:
    points = [(float(index), float(value)) for index, value in enumerate(values)]
    _write_xy_line_chart_svg(
        points,
        output_path,
        title=title,
        x_label="Generation",
        y_label="k-effective",
    )


def _write_xy_line_chart_svg(
    points: list[tuple[float, float]],
    output_path: Path,
    *,
    title: str,
    x_label: str,
    y_label: str,
) -> None:
    if not points:
        return

    width = 960
    height = 540
    left = 90
    right = 30
    top = 70
    bottom = 90
    chart_width = width - left - right
    chart_height = height - top - bottom

    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    min_x = min(x_values)
    max_x = max(x_values)
    if math.isclose(min_x, max_x):
        max_x = min_x + 1.0
    x_span = max_x - min_x

    min_value = min(y_values)
    max_value = max(y_values)
    if math.isclose(min_value, max_value):
        if math.isclose(max_value, 0.0):
            max_value = 1.0
        else:
            min_value = max_value - 0.01
            max_value = max_value + 0.01
    span = max_value - min_value

    def value_to_y(value: float) -> float:
        return top + ((max_value - value) / span) * chart_height

    def value_to_x(value: float) -> float:
        return left + ((value - min_x) / x_span) * chart_width

    polyline_points = " ".join(f"{value_to_x(x_value):.2f},{value_to_y(y_value):.2f}" for x_value, y_value in points)

    grid_values = _axis_tick_values(min_value, max_value, 5)
    grid_labels = _format_tick_labels(grid_values)
    grid_lines: list[str] = []
    for grid_value, grid_label in zip(grid_values, grid_labels):
        grid_y = value_to_y(grid_value)
        grid_lines.append(
            f'<line x1="{left}" y1="{grid_y:.2f}" x2="{width - right}" y2="{grid_y:.2f}" stroke="#d7dce2" stroke-width="1" />'
        )
        grid_lines.append(
            f'<text x="{left - 12}" y="{grid_y + 4:.2f}" text-anchor="end" font-size="12" fill="#5c6670" data-axis="y">{escape(grid_label)}</text>'
        )

    x_tick_values = _axis_tick_values(min_x, max_x, 3)
    x_tick_labels = _format_tick_labels(x_tick_values)
    x_ticks: list[str] = []
    for tick_value, tick_label in zip(x_tick_values, x_tick_labels):
        tick_x = value_to_x(tick_value)
        x_ticks.append(
            f'<line x1="{tick_x:.2f}" y1="{height - bottom}" x2="{tick_x:.2f}" y2="{height - bottom + 7}" stroke="#475569" stroke-width="1.25" data-axis="x" />'
        )
        x_ticks.append(
            f'<text x="{tick_x:.2f}" y="{height - bottom + 24}" text-anchor="middle" font-size="12" fill="#5c6670" data-axis="x">{escape(tick_label)}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#f8fafc" />
  <text x="{left}" y="38" font-size="24" font-weight="700" fill="#0f172a">{escape(title)}</text>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  <line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  {''.join(grid_lines)}
  {''.join(x_ticks)}
  <polyline fill="none" stroke="#1d4ed8" stroke-width="3" points="{polyline_points}" />
  <text x="{width / 2:.2f}" y="{height - 26}" text-anchor="middle" font-size="13" fill="#334155">{escape(x_label)}</text>
  <text x="24" y="{height / 2:.2f}" text-anchor="middle" font-size="13" fill="#334155" transform="rotate(-90 24 {height / 2:.2f})">{escape(y_label)}</text>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def _write_uncertainty_band_chart_svg(
    *,
    lower_points: list[tuple[float, float]],
    median_points: list[tuple[float, float]],
    upper_points: list[tuple[float, float]],
    output_path: Path,
    title: str,
    x_label: str,
    y_label: str,
) -> None:
    if not lower_points or not median_points or not upper_points:
        return

    width = 960
    height = 540
    left = 90
    right = 30
    top = 70
    bottom = 90
    chart_width = width - left - right
    chart_height = height - top - bottom

    x_values = [point[0] for point in median_points]
    y_values = [point[1] for point in lower_points + upper_points]
    min_x = min(x_values)
    max_x = max(x_values)
    if math.isclose(min_x, max_x):
        max_x = min_x + 1.0
    x_span = max_x - min_x

    min_value = min(y_values)
    max_value = max(y_values)
    if math.isclose(min_value, max_value):
        if math.isclose(max_value, 0.0):
            max_value = 1.0
        else:
            min_value = max_value - 0.01
            max_value = max_value + 0.01
    span = max_value - min_value

    def value_to_y(value: float) -> float:
        return top + ((max_value - value) / span) * chart_height

    def value_to_x(value: float) -> float:
        return left + ((value - min_x) / x_span) * chart_width

    lower_polyline = " ".join(f"{value_to_x(x_value):.2f},{value_to_y(y_value):.2f}" for x_value, y_value in lower_points)
    median_polyline = " ".join(f"{value_to_x(x_value):.2f},{value_to_y(y_value):.2f}" for x_value, y_value in median_points)
    upper_polyline = " ".join(f"{value_to_x(x_value):.2f},{value_to_y(y_value):.2f}" for x_value, y_value in upper_points)
    band_polygon = " ".join(
        [
            *(f"{value_to_x(x_value):.2f},{value_to_y(y_value):.2f}" for x_value, y_value in upper_points),
            *(f"{value_to_x(x_value):.2f},{value_to_y(y_value):.2f}" for x_value, y_value in reversed(lower_points)),
        ]
    )

    grid_values = _axis_tick_values(min_value, max_value, 5)
    grid_labels = _format_tick_labels(grid_values)
    grid_lines: list[str] = []
    for grid_value, grid_label in zip(grid_values, grid_labels):
        grid_y = value_to_y(grid_value)
        grid_lines.append(
            f'<line x1="{left}" y1="{grid_y:.2f}" x2="{width - right}" y2="{grid_y:.2f}" stroke="#d7dce2" stroke-width="1" />'
        )
        grid_lines.append(
            f'<text x="{left - 12}" y="{grid_y + 4:.2f}" text-anchor="end" font-size="12" fill="#5c6670" data-axis="y">{escape(grid_label)}</text>'
        )

    x_tick_values = _axis_tick_values(min_x, max_x, 3)
    x_tick_labels = _format_tick_labels(x_tick_values)
    x_ticks: list[str] = []
    for tick_value, tick_label in zip(x_tick_values, x_tick_labels):
        tick_x = value_to_x(tick_value)
        x_ticks.append(
            f'<line x1="{tick_x:.2f}" y1="{height - bottom}" x2="{tick_x:.2f}" y2="{height - bottom + 7}" stroke="#475569" stroke-width="1.25" data-axis="x" />'
        )
        x_ticks.append(
            f'<text x="{tick_x:.2f}" y="{height - bottom + 24}" text-anchor="middle" font-size="12" fill="#5c6670" data-axis="x">{escape(tick_label)}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#f8fafc" />
  <text x="{left}" y="38" font-size="24" font-weight="700" fill="#0f172a">{escape(title)}</text>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  <line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  {''.join(grid_lines)}
  {''.join(x_ticks)}
  <polygon points="{band_polygon}" fill="#93c5fd" fill-opacity="0.42" />
  <polyline fill="none" stroke="#60a5fa" stroke-width="2" points="{lower_polyline}" />
  <polyline fill="none" stroke="#1d4ed8" stroke-width="3" points="{median_polyline}" />
  <polyline fill="none" stroke="#60a5fa" stroke-width="2" points="{upper_polyline}" />
  <text x="{width / 2:.2f}" y="{height - 26}" text-anchor="middle" font-size="13" fill="#334155">{escape(x_label)}</text>
  <text x="24" y="{height / 2:.2f}" text-anchor="middle" font-size="13" fill="#334155" transform="rotate(-90 24 {height / 2:.2f})">{escape(y_label)}</text>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def _write_schedule_gantt_svg(phases: list[dict[str, Any]], output_path: Path, *, title: str) -> None:
    parsed = []
    for phase in phases:
        try:
            start = date.fromisoformat(str(phase["start_date"]))
            end = date.fromisoformat(str(phase["end_date"]))
        except (KeyError, ValueError):
            continue
        parsed.append((phase, start, end))
    if not parsed:
        return

    start_date = min(item[1] for item in parsed)
    end_date = max(item[2] for item in parsed)
    total_days = max((end_date - start_date).days, 1)
    width = 1100
    row_height = 42
    height = 120 + row_height * len(parsed)
    left = 260
    right = 50
    top = 72
    chart_width = width - left - right
    colors = {
        "planning": "#2563eb",
        "licensing": "#7c3aed",
        "procurement": "#b45309",
        "construction": "#0f766e",
        "commissioning": "#047857",
    }

    rows: list[str] = []
    for index, (phase, start, end) in enumerate(parsed):
        y = top + index * row_height
        x = left + ((start - start_date).days / total_days) * chart_width
        bar_width = max(((end - start).days / total_days) * chart_width, 4.0)
        color = colors.get(str(phase.get("category", "project")), "#475569")
        rows.append(
            f'<text x="{left - 14}" y="{y + 22}" text-anchor="end" font-size="12" fill="#334155">'
            f'{escape(str(phase.get("name", phase.get("id", "phase"))))}</text>'
        )
        rows.append(
            f'<rect x="{x:.2f}" y="{y + 5}" width="{bar_width:.2f}" height="22" rx="4" fill="{color}" />'
        )
        rows.append(
            f'<text x="{x + bar_width + 8:.2f}" y="{y + 22}" font-size="11" fill="#475569">'
            f'{escape(str(phase.get("duration_months", "")))} mo</text>'
        )

    year_lines: list[str] = []
    for year in range(start_date.year, end_date.year + 1):
        year_start = date(year, 1, 1)
        if year_start < start_date:
            continue
        x = left + ((year_start - start_date).days / total_days) * chart_width
        year_lines.append(f'<line x1="{x:.2f}" y1="{top - 10}" x2="{x:.2f}" y2="{height - 35}" stroke="#d7dce2" />')
        year_lines.append(f'<text x="{x + 4:.2f}" y="{height - 16}" font-size="11" fill="#64748b">{year}</text>')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#f8fafc" />
  <text x="{left}" y="38" font-size="24" font-weight="700" fill="#0f172a">{escape(title)}</text>
  <text x="{left}" y="58" font-size="12" fill="#475569">Planning-grade schedule from project start to commercial operation</text>
  {''.join(year_lines)}
  {''.join(rows)}
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def _write_keff_history_svg(statepoint_path: Path, output_path: Path) -> bool:
    try:
        with openmc.StatePoint(str(statepoint_path)) as statepoint:
            k_generation = getattr(statepoint, "k_generation", None)
            if k_generation is None:
                return False
            values = [float(value) for value in k_generation]
    except Exception:
        return False

    if not values:
        return False

    _write_line_chart_svg(values, output_path, "k-effective history")
    return True


def _format_value(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 100.0:
        return f"{value:.1f}"
    if magnitude >= 1.0:
        return f"{value:.3f}"
    return f"{value:.4f}"


def _axis_tick_values(min_value: float, max_value: float, count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1 or min_value == max_value:
        return [min_value]

    span = max_value - min_value
    tick_values = [min_value + (span * index / (count - 1)) for index in range(count)]
    unique_values: list[float] = []
    tolerance = max(abs(span), 1.0) * 1e-12
    for value in tick_values:
        if not any(math.isclose(value, existing, rel_tol=0.0, abs_tol=tolerance) for existing in unique_values):
            unique_values.append(value)
    return unique_values


def _format_tick_labels(values: list[float]) -> list[str]:
    if not values:
        return []

    candidates: list[tuple[int, float, int, list[str]]] = []

    def add_candidate(labels: list[str], preference: int) -> None:
        if len(set(labels)) != len(labels):
            return
        max_length = max(len(label) for label in labels)
        average_length = sum(len(label) for label in labels) / len(labels)
        candidates.append((max_length, average_length, preference, labels))

    add_candidate([_format_value(value) for value in values], 0)

    for precision in range(3, 13):
        add_candidate([_normalize_numeric_label(f"{value:.{precision}g}") for value in values], 1)

    for decimals in range(0, 13):
        add_candidate([_format_fixed_tick(value, decimals) for value in values], 2)

    for decimals in range(1, 13):
        add_candidate([_format_scientific_tick(value, decimals) for value in values], 3)

    if candidates:
        return min(candidates)[3]
    return [_normalize_numeric_label(f"{value:.15g}") for value in values]


def _format_fixed_tick(value: float, decimals: int) -> str:
    label = f"{value:.{decimals}f}"
    if "." in label:
        label = label.rstrip("0").rstrip(".")
    return _normalize_numeric_label(label)


def _format_scientific_tick(value: float, decimals: int) -> str:
    if value == 0.0:
        return "0"
    mantissa, exponent = f"{value:.{decimals}e}".split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    return _normalize_numeric_label(f"{mantissa}e{exponent}")


def _normalize_numeric_label(label: str) -> str:
    try:
        if float(label) == 0.0 and label.startswith("-"):
            return label[1:]
    except ValueError:
        return label
    return label
