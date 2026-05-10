# Result Bundles

`results/` is a runtime output directory. Git tracks only this README and `.gitkeep`; individual run bundles are ignored because they can contain generated plots, geometry meshes, solver outputs, and large intermediate files.

The stable contract is:

```text
results/<case>/<run_id>/
```

Every run bundle should be readable after the fact without consulting mutable case files.

## Bundle Lifecycle

| Command | Creates or extends | Typical files |
| --- | --- | --- |
| `build` | Creates a bundle | `case_snapshot.yaml`, `benchmark_snapshot.yaml`, `provenance.json`, `build_manifest.json`, `geometry_description.json`, `openmc/*.xml` when available |
| `run` | Creates a bundle | build outputs plus `summary.json`, `state_store.json`, `runtime_context.json`, `property_audit.json`, `metrics.csv`, `benchmark_residuals.json` |
| `validate` | Extends latest or selected bundle | `validation.json`, validation status used by reports and web |
| `report` | Extends latest or selected bundle | `report.md`, `plots/*.svg`, `plots_manifest.json` |
| `render` | Extends latest or selected bundle | `geometry/exports/*.png`, `*.svg`, `*.obj`, `*.stl`, `*.gltf`, `*_mesh_validation.json`, `render_assets.json` |
| `transient` | Extends or creates bundle | `transient.json`, transient metrics folded into `summary.json`, transient plots after report |
| `transient-sweep` | Extends or creates bundle | `transient_sweep.json`, p05/p50/p95 metrics, backend report, envelope plots |
| `economics` | Extends or creates bundle | `finance.json`, `schedule.json`, `cash_flow.csv`, `cost_breakdown.csv`, `project_plan.json`, finance plots |
| integration exporters | Extends or creates bundle | `<tool>_integration.json`, `<tool>_handoff.json`, generated input decks |

## Core Files

| File | Why it exists |
| --- | --- |
| `case_snapshot.yaml` | The exact case definition used by the run |
| `benchmark_snapshot.yaml` | The benchmark metadata active during the run, when configured |
| `provenance.json` | Origin paths, snapshot state, and reproducibility metadata |
| `build_manifest.json` | Geometry/material build facts, workflow capabilities, traceability summary, and visualization state |
| `summary.json` | Main machine-readable result summary across neutronics, BOP, flow, transient, chemistry, validation maturity, and economics |
| `metrics.csv` | Flat metric list for plotting, quick review, and spreadsheet workflows |
| `validation.json` | Acceptance target results and pass/fail status |
| `report.md` | Human-readable generated report for the run |
| `plots_manifest.json` | Plot names, paths, and display labels consumed by the web app |
| `render_assets.json` | Geometry export paths and available visual views |

## Geometry Exports

Rendered cases may contain:

```text
geometry/exports/<case>.png
geometry/exports/<case>.svg
geometry/exports/<case>.obj
geometry/exports/<case>.stl
geometry/exports/<case>.gltf
geometry/exports/<case>.bin
geometry/exports/<case>_hero_cutaway.png
geometry/exports/<case>_annotated_cutaway.png
geometry/exports/<case>_physics_overlay.png
geometry/exports/<case>_mesh_validation.json
geometry/exports/<case>_blender_gpu.py
```

The browser 3D view prefers glTF when present and falls back to generated images when a browser-safe model is unavailable.

## Web App Behavior

The web backend reads `results/` directly. It does not need a database for normal browsing. Live browser-launched jobs add:

| File | Purpose |
| --- | --- |
| `job_status.json` | Current status, command, timestamps, and failure message if any |
| `job_events.ndjson` | Append-only event stream displayed by the Run log |
| `case_snapshot.yaml` | Draft-per-run case input, including browser edits |

Canonical case files are not modified by browser runs.

## Refreshing README Figures

The top-level README does not link directly into ignored result bundles. Durable copies live in [resources/readme](../resources/readme). After regenerating a figure, copy the selected output into that directory and update [resources/README.md](../resources/README.md) with the source bundle.

## Cleanup

Generated bundles can be deleted when no longer needed, but avoid removing a bundle while the web app is reading it or a job is running. Keep `.gitkeep` and this README.
