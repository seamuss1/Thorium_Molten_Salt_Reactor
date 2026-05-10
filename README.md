# Thorium Molten Salt Reactor Lab

Config-driven molten-salt reactor simulation workbench with a Python CLI, reproducible result bundles, benchmark traceability, reduced-order thermal/flow models, geometry exports, and a single-port FastAPI + React browser lab.

This is a research and engineering scaffold, not a licensed design tool. The project is deliberately honest about maturity: dry-run neutronics are separated from solver-backed OpenMC work, modern TMSR-style cases are marked as traceable surrogates until deeper source data lands, and generated reports carry validation gaps instead of burying them.

![Thorium Lab dashboard](resources/readme/web-dashboard.png)

## Start Here

| Goal | Command | Output |
| --- | --- | --- |
| Open a configured shell | `.\scripts\Enter-PytbknShell.cmd` | Repo-aware shell with `PYTHONPATH=src` |
| Build a smoke case | `.\scripts\Run-Reactor.cmd build example_pin` | `results/example_pin/<run_id>/build_manifest.json` |
| Run without OpenMC | `.\scripts\Run-Reactor.cmd run example_pin --no-solver` | Summary, metrics, provenance, report inputs |
| Render the TMSR core | `.\scripts\Run-Reactor.cmd render tmsr_lf1_core` | PNG/SVG/OBJ/STL/glTF geometry exports |
| Start the browser lab | `.\scripts\Run-Web.cmd` | `http://localhost:18488` |
| Run tests | `.\scripts\Run-Tests.cmd` | Full pytest suite |

The Windows `.cmd` wrappers are the normal entrypoints on this host because PowerShell script execution is restricted. See [AGENTS.md](AGENTS.md) for the repo-local runtime rules and [web/README.md](web/README.md) for the browser stack.

## What This Repo Does

| Layer | What is implemented | Where to look |
| --- | --- | --- |
| Case definitions | YAML reactor inputs, material inventories, geometry parameters, transient scenarios, economics assumptions, and validation targets | [configs/cases](configs/cases) |
| Core package | CLI orchestration, config loading, neutronics build plumbing, reduced-order physics, plotting, reporting, web API, and external-code handoffs | [src/thorium_reactor](src/thorium_reactor) |
| Benchmarks | MSRE historical benchmark scaffolds plus TMSR-LF1 literature-backed surrogate targets and traceability scoring | [benchmarks](benchmarks) |
| QA artifacts | Requirements traceability matrix and controlled QA requirement records | [qa](qa) |
| Results | Immutable run bundles under `results/<case>/<run_id>/` with snapshots, metrics, plots, reports, provenance, and geometry exports | [results/README.md](results/README.md) |
| Browser lab | FastAPI serves both `/api` and the production React UI on one port | [web](web) and [docs/browser-front-end.md](docs/browser-front-end.md) |
| Science notes | Modeling equations, literature review, MSRE validation plan, taxonomy, and front-end notes indexed by the Science view | [docs](docs) |
| Legacy work | Preserved 2022 OpenMC thesis prototype and original output files | [archive/legacy_openmc_2022](archive/legacy_openmc_2022) |

## Simulation Figures

The README figures below are copied from real result bundles into [resources/readme](resources/readme) so the front page stays stable even though `results/` is ignored by Git.

| TMSR-LF1 cutaway | Physics state overlay |
| --- | --- |
| ![TMSR-LF1 annotated cutaway](resources/readme/tmsr-lf1-annotated-cutaway.png) | ![TMSR-LF1 physics overlay](resources/readme/tmsr-lf1-physics-overlay.png) |

| Transient uncertainty envelope | Flagship finance waterfall |
| --- | --- |
| ![TMSR-LF1 fuel temperature uncertainty envelope](resources/readme/tmsr-lf1-temperature-envelope.svg) | ![Flagship cost waterfall](resources/readme/flagship-finance-cost-waterfall.svg) |

