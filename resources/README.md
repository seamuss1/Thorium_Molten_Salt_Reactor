# Resources

`resources/` stores durable documentation assets. Generated runtime outputs normally belong in `results/`, but selected figures are copied here when a README needs stable links.

## README Figure Gallery

| File | Source |
| --- | --- |
| [`readme/tmsr-lf1-annotated-cutaway.png`](readme/tmsr-lf1-annotated-cutaway.png) | `results/tmsr_lf1_core/full-suite-20260502-1916-r2/geometry/exports/tmsr_lf1_core_annotated_cutaway.png` |
| [`readme/tmsr-lf1-physics-overlay.png`](readme/tmsr-lf1-physics-overlay.png) | `results/tmsr_lf1_core/full-suite-20260502-1916-r2/geometry/exports/tmsr_lf1_core_physics_overlay.png` |
| [`readme/tmsr-lf1-temperature-envelope.svg`](readme/tmsr-lf1-temperature-envelope.svg) | `results/tmsr_lf1_core/full-suite-20260502-1916-r2/plots/transient_sweep_fuel_temperature_envelope.svg` |
| [`readme/flagship-finance-cost-waterfall.svg`](readme/flagship-finance-cost-waterfall.svg) | `results/flagship_grid_msr/full-suite-20260502-1916-r2/plots/finance_cost_waterfall.svg` |
| [`readme/web-dashboard.png`](readme/web-dashboard.png) | Captured from `http://localhost:18488/` |
| [`readme/web-builder.png`](readme/web-builder.png) | Captured from `http://localhost:18488/builder` |
| [`tmsr_lf1_core_csg.png`](tmsr_lf1_core_csg.png) | Earlier TMSR-LF1 CSG render retained for compatibility with older docs |

## Refreshing From Result Bundles

After regenerating a run, copy only the figures that should be part of the durable documentation surface:

```powershell
Copy-Item results\tmsr_lf1_core\<run_id>\geometry\exports\tmsr_lf1_core_annotated_cutaway.png resources\readme\tmsr-lf1-annotated-cutaway.png -Force
Copy-Item results\tmsr_lf1_core\<run_id>\geometry\exports\tmsr_lf1_core_physics_overlay.png resources\readme\tmsr-lf1-physics-overlay.png -Force
Copy-Item results\tmsr_lf1_core\<run_id>\plots\transient_sweep_fuel_temperature_envelope.svg resources\readme\tmsr-lf1-temperature-envelope.svg -Force
```

Update the source table above when the source bundle changes.

## Git Ignore Note

The repository ignores generated `*.png` and `*.svg` files by default, but `.gitignore` explicitly re-includes `resources/**/*.png` and `resources/**/*.svg`. Keep this directory curated; do not mirror whole result bundles here.
