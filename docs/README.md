# Science Documentation Map

The browser Science view indexes the top-level [README.md](../README.md) and every Markdown file in this directory. Use H1 headings for readable titles and `$...$` or `$$...$$` for math so the React/KaTeX renderer can display equations.

## Reading Order

| Document | Start here when you need |
| --- | --- |
| [current-model-equations.md](current-model-equations.md) | The equations, correlations, unit assumptions, and current reduced-order model details |
| [thermal-hydraulics-modeling-strategy.md](thermal-hydraulics-modeling-strategy.md) | The modeling ladder from whole-loop reduced-order screens to local CFD |
| [msd-tp-thermophysical-data.md](msd-tp-thermophysical-data.md) | MSD-TP data provenance, formulas, runtime providers, guardrails, and limitations |
| [recent-msr-simulation-literature.md](recent-msr-simulation-literature.md) | Literature context behind delayed-neutron transport, property uncertainty, and realism upgrades |
| [msre-first-criticality-validation-plan.md](msre-first-criticality-validation-plan.md) | What the MSRE benchmark path still needs before claiming benchmark-ready status |
| [msre-first-criticality-peer-review-handoff.md](msre-first-criticality-peer-review-handoff.md) | Manual OpenMC bundle workflow and peer-review gates for MSRE first criticality |
| [nonconformance-corrective-action-log.md](nonconformance-corrective-action-log.md) | Open nonconformance and corrective-action records for benchmark, QA, documentation, and validation deficiencies |
| [export-control-sensitive-information-review.md](export-control-sensitive-information-review.md) | Export-control and sensitive-information screen for contributions, generated artifacts, and releases |
| [reactor-taxonomy-and-flagship.md](reactor-taxonomy-and-flagship.md) | Case roles: smoke tests, research cases, benchmarks, surrogates, and the flagship planning target |
| [browser-front-end.md](browser-front-end.md) | Browser lab runtime, access controls, run safety, and development checks |
| [github-projects.md](github-projects.md) | GitHub issue, project-board, and status-field workflow |
| [openmsr-review.md](openmsr-review.md) | Notes from adjacent open MSR projects and where this repo can borrow ideas |

## Writing Notes

- Prefer concise top sections and push derivations into clearly named subsections.
- Link back to case YAML, benchmark YAML, source modules, or result bundle files when a claim depends on an implementation detail.
- Mark surrogate assumptions explicitly. The reports and README should preserve maturity context.
- Use tables for parameter summaries and equations for model definitions; do not bury units in prose.
