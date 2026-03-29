from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path
from typing import Any

from thorium_reactor.neutronics.openmc_compat import openmc


def generate_summary_plots(bundle, summary: dict[str, Any]) -> dict[str, str]:
    bundle.plots_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[str, str] = {}

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

    return _update_plot_manifest(bundle.root / "plots_manifest.json", assets)


def generate_validation_plot(bundle, validation: dict[str, Any]) -> dict[str, str]:
    bundle.plots_dir.mkdir(parents=True, exist_ok=True)
    counts = {"pass": 0, "fail": 0, "pending": 0}
    for check in validation.get("checks", []):
        status = str(check.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1

    path = bundle.plots_dir / "validation_summary.svg"
    _write_bar_chart_svg(
        counts,
        path,
        title=f"{validation.get('case', 'case')} validation summary",
        palette=["#2e8b57", "#c0392b", "#d4ac0d"],
    )
    return _update_plot_manifest(bundle.root / "plots_manifest.json", {"validation_summary": str(path)})


def load_plot_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def _update_plot_manifest(path: Path, assets: dict[str, str]) -> dict[str, str]:
    manifest = load_plot_manifest(path)
    manifest.update(assets)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


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
    if statepoint_path.exists():
        return statepoint_path

    candidate = bundle.openmc_dir / statepoint_path.name
    if candidate.exists():
        return candidate
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

    grid_lines: list[str] = []
    for index in range(5):
        grid_value = min_value + (span * index / 4.0)
        grid_y = value_to_y(grid_value)
        grid_lines.append(
            f'<line x1="{left}" y1="{grid_y:.2f}" x2="{width - right}" y2="{grid_y:.2f}" stroke="#d7dce2" stroke-width="1" />'
        )
        grid_lines.append(
            f'<text x="{left - 12}" y="{grid_y + 4:.2f}" text-anchor="end" font-size="12" fill="#5c6670">{_format_value(grid_value)}</text>'
        )

    bars: list[str] = []
    for index, (label, value) in enumerate(items):
        center_x = left + step * (index + 0.5)
        value_y = value_to_y(value)
        rect_y = min(value_y, baseline_y)
        rect_height = max(abs(value_y - baseline_y), 1.5)
        color = colors[index % len(colors)]
        bars.append(
            f'<rect x="{center_x - bar_width / 2:.2f}" y="{rect_y:.2f}" width="{bar_width:.2f}" height="{rect_height:.2f}" rx="6" fill="{color}" />'
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

    grid_lines: list[str] = []
    for index in range(5):
        grid_value = min_value + (span * index / 4.0)
        grid_y = value_to_y(grid_value)
        grid_lines.append(
            f'<line x1="{left}" y1="{grid_y:.2f}" x2="{width - right}" y2="{grid_y:.2f}" stroke="#d7dce2" stroke-width="1" />'
        )
        grid_lines.append(
            f'<text x="{left - 12}" y="{grid_y + 4:.2f}" text-anchor="end" font-size="12" fill="#5c6670">{_format_value(grid_value)}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="#f8fafc" />
  <text x="{left}" y="38" font-size="24" font-weight="700" fill="#0f172a">{escape(title)}</text>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  <line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#475569" stroke-width="1.5" />
  {''.join(grid_lines)}
  <polyline fill="none" stroke="#1d4ed8" stroke-width="3" points="{polyline_points}" />
  <text x="{width / 2:.2f}" y="{height - 26}" text-anchor="middle" font-size="13" fill="#334155">{escape(x_label)}</text>
  <text x="24" y="{height / 2:.2f}" text-anchor="middle" font-size="13" fill="#334155" transform="rotate(-90 24 {height / 2:.2f})">{escape(y_label)}</text>
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
