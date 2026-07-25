from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from thorium_reactor.precursors import normalize_precursor_groups

RKDG_TRANSPORT_MODEL = "native_rz_rkdg_scalar_transport_v1"
TRANSPORT_NPZ_SCHEMA_VERSION = 1
DEFAULT_DECAY_HEAT_PRECURSOR_GROUPS: tuple[dict[str, float | str], ...] = (
    {"name": "decay_heat_fast", "decay_constant_s": 0.21, "yield_fraction": 0.38},
    {"name": "decay_heat_medium", "decay_constant_s": 0.030, "yield_fraction": 0.37},
    {"name": "decay_heat_slow", "decay_constant_s": 0.0030, "yield_fraction": 0.25},
)


@dataclass(slots=True)
class RZStructuredMesh:
    radial_edges_m: np.ndarray
    axial_edges_m: np.ndarray

    @property
    def radial_cells(self) -> int:
        return int(self.radial_edges_m.size - 1)

    @property
    def axial_cells(self) -> int:
        return int(self.axial_edges_m.size - 1)

    @property
    def radial_centers_m(self) -> np.ndarray:
        return 0.5 * (self.radial_edges_m[:-1] + self.radial_edges_m[1:])

    @property
    def axial_centers_m(self) -> np.ndarray:
        return 0.5 * (self.axial_edges_m[:-1] + self.axial_edges_m[1:])

    @property
    def dz_m(self) -> float:
        return float(np.min(np.diff(self.axial_edges_m)))

    @property
    def dr_m(self) -> float:
        return float(np.min(np.diff(self.radial_edges_m)))

    @property
    def cell_volumes_m3(self) -> np.ndarray:
        radial_shell_areas = np.pi * (self.radial_edges_m[1:] ** 2 - self.radial_edges_m[:-1] ** 2)
        axial_heights = np.diff(self.axial_edges_m)
        return axial_heights[:, None] * radial_shell_areas[None, :]

    def to_json(self) -> dict[str, Any]:
        return {
            "mesh": "rz_structured",
            "radial_cells": self.radial_cells,
            "axial_cells": self.axial_cells,
            "radial_edges_m": [_round_float(value) for value in self.radial_edges_m],
            "axial_edges_m": [_round_float(value) for value in self.axial_edges_m],
            "radial_extent_m": _round_float(float(self.radial_edges_m[-1])),
            "axial_extent_m": _round_float(float(self.axial_edges_m[-1] - self.axial_edges_m[0])),
            "cell_volume_m3": {
                "min": _round_float(float(np.min(self.cell_volumes_m3))),
                "max": _round_float(float(np.max(self.cell_volumes_m3))),
                "total": _round_float(float(np.sum(self.cell_volumes_m3))),
            },
            "axis_boundary": "finite_no_flux",
            "wall_boundary": "zero_normal_flux",
            "axial_boundary": "upwind_inlet_outlet",
        }


@dataclass(frozen=True, slots=True)
class TransportFieldSpec:
    name: str
    group_set: str
    decay_constant_s: float
    yield_fraction: float


@dataclass(slots=True)
class TransportResult:
    mesh: RZStructuredMesh
    field_specs: list[TransportFieldSpec]
    field_values: np.ndarray
    summary: dict[str, Any]


def build_rz_mesh(
    config: Any | None = None,
    *,
    radial_cells: int = 24,
    axial_cells: int = 48,
    radius_m: float | None = None,
    height_m: float | None = None,
) -> RZStructuredMesh:
    geometry = getattr(config, "geometry", {}) if config is not None else {}
    if radius_m is None:
        radius_cm = _first_number(
            geometry,
            ("core_radius", "plenum_radius", "vessel_outer_radius", "reflector_outer_radius"),
            default=20.5,
        )
        radius_m = radius_cm / 100.0
    if height_m is None:
        height_cm = _first_number(geometry, ("active_core_height_cm", "height_cm"), default=52.0)
        height_m = height_cm / 100.0
    if radial_cells <= 0 or axial_cells <= 0:
        raise ValueError("R-Z mesh radial_cells and axial_cells must be positive.")
    if radius_m <= 0.0 or height_m <= 0.0:
        raise ValueError("R-Z mesh radius_m and height_m must be positive.")
    return RZStructuredMesh(
        radial_edges_m=np.linspace(0.0, float(radius_m), int(radial_cells) + 1),
        axial_edges_m=np.linspace(0.0, float(height_m), int(axial_cells) + 1),
    )


