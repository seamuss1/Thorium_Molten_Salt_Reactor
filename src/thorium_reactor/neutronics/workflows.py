from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thorium_reactor.benchmarking import assess_benchmark_traceability
from thorium_reactor.bop.steady_state import BOPInputs, run_steady_state_bop
from thorium_reactor.config import CaseConfig, load_yaml
from thorium_reactor.flow.properties import (
    average_primary_temperature_c,
    evaluate_property,
    primary_coolant_cp_kj_kgk,
    property_reference_temperature_c,
)
from thorium_reactor.flow.primary_system import build_primary_system_summary
from thorium_reactor.flow.reduced_order import build_reduced_order_flow_summary
from thorium_reactor.geometry.exporters import export_geometry
from thorium_reactor.geometry.molten_salt_reactor import (
    build_msr_flow_summary,
    build_msr_geometry_description,
    build_msr_invariants,
    resolve_msr_geometry,
)
from thorium_reactor.neutronics.openmc_compat import openmc
from thorium_reactor.reporting.plots import generate_summary_plots, generate_validation_plot


@dataclass(slots=True)
class BuiltCase:
    manifest: dict[str, Any]
    geometry_description: dict[str, Any]
    model: Any | None
    benchmark: dict[str, Any]


def build_case(config: CaseConfig, output_dir: Path | None = None) -> BuiltCase:
    benchmark = load_yaml(config.benchmark_file) if config.benchmark_file and config.benchmark_file.exists() else {}
    geometry_kind = config.geometry["kind"]
    if geometry_kind in {"pin_cell", "layered_channel"}:
        return _build_pin_case(config, benchmark)
    if geometry_kind == "ring_lattice_core":
        return _build_ring_lattice_core(config, benchmark)
    raise ValueError(f"Unsupported geometry kind: {geometry_kind}")


def material_sanity_checks(config: CaseConfig) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, spec in config.materials.items():
        density = spec.get("density", {})
        if density:
            density_temperature_c = property_reference_temperature_c(config.reactor, density)
            try:
                density_value = evaluate_property(
                    density,
                    temperature_c=density_temperature_c,
                    expected_quantity="density",
                )
            except Exception:
                density_value = None
            if density_value is not None:
                passed = float(density_value) > 0.0
                checks.append(
                    {
                        "name": f"material_density::{name}",
                        "passed": passed,
                        "message": (
                            f"Material {name} has positive density."
                            if passed
                            else f"Material {name} must have a positive density."
                        ),
                    }
                )
        nuclides = spec.get("nuclides", [])
        elements = spec.get("elements", [])
        checks.append(
            {
                "name": f"material_composition::{name}",
                "passed": bool(nuclides or elements),
                "message": (
                    f"Material {name} contains nuclides/elements."
                    if (nuclides or elements)
                    else f"Material {name} is missing composition data."
                ),
            }
        )
    return checks


