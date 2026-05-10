# Benchmarks And Evidence

Benchmark metadata gives the simulation outputs context. It is separate from case YAML so a case can say "use this benchmark dossier" without embedding every source, assumption, and acceptance target inline.

Typical link from a case:

```yaml
reactor:
  benchmark: benchmarks/tmsr_lf1/benchmark.yaml
```

## Current Benchmark Families

| Area | Files | Status |
| --- | --- | --- |
| MSRE first criticality | `benchmarks/msre_first_criticality/` plus [docs/msre-first-criticality-validation-plan.md](../docs/msre-first-criticality-validation-plan.md) | Historical benchmark path with quality gates, but not benchmark-ready until source-indexed geometry/material reconstruction and solver-backed results are published |
| TMSR-LF1 surrogate context | `benchmarks/tmsr_lf1/` | Literature-backed modern test-reactor context and surrogate targets for TMSR-style cases |
| MSRE zero-power cases | Case-level harnesses under [configs/cases](../configs/cases) | Regression/reporting harnesses for historical benchmark plumbing |

## Benchmark YAML Concepts

Benchmark files can carry:

| Field family | Meaning |
| --- | --- |
| evidence records | Source-linked claims, confidence levels, and relevance notes |
| assumptions | Explicit modeling assumptions with basis and evidence references |
| targets | Scalar or banded quantities used by validation and residual plots |
| datasets | Grouped observables for a phenomenon such as operating point, property uncertainty, or licensing milestone |
| quality gates | Conditions that must be satisfied before calling a case benchmark-ready |
| maturity notes | Validation stage, gaps, cross-code checks, and confidence summaries |

The generated reports turn this into traceability scorecards rather than leaving it as hidden metadata.

## Validation Targets

Case YAML owns `validation_targets` because the target often depends on the exact model representation:

```yaml
validation_targets:
  expected_cells:
    metric: expected_cells
    source: metrics
    min: 456
    max: 456
```

Benchmark YAML owns the evidence behind why a target exists. Reports and summaries connect both sides through benchmark target ids when present.

## Interpreting Status

| Status phrase | Meaning in this repo |
| --- | --- |
| `dry-run` | OpenMC was not executed. Geometry, reports, BOP, and reduced-order workflows may still be complete. |
| `skipped_missing_solver` | A solver-backed path was requested but OpenMC was not available in the active runtime. |
| `traceable_surrogate` | Assumptions and sources are tracked, but the case is not a validated replica. |
| `screening_backed` | Enough model structure exists for comparative screens, not licensing-grade conclusions. |
| `benchmark_ready` | Reserved for cases with source-indexed inputs, solver-backed results, documented uncertainty, and satisfied quality gates. |

## Practical Rule

If a result is going into a README, report, paper draft, or design review, include the bundle path and the benchmark maturity status. A pretty plot without maturity context is too easy to misuse.