def solve_transport_fields(
    mesh: RZStructuredMesh,
    field_specs: Sequence[TransportFieldSpec],
    *,
    duration_s: float,
    time_step_s: float,
    velocity_z_m_s: float = 0.0,
    diffusion_coefficient_m2_s: float = 0.0,
    cleanup_rate_s: float = 0.0,
    source_density: np.ndarray | None = None,
    initial_fields: np.ndarray | None = None,
    polynomial_order: int = 1,
    positivity_floor: float = 0.0,
) -> TransportResult:
    if not field_specs:
        raise ValueError("At least one transported field is required.")
    if duration_s < 0.0:
        raise ValueError("duration_s must be non-negative.")
    if time_step_s <= 0.0:
        raise ValueError("time_step_s must be positive.")
    if polynomial_order < 0:
        raise ValueError("polynomial_order must be non-negative.")

    shape = (len(field_specs), mesh.axial_cells, mesh.radial_cells)
    if initial_fields is None:
        fields = np.zeros(shape, dtype=float)
    else:
        fields = np.array(initial_fields, dtype=float, copy=True)
        if fields.shape != shape:
            raise ValueError(f"initial_fields must have shape {shape}.")
    if source_density is None:
        source = np.zeros(shape, dtype=float)
    else:
        source = np.array(source_density, dtype=float, copy=True)
        if source.shape != shape:
            raise ValueError(f"source_density must have shape {shape}.")

    volumes = mesh.cell_volumes_m3
    initial_inventory = _field_masses(fields, volumes)
    source_integral = np.zeros(len(field_specs), dtype=float)
    decay_integral = np.zeros(len(field_specs), dtype=float)
    cleanup_integral = np.zeros(len(field_specs), dtype=float)
    outlet_integral = np.zeros(len(field_specs), dtype=float)
    minimum_value = float(np.min(fields)) if fields.size else 0.0

    elapsed = 0.0
    step_count = 0
    while elapsed < duration_s - 1.0e-15:
        dt = min(float(time_step_s), float(duration_s) - elapsed)
        source_integral += np.sum(source * volumes[None, :, :], axis=(1, 2)) * dt
        decay_integral += _decay_rates(fields, field_specs, volumes) * dt
        cleanup_integral += np.sum(max(cleanup_rate_s, 0.0) * fields * volumes[None, :, :], axis=(1, 2)) * dt
        outlet_integral += _outlet_rates(fields, mesh, velocity_z_m_s) * dt
        fields = _ssp_rk3_step(
            fields,
            dt,
            mesh,
            field_specs,
            source,
            velocity_z_m_s=velocity_z_m_s,
            diffusion_coefficient_m2_s=diffusion_coefficient_m2_s,
            cleanup_rate_s=cleanup_rate_s,
            positivity_floor=positivity_floor,
        )
        minimum_value = min(minimum_value, float(np.min(fields)))
        elapsed += dt
        step_count += 1

    final_inventory = _field_masses(fields, volumes)
    expected_final = initial_inventory + source_integral - decay_integral - cleanup_integral - outlet_integral
    scale = np.maximum(np.maximum(np.abs(initial_inventory), np.abs(final_inventory)), 1.0)
    residuals = np.abs(final_inventory - expected_final) / scale
    dof_per_cell = (int(polynomial_order) + 1) ** 2
    field_summaries: list[dict[str, Any]] = []
    for index, spec in enumerate(field_specs):
        field_summaries.append(
            {
                "name": spec.name,
                "group_set": spec.group_set,
                "decay_constant_s": _round_float(spec.decay_constant_s),
                "yield_fraction": _round_float(spec.yield_fraction),
                "initial_inventory": _round_float(initial_inventory[index]),
                "final_inventory": _round_float(final_inventory[index]),
                "source_integral": _round_float(source_integral[index]),
                "decay_integral": _round_float(decay_integral[index]),
                "cleanup_integral": _round_float(cleanup_integral[index]),
                "outlet_integral": _round_float(outlet_integral[index]),
                "balance_residual": _round_float(residuals[index]),
            }
        )
    summary = {
        "status": "completed",
        "model": RKDG_TRANSPORT_MODEL,
        "mesh": {
            "type": "rz_structured",
            "radial_cells": mesh.radial_cells,
            "axial_cells": mesh.axial_cells,
            "radial_extent_m": _round_float(float(mesh.radial_edges_m[-1])),
            "axial_extent_m": _round_float(float(mesh.axial_edges_m[-1] - mesh.axial_edges_m[0])),
        },
        "polynomial_order": int(polynomial_order),
        "dof_per_cell": dof_per_cell,
        "time_integration": "ssp_rk3",
        "duration_s": _round_float(duration_s),
        "time_step_s": _round_float(time_step_s),
        "step_count": step_count,
        "velocity_z_m_s": _round_float(velocity_z_m_s),
        "diffusion_coefficient_m2_s": _round_float(diffusion_coefficient_m2_s),
        "cleanup_rate_s": _round_float(cleanup_rate_s),
        "limiter": "positivity_preserving_floor",
        "minimum_field_value": _round_float(max(minimum_value, positivity_floor)),
        "conservation_residual": _round_float(float(np.max(residuals)) if residuals.size else 0.0),
        "initial_inventory": _round_float(float(np.sum(initial_inventory))),
        "final_inventory": _round_float(float(np.sum(final_inventory))),
        "source_integral": _round_float(float(np.sum(source_integral))),
        "decay_integral": _round_float(float(np.sum(decay_integral))),
        "cleanup_integral": _round_float(float(np.sum(cleanup_integral))),
        "outlet_integral": _round_float(float(np.sum(outlet_integral))),
        "fields": field_summaries,
    }
    return TransportResult(mesh=mesh, field_specs=list(field_specs), field_values=fields, summary=summary)