def run_case(config: CaseConfig, bundle, solver_enabled: bool = True) -> dict[str, Any]:
    built = build_case(config, bundle.openmc_dir)
    geometry_assets = export_geometry(built.geometry_description, bundle.geometry_exports_dir)
    build_manifest = dict(built.manifest)
    build_manifest["geometry_assets"] = geometry_assets
    bundle.write_json("build_manifest.json", build_manifest)

    summary: dict[str, Any] = {
        "case": config.name,
        "result_dir": str(bundle.root),
        "neutronics": {
            "status": "dry-run",
            "openmc_available": openmc is not None,
        },
        "metrics": {
            "design_power_mwth": config.reactor.get("design_power_mwth", 0.0),
            "expected_cells": built.manifest.get("cell_count", 0),
            "geometry_kind": config.geometry["kind"],
        },
    }
    if "channel_count" in built.manifest:
        summary["metrics"]["channel_count"] = built.manifest["channel_count"]
    if "flow_summary" in built.manifest:
        summary["flow"] = json.loads(json.dumps(built.manifest["flow_summary"]))

    if openmc is not None and solver_enabled and built.model is not None:
        built.model.export_to_xml(directory=str(bundle.openmc_dir))
        try:
            openmc.run(cwd=str(bundle.openmc_dir))
            statepoints = sorted(bundle.openmc_dir.glob("statepoint.*.h5"))
            if statepoints:
                statepoint_path = statepoints[-1]
                with openmc.StatePoint(str(statepoint_path)) as statepoint:
                    keff = float(statepoint.keff.nominal_value)
                summary["neutronics"] = {
                    "status": "completed",
                    "statepoint": str(statepoint_path),
                }
                summary["metrics"]["keff"] = round(keff, 6)
            else:
                summary["neutronics"] = {
                    "status": "completed_without_statepoint",
                }
        except Exception as exc:  # pragma: no cover - depends on external solver
            summary["neutronics"] = {
                "status": "failed",
                "error": str(exc),
            }
    else:
        if built.model is not None and openmc is not None:
            built.model.export_to_xml(directory=str(bundle.openmc_dir))
        summary["neutronics"]["status"] = "skipped_missing_solver" if openmc is None else "dry-run"

    if config.reactor.get("design_power_mwth"):
        bop_inputs = BOPInputs(
            thermal_power_mw=float(config.reactor["design_power_mwth"]),
            hot_leg_temp_c=float(config.reactor.get("hot_leg_temp_c", 700.0)),
            cold_leg_temp_c=float(config.reactor.get("cold_leg_temp_c", 560.0)),
            primary_cp_kj_kgk=primary_coolant_cp_kj_kgk(
                config,
                temperature_c=average_primary_temperature_c(config.reactor),
            ),
            steam_generator_effectiveness=float(config.reactor.get("steam_generator_effectiveness", 0.92)),
            turbine_efficiency=float(config.reactor.get("turbine_efficiency", 0.42)),
            generator_efficiency=float(config.reactor.get("generator_efficiency", 0.98)),
        )
        summary["bop"] = run_steady_state_bop(bop_inputs).to_dict()
        summary["metrics"]["electric_power_mwe"] = round(summary["bop"]["electric_power_mw"], 3)
        if "flow" in summary:
            reduced_order_flow = build_reduced_order_flow_summary(
                config,
                summary["flow"],
                float(summary["bop"]["primary_mass_flow_kg_s"]),
            )
            summary["flow"]["reduced_order"] = reduced_order_flow
            summary["metrics"]["active_flow_channel_count"] = reduced_order_flow["active_flow"]["channel_count"]
            summary["metrics"]["active_flow_area_cm2"] = reduced_order_flow["active_flow"]["total_flow_area_cm2"]
            summary["metrics"]["active_flow_velocity_m_s"] = reduced_order_flow["active_flow"]["representative_velocity_m_s"]
            summary["metrics"]["active_flow_residence_time_s"] = reduced_order_flow["active_flow"]["representative_residence_time_s"]
            summary["metrics"]["disconnected_flow_inventory_channels"] = reduced_order_flow["disconnected_inventory"]["channel_count"]
            primary_system = build_primary_system_summary(
                config,
                built.geometry_description,
                reduced_order_flow,
                summary["bop"],
            )
            if primary_system:
                summary["primary_system"] = primary_system
                summary["fuel_cycle"] = json.loads(json.dumps(primary_system["fuel_cycle"]))
                hydraulics = primary_system["loop_hydraulics"]
                heat_exchanger = primary_system["heat_exchanger"]
                summary["metrics"]["primary_total_pressure_drop_kpa"] = hydraulics["total_pressure_drop_kpa"]
                summary["metrics"]["primary_pump_head_m"] = hydraulics["pump_head_m"]
                summary["metrics"]["primary_hx_area_m2"] = heat_exchanger["required_area_m2"]
                summary["metrics"]["fuel_salt_inventory_m3"] = primary_system["inventory"]["fuel_salt"]["total_m3"]
                summary["metrics"]["coolant_salt_inventory_m3"] = primary_system["inventory"]["coolant_salt"]["net_pool_inventory_m3"]
                summary["metrics"]["fissile_inventory_kg"] = primary_system["fuel_cycle"]["fissile_inventory_kg"]
    if built.manifest.get("benchmark_traceability"):
        summary["benchmark_traceability"] = json.loads(json.dumps(built.manifest["benchmark_traceability"]))
        summary["metrics"]["benchmark_traceability_score"] = built.manifest["benchmark_traceability"]["traceability_score"]

    bundle.write_json("summary.json", summary)
    if "flow" in summary:
        bundle.write_json("flow_summary.json", summary["flow"])
    bundle.write_metrics(summary["metrics"])
    generate_summary_plots(bundle, summary)
    return summary


