from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from thorium_reactor.neutronics.workflows import build_case, run_case


def run_moose_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    built = build_case(config, bundle.openmc_dir, benchmark=benchmark)
    summary = _ensure_summary(config, bundle, benchmark=benchmark, provenance=provenance)
    settings = _integration_settings(config, "moose")
    input_path = bundle.root / "moose_input.i"
    handoff_path = bundle.root / "moose_handoff.json"
    input_path.write_text(_render_moose_input(config, summary, built.geometry_description, settings), encoding="utf-8")
    handoff_path.write_text(
        json.dumps(_build_handoff_payload(config, summary, built.manifest, built.geometry_description, settings, "moose"), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    result = {
        "tool": "moose",
        "status": "exported",
        "input_path": str(input_path),
        "handoff_path": str(handoff_path),
        "execution_mode": "export_only",
        "application": settings.get("application", "app-opt"),
        "input_deck_kind": "thermal_hydraulics_proxy",
    }
    if execute:
        command = _build_moose_command(settings, input_path)
        result.update(_run_external_command(command, repo_root=bundle.root, runtime_label="moose"))
    return result


def run_scale_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    built = build_case(config, bundle.openmc_dir, benchmark=benchmark)
    summary = _ensure_summary(config, bundle, benchmark=benchmark, provenance=provenance)
    settings = _integration_settings(config, "scale")
    input_path = bundle.root / "scale_input.inp"
    handoff_path = bundle.root / "scale_handoff.json"
    input_path.write_text(_render_scale_input(config, summary, built.manifest, settings), encoding="utf-8")
    handoff_path.write_text(
        json.dumps(_build_handoff_payload(config, summary, built.manifest, built.geometry_description, settings, "scale"), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    result = {
        "tool": "scale",
        "status": "exported",
        "input_path": str(input_path),
        "handoff_path": str(handoff_path),
        "execution_mode": "export_only",
        "sequence": settings.get("sequence", "csas6"),
        "input_deck_kind": "criticality_proxy",
    }
    if execute:
        command = _build_scale_command(settings, input_path)
        result.update(_run_external_command(command, repo_root=bundle.root, runtime_label="scale"))
    return result


def persist_integration_result(bundle, summary: dict[str, Any], name: str, payload: dict[str, Any]) -> None:
    summary.setdefault("integrations", {})
    summary["integrations"][name] = json.loads(json.dumps(payload))
    bundle.write_json("summary.json", summary)
    bundle.write_json(f"{name}_integration.json", payload)


def _ensure_summary(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    summary_path = bundle.root / "summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    return run_case(
        config,
        bundle,
        benchmark=benchmark,
        solver_enabled=False,
        provenance=provenance,
    )


def _integration_settings(config: Any, name: str) -> dict[str, Any]:
    integrations = config.data.get("integrations", {})
    if not isinstance(integrations, dict):
        return {}
    settings = integrations.get(name, {})
    return settings if isinstance(settings, dict) else {}


def _build_moose_command(settings: dict[str, Any], input_path: Path) -> list[str]:
    executable = str(settings.get("application", "app-opt"))
    extra_args = [str(arg) for arg in settings.get("args", [])] if isinstance(settings.get("args"), list) else []
    return [executable, "-i", str(input_path), *extra_args]


def _build_scale_command(settings: dict[str, Any], input_path: Path) -> list[str]:
    executable = str(settings.get("executable", "scalerte"))
    extra_args = [str(arg) for arg in settings.get("args", [])] if isinstance(settings.get("args"), list) else []
    return [executable, str(input_path), *extra_args]


def _run_external_command(command: list[str], *, repo_root: Path, runtime_label: str) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        return {
            "status": "exported_missing_runtime",
            "execution_mode": "requested_but_unavailable",
            "runtime": runtime_label,
            "command": command,
            "error": f"Executable '{command[0]}' was not found on PATH.",
        }
    completed = subprocess.run(
        [executable, *command[1:]],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "status": "completed" if completed.returncode == 0 else "failed",
        "execution_mode": "executed",
        "runtime": runtime_label,
        "command": [executable, *command[1:]],
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _build_handoff_payload(
    config: Any,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    geometry_description: dict[str, Any],
    settings: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    geometry = config.geometry
    reactor = config.reactor
    materials = {}
    for material_name, spec in config.materials.items():
        materials[str(material_name)] = {
            "name": spec.get("name", material_name),
            "density": spec.get("density"),
            "cp": spec.get("cp"),
            "dynamic_viscosity": spec.get("dynamic_viscosity"),
            "thermal_conductivity": spec.get("thermal_conductivity"),
            "nuclide_count": len(spec.get("nuclides", [])),
            "element_count": len(spec.get("elements", [])),
        }
    return {
        "tool": tool_name,
        "case": {
            "name": config.name,
            "reactor_name": reactor.get("name", config.name),
            "family": reactor.get("family"),
            "stage": reactor.get("stage"),
        },
        "geometry": {
            "kind": geometry.get("kind"),
            "style": geometry.get("style"),
            "boundary": geometry.get("boundary"),
            "axial_boundary": geometry.get("axial_boundary"),
            "pitch": geometry.get("pitch"),
            "core_radius": geometry.get("core_radius"),
            "height_cm": geometry.get("height_cm"),
            "channel_count": manifest.get("channel_count"),
            "cell_count": manifest.get("cell_count"),
            "description_type": geometry_description.get("type"),
        },
        "operating_point": {
            "design_power_mwth": reactor.get("design_power_mwth"),
            "hot_leg_temp_c": reactor.get("hot_leg_temp_c"),
            "cold_leg_temp_c": reactor.get("cold_leg_temp_c"),
            "summary_metrics": summary.get("metrics", {}),
            "bop": summary.get("bop", {}),
            "primary_system": summary.get("primary_system", {}),
            "reduced_order_flow": summary.get("flow", {}).get("reduced_order", {}),
        },
        "materials": materials,
        "integration_settings": settings,
    }


def _render_moose_input(
    config: Any,
    summary: dict[str, Any],
    geometry_description: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    bop = summary.get("bop", {})
    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    primary_system = summary.get("primary_system", {})
    heat_exchanger = primary_system.get("heat_exchanger", {})
    thermal_profile = primary_system.get("thermal_profile", {})
    case_name = config.name
    executioner = str(settings.get("executioner", "Steady"))
    end_time = float(settings.get("end_time", 10.0))
    dt = float(settings.get("dt", 1.0))
    active_channels = reduced_order.get("active_flow", {}).get("channel_count", 0)
    representative_velocity = reduced_order.get("active_flow", {}).get("representative_velocity_m_s", 0.0)
    peak_temp = thermal_profile.get("estimated_hot_leg_temp_c", config.reactor.get("hot_leg_temp_c", 700.0))
    cold_temp = thermal_profile.get("estimated_cold_leg_temp_c", config.reactor.get("cold_leg_temp_c", 560.0))
    hx_area = heat_exchanger.get("required_area_m2", 0.0)
    thermal_power = bop.get("thermal_power_mw", config.reactor.get("design_power_mwth", 0.0))
    return "\n".join(
        [
            f"# Auto-generated MOOSE integration input for {case_name}",
            f"# Geometry kind: {geometry_description.get('type', 'unknown')}",
            "",
            "[GlobalParams]",
            f"  case_name = '{case_name}'",
            f"  thermal_power_mw = {thermal_power}",
            f"  active_channel_count = {active_channels}",
            f"  representative_velocity_m_s = {representative_velocity}",
            f"  hot_leg_temp_c = {peak_temp}",
            f"  cold_leg_temp_c = {cold_temp}",
            f"  heat_exchanger_area_m2 = {hx_area}",
            "[]",
            "",
            "[Executioner]",
            f"  type = {executioner}",
            f"  end_time = {end_time}",
            f"  dt = {dt}",
            "[]",
            "",
            "[Functions]",
            "  [./core_power]",
            "    type = ParsedFunction",
            "    expression = 'thermal_power_mw * 1e6'",
            "  [../]",
            "[]",
            "",
            "[Postprocessors]",
            "  [./active_channels]",
            "    type = ConstantPostprocessor",
            f"    value = {active_channels}",
            "  [../]",
            "[]",
            "",
            "# Replace this proxy deck with a plant-specific ThermalHydraulics/Cardinal model as needed.",
            "",
        ]
    )


def _render_scale_input(
    config: Any,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    sequence = str(settings.get("sequence", "csas6"))
    material_inventory = manifest.get("material_inventory", [])
    particles = summary.get("neutronics", {}).get("simulation", {}).get("particles", config.simulation.get("particles"))
    batches = summary.get("neutronics", {}).get("simulation", {}).get("batches", config.simulation.get("batches"))
    keff_hint = summary.get("metrics", {}).get("keff", "n/a")
    lines = [
        f"=shell",
        f"# Auto-generated SCALE integration input for {config.name}",
        f"# Sequence: {sequence}",
        f"# OpenMC keff hint: {keff_hint}",
        f"# Material inventory: {', '.join(material_inventory)}",
        "=end",
        "",
        f"={sequence}",
        "title",
        f"  {config.reactor.get('name', config.name)} external integration proxy",
        "end title",
        "",
        "parameters",
        f"  npg={particles}",
        f"  generations={batches}",
        "end parameters",
        "",
        "comments",
        "  Replace this proxy input with a validated KENO/TRITON model tied to case materials and geometry.",
        "end comments",
        "",
        "end",
        "",
    ]
    return "\n".join(lines)