The latest TMSR-LF1 gallery source bundle used here is `results/tmsr_lf1_core/full-suite-20260502-1916-r2/`. Its summary reports a 250 MWth surrogate core, 456 generated cells, 91 channels, 85 active-flow salt-bearing channels, `beta_eff = 0.000495`, a traceability score of 88.3, and a validation maturity score of 68.9. Its transient ensemble used the NumPy backend with 512 samples; the p95 fuel-temperature peak is 718.7 C for the configured mild reactivity insertion scenario.

## Browser Lab

The app is a practical lab console, not a landing page. It discovers case YAML, reads result bundles, shows generated plots and reports, launches safe workflow phases, and renders Markdown science notes with KaTeX.

![Simulation builder](resources/readme/web-builder.png)

Main screens:

| Screen | Purpose |
| --- | --- |
| Dashboard | Portfolio snapshot, latest outputs, documentation index, quick launch |
| Simulations | Case cards, capabilities, parameters, and latest run context |
| Builder | Draft-per-run YAML parameter edits and safe command selection |
| Run log | Job status, event stream, reports, plots, raw JSON, and artifacts |
| Science | `README.md` plus `docs/*.md` rendered inside the app |
| 3D | glTF geometry viewer with generated image fallback |
| Admin | Local/deployed run limit reset tools for configured admins |

Browser-launched runs write isolated snapshots under `results/<case>/<run_id>/` and do not mutate `configs/cases/*/case.yaml`. The web command allowlist is intentionally narrow: `build`, `run --no-solver`, `transient`, `transient-sweep`, `validate`, `render`, and `report`.

## Case Portfolio

| Case | Role | Best first command | Notes |
| --- | --- | --- | --- |
| [`example_pin`](configs/cases/example_pin/case.yaml) | Smoke/regression pin | `.\scripts\Run-Reactor.cmd run example_pin --no-solver` | Fastest way to check runtime, reporting, and web bundle display |
| [`fuel_channel`](configs/cases/fuel_channel/case.yaml) | Layered channel submodel | `.\scripts\Run-Reactor.cmd build fuel_channel` | Useful for material and CSG channel checks |
| [`msre_first_criticality`](configs/cases/msre_first_criticality/case.yaml) | Historical benchmark harness | `.\scripts\Run-Reactor.cmd validate msre_first_criticality` | See [MSRE validation plan](docs/msre-first-criticality-validation-plan.md) |
| [`msre_zero_power_physics`](configs/cases/msre_zero_power_physics/case.yaml) | MSRE zero-power regression | `.\scripts\Run-Reactor.cmd report msre_zero_power_physics` | Report and residual plumbing |
| [`msre_u233_zero_power`](configs/cases/msre_u233_zero_power/case.yaml) | U-233 MSRE zero-power harness | `.\scripts\Run-Reactor.cmd validate msre_u233_zero_power` | Historical benchmark path |
| [`tmsr_lf1_core`](configs/cases/tmsr_lf1_core/case.yaml) | Modern TMSR-LF1-inspired core | `.\scripts\Run-Reactor.cmd render tmsr_lf1_core` | Detailed ring-lattice CSG, transient proxy, uncertainty sweep |
| [`immersed_pool_reference`](configs/cases/immersed_pool_reference/case.yaml) | Immersed-pool demonstrator | `.\scripts\Run-Reactor.cmd transient immersed_pool_reference --scenario partial_heat_sink_loss` | Pool layout, loop segments, flow animation assets |
| [`flagship_grid_msr`](configs/cases/flagship_grid_msr/case.yaml) | Commercial planning target | `.\scripts\Run-Reactor.cmd economics flagship_grid_msr --scenario conservative_foak` | 300 MWe planning case with cost and schedule outputs |

More detail lives in [configs/cases/README.md](configs/cases/README.md).

## Result Bundle Contract

Every workflow writes into a run bundle:

```text
results/<case>/<run_id>/
  case_snapshot.yaml
  benchmark_snapshot.yaml
  provenance.json
  build_manifest.json
  geometry_description.json
  summary.json
  metrics.csv
  validation.json
  report.md
  plots/
  geometry/exports/
```