def run_transport_case(config: Any, bundle: Any, summary: dict[str, Any]) -> dict[str, Any]:
    settings = _transport_settings(config)
    mesh = build_rz_mesh(
        config,
        radial_cells=int(settings.get("radial_cells", 24)),
        axial_cells=int(settings.get("axial_cells", 48)),
    )
    specs = _field_specs(config, settings)
    velocity = _representative_velocity(summary, settings)
    diffusion = max(float(settings.get("diffusion_coefficient_m2_s", 1.0e-5)), 0.0)
    cfl = max(float(settings.get("cfl", 0.35)), 1.0e-6)
    duration = max(float(settings.get("duration_s", 2.0)), 0.0)
    configured_dt = settings.get("time_step_s")
    if configured_dt is None:
        time_step = _stable_time_step(mesh, cfl=cfl, velocity_z_m_s=velocity, diffusion_coefficient_m2_s=diffusion)
    else:
        time_step = float(configured_dt)
    time_step = min(max(time_step, 1.0e-9), max(duration, 1.0e-9))
    polynomial_order = int(settings.get("polynomial_order", 1))
    cleanup_rate = max(float(settings.get("cleanup_rate_s", _cleanup_rate_from_summary(summary))), 0.0)
    source_density = _source_density(mesh, specs, summary)
    initial_fields = _initial_fields(mesh, specs, source_density, cleanup_rate_s=cleanup_rate)
    result = solve_transport_fields(
        mesh,
        specs,
        duration_s=duration,
        time_step_s=time_step,
        velocity_z_m_s=velocity,
        diffusion_coefficient_m2_s=diffusion,
        cleanup_rate_s=cleanup_rate,
        source_density=source_density,
        initial_fields=initial_fields,
        polynomial_order=polynomial_order,
        positivity_floor=max(float(settings.get("positivity_floor", 0.0)), 0.0),
    )

    group_sets: dict[str, dict[str, float]] = {}
    for field in result.summary["fields"]:
        group = field["group_set"]
        record = group_sets.setdefault(
            group,
            {
                "initial_inventory": 0.0,
                "final_inventory": 0.0,
                "source_integral": 0.0,
                "decay_integral": 0.0,
                "outlet_integral": 0.0,
            },
        )
        for key in ("initial_inventory", "final_inventory", "source_integral", "decay_integral", "outlet_integral"):
            record[key] += float(field[key])
    source_fractions = {}
    for group, record in group_sets.items():
        total_source = max(record["decay_integral"] + record["outlet_integral"], 1.0e-30)
        source_fractions[group] = {
            "decay_source_fraction": _round_float(record["decay_integral"] / total_source),
            "outlet_source_fraction": _round_float(record["outlet_integral"] / total_source),
            "final_inventory_fraction": _round_float(
                record["final_inventory"] / max(result.summary["final_inventory"], 1.0e-30)
            ),
        }

    mesh_path = bundle.write_json("transport_mesh.json", mesh.to_json())
    transport_summary = dict(result.summary)
    transport_summary.update(
        {
            "cfl": _round_float(cfl),
            "field_count": len(specs),
            "group_sets": sorted(group_sets),
            "source_fractions": source_fractions,
            "artifacts": {
                "mesh_path": str(mesh_path.name),
                "summary_path": "transport_summary.json",
                "solution_path": "transport_solution.npz",
                "solution_schema_path": "transport_solution.schema.json",
                "solution_human_summary_path": "transport_solution.summary.md",
            },
            "coupling_status": "additive_artifact_not_coupled_to_reduced_order_physics_core",
        }
    )
    bundle.write_json("transport_summary.json", transport_summary)
    _write_solution_npz(bundle.root / "transport_solution.npz", result)
    summary["transport_solver"] = transport_summary
    metrics = summary.setdefault("metrics", {})
    metrics["transport_rkdg_radial_cells"] = mesh.radial_cells
    metrics["transport_rkdg_axial_cells"] = mesh.axial_cells
    metrics["transport_rkdg_conservation_residual"] = transport_summary["conservation_residual"]
    metrics["transport_rkdg_minimum_field_value"] = transport_summary["minimum_field_value"]
    bundle.write_json("summary.json", summary)
    bundle.write_metrics(metrics)
    return transport_summary


