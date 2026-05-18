# MSRE First Criticality Validation Plan

This plan is the promotion path for turning `msre_first_criticality` from an
illustrative harness into the repository's first scientifically credible
benchmark workflow.

## Scientific Claim

The target claim is narrow:

> This repository can reproduce and report an OpenMC model of the MSRE U-235
> first criticality benchmark with traceable geometry, materials, uncertainty,
> runtime provenance, and benchmark residuals.

The current repository must not claim validated reactor-design predictions from
this case until the benchmark quality gates pass.

## Current Status

The benchmark source dossier now lives in:

- `benchmarks/msre_first_criticality/source_index.yaml`
- `benchmarks/msre_first_criticality/parameters.yaml`
- `benchmarks/msre_first_criticality/assumptions.md`

The active case still uses a simplified layered-channel geometry. Its value is
workflow validation: OpenMC handoff, bundle reproducibility, report generation,
traceability scoring, quality gates, and uncertainty-aware residual logic.

## Promotion Gates

The generated report must show all benchmark quality gates as passing:

- source dossier declared
- no placeholder or surrogate benchmark targets
- numerical keff uncertainty declared
- uncertainty propagated across major geometry and material inputs
- cross-code comparison completed
- geometry marked as `benchmark_reconstruction`
- materials marked as `source_indexed_isotopic`
- solver statistics marked as `published_solver_bundle`

Until then, the case remains `benchmark_blocked`, even if a broad keff check
would pass.

## Implementation Work Remaining

1. Reconstruct the full MSRE first-criticality geometry from the evaluated
   benchmark record.
2. Replace illustrative material compositions with source-indexed isotopic fuel
   salt, graphite, and structure definitions.
3. Capture cross-section library metadata and OpenMC source convergence
   diagnostics in the result bundle.
4. Run a solver-backed benchmark bundle with enough particles and batches to
   make Monte Carlo uncertainty meaningful.
5. Add geometry and material perturbation sweeps for propagated uncertainty.
6. Complete the OpenMC-vs-Serpent reference comparison.

## Definition Of Done

The deficiency is fixed when `reactor benchmark msre_first_criticality
--docker-openmc` produces a bundle whose report says `Benchmark ready: true`
and whose keff residual is evaluated against combined benchmark and Monte Carlo
uncertainty, not a placeholder band.

## Solver-Backed UQ Sweep

The issue #9 geometry/material uncertainty workflow is launched separately from
the browser-safe job set:

```powershell
.\scripts\Run-Reactor.cmd uncertainty-sweep msre_first_criticality --run-id msre-uq-openmc --docker-openmc --samples 32 --resume
```

The command creates a root bundle with `uncertainty_budget.json`,
`uncertainty_samples.json`, `uncertainty_results.json`, and plots, plus one
OpenMC child bundle per nominal, one-at-a-time, and Sobol sample. Current MSRE
inputs are still assumption-backed, so the generated uncertainty section is a
solver-backed workflow demonstration rather than benchmark-ready coverage until
the source-indexed geometry/material issues are complete.
