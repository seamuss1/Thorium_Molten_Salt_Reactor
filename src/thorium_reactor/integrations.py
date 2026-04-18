from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from thorium_reactor.neutronics.workflows import build_case, run_case
from thorium_reactor.runtime_context import build_runtime_context


INTEGRATION_DEFINITIONS = {
    "moose": {
        "input_name": "moose_input.i",
        "handoff_name": "moose_handoff.json",
        "input_deck_kind": "thermal_hydraulics_proxy",
        "execution_mode": "export_only",
        "render_mode": "text",
    },
    "scale": {
        "input_name": "scale_input.inp",
        "handoff_name": "scale_handoff.json",
        "input_deck_kind": "criticality_proxy",
        "execution_mode": "export_only",
        "render_mode": "text",
    },
    "thermochimica": {
        "input_name": "thermochimica_input.json",
        "handoff_name": "thermochimica_handoff.json",
        "input_deck_kind": "thermochemical_state_request",
        "execution_mode": "export_only",
        "render_mode": "json",
    },
    "saltproc": {
        "input_name": "saltproc_input.json",
        "handoff_name": "saltproc_handoff.json",
        "input_deck_kind": "online_processing_proxy",
        "execution_mode": "export_only",
        "render_mode": "json",
    },
    "moltres": {
        "input_name": "moltres_input.i",
        "handoff_name": "moltres_handoff.json",
        "input_deck_kind": "multigroup_transient_proxy",
        "execution_mode": "export_only",
        "render_mode": "text",
    },
}


