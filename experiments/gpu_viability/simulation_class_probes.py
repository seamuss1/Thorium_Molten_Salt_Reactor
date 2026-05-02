from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable


SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
REPO_ROOT = SCRIPT_PATH.parents[2]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".tmp" / "gpu-viability-simulation-classes"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gpu_viability_bench import BaseBackend, create_backend, resolve_auto_backend, runtime_environment_report  # noqa: E402


ProbeFn = Callable[[BaseBackend, argparse.Namespace], dict[str, Any]]


def laplace2(backend: BaseBackend, field: Any, *, axis_offset: int = 0) -> Any:
    return (
        backend.roll(field, shift=1, axis=axis_offset)
        + backend.roll(field, shift=-1, axis=axis_offset)
        + backend.roll(field, shift=1, axis=axis_offset + 1)
        + backend.roll(field, shift=-1, axis=axis_offset + 1)
        - 4.0 * field
    )


def grad_x(backend: BaseBackend, field: Any, *, axis: int) -> Any:
    return 0.5 * (backend.roll(field, shift=-1, axis=axis) - backend.roll(field, shift=1, axis=axis))


def grad_y(backend: BaseBackend, field: Any, *, axis: int) -> Any:
    return 0.5 * (backend.roll(field, shift=-1, axis=axis) - backend.roll(field, shift=1, axis=axis))


def scalar_mean(backend: BaseBackend, field: Any, count: int) -> float:
    return backend.scalar(backend.sum(field)) / max(int(count), 1)


def stability_status(values: dict[str, float], invariants: dict[str, bool]) -> str:
    if not all(math.isfinite(value) for value in values.values()):
        return "non_finite_metric"
    return "ok" if all(invariants.values()) else "invariant_failed"


def annotate(
    *,
    name: str,
    physical_fidelity: str,
    production_mapping: str,
    missing_physics: list[str],
    validation_status: str,
    metrics: dict[str, float | int | str | list[int] | None],
    throughput: dict[str, float],
    invariants: dict[str, bool],
) -> dict[str, Any]:
    numeric_metrics = {key: float(value) for key, value in metrics.items() if isinstance(value, (int, float))}
    invariant_report = {key: bool(value) for key, value in invariants.items()}
    return {
        "name": name,
        "physical_fidelity": physical_fidelity,
        "production_mapping": production_mapping,
        "missing_physics": missing_physics,
        "validation_status": validation_status,
        "numerical_health": stability_status(numeric_metrics, invariant_report),
        "invariants": invariant_report,
        "invariant_failures": [key for key, value in invariant_report.items() if not value],
        "runtime_environment": runtime_environment_report(),
        "metrics": metrics,
        "throughput": throughput,
    }