def _ssp_rk3_step(
    fields: np.ndarray,
    dt: float,
    mesh: RZStructuredMesh,
    field_specs: Sequence[TransportFieldSpec],
    source: np.ndarray,
    *,
    velocity_z_m_s: float,
    diffusion_coefficient_m2_s: float,
    cleanup_rate_s: float,
    positivity_floor: float,
) -> np.ndarray:
    u0 = fields
    u1 = _enforce_floor(
        u0 + dt * _rhs(u0, mesh, field_specs, source, velocity_z_m_s, diffusion_coefficient_m2_s, cleanup_rate_s),
        positivity_floor,
    )
    u2 = _enforce_floor(
        0.75 * u0
        + 0.25
        * (u1 + dt * _rhs(u1, mesh, field_specs, source, velocity_z_m_s, diffusion_coefficient_m2_s, cleanup_rate_s)),
        positivity_floor,
    )
    return _enforce_floor(
        (1.0 / 3.0) * u0
        + (2.0 / 3.0)
        * (u2 + dt * _rhs(u2, mesh, field_specs, source, velocity_z_m_s, diffusion_coefficient_m2_s, cleanup_rate_s)),
        positivity_floor,
    )


def _rhs(
    fields: np.ndarray,
    mesh: RZStructuredMesh,
    field_specs: Sequence[TransportFieldSpec],
    source: np.ndarray,
    velocity_z_m_s: float,
    diffusion_coefficient_m2_s: float,
    cleanup_rate_s: float,
) -> np.ndarray:
    rhs = np.array(source, dtype=float, copy=True)
    for index, spec in enumerate(field_specs):
        rhs[index] -= (max(spec.decay_constant_s, 0.0) + max(cleanup_rate_s, 0.0)) * fields[index]
    if abs(velocity_z_m_s) > 0.0:
        rhs -= _axial_upwind_divergence(fields, mesh, velocity_z_m_s)
    if diffusion_coefficient_m2_s > 0.0:
        rhs += diffusion_coefficient_m2_s * _axisymmetric_laplacian(fields, mesh)
    return rhs