def run_named_integration(
    name: str,
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    definition = _integration_definition(name)
    built = build_case(config, bundle.openmc_dir, benchmark=benchmark)
    summary = _ensure_summary(config, bundle, benchmark=benchmark, provenance=provenance)
    settings = _integration_settings(config, name)
    runtime_context = build_runtime_context(command=[name, config.name])
    input_path = bundle.root / definition["input_name"]
    handoff_path = bundle.root / definition["handoff_name"]
    rendered_input = _render_integration_input(name, config, summary, built.manifest, built.geometry_description, settings)
    if definition["render_mode"] == "json":
        input_path.write_text(json.dumps(rendered_input, indent=2, sort_keys=True), encoding="utf-8")
        status = "input_bundle_exported"
    else:
        input_path.write_text(str(rendered_input), encoding="utf-8")
        status = "input_deck_exported"
    handoff_path.write_text(
        json.dumps(
            _build_handoff_payload(
                config,
                summary,
                built.manifest,
                built.geometry_description,
                settings,
                name,
                runtime_context=runtime_context,
                provenance=provenance,
            ),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = {
        "tool": name,
        "status": status,
        "input_path": str(input_path),
        "handoff_path": str(handoff_path),
        "execution_mode": definition["execution_mode"],
        "input_deck_kind": definition["input_deck_kind"],
        "provenance": {
            "runtime_context": json.loads(json.dumps(runtime_context)),
            "input_provenance": json.loads(json.dumps(provenance or {})),
        },
    }
    if name == "moose":
        result["application"] = settings.get("application", "app-opt")
    elif name == "scale":
        result["sequence"] = settings.get("sequence", "csas6")
    elif name in {"thermochimica", "saltproc", "moltres"}:
        result["executable"] = _default_executable(name, settings)
    if execute:
        command = _build_named_command(name, settings, input_path)
        result.update(_run_external_command(command, repo_root=bundle.root, runtime_label=name))
    return result


def run_moose_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    return run_named_integration(
        "moose",
        config,
        bundle,
        benchmark=benchmark,
        provenance=provenance,
        execute=execute,
    )


def run_scale_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    return run_named_integration(
        "scale",
        config,
        bundle,
        benchmark=benchmark,
        provenance=provenance,
        execute=execute,
    )


def run_thermochimica_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    return run_named_integration(
        "thermochimica",
        config,
        bundle,
        benchmark=benchmark,
        provenance=provenance,
        execute=execute,
    )


def run_saltproc_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    return run_named_integration(
        "saltproc",
        config,
        bundle,
        benchmark=benchmark,
        provenance=provenance,
        execute=execute,
    )


def run_moltres_integration(
    config: Any,
    bundle,
    *,
    benchmark: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    return run_named_integration(
        "moltres",
        config,
        bundle,
        benchmark=benchmark,
        provenance=provenance,
        execute=execute,
    )


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


def _integration_definition(name: str) -> dict[str, Any]:
    if name not in INTEGRATION_DEFINITIONS:
        raise ValueError(f"Unsupported integration '{name}'.")
    return INTEGRATION_DEFINITIONS[name]


def _default_executable(name: str, settings: dict[str, Any]) -> str:
    defaults = {
        "thermochimica": "thermochimica",
        "saltproc": "saltproc",
        "moltres": "moltres-opt",
    }
    return str(settings.get("executable", defaults.get(name, name)))


def _build_named_command(name: str, settings: dict[str, Any], input_path: Path) -> list[str]:
    if name == "moose":
        return _build_moose_command(settings, input_path)
    if name == "scale":
        return _build_scale_command(settings, input_path)
    executable = _default_executable(name, settings)
    extra_args = [str(arg) for arg in settings.get("args", [])] if isinstance(settings.get("args"), list) else []
    return [executable, str(input_path), *extra_args]


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
            "status": "input_deck_exported_missing_runtime",
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
    *,
    runtime_context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
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
            "mode": reactor.get("mode"),
            "hot_leg_temp_c": reactor.get("hot_leg_temp_c"),
            "cold_leg_temp_c": reactor.get("cold_leg_temp_c"),
            "summary_metrics": summary.get("metrics", {}),
            "bop": summary.get("bop", {}),
            "primary_system": summary.get("primary_system", {}),
            "reduced_order_flow": summary.get("flow", {}).get("reduced_order", {}),
            "state_store_path": str((Path(summary.get("result_dir", "")) / "state_store.json")) if summary.get("result_dir") else None,
        },
        "materials": materials,
        "integration_settings": settings,
        "processing": summary.get("processing", config.data.get("processing", {})),
        "benchmark_residuals": summary.get("benchmark_residuals", {}),
        "provenance": {
            "runtime_context": json.loads(json.dumps(runtime_context or {})),
            "input_provenance": json.loads(json.dumps(provenance or {})),
        },
    }


def _render_integration_input(
    name: str,
    config: Any,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    geometry_description: dict[str, Any],
    settings: dict[str, Any],
) -> str | dict[str, Any]:
    if name == "moose":
        return _render_moose_input(config, summary, geometry_description, settings)
    if name == "scale":
        return _render_scale_input(config, summary, manifest, settings)
    if name == "thermochimica":
        return _render_thermochimica_input(config, summary, settings)
    if name == "saltproc":
        return _render_saltproc_input(config, summary, settings)
    if name == "moltres":
        return _render_moltres_input(config, summary, geometry_description, settings)
    raise ValueError(f"Unsupported integration '{name}'.")


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


def _render_thermochimica_input(
    config: Any,
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case": config.name,
        "reactor_mode": config.reactor.get("mode", "modern_test_reactor"),
        "chemistry": summary.get("chemistry", {}),
        "fuel_cycle": summary.get("fuel_cycle", {}),
        "processing": config.data.get("processing", {}),
        "settings": settings,
    }


def _render_saltproc_input(
    config: Any,
    summary: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case": config.name,
        "reactor_mode": config.reactor.get("mode", "modern_test_reactor"),
        "fuel_cycle": summary.get("fuel_cycle", {}),
        "chemistry": summary.get("chemistry", {}),
        "processing": config.data.get("processing", {}),
        "state_store_path": str(Path(summary.get("result_dir", "")) / "state_store.json") if summary.get("result_dir") else None,
        "settings": settings,
    }


def _render_moltres_input(
    config: Any,
    summary: dict[str, Any],
    geometry_description: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    active_flow = reduced_order.get("active_flow", {})
    primary_system = summary.get("primary_system", {})
    chemistry = summary.get("chemistry", {})
    return "\n".join(
        [
            f"# Auto-generated Moltres integration input for {config.name}",
            f"# Geometry kind: {geometry_description.get('type', 'unknown')}",
            "",
            "[GlobalParams]",
            f"  case_name = '{config.name}'",
            f"  active_channel_count = {active_flow.get('channel_count', 0)}",
            f"  representative_velocity_m_s = {active_flow.get('representative_velocity_m_s', 0.0)}",
            f"  redox_state_ev = {chemistry.get('redox_state_ev', config.data.get('chemistry', {}).get('initial_redox_state_ev', -0.02))}",
            f"  loop_pressure_drop_kpa = {primary_system.get('loop_hydraulics', {}).get('total_pressure_drop_kpa', 0.0)}",
            "[]",
            "",
            "[Executioner]",
            "  type = Transient",
            f"  end_time = {float(settings.get('end_time', 20.0))}",
            f"  dt = {float(settings.get('dt', 1.0))}",
            "[]",
            "",
            "# Replace this proxy deck with a validated Moltres model and group constants.",
            "",
        ]
    )