def run_1d_loop_network(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    samples = args.network_samples
    branches = args.network_branches
    segments = args.network_segments
    steps = args.steps
    dt = 0.02

    flow = backend.full((samples, branches), 0.8)
    branch_bias = backend.clip(backend.normal(mean=1.0, sigma=0.08, shape=(samples, branches), seed_offset=10), 0.7, 1.3)
    temperature = backend.full((samples, branches, segments), 610.0)
    source = backend.full((samples, branches, segments), 0.015)
    pump_head = backend.full((samples, branches), 1.0)
    loss_k = backend.full((samples, branches), 0.42) * branch_bias
    inertia = 4.5

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(steps):
        speed = (flow * flow + 1.0e-8) ** 0.5
        pressure_residual = pump_head - loss_k * flow * speed - 0.03 * (temperature[:, :, -1] - temperature[:, :, 0])
        flow = backend.clip(flow + dt * pressure_residual / inertia, 0.02, 2.5)
        inlet = backend.roll(temperature, shift=1, axis=2)
        advective_mix = 0.11 * flow[:, :, None] * (inlet - temperature)
        heat_removal = 0.004 * (temperature - 585.0)
        temperature = temperature + advective_mix + source - heat_removal
    backend.synchronize()
    elapsed = time.perf_counter() - start

    work_items = samples * branches * segments * steps
    flow_mean = scalar_mean(backend, flow, samples * branches)
    metrics = {
        "samples": samples,
        "branches": branches,
        "segments": segments,
        "steps": steps,
        "mean_flow_fraction": round(flow_mean, 6),
        "minimum_flow_fraction": round(backend.min_scalar(flow), 6),
        "peak_temperature_c": round(backend.max_scalar(temperature), 6),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="1d_loop_hydraulic_network_transient",
        physical_fidelity="reduced-order pressure/energy proxy with vectorized branch ensembles",
        production_mapping="future 1D loop transient model for pump coastdown, branch flow splitting, and system heat balance",
        missing_physics=[
            "real friction correlations",
            "component-specific pump curves",
            "buoyancy head from geometry",
            "implicit network solve",
            "validated heat-exchanger closure",
        ],
        validation_status="performance and stability proxy only; not a validated thermal-hydraulic solver",
        metrics=metrics,
        throughput={"branch_segment_steps_per_s": round(work_items / max(elapsed, 1.0e-12), 3)},
        invariants={
            "flow_non_negative": metrics["minimum_flow_fraction"] >= -1.0e-7,
            "flow_within_clamp": metrics["mean_flow_fraction"] <= 2.5 + 1.0e-6,
            "temperature_plausible": 250.0 <= metrics["peak_temperature_c"] <= 1500.0,
        },
    )


def run_porous_ltne_core(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    n = args.grid
    steps = args.steps
    cells = n * n
    dt = 0.08
    fluid = backend.full((n, n), 625.0)
    solid = backend.full((n, n), 655.0)
    power_shape = backend.clip(backend.normal(mean=1.0, sigma=0.06, shape=(n, n), seed_offset=20), 0.85, 1.2)

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(steps):
        exchange = 0.018 * (solid - fluid)
        fluid = fluid + dt * (0.055 * laplace2(backend, fluid) + exchange + 0.030 * power_shape)
        solid = solid + dt * (0.018 * laplace2(backend, solid) - exchange + 0.020 * power_shape)
    backend.synchronize()
    elapsed = time.perf_counter() - start

    metrics = {
        "grid": [n, n],
        "steps": steps,
        "fluid_mean_c": round(scalar_mean(backend, fluid, cells), 6),
        "solid_mean_c": round(scalar_mean(backend, solid, cells), 6),
        "peak_solid_temperature_c": round(backend.max_scalar(solid), 6),
        "minimum_fluid_temperature_c": round(backend.min_scalar(fluid), 6),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="2d_porous_core_ltne_thermal",
        physical_fidelity="finite-difference local-thermal-nonequilibrium proxy with separate fluid and solid temperatures",
        production_mapping="porous/homogenized core thermal model for graphite-salt heat exchange and radial/axial gradients",
        missing_physics=[
            "geometry-derived porosity/permeability",
            "Darcy-Forchheimer momentum coupling",
            "temperature-dependent salt/graphite properties",
            "boundary conditions from loop hardware",
            "verified discretization error study",
        ],
        validation_status="prototype stencil workload; not calibrated to a specific reactor core",
        metrics=metrics,
        throughput={"cell_steps_per_s": round(cells * steps / max(elapsed, 1.0e-12), 3)},
        invariants={
            "fluid_temperature_positive": metrics["minimum_fluid_temperature_c"] > 250.0,
            "solid_temperature_bounded": metrics["peak_solid_temperature_c"] < 1500.0,
            "ltne_gap_bounded": abs(metrics["solid_mean_c"] - metrics["fluid_mean_c"]) < 200.0,
        },
    )


def run_precursor_advection_diffusion(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    n = args.grid
    groups = args.groups
    steps = args.steps
    cells = n * n
    dt = 0.03
    precursor = backend.full((groups, n, n), 1.0 / max(groups, 1))
    flux_source = backend.clip(backend.normal(mean=1.0, sigma=0.05, shape=(n, n), seed_offset=30), 0.8, 1.25)
    decay = backend.asarray([0.0124, 0.0305, 0.111, 0.301, 1.14, 3.01][:groups])
    if groups > 6:
        decay = backend.asarray([0.0124 + 0.04 * index for index in range(groups)])
    decay3 = decay[:, None, None]
    yield3 = backend.asarray([1.0 / groups for _ in range(groups)])[:, None, None]

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(steps):
        upwind_x = precursor - backend.roll(precursor, shift=1, axis=2)
        upwind_y = precursor - backend.roll(precursor, shift=1, axis=1)
        precursor = backend.maximum(
            precursor
            + dt
            * (
                -0.45 * upwind_x
                -0.20 * upwind_y
                + 0.035 * laplace2(backend, precursor, axis_offset=1)
                + yield3 * flux_source
                - decay3 * precursor
            ),
            0.0,
        )
    delayed_source = backend.sum(precursor * decay3, axis=0)
    backend.synchronize()
    elapsed = time.perf_counter() - start

    metrics = {
        "grid": [n, n],
        "groups": groups,
        "steps": steps,
        "minimum_precursor": round(backend.min_scalar(precursor), 8),
        "total_precursor_inventory": round(backend.scalar(backend.sum(precursor)), 6),
        "peak_delayed_source": round(backend.max_scalar(delayed_source), 6),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="delayed_neutron_precursor_advection_diffusion_pde",
        physical_fidelity="periodic finite-difference advection-diffusion-decay-source proxy",
        production_mapping="spatial precursor transport for fueled-salt cores and external-loop regions",
        missing_physics=[
            "real velocity field from hydraulics",
            "reactor-boundary conditions",
            "group-specific diffusion data",
            "source from neutronic fission rate",
            "implicit or high-resolution advection scheme",
        ],
        validation_status="mathematical workload proxy; suitable for GPU scaling, not physics validation",
        metrics=metrics,
        throughput={"group_cell_steps_per_s": round(groups * cells * steps / max(elapsed, 1.0e-12), 3)},
        invariants={
            "precursor_non_negative": metrics["minimum_precursor"] >= -1.0e-7,
            "inventory_non_negative": metrics["total_precursor_inventory"] >= 0.0,
            "delayed_source_non_negative": metrics["peak_delayed_source"] >= 0.0,
        },
    )


def run_multigroup_diffusion(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    n = args.grid
    groups = max(args.flux_groups, 2)
    steps = args.steps
    cells = n * n
    dt = 0.025
    flux = backend.full((groups, n, n), 1.0 / groups)
    precursor_source = backend.clip(backend.normal(mean=0.004, sigma=0.0004, shape=(n, n), seed_offset=40), 0.002, 0.007)
    diffusion = [0.10 / (1 + index) for index in range(groups)]
    removal = [0.030 + 0.010 * index for index in range(groups)]
    nu_fission = [0.022 / (1 + 0.3 * index) for index in range(groups)]

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(steps):
        total_flux = backend.sum(flux, axis=0)
        next_groups = []
        for group in range(groups):
            scatter_in = 0.006 * (total_flux - flux[group])
            fission_source = nu_fission[group] * total_flux
            next_group = backend.maximum(
                flux[group]
                + dt
                * (
                    diffusion[group] * laplace2(backend, flux[group])
                    - removal[group] * flux[group]
                    + scatter_in
                    + fission_source
                    + precursor_source
                ),
                0.0,
            )
            next_groups.append(next_group)
        flux = backend.stack(next_groups, axis=0)
    power_density = backend.sum(flux, axis=0)
    backend.synchronize()
    elapsed = time.perf_counter() - start

    metrics = {
        "grid": [n, n],
        "flux_groups": groups,
        "steps": steps,
        "total_flux": round(backend.scalar(backend.sum(flux)), 6),
        "peak_power_density": round(backend.max_scalar(power_density), 6),
        "minimum_flux": round(backend.min_scalar(flux), 8),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="multigroup_neutron_diffusion_proxy",
        physical_fidelity="positive finite-difference multigroup diffusion/source/removal proxy",
        production_mapping="future coupled neutronics feedback model for power-shape and precursor-source studies",
        missing_physics=[
            "cross-section libraries",
            "eigenvalue normalization",
            "transport effects",
            "temperature-dependent cross sections",
            "verified boundary conditions",
            "coupling to OpenMC-generated data",
        ],
        validation_status="algorithmic GPU probe only; not a neutron-physics result",
        metrics=metrics,
        throughput={"group_cell_steps_per_s": round(groups * cells * steps / max(elapsed, 1.0e-12), 3)},
        invariants={
            "flux_non_negative": metrics["minimum_flux"] >= -1.0e-7,
            "total_flux_positive": metrics["total_flux"] > 0.0,
            "power_density_non_negative": metrics["peak_power_density"] >= 0.0,
        },
    )


def run_monte_carlo_transport_proxy(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    particles = args.particles
    steps = args.mc_steps
    x = backend.normal(mean=0.0, sigma=0.20, shape=(particles,), seed_offset=50)
    y = backend.normal(mean=0.0, sigma=0.20, shape=(particles,), seed_offset=51)
    z = backend.normal(mean=0.0, sigma=0.20, shape=(particles,), seed_offset=52)
    weight = backend.ones((particles,))

    backend.synchronize()
    start = time.perf_counter()
    for step in range(steps):
        dx = backend.normal(mean=0.0, sigma=0.055, shape=(particles,), seed_offset=1000 + step * 3)
        dy = backend.normal(mean=0.0, sigma=0.055, shape=(particles,), seed_offset=1001 + step * 3)
        dz = backend.normal(mean=0.0, sigma=0.055, shape=(particles,), seed_offset=1002 + step * 3)
        x = backend.clip(x + dx, -1.0, 1.0)
        y = backend.clip(y + dy, -1.0, 1.0)
        z = backend.clip(z + dz, -1.0, 1.0)
        radius2 = x * x + y * y + z * z
        weight = weight * (0.997 / (1.0 + 0.002 * radius2))
    backend.synchronize()
    elapsed = time.perf_counter() - start

    metrics = {
        "particles": particles,
        "steps": steps,
        "total_weight": round(backend.scalar(backend.sum(weight)), 6),
        "mean_weight": round(scalar_mean(backend, weight, particles), 8),
        "mean_radius_squared": round(scalar_mean(backend, x * x + y * y + z * z, particles), 8),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="monte_carlo_particle_transport_proxy",
        physical_fidelity="branch-light random-walk attenuation workload, not OpenMC particle transport",
        production_mapping="GPU suitability estimate for particle-history style workloads and random-number throughput",
        missing_physics=[
            "nuclear cross sections",
            "geometry surface tracking",
            "collision physics",
            "branch-heavy reaction sampling",
            "tallies and statistical uncertainty estimators",
            "OpenMC source/statepoint compatibility",
        ],
        validation_status="hardware workload proxy only; must not be reported as neutronics evidence",
        metrics=metrics,
        throughput={"particle_steps_per_s": round(particles * steps / max(elapsed, 1.0e-12), 3)},
        invariants={
            "weight_non_negative": metrics["total_weight"] >= 0.0,
            "mean_weight_probability_like": 0.0 <= metrics["mean_weight"] <= 1.0,
            "radius_inside_clipped_box": 0.0 <= metrics["mean_radius_squared"] <= 3.0,
        },
    )


def run_depletion_chain_proxy(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    samples = args.depletion_samples
    species = args.species
    steps = args.depletion_steps
    dt_days = 0.20
    inventory = backend.zeros((samples, species))
    inventory = inventory + backend.asarray([1.0 if index == 0 else 0.0 for index in range(species)])
    power = backend.clip(backend.normal(mean=1.0, sigma=0.08, shape=(samples,), seed_offset=60), 0.7, 1.3)
    burn = backend.asarray([0.006 / (1.0 + 0.15 * index) for index in range(species)])
    cleanup = backend.asarray([0.0005 * index for index in range(species)])
    yield_fraction = backend.asarray([0.72 if index < species - 1 else 0.0 for index in range(species)])

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(steps):
        losses = inventory * (burn[None, :] * power[:, None] + cleanup[None, :]) * dt_days
        feeds = []
        for index in range(species):
            if index == 0:
                feeds.append(0.00035 * power)
            else:
                feeds.append(losses[:, index - 1] * yield_fraction[index - 1])
        feed_matrix = backend.stack(feeds, axis=1)
        inventory = backend.maximum(inventory - losses + feed_matrix, 0.0)
    backend.synchronize()
    elapsed = time.perf_counter() - start

    total_inventory = backend.sum(inventory, axis=1)
    metrics = {
        "samples": samples,
        "species": species,
        "steps": steps,
        "mean_total_inventory": round(scalar_mean(backend, total_inventory, samples), 8),
        "minimum_inventory": round(backend.min_scalar(inventory), 10),
        "terminal_species_mean": round(scalar_mean(backend, inventory[:, -1], samples), 10),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="depletion_chain_bateman_proxy",
        physical_fidelity="explicit linear chain inventory proxy inspired by Bateman depletion equations",
        production_mapping="future isotope-chain burnup, breeding, cleanup, and uncertainty ensembles",
        missing_physics=[
            "real transmutation chains",
            "decay constants and fission yields from evaluated nuclear data",
            "matrix exponential or implicit stiff solve",
            "online processing chemistry coupling",
            "neutron-spectrum-dependent reaction rates",
        ],
        validation_status="throughput and positivity probe; not isotope-inventory evidence",
        metrics=metrics,
        throughput={"sample_species_steps_per_s": round(samples * species * steps / max(elapsed, 1.0e-12), 3)},
        invariants={
            "inventory_non_negative": metrics["minimum_inventory"] >= -1.0e-9,
            "total_inventory_positive": metrics["mean_total_inventory"] > 0.0,
            "terminal_species_non_negative": metrics["terminal_species_mean"] >= 0.0,
        },
    )


def run_local_cfd_proxy(backend: BaseBackend, args: argparse.Namespace) -> dict[str, Any]:
    n = args.grid
    steps = args.steps
    projection_iters = max(int(args.cfd_projection_iters), 1)
    cells = n * n
    dt = 0.015
    u = backend.full((n, n), 0.15)
    v = backend.full((n, n), 0.02)
    temperature = backend.full((n, n), 650.0) + backend.clip(
        backend.normal(mean=0.0, sigma=1.0, shape=(n, n), seed_offset=70),
        -2.0,
        2.0,
    )
    viscosity = 0.045
    thermal_diffusivity = 0.030

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(steps):
        dudx = grad_x(backend, u, axis=1)
        dudy = grad_y(backend, u, axis=0)
        dvdx = grad_x(backend, v, axis=1)
        dvdy = grad_y(backend, v, axis=0)
        dtdx = grad_x(backend, temperature, axis=1)
        dtdy = grad_y(backend, temperature, axis=0)
        adv_u = u * dudx + v * dudy
        adv_v = u * dvdx + v * dvdy
        buoyancy = 0.00018 * (temperature - 650.0)
        u = u + dt * (-adv_u + viscosity * laplace2(backend, u))
        v = v + dt * (-adv_v + viscosity * laplace2(backend, v) + buoyancy)
        for _ in range(projection_iters):
            divergence = grad_x(backend, u, axis=1) + grad_y(backend, v, axis=0)
            u = u + 0.18 * grad_x(backend, divergence, axis=1)
            v = v + 0.18 * grad_y(backend, divergence, axis=0)
        temperature = temperature + dt * (
            -(u * dtdx + v * dtdy)
            + thermal_diffusivity * laplace2(backend, temperature)
            + 0.012
        )
    divergence = grad_x(backend, u, axis=1) + grad_y(backend, v, axis=0)
    divergence_l2 = (backend.scalar(backend.sum(divergence * divergence)) / max(cells, 1)) ** 0.5
    backend.synchronize()
    elapsed = time.perf_counter() - start

    metrics = {
        "grid": [n, n],
        "steps": steps,
        "projection_iters": projection_iters,
        "peak_speed": round(backend.max_scalar((u * u + v * v) ** 0.5), 8),
        "divergence_l2_proxy": round(divergence_l2, 10),
        "peak_temperature_c": round(backend.max_scalar(temperature), 6),
        "minimum_temperature_c": round(backend.min_scalar(temperature), 6),
        "elapsed_s": round(elapsed, 6),
    }
    return annotate(
        name="local_cfd_convection_diffusion_proxy",
        physical_fidelity="periodic 2D advection-diffusion velocity/temperature workload with divergence damping",
        production_mapping="future local CFD studies for plena, manifolds, stratification, and freeze-margin screening",
        missing_physics=[
            "real pressure Poisson solve",
            "no-slip walls and inlet/outlet boundaries",
            "RANS/LES turbulence modeling",
            "conjugate heat transfer to structures",
            "temperature-dependent molten-salt properties",
            "mesh-quality and time-step convergence studies",
        ],
        validation_status="CFD-shaped GPU workload only; not a CFD validation case",
        metrics=metrics,
        throughput={"cell_steps_per_s": round(cells * steps / max(elapsed, 1.0e-12), 3)},
        invariants={
            "divergence_damped": metrics["divergence_l2_proxy"] < 0.1,
            "speed_bounded": metrics["peak_speed"] < 5.0,
            "temperature_positive": metrics["minimum_temperature_c"] > 250.0,
            "temperature_bounded": metrics["peak_temperature_c"] < 1500.0,
        },
    )


PROBES: dict[str, ProbeFn] = {
    "loop-1d": run_1d_loop_network,
    "porous-ltne": run_porous_ltne_core,
    "precursor-pde": run_precursor_advection_diffusion,
    "multigroup-diffusion": run_multigroup_diffusion,
    "monte-carlo-proxy": run_monte_carlo_transport_proxy,
    "depletion-chain": run_depletion_chain_proxy,
    "local-cfd-proxy": run_local_cfd_proxy,
}


def parse_probe_names(raw: str) -> list[str]:
    if raw == "all":
        return list(PROBES)
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [name for name in names if name not in PROBES]
    if unknown:
        raise ValueError(f"Unknown probe(s): {', '.join(unknown)}")
    return names


def host_report() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "repo_root": str(REPO_ROOT),
        "environment": runtime_environment_report(),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GPU usability probes for reactor simulation classes.")
    parser.add_argument("--backend", default="auto", choices=["auto", "torch-xpu", "numpy", "torch-cpu", "cupy", "dpnp", "torch-cuda"])
    parser.add_argument("--dtype", default="float32", choices=["float32", "float64"])
    parser.add_argument("--probes", default="all", help="Comma-separated probe names or 'all'.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--steps", type=int, default=64)
    parser.add_argument("--grid", type=int, default=192)
    parser.add_argument("--groups", type=int, default=6)
    parser.add_argument("--flux-groups", type=int, default=4)
    parser.add_argument("--network-samples", type=int, default=32768)
    parser.add_argument("--network-branches", type=int, default=8)
    parser.add_argument("--network-segments", type=int, default=32)
    parser.add_argument("--particles", type=int, default=1048576)
    parser.add_argument("--mc-steps", type=int, default=64)
    parser.add_argument("--depletion-samples", type=int, default=262144)
    parser.add_argument("--depletion-steps", type=int, default=128)
    parser.add_argument("--species", type=int, default=12)
    parser.add_argument("--cfd-projection-iters", type=int, default=4)
    args = parser.parse_args(argv)

    backend = resolve_auto_backend(dtype=args.dtype, seed=42) if args.backend == "auto" else create_backend(
        args.backend,
        dtype=args.dtype,
        seed=42,
    )
    names = parse_probe_names(args.probes)
    results = []
    failures = []
    for name in names:
        try:
            print(f"running {name} on {backend.name} ({backend.device_label})")
            result = PROBES[name](backend, args)
            results.append(result)
            throughput = ", ".join(f"{key}={value}" for key, value in result["throughput"].items())
            print(f"  {result['numerical_health']} | {throughput}")
        except BaseException as exc:
            failure = {"name": name, "error": repr(exc)}
            failures.append(failure)
            print(f"  failed: {failure['error']}")

    payload = {
        "host": host_report(),
        "backend": backend.describe(),
        "academic_integrity": {
            "summary": (
                "These probes are GPU usability and algorithm-shape tests. They are not validated "
                "reactor analyses, licensing calculations, or evidence of physical performance."
            ),
            "intended_use": [
                "identify which future simulation classes can exploit device parallelism",
                "estimate throughput and memory pressure",
                "guide production refactor priorities",
                "surface runtime dependency and backend issues",
            ],
        },
        "parameters": vars(args),
        "results": results,
        "failures": failures,
    }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.output_root).resolve() / f"simulation_class_probes_{backend.name}_{stamp}.json"
    write_json(output_path, payload)
    print(f"wrote {output_path}")
    return 0 if results and not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
