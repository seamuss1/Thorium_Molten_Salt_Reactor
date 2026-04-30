# Thorium Molten Salt Reactor Platform

Container-first molten-salt reactor design monorepo for benchmarked MSR workflows. The repository now centers on an MSRE-backed validation spine, a modern TMSR-LF1 extension path, Docker Compose-based execution, standardized result bundles, steady-state and reduced-order system models, and reproducible report and visualization artifacts while preserving the original 2022 thesis outputs in an in-repo archive.

## Featured Geometry

![Detailed TMSR-LF1-inspired molten salt reactor cutaway](resources/tmsr_lf1_core_csg.png)

The `tmsr_lf1_core` case now resolves to a detailed CSG reactor stack with active fuel channels, control-guide channels, instrumentation wells, stacked plena, graphite reflector zones, a downcomer annulus, and dual vessel shells. The same Python geometry definition drives both the OpenMC model build and the repository render artifact.

## What This Repo Now Contains

- `src/thorium_reactor`: installable platform package and `reactor` CLI
- `configs/cases`: canonical reactor cases
- `benchmarks/msre_*`: historic benchmark metadata and dataset-centric acceptance targets
- `benchmarks/tmsr_lf1`: modern test-reactor metadata with contextual and low-confidence numerical targets
- `results`: generated result bundles at `results/<case>/<run_id>/`
- `resources`: rendered reference images used in project documentation
- `archive/legacy_openmc_2022`: preserved historical scripts and outputs from the thesis prototype
- `tests`: unit tests for config loading, geometry manifests, BOP closure, and CLI wiring

## Canonical Cases

- `example_pin`: fast smoke/regression case
- `fuel_channel`: layered fuel-channel submodel
- `msre_first_criticality`: historic-benchmark harness for an MSRE first-criticality style acceptance band
- `msre_zero_power_physics`: historic-benchmark harness for zero-power regression and report plumbing
- `msre_u233_zero_power`: historic-benchmark harness for U-233-focused zero-power studies
- `tmsr_lf1_core`: detailed OpenMC CSG core with vessel stack and specialized channel families inspired by the TMSR-LF1 concept
- `immersed_pool_reference`: reference-inspired immersed-pool concept with offset core enclosure, primary-loop hardware, and animated flow render output

## Supported Runtime

Docker Compose is the supported workflow for development, testing, benchmarking, reporting, and solver-backed runs. The Windows wrapper scripts in [`scripts`](scripts) are thin Compose launchers and should be treated as the normal entrypoints.

```bash
docker compose build app
docker compose run --rm app python -m pytest
docker compose run --rm app python -m thorium_reactor.cli run example_pin --no-solver
docker compose run --rm openmc python -m thorium_reactor.cli benchmark msre_first_criticality
```

Windows-friendly wrappers target the same runtime:

```powershell
.\scripts\Run-Tests.cmd
.\scripts\Run-Reactor.cmd run example_pin --no-solver
.\scripts\Run-Reactor.cmd benchmark msre_first_criticality
.\scripts\Enter-PytbknShell.cmd
```

Host Python environments remain best-effort for local development, but they are no longer the documented default. `environment.yml` and `environment-openmc-linux.yml` remain in the repo as fallback/reference environments.

## Docker Compose Layout

The canonical runtime interface is [`docker-compose.yml`](docker-compose.yml). It defines:

- `app`: default service for `build`, `run --no-solver`, `validate`, `report`, `render`, `transient`, `transient-sweep`, and `pytest`
- `openmc`: solver-backed `run` and `benchmark`
- `thermochimica`: chemistry/speciation integration service
- `saltproc`: online-processing and inventory-accounting integration service
- `moltres`: multigroup and moving-precursor integration service

Examples:

```bash
docker compose build app openmc
docker compose run --rm app python -m thorium_reactor.cli run example_pin --run-id docker-example --no-solver
docker compose run --rm openmc python -m thorium_reactor.cli benchmark msre_first_criticality --run-id docker-benchmark
docker compose run --rm thermochimica python -m thorium_reactor.cli thermochimica tmsr_lf1_core --run-id docker-chem
```

[`docker-compose.openmc.yml`](docker-compose.openmc.yml) remains as a compatibility shim for one migration cycle.

## CLI Workflow

```bash
reactor build example_pin
reactor run example_pin --no-solver
reactor benchmark msre_first_criticality --docker-openmc
reactor validate example_pin
reactor report example_pin
reactor render tmsr_lf1_core
reactor transient immersed_pool_reference --scenario partial_heat_sink_loss
reactor transient-sweep immersed_pool_reference --scenario partial_heat_sink_loss --samples 2048 --prefer-gpu
reactor moose immersed_pool_reference
reactor scale tmsr_lf1_core
reactor thermochimica tmsr_lf1_core
reactor saltproc tmsr_lf1_core
reactor moltres immersed_pool_reference
```

Command behavior:

