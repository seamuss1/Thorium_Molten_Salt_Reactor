# Quality Assurance Artifacts

This directory holds controlled quality records for the reactor modeling workflow.
The artifacts are intentionally plain text so they can be inspected by reviewers,
loaded by reports, and packaged into future dossier outputs.

| Artifact | Purpose |
| --- | --- |
| `requirements.yaml` | Canonical requirement records with scope, verification, validation evidence, acceptance criteria, and current status. |
| `requirements_traceability_matrix.csv` | Flat requirements traceability matrix for spreadsheet review and automated checks. |

The Python loader in `thorium_reactor.qa` validates required fields, matrix
columns, unique IDs, and matrix-to-requirement links.

Run the repo-level QA gate with:

```powershell
.\scripts\Run-Reactor.cmd qa --format json
```

The JSON summary is stable enough for future report and dossier generators to
consume without reimplementing the artifact parsing rules.
