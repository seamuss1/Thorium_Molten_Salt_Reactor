# Nonconformance And Corrective-Action Log

This log tracks benchmark, QA, documentation, and validation deficiencies that
could cause a reviewer to overstate model maturity, miss evidence gaps, or reuse
an artifact outside its qualified context.

The log is a controlled documentation artifact. It does not replace automated QA
checks, benchmark quality gates, or issue tracking. It records the defect, the
expected disposition, the corrective action, the owner, and the evidence needed
before closure.

## GitHub Issues And Nonconformance Records

An existing GitHub issue also needs a nonconformance record when the issue
identifies a deficiency that can affect benchmark, QA, documentation,
validation, export-control, or release confidence. Use the issue to manage
discussion and implementation work; use the `NCA-*` record to control the
defect disposition, owner, status, and closure evidence before the affected
artifact is promoted, externally shared, or relied on.

A GitHub issue alone is sufficient for ordinary feature work, cleanup, or
planning when it does not identify an artifact deficiency, evidence gap,
misleading maturity claim, failed quality gate, accepted limitation, or release hold condition.

## Record Format

Every nonconformance record must include these fields.

| Field | Required content |
| --- | --- |
| Nonconformance ID | Stable `NCA-YYYY-NNN` identifier. Do not reuse closed IDs. |
| Description | Reviewer-readable deficiency, including the risk of leaving it unresolved. |
| Affected artifact | File, result bundle, report, benchmark dossier, QA record, or documentation page. |
| Severity | One of `critical`, `major`, `minor`, or `observation`. |
| Disposition | One of `block_release`, `block_promotion`, `correct_before_use`, `accepted_limitation`, or `monitor`. |
| Corrective action | Specific work needed to remove or deliberately accept the deficiency. |
| Owner | Person, role, team, or issue owner responsible for driving closure. |
| Closure evidence | Concrete evidence required before status changes to `closed`. |
| Status | One of `open`, `in_progress`, `review`, `closed`, or `deferred`. |

## Severity Rules

| Severity | Use when |
| --- | --- |
| `critical` | The artifact could enable an unsafe, controlled, proprietary, or materially false release if used as-is. |
| `major` | Benchmark, validation, QA, or documentation claims are materially incomplete, but the deficiency is already visible or contained. |
| `minor` | The artifact is technically usable, but clarity, traceability, or repeatability is incomplete. |
| `observation` | No immediate deficiency is confirmed, but a reviewer identified a trend or risk worth monitoring. |

## Lifecycle Rules

1. Open a nonconformance when a benchmark gate fails, a QA requirement is blocked
   or active due to evidence gaps, a documentation page could misstate maturity,
   or a validation artifact lacks enough provenance for review.
2. Assign the owner before marking the record `in_progress`.
3. Use `block_release` when public release, generated report publication, or
   externally shared artifacts must wait for review.
4. Use `block_promotion` when the deficiency only blocks status language such as
   `benchmark_ready`, `validated`, or `production`.
5. Use `accepted_limitation` only when the limitation is visible in the affected
   artifact and the owning reviewer records why correction is not required for
   the intended use.
6. Move a record to `review` only after the corrective action is complete and the
   closure evidence exists.
7. Close a record only after the reviewer confirms that the affected artifact,
   generated outputs, and linked documentation agree.
8. Reopen a closed record if later work invalidates the closure evidence.

## Open Log

| Nonconformance ID | Description | Affected artifact | Severity | Disposition | Corrective action | Owner | Closure evidence | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NCA-2026-001 | MSRE first-criticality benchmark promotion is blocked because the active case still uses illustrative geometry rather than a source-indexed benchmark reconstruction. | `benchmarks/msre_first_criticality/benchmark.yaml`; `configs/cases/msre_first_criticality/case.yaml`; `docs/msre-first-criticality-validation-plan.md` | major | block_promotion | Replace the illustrative geometry with a source-indexed reconstruction and keep quality gates failing closed until the replacement is reviewed. | Benchmark owner | Benchmark quality gate `benchmark_geometry_reconstructed` is completed, generated report no longer labels the case `benchmark_blocked` for geometry, and source-indexed geometry evidence is linked. | open |
| NCA-2026-002 | MSRE first-criticality material records are not yet source-indexed isotopic fuel salt, graphite, and structural material records. | `benchmarks/msre_first_criticality/parameters.yaml`; `benchmarks/msre_first_criticality/assumptions.md`; `configs/cases/msre_first_criticality/case.yaml` | major | block_promotion | Complete source-indexed material reconstruction and update benchmark assumptions to separate measured inputs from modeling choices. | Benchmark owner | Benchmark quality gate `source_indexed_materials` is completed and closure notes link the reviewed material source records. | open |
| NCA-2026-003 | MSRE first-criticality solver-backed OpenMC evidence bundle and OpenMC-vs-Serpent cross-code comparison remain planned rather than published. | `benchmarks/msre_first_criticality/benchmark.yaml`; `results/`; generated benchmark reports | major | block_promotion | Publish a reproducible solver-backed bundle with runtime provenance, statepoint statistics, residuals against benchmark uncertainty, and cross-code comparison notes. | Validation owner | Result bundle path, report path, OpenMC statistics, residual summary, and cross-code comparison evidence are recorded in the benchmark dossier. | open |
| NCA-2026-004 | TMSR-LF1 context still contains surrogate acceptance bands, including the broad `expected_keff_band`, so outputs must not be described as validated reactor performance. | `benchmarks/tmsr_lf1/benchmark.yaml`; `docs/reactor-taxonomy-and-flagship.md`; generated reports for TMSR-style cases | major | accepted_limitation | Keep surrogate labels visible, tighten bands only when source-linked or solver-backed evidence exists, and prevent generated reports from promoting the surrogate to benchmark-ready status. | Documentation and benchmark owners | Reports and docs show `surrogate` or equivalent maturity language next to TMSR-LF1 claims; any tightened bands link to reviewed evidence. | open |
| NCA-2026-005 | The QA record set identifies blocked and active evidence gaps, but this nonconformance log is the first centralized corrective-action record for benchmark, QA, documentation, and validation deficiencies. | `qa/requirements.yaml`; `qa/requirements_traceability_matrix.csv`; this document | minor | correct_before_use | Link this log from the documentation map and use it for future benchmark or release reviews. | QA owner | `docs/README.md` links this log and future closure records cite `NCA-*` IDs when correcting known deficiencies. | review |

## Closure Evidence Rules

Closure evidence must be reviewable without relying on memory or chat history.
Acceptable evidence includes:

- a committed file path plus the reviewed line or heading,
- a result bundle path under `results/<case>/<run_id>/`,
- a generated report path,
- a benchmark quality-gate summary,
- a QA command output summary,
- a linked issue or pull request that records verification.

Do not close a record with only "fixed", "done", or an unqualified plot image.
For benchmark and validation records, closure evidence must include maturity
language showing whether the artifact is `benchmark_ready`, `traceable_surrogate`,
`screening_backed`, `dry-run`, or still blocked.
