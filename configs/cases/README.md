# Case Catalog

The canonical simulation inputs live one directory per case:

```text
configs/cases/<case>/case.yaml
```

Each case is intentionally self-contained. The CLI snapshots the YAML into every result bundle before it starts work, so browser and command-line runs can be audited later without guessing which version of a case file was active.

## Portfolio

| Case | Stage | Main use | Good first command |
| --- | --- | --- | --- |
| [`example_pin`](example_pin/case.yaml) | smoke | Fast runtime, metrics, report, and web display check | `.\scripts\Run-Reactor.cmd run example_pin --no-solver` |
| [`fuel_channel`](fuel_channel/case.yaml) | submodel | Layered channel geometry and material plumbing | `.\scripts\Run-Reactor.cmd build fuel_channel` |
| [`msre_first_criticality`](msre_first_criticality/case.yaml) | historic benchmark | MSRE first-criticality validation harness | `.\scripts\Run-Reactor.cmd validate msre_first_criticality` |
| [`msre_zero_power_physics`](msre_zero_power_physics/case.yaml) | historic benchmark | Zero-power physics regression and reporting | `.\scripts\Run-Reactor.cmd report msre_zero_power_physics` |
| [`msre_u233_zero_power`](msre_u233_zero_power/case.yaml) | historic benchmark | U-233-focused zero-power MSRE harness | `.\scripts\Run-Reactor.cmd validate msre_u233_zero_power` |
| [`tmsr_lf1_core`](tmsr_lf1_core/case.yaml) | full-core surrogate | Detailed TMSR-LF1-inspired CSG, thermal outputs, transient proxy, and uncertainty sweep | `.\scripts\Run-Reactor.cmd render tmsr_lf1_core` |
| [`immersed_pool_reference`](immersed_pool_reference/case.yaml) | full-core demonstrator | Immersed-pool geometry, primary loop, heat-sink-loss transient, and flow animation assets | `.\scripts\Run-Reactor.cmd transient immersed_pool_reference --scenario partial_heat_sink_loss` |
| [`flagship_grid_msr`](flagship_grid_msr/case.yaml) | commercial planning | 300 MWe grid target with plant economics plus stress transient, GPU sweep, native transport, and multi-zone depletion workloads | `.\scripts\Run-Reactor.cmd transient-sweep flagship_grid_msr --scenario flagship_grid_stress --prefer-gpu` |

## Case Anatomy

The schema is intentionally plain YAML. Most fields are optional, but these sections are common:

| Section | Meaning | Consumed by |
| --- | --- | --- |
| `name` | Stable case id. Must match the containing directory name. | CLI, web app, result path builder |
| `reactor` | High-level design point: family, stage, mode, power, temperatures, efficiencies, benchmark link, and optional classification metadata | BOP, reports, web summaries, economics |
| `materials` | Density, thermophysical properties, nuclide fractions, and elemental compositions | OpenMC build, property audit, render material labels |
| `geometry` | Procedural CSG parameters, ring layout, channel layers, special channel families, and optional plant render layout | build, validate, render, 3D viewer |
| `simulation` | OpenMC mode, batches, particles, source, and tallies | build, run, benchmark, reports |
| `flow` | Core-flow model, active/stagnant channel families, allocation rule, and reduced-order hydraulic assumptions | steady-state and transient models |
| `transient` | Reduced-order transient constants, feedback coefficients, precursor groups, and named event scenarios | `transient`, `transient-sweep`, runtime benchmarks |
| `economics` | Cost basis and scenario modifiers | `economics` for commercial cases |
| `project_schedule` | Planning phases, durations, and dependencies | `economics` schedule outputs |
| `validation_targets` | Acceptance bands mapped to metrics or build-manifest fields | `validate`, `report`, web status |
| `integrations` | External-code command hints and deck metadata | MOOSE, SCALE, Thermochimica, SaltProc, Moltres exporters |

## Editing Rules

- Keep canonical YAML edits in `configs/cases/*/case.yaml`.
- Browser-launched edits are draft-per-run only. They create `case_snapshot.yaml` inside a result bundle and do not modify canonical YAML.
- Prefer explicit units in nested mappings, especially for properties that can be represented in either cgs or SI.
- Add validation targets beside the case when the target is case-specific. Put shared benchmark evidence in `benchmarks/<benchmark>/benchmark.yaml`.
- Use descriptive scenario names. They appear in reports and web controls, so `partial_heat_sink_loss` is better than `scenario_2`.

## Geometry Details

The detailed molten-salt cases use `geometry.kind: ring_lattice_core`. The builder expands this into channel families:

- `channel_layers` describe the default channel stack, usually graphite moderator, fuel annulus, gas gaps, and structure.
- `special_channels.control_guides` replaces selected positions with control-guide geometry.
- `special_channels.instrumentation_wells` replaces selected positions with instrumentation geometry.
- `rings` determine radial placement and count.
- `render_layout` adds plant-scale components and flow paths for cases that should export more than the core.

Validation targets such as `expected_cells` and `channel_count` intentionally pin generated geometry. If a geometry edit is deliberate, update the target and explain the change in the commit or PR.

## Naming And Run IDs

When a command does not receive `--run-id`, the CLI creates a timestamp-like run id. Reusing an existing bundle is only allowed by commands that extend results, such as `transient`, `transient-sweep`, `economics`, and integration exporters.

Examples:

```powershell
.\scripts\Run-Reactor.cmd run tmsr_lf1_core --run-id readme-demo --no-solver
.\scripts\Run-Reactor.cmd transient-sweep tmsr_lf1_core --run-id readme-demo --scenario mild_reactivity_insertion --samples 512
.\scripts\Run-Reactor.cmd report tmsr_lf1_core --run-id readme-demo
```

For the flagship commercial case, `flagship_grid_stress` is intentionally heavier than the smoke scenarios: 900 s at 0.25 s resolution, multiple coupled reactivity/flow/heat-sink/chemistry events, 36 x 72 native R-Z transport, extra transported tracer groups, and a 3-zone native depletion matrix using `resources/depletion/flagship_thorium_chain.yaml`.