def _axial_upwind_divergence(fields: np.ndarray, mesh: RZStructuredMesh, velocity_z_m_s: float) -> np.ndarray:
    dz = np.diff(mesh.axial_edges_m)
    face_flux = np.zeros((fields.shape[0], mesh.axial_cells + 1, mesh.radial_cells), dtype=float)
    if velocity_z_m_s >= 0.0:
        face_flux[:, 1:-1, :] = velocity_z_m_s * fields[:, :-1, :]
        face_flux[:, -1, :] = velocity_z_m_s * fields[:, -1, :]
    else:
        face_flux[:, 1:-1, :] = velocity_z_m_s * fields[:, 1:, :]
        face_flux[:, 0, :] = velocity_z_m_s * fields[:, 0, :]
    return (face_flux[:, 1:, :] - face_flux[:, :-1, :]) / dz[None, :, None]


def _axisymmetric_laplacian(fields: np.ndarray, mesh: RZStructuredMesh) -> np.ndarray:
    volumes = mesh.cell_volumes_m3
    radial_edges = mesh.radial_edges_m
    axial_edges = mesh.axial_edges_m
    dr = np.diff(radial_edges)
    dz = np.diff(axial_edges)
    lap = np.zeros_like(fields)

    for radial_index in range(1, mesh.radial_cells):
        r_face = radial_edges[radial_index]
        left_dr = 0.5 * (dr[radial_index - 1] + dr[radial_index])
        flux = (
            2.0 * np.pi * r_face * dz[None, :] * (fields[:, :, radial_index] - fields[:, :, radial_index - 1]) / left_dr
        )
        lap[:, :, radial_index - 1] += flux / volumes[None, :, radial_index - 1]
        lap[:, :, radial_index] -= flux / volumes[None, :, radial_index]

    for axial_index in range(1, mesh.axial_cells):
        z_dr = 0.5 * (dz[axial_index - 1] + dz[axial_index])
        shell_area = np.pi * (radial_edges[1:] ** 2 - radial_edges[:-1] ** 2)
        flux = shell_area[None, :] * (fields[:, axial_index, :] - fields[:, axial_index - 1, :]) / z_dr
        lap[:, axial_index - 1, :] += flux / volumes[None, axial_index - 1, :]
        lap[:, axial_index, :] -= flux / volumes[None, axial_index, :]
    return lap


