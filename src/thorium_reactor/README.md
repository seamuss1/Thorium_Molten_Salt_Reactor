# `thorium_reactor` Package Map

The package is a small platform rather than a single solver. The CLI composes case loading, build, dry-run or solver-backed execution, validation, reporting, visualization, transient screens, economics, and web serving.

## Command Flow

```text
case.yaml
  -> config.load_case_config
  -> bundle_inputs.ensure_bundle_inputs
  -> paths.ResultBundle
  -> command-specific workflow
  -> summary/report/plots/geometry artifacts
```

The command entrypoint is [`cli.py`](cli.py). It keeps command dispatch thin and pushes domain work into modules that can be tested independently.

## Module Responsibilities

| Module | Responsibility |
| --- | --- |
| [`config.py`](config.py) | Load and normalize case YAML |
| [`paths.py`](paths.py) | Discover repo root, create and locate result bundles |
| [`bundle_inputs.py`](bundle_inputs.py) | Snapshot case and benchmark inputs into a bundle |
| [`neutronics/workflows.py`](neutronics/workflows.py) | Build, run, and validate case workflows around OpenMC-compatible model data |
| [`neutronics/openmc_compat.py`](neutronics/openmc_compat.py) | Isolate optional OpenMC imports and missing-solver messaging |
| [`geometry/`](geometry) | Procedural molten-salt geometry construction and render/export helpers |
| [`flow/`](flow) | Salt properties, primary-system screens, and reduced-order flow allocation |
| [`bop/steady_state.py`](bop/steady_state.py) | Balance-of-plant closure and electric output estimates |
| [`physics_core.py`](physics_core.py) | Compact physics metrics used by reports and transients |
| [`precursors.py`](precursors.py) | Flowing-fuel delayed-neutron precursor model helpers |
| [`transport/`](transport) | Native structured R-Z SSP-RK3 transport for precursor-like scalar fields |
| [`depletion/`](depletion) | Native sparse Bateman depletion matrix, YAML/OpenMC chain import, and inventory stepping |
| [`transient.py`](transient.py) | Single-scenario reduced-order transient proxy |
| [`transient_sweep.py`](transient_sweep.py) | Vectorized uncertainty ensemble with backend selection |
| [`economics/`](economics) | Finance, schedule, cash-flow, and cost-breakdown outputs |
| [`benchmarking.py`](benchmarking.py) | Benchmark residuals, traceability, and Docker/OpenMC benchmark helpers |
| [`qa.py`](qa.py) | Load and validate QA requirement records and the requirements traceability matrix |
| [`reporting/`](reporting) | Markdown report generation and SVG plot generation |
| [`integrations.py`](integrations.py) | External-code export and optional execution adapters |
| [`web/`](web) | FastAPI app, filesystem repository adapter, job runner, permissions, schemas |

## Result Contracts

The package writes JSON with stable, explicit names rather than hidden process state. If a new workflow needs to add data, prefer one of these patterns:

- Add domain-specific detail to a new file such as `chemistry.json` or `<tool>_integration.json`.
- Add headline metrics to `summary.json` so reports and the web app can discover them.
- Add flat numeric values to `metrics.csv` when the value should appear in generic charts.
- Add generated figures to `plots/` and register them in `plots_manifest.json`.
- Add geometry artifacts to `geometry/exports/` and register them in `render_assets.json`.

## Adding A CLI Workflow

1. Add the subcommand in `build_parser()` inside [`cli.py`](cli.py).
2. Decide whether the command creates a fresh bundle or can extend an existing one.
3. Use `ensure_bundle_inputs()` before reading mutable case or benchmark data.
4. Write structured outputs through `ResultBundle.write_json()` or `ResultBundle.write_text()`.
5. Fold a small summary into `summary.json` if the browser or report should surface the result.
6. Add focused tests under [../../tests](../../tests).

## Optional Dependencies

OpenMC and external solvers are optional from the package point of view. Missing solver paths should produce clear status and messaging rather than import-time failures. This is what allows the Windows dry-run workflow, report generation, geometry rendering, and web app to remain usable without a local OpenMC install.