- `reactor build <case>` creates a new result bundle, emits a build manifest plus geometry description JSON, and exports OpenMC XML when OpenMC is installed.
- `reactor run <case>` performs the build, computes steady-state BOP outputs, and writes `summary.json`, `state_store.json`, `runtime_context.json`, `property_audit.json`, and `benchmark_residuals.json`. With `--no-solver`, the run is an explicit `dry-run`. Without `--no-solver`, the run uses OpenMC when available and otherwise completes as `skipped_missing_solver`.
- `reactor benchmark <case>` requires a solver-backed runtime and is intended to run through the Docker Compose `openmc` service.
- `reactor validate <case>` checks geometry/material invariants and compares available metrics to configured acceptance bands.
- `reactor report <case>` generates `report.md` from the latest or specified run bundle, including benchmark traceability scorecards, runtime context, and benchmark residual summaries when metadata is present.
- `reactor render <case>` writes procedural geometry exports for visualization workflows, including OBJ, STL, watertight mesh validation JSON, a rendered PNG, animated GIF flow output, and MP4 video output when a case defines flow-animation paths and `ffmpeg` is available.
- `reactor transient <case>` runs a reduced-order nodal transient proxy from the steady-state summary, writes `transient.json`, updates `summary.json`, and emits transient plots when the case defines transient scenarios.
- `reactor transient-sweep <case>` runs an uncertainty ensemble around the reduced-order transient model, writes `transient_sweep.json`, updates `summary.json` with p50/p95 envelope metrics, and prefers CuPy when `--prefer-gpu` is supplied and a CUDA device is available. Otherwise it uses the built-in CPU backend.
- `reactor moose <case>`, `reactor scale <case>`, `reactor thermochimica <case>`, `reactor saltproc <case>`, and `reactor moltres <case>` export integration inputs plus handoff metadata into the current bundle, and can optionally attempt external execution with `--run-external`.

## Result Bundle Contract

Each active run is written to `results/<case>/<run_id>/` and is expected to contain:

- `build_manifest.json`
- `summary.json`
- `state_store.json`
- `runtime_context.json`
- `property_audit.json`
- `benchmark_residuals.json`
- `metrics.csv`
- `validation.json` after validation
- `report.md` after report generation
- `transient.json` and/or `transient_sweep.json` when transient studies are run
- benchmark traceability in `build_manifest.json` and `summary.json` when a case is linked to benchmark metadata
- `*_integration.json` and `*_handoff.json` for external tool exports
- `openmc/` for solver XML and statepoints
- `geometry/exports/` for SVG, OBJ, STL, watertight mesh validation, GPU-friendly glTF + binary buffers, a Blender Cycles GPU script, rendered PNG, and optional animated GIF or MP4 geometry exports

## Validation Status

The benchmark layer now supports dataset-centric evidence, assumptions, target confidence, and traceability scoring. MSRE cases are the quantitative historic-benchmark path; TMSR-LF1 remains a modern test-reactor extension with contextual and lower-confidence public targets until richer numerical datasets are added.

## Modeling Strategy Notes

- [docs/thermal-hydraulics-modeling-strategy.md](docs/thermal-hydraulics-modeling-strategy.md) describes the recommended analysis ladder for this repo: whole-loop reduced-order thermal-hydraulics first, porous or homogenized core models second, and local 3D CFD only where geometry controls the answer. It also lays out the additional precursor-transport and neutronics coupling needed for liquid-fueled MSR studies.
- [docs/current-model-equations.md](docs/current-model-equations.md) documents the equations, correlations, supported property units, and OpenMC input assumptions used by the current reduced-order implementation.
- [docs/recent-msr-simulation-literature.md](docs/recent-msr-simulation-literature.md) summarizes recent literature used to choose the current delayed-neutron precursor transport upgrade and the next realism steps.

## External Solver Hooks

The repository now includes pragmatic integration hooks for MOOSE/Cardinal, SCALE, Thermochimica, SaltProc, and Moltres:

- case configs may define `integrations.moose`, `integrations.scale`, `integrations.thermochimica`, `integrations.saltproc`, and `integrations.moltres`,
- result bundles capture exported input decks, structured handoff JSON, runtime provenance, and execution metadata,
- generated reports surface those integration artifacts under an external integrations section.

These hooks are export/runtime adapters, not full validated model translations. They are meant to give this repo a clean handoff path into external toolchains.

## GPU Workflow

The repository can now hand off both numerics and visualization to GPU-capable local tools without changing the core case format.

- `reactor transient-sweep immersed_pool_reference --scenario partial_heat_sink_loss --samples 2048 --prefer-gpu` runs an uncertainty ensemble of reduced-order transients. It falls back to the built-in CPU backend when CuPy or a CUDA device is not available.
- `reactor render immersed_pool_reference` now writes `*.gltf`, `*.bin`, and a `*_blender_gpu.py` helper script under `results/<case>/<run_id>/geometry/exports/`.
- Run the generated Blender helper with `blender --background --python results/<case>/<run_id>/geometry/exports/<case>_blender_gpu.py` to get a Cycles render that prefers GPU backends such as OptiX, CUDA, HIP, Metal, or oneAPI when Blender exposes them.

This GPU path accelerates the reduced-order transient ensemble and the final photorealistic render. The OpenMC solver path remains a separate runtime concern and can still be handed off through the existing Docker or external-code integrations.