Most commands are additive. `run` creates the core summary; `validate` adds acceptance checks; `report` generates Markdown and plots; `render` adds geometry exports; `transient`, `transient-sweep`, `economics`, and external integration commands append their own domain artifacts. The bundle is meant to be inspectable by Git-unaware tools, the CLI, and the browser app.

See [results/README.md](results/README.md) for the full file-by-file contract.

## Runtime Notes

For routine Windows development, use the checked-in wrappers:

```powershell
.\scripts\Run-Reactor.cmd run example_pin --no-solver
.\scripts\Run-Reactor.cmd report example_pin
.\scripts\Run-Tests.cmd
```

Interactive work after entering the configured shell:

```powershell
python -m thorium_reactor.cli build example_pin
python -m thorium_reactor.cli run example_pin --no-solver
reactor render tmsr_lf1_core
reactor report example_pin
```

OpenMC-backed runs remain a separate solver path. Dry-run workflows are the default Windows path for geometry, reporting, reduced-order flow, and browser-launched runs. Solver-backed OpenMC benchmarks should be run only through the documented OpenMC-capable runtime path.

## Validation Posture

This repository is useful because it exposes uncertainty instead of pretending it is done.

| Area | Current status |
| --- | --- |
| MSRE historical validation | Source dossiers, acceptance bands, assumption logs, and quality gates exist, but the first-criticality case is intentionally not marked benchmark-ready until a source-indexed geometry/material reconstruction and solver-backed bundle are published |
| TMSR-LF1-inspired core | Literature-backed operating point and property-uncertainty context, but still a traceable surrogate rather than a proprietary plant replica |
| Reduced-order transients | Point-kinetics-like proxy with flowing-fuel precursor transport screens, cleanup/depletion placeholders, and uncertainty ensembles |
| Geometry exports | Procedural CSG-derived render assets, mesh checks, OBJ/STL/glTF exports, and image overlays |
| Commercial planning | Finance and schedule model for `flagship_grid_msr`, separated from benchmark and research cases |

Key science references and design reasoning are in:

- [docs/current-model-equations.md](docs/current-model-equations.md)
- [docs/thermal-hydraulics-modeling-strategy.md](docs/thermal-hydraulics-modeling-strategy.md)
- [docs/recent-msr-simulation-literature.md](docs/recent-msr-simulation-literature.md)
- [docs/msre-first-criticality-validation-plan.md](docs/msre-first-criticality-validation-plan.md)
- [docs/reactor-taxonomy-and-flagship.md](docs/reactor-taxonomy-and-flagship.md)
- [docs/openmsr-review.md](docs/openmsr-review.md)

## External Tool Hooks

The CLI can export integration inputs and handoff metadata for MOOSE/Cardinal-style workflows, SCALE, Thermochimica, SaltProc, and Moltres:

```powershell
.\scripts\Run-Reactor.cmd moose immersed_pool_reference
.\scripts\Run-Reactor.cmd scale tmsr_lf1_core
.\scripts\Run-Reactor.cmd thermochimica tmsr_lf1_core
.\scripts\Run-Reactor.cmd saltproc tmsr_lf1_core
.\scripts\Run-Reactor.cmd moltres immersed_pool_reference
```

These are pragmatic export adapters. They do not claim validated one-to-one translations into those external codes.

## Development Links

- [AGENTS.md](AGENTS.md): local runtime and workflow rules
- [configs/cases/README.md](configs/cases/README.md): case schema and portfolio details
- [src/thorium_reactor/README.md](src/thorium_reactor/README.md): package map and command flow
- [qa/README.md](qa/README.md): requirements traceability and QA record artifacts
- [web/README.md](web/README.md): browser app architecture and operational notes
- [benchmarks/README.md](benchmarks/README.md): benchmark evidence structure
- [results/README.md](results/README.md): bundle anatomy
- [resources/README.md](resources/README.md): README figure refresh notes
- [experiments/gpu_viability/README.md](experiments/gpu_viability/README.md): GPU viability experiments
- [archive/legacy_openmc_2022/README.md](archive/legacy_openmc_2022/README.md): preserved legacy prototype

## License

MIT. See [LICENSE](LICENSE).