def _field_masses(fields: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    return np.sum(fields * volumes[None, :, :], axis=(1, 2))


def _decay_rates(fields: np.ndarray, field_specs: Sequence[TransportFieldSpec], volumes: np.ndarray) -> np.ndarray:
    rates = np.zeros(len(field_specs), dtype=float)
    for index, spec in enumerate(field_specs):
        rates[index] = max(spec.decay_constant_s, 0.0) * float(np.sum(fields[index] * volumes))
    return rates


def _outlet_rates(fields: np.ndarray, mesh: RZStructuredMesh, velocity_z_m_s: float) -> np.ndarray:
    if velocity_z_m_s == 0.0:
        return np.zeros(fields.shape[0], dtype=float)
    radial_shell_areas = np.pi * (mesh.radial_edges_m[1:] ** 2 - mesh.radial_edges_m[:-1] ** 2)
    if velocity_z_m_s > 0.0:
        outlet_values = fields[:, -1, :]
        return velocity_z_m_s * np.sum(outlet_values * radial_shell_areas[None, :], axis=1)
    outlet_values = fields[:, 0, :]
    return abs(velocity_z_m_s) * np.sum(outlet_values * radial_shell_areas[None, :], axis=1)


def _enforce_floor(fields: np.ndarray, positivity_floor: float) -> np.ndarray:
    if positivity_floor <= 0.0:
        return np.maximum(fields, 0.0)
    return np.maximum(fields, positivity_floor)


def _transport_settings(config: Any) -> dict[str, Any]:
    data = getattr(config, "data", {})
    settings = data.get("transport_solver", {})
    return dict(settings) if isinstance(settings, Mapping) else {}


def _field_specs(config: Any, settings: Mapping[str, Any]) -> list[TransportFieldSpec]:
    transient = getattr(config, "data", {}).get("transient", {})
    physics_core = getattr(config, "data", {}).get("physics_core", {})
    precursor_transport = physics_core.get("precursor_transport", {}) if isinstance(physics_core, Mapping) else {}
    delayed_groups = normalize_precursor_groups(
        transient.get("delayed_neutron_precursor_groups") if isinstance(transient, Mapping) else None
    )
    decay_heat_groups = _normalize_group_set(
        precursor_transport.get("decay_heat_groups") if isinstance(precursor_transport, Mapping) else None,
        DEFAULT_DECAY_HEAT_PRECURSOR_GROUPS,
    )
    specs: list[TransportFieldSpec] = []
    for group in delayed_groups:
        specs.append(
            TransportFieldSpec(
                name=f"dnp_{group['name']}",
                group_set="delayed_neutron_precursors",
                decay_constant_s=float(group["decay_constant_s"]),
                yield_fraction=float(group["relative_yield_fraction"]),
            )
        )
    for group in decay_heat_groups:
        specs.append(
            TransportFieldSpec(
                name=str(group["name"]),
                group_set="decay_heat_precursors",
                decay_constant_s=float(group["decay_constant_s"]),
                yield_fraction=float(group["relative_yield_fraction"]),
            )
        )
    for custom in settings.get("custom_group_sets", []) if isinstance(settings.get("custom_group_sets"), list) else []:
        if not isinstance(custom, Mapping):
            continue
        group_set = str(custom.get("name", "custom"))
        for group in _normalize_group_set(custom.get("groups"), ()):
            specs.append(
                TransportFieldSpec(
                    name=f"{group_set}_{group['name']}",
                    group_set=group_set,
                    decay_constant_s=float(group["decay_constant_s"]),
                    yield_fraction=float(group["relative_yield_fraction"]),
                )
            )
    return specs


def _normalize_group_set(
    raw_groups: Any | None, default_groups: Sequence[Mapping[str, float | str]]
) -> list[dict[str, float | str]]:
    groups = list(default_groups if raw_groups is None else raw_groups)
    if not groups:
        return []
    total = 0.0
    normalized: list[dict[str, float | str]] = []
    for index, group in enumerate(groups, start=1):
        if not isinstance(group, Mapping):
            raise ValueError(f"Transport group {index} must be a mapping.")
        decay_constant_s = float(group.get("decay_constant_s", 0.0))
        yield_fraction = float(group.get("yield_fraction", 0.0))
        if decay_constant_s <= 0.0:
            raise ValueError(f"Transport group {index} must have positive decay_constant_s.")
        if yield_fraction < 0.0:
            raise ValueError(f"Transport group {index} must have non-negative yield_fraction.")
        total += yield_fraction
        normalized.append(
            {
                "name": str(group.get("name", f"group_{index}")),
                "decay_constant_s": decay_constant_s,
                "yield_fraction": yield_fraction,
            }
        )
    if total <= 0.0:
        raise ValueError("Transport groups must have positive total yield_fraction.")
    for group in normalized:
        group["relative_yield_fraction"] = float(group["yield_fraction"]) / total
    return normalized


def _representative_velocity(summary: Mapping[str, Any], settings: Mapping[str, Any]) -> float:
    if "velocity_z_m_s" in settings:
        return float(settings["velocity_z_m_s"]) * max(float(settings.get("flow_fraction", 1.0)), 0.0)
    reduced = summary.get("flow", {}).get("reduced_order", {}) if isinstance(summary.get("flow"), Mapping) else {}
    active_flow = reduced.get("active_flow", {}) if isinstance(reduced, Mapping) else {}
    velocity = active_flow.get("representative_velocity_m_s")
    if velocity is None:
        velocity = summary.get("primary_system", {}).get("loop_hydraulics", {}).get("limiting_velocity_m_s", 0.02)
    return float(velocity) * max(float(settings.get("flow_fraction", 1.0)), 0.0)


def _cleanup_rate_from_summary(summary: Mapping[str, Any]) -> float:
    cleanup_days = (
        summary.get("fuel_cycle", {}).get("cleanup_turnover_days")
        if isinstance(summary.get("fuel_cycle"), Mapping)
        else None
    )
    if cleanup_days is None:
        return 0.0
    cleanup_efficiency = float(summary.get("fuel_cycle", {}).get("cleanup_removal_efficiency", 0.0))
    return max(cleanup_efficiency, 0.0) / max(float(cleanup_days) * 86400.0, 1.0e-12)


def _stable_time_step(
    mesh: RZStructuredMesh,
    *,
    cfl: float,
    velocity_z_m_s: float,
    diffusion_coefficient_m2_s: float,
) -> float:
    advective = np.inf if abs(velocity_z_m_s) <= 0.0 else cfl * mesh.dz_m / abs(velocity_z_m_s)
    diffusive = (
        np.inf
        if diffusion_coefficient_m2_s <= 0.0
        else cfl * min(mesh.dr_m, mesh.dz_m) ** 2 / max(4.0 * diffusion_coefficient_m2_s, 1.0e-30)
    )
    return float(min(advective, diffusive, 0.25))


def _source_density(
    mesh: RZStructuredMesh, specs: Sequence[TransportFieldSpec], summary: Mapping[str, Any]
) -> np.ndarray:
    axial_shape = _axial_power_shape(mesh, summary)
    radial_shape = np.ones(mesh.radial_cells, dtype=float)
    base_shape = axial_shape[:, None] * radial_shape[None, :]
    volumes = mesh.cell_volumes_m3
    normalized_shape = base_shape / max(float(np.sum(base_shape * volumes)), 1.0e-30)
    source = np.zeros((len(specs), mesh.axial_cells, mesh.radial_cells), dtype=float)
    for index, spec in enumerate(specs):
        source[index] = spec.yield_fraction * normalized_shape
    return source


def _initial_fields(
    mesh: RZStructuredMesh,
    specs: Sequence[TransportFieldSpec],
    source_density: np.ndarray,
    *,
    cleanup_rate_s: float,
) -> np.ndarray:
    fields = np.zeros((len(specs), mesh.axial_cells, mesh.radial_cells), dtype=float)
    for index, spec in enumerate(specs):
        fields[index] = source_density[index] / max(spec.decay_constant_s + cleanup_rate_s, 1.0e-30)
    return fields


def _axial_power_shape(mesh: RZStructuredMesh, summary: Mapping[str, Any]) -> np.ndarray:
    power_shape = None
    physics_core = summary.get("physics_core", {}) if isinstance(summary.get("physics_core"), Mapping) else {}
    neutronics = physics_core.get("neutronics", {}) if isinstance(physics_core, Mapping) else {}
    candidate = neutronics.get("power_shape") if isinstance(neutronics, Mapping) else None
    if isinstance(candidate, list) and candidate:
        power_shape = np.array([max(float(value), 0.0) for value in candidate], dtype=float)
    if power_shape is None or not np.any(power_shape > 0.0):
        z = mesh.axial_centers_m / max(float(mesh.axial_edges_m[-1]), 1.0e-30)
        power_shape = 0.75 + 0.5 * np.sin(np.pi * z)
    source_grid = np.linspace(0.0, 1.0, power_shape.size)
    target_grid = mesh.axial_centers_m / max(float(mesh.axial_edges_m[-1]), 1.0e-30)
    interpolated = np.interp(target_grid, source_grid, power_shape)
    return np.maximum(interpolated, 1.0e-12)


def _write_solution_npz(path: Path, result: TransportResult) -> None:
    arrays = {
        "field_values": result.field_values,
        "field_names": np.array([spec.name for spec in result.field_specs]),
        "group_sets": np.array([spec.group_set for spec in result.field_specs]),
        "decay_constants_s": np.array([spec.decay_constant_s for spec in result.field_specs], dtype=float),
        "radial_edges_m": result.mesh.radial_edges_m,
        "axial_edges_m": result.mesh.axial_edges_m,
        "cell_volumes_m3": result.mesh.cell_volumes_m3,
    }
    np.savez_compressed(path, **arrays)
    schema = _transport_npz_schema(result, arrays)
    _write_npz_sidecars(
        path,
        schema,
        title="Transport Solution NPZ",
        notes=[
            "Coordinates use axisymmetric R-Z ordering with field_values[field, axial_cell, radial_cell].",
            "Cell volumes are in m3 and correspond to axial_cells x radial_cells.",
            f"Maximum conservation residual reported by the solver: {result.summary.get('conservation_residual')}.",
        ],
    )


def _transport_npz_schema(result: TransportResult, arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    return {
        "schema_version": TRANSPORT_NPZ_SCHEMA_VERSION,
        "generator": "thorium_reactor.transport.rzdg._write_solution_npz",
        "artifact": "transport_solution.npz",
        "model": RKDG_TRANSPORT_MODEL,
        "coordinate_conventions": {
            "mesh": "axisymmetric_rz_structured",
            "field_values_order": ["field", "axial_cell", "radial_cell"],
            "radial_coordinate": "radial_edges_m bound radial cells in meters from centerline",
            "axial_coordinate": "axial_edges_m bound axial cells in meters from inlet plane",
        },
        "normalization": {
            "source_density": "source shapes are normalized over cell volumes before field scaling",
            "conservation": "field inventories use field_values times cell_volumes_m3",
            "residual": result.summary.get("conservation_residual"),
        },
        "arrays": {
            name: {
                "shape": list(value.shape),
                "dtype": str(value.dtype),
                "units": _transport_units(name),
                "description": _transport_array_description(name),
            }
            for name, value in arrays.items()
        },
    }


def _write_npz_sidecars(path: Path, schema: dict[str, Any], *, title: str, notes: Sequence[str]) -> None:
    schema_path = path.with_suffix(".schema.json")
    summary_path = path.with_suffix(".summary.md")
    schema_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    lines = [f"# {title}", "", f"- Artifact: `{path.name}`", f"- Schema version: `{schema['schema_version']}`"]
    lines.extend(f"- {note}" for note in notes)
    lines.extend(["", "## Arrays", ""])
    for name, spec in schema["arrays"].items():
        lines.append(
            f"- `{name}`: shape=`{spec['shape']}`, dtype=`{spec['dtype']}`, "
            f"units=`{spec['units']}`, {spec['description']}"
        )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _transport_units(name: str) -> str:
    return {
        "field_values": "arbitrary precursor density per m3 basis",
        "field_names": "label",
        "group_sets": "label",
        "decay_constants_s": "1/s",
        "radial_edges_m": "m",
        "axial_edges_m": "m",
        "cell_volumes_m3": "m3",
    }[name]


def _transport_array_description(name: str) -> str:
    return {
        "field_values": "transported scalar fields on the R-Z mesh",
        "field_names": "field labels aligned with field_values axis 0",
        "group_sets": "precursor group-set labels aligned with field_values axis 0",
        "decay_constants_s": "decay constants aligned with field_values axis 0",
        "radial_edges_m": "radial cell edge coordinates",
        "axial_edges_m": "axial cell edge coordinates",
        "cell_volumes_m3": "axisymmetric cell volumes by axial and radial cell",
    }[name]


def _first_number(mapping: Mapping[str, Any], keys: Sequence[str], *, default: float) -> float:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return float(value)
    return float(default)


def _round_float(value: Any, digits: int = 10) -> float:
    return round(float(value), digits)
