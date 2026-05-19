# MSRE First Criticality Peer Review Handoff

This handoff supports issues #15 and #16 by giving reviewers one reproducible
solver-backed OpenMC bundle path and one explicit readiness rule for the MSRE
first-criticality benchmark.

## Manual Workflow

Run the GitHub Actions workflow `MSRE OpenMC benchmark` from the Actions tab.
The optional `run_id` input selects the result bundle name; if omitted, the
workflow uses `msre-first-criticality-<github-run-id>-<attempt>`.

The workflow runs the benchmark inside the Docker Compose `openmc` service:

```bash
docker compose run --build --rm openmc \
  python -m thorium_reactor.cli benchmark msre_first_criticality \
    --run-id <run_id>
```

It uploads the bundle at `results/msre_first_criticality/<run_id>/` as the
artifact `msre-first-criticality-openmc-<run_id>`.

Every bundle must also carry the generated evidence sidecars:

- `benchmark_evidence.json`
- `nuclear_data_provenance.json`
- `source_convergence_diagnostics.json`
- `cross_code_comparison.json`
- `uncertainty_budget.json`

## Readiness Rule

The benchmark is not ready until statepoint, nuclear data, convergence,
cross-code, and UQ artifacts pass peer review and are linked from the benchmark
dossier or generated report.

In short: the benchmark is not ready until statepoint, nuclear data, convergence, cross-code, and UQ artifacts pass.

A green workflow run only means the current containerized OpenMC path produced
a result bundle. It does not promote the case to `benchmark_ready`.

## Review Checklist

| Gate | Evidence to inspect | Pass condition |
| --- | --- | --- |
| Statepoint | `openmc/statepoint.*.h5`, `summary.json`, `artifact_status.json`, `report.md` | Solver-backed statepoint is present, `neutronics.status` is `completed`, and keff plus Monte Carlo uncertainty are reported. |
| Nuclear data | OpenMC container provenance, cross-section library metadata, and any library checksum/version notes in the bundle | Reviewers can identify the exact nuclear data basis used for the statepoint. |
| Convergence | OpenMC generation/source diagnostics and report residual notes | Source convergence is documented well enough to support the keff uncertainty claim. |
| Cross-code | OpenMC-vs-Serpent/SCALE comparison artifact and residual notes | Independent-code comparison is complete and accepted against the benchmark target. |
| UQ | `uncertainty_budget.json`, `uncertainty_samples.json`, `uncertainty_results.json`, plots, and child OpenMC bundles from the solver-backed UQ sweep | Source-backed geometry/material uncertainty coverage is complete and accepted. |

## Handoff Notes

- Keep `docs/msre-first-criticality-validation-plan.md` as the promotion plan.
- Keep `docs/nonconformance-corrective-action-log.md` open until the missing
  evidence is reviewed and closure evidence is recorded.
- Do not describe the MSRE first-criticality case as benchmark-ready from this
  workflow alone.