def validate_case(config: CaseConfig, bundle, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    built = build_case(config, bundle.openmc_dir)
    benchmark = built.benchmark
    if summary is None:
        summary_path = bundle.root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}

    checks: list[dict[str, Any]] = []
    metrics = summary.get("metrics", {})

    for name, target in config.validation_targets.items():
        checks.append(_evaluate_target(name, target, metrics, built.manifest))

    for invariant in built.manifest.get("invariants", []):
        checks.append(
            {
                "name": invariant["name"],
                "status": "pass" if invariant["passed"] else "fail",
                "message": invariant["message"],
            }
        )

    for check in summary.get("primary_system", {}).get("checks", []):
        checks.append(dict(check))

    if benchmark:
        checks.append(
            {
                "name": "benchmark_metadata_loaded",
                "status": "pass",
                "message": benchmark.get("title", "Loaded benchmark metadata"),
            }
        )

    result = {
        "case": config.name,
        "checks": checks,
        "passed": all(check["status"] == "pass" for check in checks),
    }
    bundle.write_json("validation.json", result)
    generate_validation_plot(bundle, result)
    return result


def _evaluate_target(
    name: str,
    target: dict[str, Any],
    metrics: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    source_name = target.get("source", "metrics")
    source = metrics if source_name == "metrics" else manifest
    value = source.get(target["metric"])
    if value is None:
        return {
            "name": name,
            "status": "pending",
            "message": f"Metric '{target['metric']}' is not available yet.",
        }

    minimum = target.get("min")
    maximum = target.get("max")
    if minimum is not None and value < minimum:
        return {
            "name": name,
            "status": "fail",
            "message": f"{value} is below the minimum bound {minimum}.",
        }
    if maximum is not None and value > maximum:
        return {
            "name": name,
            "status": "fail",
            "message": f"{value} is above the maximum bound {maximum}.",
        }
    return {
        "name": name,
        "status": "pass",
        "message": f"{value} is within the expected range.",
    }


def _build_pin_case(config: CaseConfig, benchmark: dict[str, Any]) -> BuiltCase:
    geometry = config.geometry
    layers = list(geometry["layers"])
    invariants = _validate_layers(layers)
    cell_count = len(layers) + 1
    material_inventory = sorted({layer["material"] for layer in layers if layer.get("material")} | {geometry["background_material"]})
    invariants.extend(material_sanity_checks(config))
    benchmark_traceability = assess_benchmark_traceability(config, benchmark) if benchmark else {}

    model = _create_openmc_pin_model(config) if openmc is not None else None

    return BuiltCase(
        manifest={
            "case": config.name,
            "cell_count": cell_count,
            "geometry_kind": geometry["kind"],
            "material_inventory": material_inventory,
            "invariants": invariants,
            "benchmark_traceability": benchmark_traceability,
        },
        geometry_description={
            "name": config.name,
            "type": "pin",
            "pitch": geometry["pitch"],
            "height": geometry.get("height_cm", 100.0),
            "layers": layers,
        },
        model=model,
        benchmark=benchmark,
    )


def _build_ring_lattice_core(config: CaseConfig, benchmark: dict[str, Any]) -> BuiltCase:
    geometry = config.geometry
    if geometry.get("style") == "detailed_msr":
        resolved = resolve_msr_geometry(config)
        invariants = build_msr_invariants(config, resolved)
        flow_summary = build_msr_flow_summary(config, resolved)
        geometry_description = build_msr_geometry_description(config, resolved)
        benchmark_traceability = assess_benchmark_traceability(config, benchmark) if benchmark else {}
        model = _create_openmc_detailed_msr_model(config, resolved) if openmc is not None else None
        material_inventory = {
            geometry["matrix_material"],
            geometry["background_material"],
            geometry.get("salt_material", "fuel_salt"),
            geometry.get("structure_material", "pipe"),
        }
        for channel in resolved.channels:
            for layer in channel["layers"]:
                if layer.get("material"):
                    material_inventory.add(layer["material"])
        for solid in geometry_description["render_solids"]:
            if solid.get("material"):
                material_inventory.add(str(solid["material"]))
        invariants.extend(material_sanity_checks(config))
        channel_cell_count = sum(len(channel["layers"]) for channel in resolved.channels)
        static_cell_count = 13
        return BuiltCase(
            manifest={
                "case": config.name,
                "cell_count": channel_cell_count + static_cell_count,
                "channel_count": len(resolved.channels),
                "channel_variant_counts": dict(resolved.channel_variant_counts),
                "geometry_kind": geometry["kind"],
                "geometry_style": geometry["style"],
                "flow_summary": flow_summary,
                "material_inventory": sorted(material_inventory),
                "invariants": invariants,
                "benchmark_traceability": benchmark_traceability,
            },
            geometry_description=geometry_description,
            model=model,
            benchmark=benchmark,
        )

    channel_layers = list(geometry["channel_layers"])
    invariants = _validate_layers(channel_layers)
    channels: list[dict[str, Any]] = []
    last_radius = channel_layers[-1]["outer_radius"]
    for ring in geometry["rings"]:
        radius = float(ring["radius"])
        count = int(ring["count"])
        positions = [(0.0, 0.0)] if count == 1 else [
            (
                radius * math.cos(2.0 * math.pi * index / count),
                radius * math.sin(2.0 * math.pi * index / count),
            )
            for index in range(count)
        ]
        for index, (x_pos, y_pos) in enumerate(positions):
            channels.append(
                {
                    "name": f"ring_{radius:.2f}_{index}",
                    "x": x_pos,
                    "y": y_pos,
                    "layers": channel_layers,
                }
            )
    core_radius = float(geometry["core_radius"])
    for channel in channels:
        distance = (channel["x"] ** 2 + channel["y"] ** 2) ** 0.5 + last_radius
        passed = distance <= core_radius
        invariants.append(
            {
                "name": f"channel_fit::{channel['name']}",
                "passed": passed,
                "message": (
                    f"Channel {channel['name']} fits within the graphite core."
                    if passed
                    else f"Channel {channel['name']} exceeds the graphite core radius."
                ),
            }
        )

    model = _create_openmc_ring_core_model(config) if openmc is not None else None
    material_inventory = sorted(
        {layer["material"] for layer in channel_layers if layer.get("material")} | {geometry["matrix_material"], geometry["background_material"]}
    )
    invariants.extend(material_sanity_checks(config))
    benchmark_traceability = assess_benchmark_traceability(config, benchmark) if benchmark else {}
    return BuiltCase(
        manifest={
            "case": config.name,
            "cell_count": len(channels) * len(channel_layers) + 2,
            "channel_count": len(channels),
            "geometry_kind": geometry["kind"],
            "material_inventory": material_inventory,
            "invariants": invariants,
            "benchmark_traceability": benchmark_traceability,
        },
        geometry_description={
            "name": config.name,
            "type": "ring_lattice_core",
            "pitch": geometry["pitch"],
            "height": geometry.get("height_cm", 200.0),
            "core_radius": core_radius,
            "channels": channels,
        },
        model=model,
        benchmark=benchmark,
    )


def _validate_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    invariants: list[dict[str, Any]] = []
    previous_outer = 0.0
    for layer in layers:
        inner = float(layer.get("inner_radius", previous_outer))
        outer = float(layer["outer_radius"])
        passed = inner >= previous_outer and outer > inner
        invariants.append(
            {
                "name": f"monotonic_radius::{layer['name']}",
                "passed": passed,
                "message": (
                    f"Layer {layer['name']} radii are monotonic."
                    if passed
                    else f"Layer {layer['name']} has invalid radii ({inner}, {outer})."
                ),
            }
        )
        previous_outer = outer
    return invariants


def _create_materials(config: CaseConfig):
    materials = {}
    for name, spec in config.materials.items():
        material = openmc.Material(name=spec.get("name", name))
        density = spec.get("density")
        if density:
            density_temperature_c = property_reference_temperature_c(
                config.reactor,
                density,
                require_declared=True,
            )
            density_value_si = evaluate_property(
                density,
                temperature_c=density_temperature_c,
                expected_quantity="density",
            )
            material.set_density("kg/m3", density_value_si)
        for nuclide in spec.get("nuclides", []):
            material.add_nuclide(nuclide["name"], nuclide["ao"])
        for element in spec.get("elements", []):
            material.add_element(element["name"], element["ao"])
        for sab in spec.get("sab", []):
            material.add_s_alpha_beta(sab)
        materials[name] = material
    return materials


def _create_openmc_pin_model(config: CaseConfig):
    materials = _create_materials(config)
    geometry = config.geometry
    layers = geometry["layers"]
    surfaces = []
    cells = []
    cell_lookup = {}

    for layer in layers:
        surfaces.append(openmc.ZCylinder(r=layer["outer_radius"]))

    previous_surface = None
    for layer, surface in zip(layers, surfaces):
        cell = openmc.Cell(name=layer["name"])
        if layer.get("material"):
            cell.fill = materials[layer["material"]]
        cell.region = -surface if previous_surface is None else +previous_surface & -surface
        previous_surface = surface
        cell_lookup[layer["name"]] = cell
        if layer.get("tag_as"):
            cell_lookup[layer["tag_as"]] = cell
        cells.append(cell)

    pitch = geometry["pitch"]
    boundary = geometry.get("boundary", "reflective")
    left = openmc.XPlane(x0=-pitch / 2.0, boundary_type=boundary)
    right = openmc.XPlane(x0=pitch / 2.0, boundary_type=boundary)
    bottom = openmc.YPlane(y0=-pitch / 2.0, boundary_type=boundary)
    top = openmc.YPlane(y0=pitch / 2.0, boundary_type=boundary)
    background = openmc.Cell(name=geometry.get("background_name", "background"))
    background.fill = materials[geometry["background_material"]]
    background.region = +left & -right & +bottom & -top & +surfaces[-1]
    cells.append(background)
    cell_lookup[background.name] = background

    universe = openmc.Universe(cells=cells)
    model = openmc.Model()
    model.materials = openmc.Materials(list(materials.values()))
    model.geometry = openmc.Geometry(universe)
    model.settings = _create_settings(config)
    model.tallies = _create_tallies(config, cell_lookup)
    return model


def _create_openmc_ring_core_model(config: CaseConfig):
    materials = _create_materials(config)
    geometry = config.geometry
    channel_layers = geometry["channel_layers"]
    core_radius = geometry["core_radius"]
    boundary = geometry.get("boundary", "reflective")
    core_surface = openmc.ZCylinder(r=core_radius)
    root_cells = []
    channel_surfaces = []
    cell_lookup = {}

    for ring in geometry["rings"]:
        radius = float(ring["radius"])
        count = int(ring["count"])
        positions = [(0.0, 0.0)] if count == 1 else [
            (
                radius * math.cos(2.0 * math.pi * index / count),
                radius * math.sin(2.0 * math.pi * index / count),
            )
            for index in range(count)
        ]
        for x_pos, y_pos in positions:
            previous_surface = None
            outermost_surface = None
            for layer in channel_layers:
                surface = openmc.ZCylinder(x0=x_pos, y0=y_pos, r=layer["outer_radius"])
                cell = openmc.Cell(name=f"{layer['name']}_{len(root_cells)}")
                if layer.get("material"):
                    cell.fill = materials[layer["material"]]
                cell.region = -surface if previous_surface is None else +previous_surface & -surface
                root_cells.append(cell)
                cell_lookup[layer["name"]] = cell
                if layer.get("tag_as") and layer["tag_as"] not in cell_lookup:
                    cell_lookup[layer["tag_as"]] = cell
                previous_surface = surface
                outermost_surface = surface
            if outermost_surface is not None:
                channel_surfaces.append(+outermost_surface)

    matrix_region = -core_surface
    for surface in channel_surfaces:
        matrix_region &= surface
    matrix = openmc.Cell(name="core_matrix")
    matrix.fill = materials[geometry["matrix_material"]]
    matrix.region = matrix_region
    root_cells.append(matrix)
    cell_lookup["core_matrix"] = matrix

    pitch = geometry["pitch"]
    left = openmc.XPlane(x0=-pitch / 2.0, boundary_type=boundary)
    right = openmc.XPlane(x0=pitch / 2.0, boundary_type=boundary)
    bottom = openmc.YPlane(y0=-pitch / 2.0, boundary_type=boundary)
    top = openmc.YPlane(y0=pitch / 2.0, boundary_type=boundary)
    outside = openmc.Cell(name="outside")
    outside.fill = materials[geometry["background_material"]]
    outside.region = +left & -right & +bottom & -top & +core_surface
    root_cells.append(outside)

    universe = openmc.Universe(cells=root_cells)
    model = openmc.Model()
    model.materials = openmc.Materials(list(materials.values()))
    model.geometry = openmc.Geometry(universe)
    model.settings = _create_settings(config)
    model.tallies = _create_tallies(config, cell_lookup)
    return model


def _create_openmc_detailed_msr_model(config: CaseConfig, resolved) -> Any:
    materials = _create_materials(config)
    geometry = config.geometry
    boundary = geometry.get("boundary", "reflective")
    axial_boundary = geometry.get("axial_boundary", "vacuum")
    salt_material = geometry.get("salt_material", "fuel_salt")
    structure_material = geometry.get("structure_material", "pipe")

    core_surface = openmc.ZCylinder(r=resolved.core_radius)
    plenum_surface = openmc.ZCylinder(r=resolved.plenum_radius)
    reflector_outer_surface = openmc.ZCylinder(r=resolved.reflector_outer_radius)
    liner_outer_surface = openmc.ZCylinder(r=resolved.downcomer_liner_outer_radius)
    downcomer_outer_surface = openmc.ZCylinder(r=resolved.downcomer_outer_radius)
    vessel_outer_surface = openmc.ZCylinder(r=resolved.vessel_outer_radius)
    guard_gap_outer_surface = openmc.ZCylinder(r=resolved.guard_gap_outer_radius)
    guard_vessel_outer_surface = openmc.ZCylinder(r=resolved.guard_vessel_outer_radius)

    x_left = openmc.XPlane(x0=-geometry["pitch"] / 2.0, boundary_type=boundary)
    x_right = openmc.XPlane(x0=geometry["pitch"] / 2.0, boundary_type=boundary)
    y_bottom = openmc.YPlane(y0=-geometry["pitch"] / 2.0, boundary_type=boundary)
    y_top = openmc.YPlane(y0=geometry["pitch"] / 2.0, boundary_type=boundary)
    z_bottom = openmc.ZPlane(z0=resolved.bottom_z, boundary_type=axial_boundary)
    z_lower_plenum_top = openmc.ZPlane(z0=resolved.lower_plenum_top_z)
    z_active_top = openmc.ZPlane(z0=resolved.active_top_z)
    z_upper_plenum_top = openmc.ZPlane(z0=resolved.upper_plenum_top_z)
    z_top = openmc.ZPlane(z0=resolved.top_z, boundary_type=axial_boundary)

    axial_active_region = +z_lower_plenum_top & -z_active_top
    axial_lower_region = +z_bottom & -z_lower_plenum_top
    axial_upper_plenum_region = +z_active_top & -z_upper_plenum_top
    axial_cover_gas_region = +z_upper_plenum_top & -z_top
    full_height_region = +z_bottom & -z_top
    bounding_region = +x_left & -x_right & +y_bottom & -y_top & full_height_region

    root_cells = []
    cell_lookup = {}
    channel_outer_regions = []

    for channel in resolved.channels:
        previous_surface = None
        outermost_surface = None
        for layer in channel["layers"]:
            surface = openmc.ZCylinder(x0=channel["x"], y0=channel["y"], r=layer["outer_radius"])
            cell = openmc.Cell(name=f"{channel['name']}::{layer['name']}")
            if layer.get("material"):
                cell.fill = materials[layer["material"]]
            cell.region = (-surface if previous_surface is None else +previous_surface & -surface) & axial_active_region
            root_cells.append(cell)
            if layer["name"] not in cell_lookup:
                cell_lookup[layer["name"]] = cell
            if layer.get("tag_as") and layer["tag_as"] not in cell_lookup:
                cell_lookup[layer["tag_as"]] = cell
            previous_surface = surface
            outermost_surface = surface
        if outermost_surface is not None:
            channel_outer_regions.append(+outermost_surface)

    active_matrix_region = -core_surface & axial_active_region
    for outer_region in channel_outer_regions:
        active_matrix_region &= outer_region

    active_matrix = openmc.Cell(name="core_matrix")
    active_matrix.fill = materials[geometry["matrix_material"]]
    active_matrix.region = active_matrix_region
    root_cells.append(active_matrix)
    cell_lookup["core_matrix"] = active_matrix

    lower_plenum = openmc.Cell(name="lower_plenum")
    lower_plenum.fill = materials[salt_material]
    lower_plenum.region = -plenum_surface & axial_lower_region
    root_cells.append(lower_plenum)

    lower_reflector = openmc.Cell(name="lower_reflector")
    lower_reflector.fill = materials[geometry["matrix_material"]]
    lower_reflector.region = +plenum_surface & -reflector_outer_surface & axial_lower_region
    root_cells.append(lower_reflector)

    radial_reflector = openmc.Cell(name="radial_reflector")
    radial_reflector.fill = materials[geometry["matrix_material"]]
    radial_reflector.region = +core_surface & -reflector_outer_surface & axial_active_region
    root_cells.append(radial_reflector)

    upper_plenum = openmc.Cell(name="upper_plenum")
    upper_plenum.fill = materials[salt_material]
    upper_plenum.region = -plenum_surface & axial_upper_plenum_region
    root_cells.append(upper_plenum)

    upper_reflector = openmc.Cell(name="upper_reflector")
    upper_reflector.fill = materials[geometry["matrix_material"]]
    upper_reflector.region = +plenum_surface & -reflector_outer_surface & (axial_upper_plenum_region | axial_cover_gas_region)
    root_cells.append(upper_reflector)

    cover_gas = openmc.Cell(name="cover_gas")
    cover_gas.fill = materials[geometry["background_material"]]
    cover_gas.region = -plenum_surface & axial_cover_gas_region
    root_cells.append(cover_gas)

    downcomer_liner = openmc.Cell(name="downcomer_liner")
    downcomer_liner.fill = materials[structure_material]
    downcomer_liner.region = +reflector_outer_surface & -liner_outer_surface & full_height_region
    root_cells.append(downcomer_liner)

    downcomer = openmc.Cell(name="downcomer")
    downcomer.fill = materials[salt_material]
    downcomer.region = +liner_outer_surface & -downcomer_outer_surface & full_height_region
    root_cells.append(downcomer)

    reactor_vessel = openmc.Cell(name="reactor_vessel")
    reactor_vessel.fill = materials[structure_material]
    reactor_vessel.region = +downcomer_outer_surface & -vessel_outer_surface & full_height_region
    root_cells.append(reactor_vessel)

    guard_gap = openmc.Cell(name="guard_gap")
    guard_gap.fill = materials[geometry["background_material"]]
    guard_gap.region = +vessel_outer_surface & -guard_gap_outer_surface & full_height_region
    root_cells.append(guard_gap)

    guard_vessel = openmc.Cell(name="guard_vessel")
    guard_vessel.fill = materials[structure_material]
    guard_vessel.region = +guard_gap_outer_surface & -guard_vessel_outer_surface & full_height_region
    root_cells.append(guard_vessel)

    outside = openmc.Cell(name="outside")
    outside.fill = materials[geometry["background_material"]]
    outside.region = bounding_region & +guard_vessel_outer_surface
    root_cells.append(outside)

    universe = openmc.Universe(cells=root_cells)
    model = openmc.Model()
    model.materials = openmc.Materials(list(materials.values()))
    model.geometry = openmc.Geometry(universe)
    model.settings = _create_settings(config)
    model.tallies = _create_tallies(config, cell_lookup)
    return model


def _create_settings(config: CaseConfig):
    simulation = config.simulation
    settings = openmc.Settings()
    settings.run_mode = simulation.get("mode", "eigenvalue")
    settings.particles = simulation["particles"]
    settings.batches = simulation["batches"]
    settings.inactive = simulation.get("inactive", 0)
    source = simulation.get("source", {"type": "point", "parameters": [0.0, 0.0, 0.0]})
    if source["type"] == "point":
        settings.source = openmc.IndependentSource(space=openmc.stats.Point(tuple(source["parameters"])))
    return settings


def _create_tallies(config: CaseConfig, cell_lookup: dict[str, Any]):
    tallies = []
    for index, spec in enumerate(config.simulation.get("tallies", []), start=1):
        tally = openmc.Tally(name=spec.get("name", f"tally_{index}"))
        tally.filters = [openmc.CellFilter(cell_lookup[spec["cell"]])]
        tally.scores = spec["scores"]
        if spec.get("nuclides"):
            tally.nuclides = spec["nuclides"]
        tallies.append(tally)
    return openmc.Tallies(tallies)
