# Export-Control And Sensitive-Information Review Policy

This repository is public, educational, and benchmark-oriented. It must not
publish controlled, proprietary, security-sensitive, or deployment-enabling
information. When a reviewer is unsure, the artifact is held from release until
an export-control or legal reviewer clears it.

This policy is an operating screen for contributors. It is not a legal
classification, license determination, or authorization to export technical
data. Current authority and review questions should be checked against official
sources such as [DOE/NNSA 10 CFR Part 810 guidance](https://www.energy.gov/nnsa/10-cfr-part-810),
[NRC 10 CFR Part 110 export and import guidance](https://www.nrc.gov/about-nrc/ip/export-import),
and [BIS Export Administration Regulations guidance](https://www.bis.gov/regulations/ear/734).

## Review Triggers

Run this review before merging, publishing, attaching, or externally sharing any
of the following:

| Trigger | Examples requiring review |
| --- | --- |
| New data | Benchmark source dossiers, measured operating points, proprietary datasets, vendor values, experimental logs, survey data, or copied tables from restricted sources. |
| Geometry | New reactor layouts, dimensions, lattice definitions, CAD-derived files, mesh exports, STL/OBJ/glTF artifacts, or geometry that could move a model from illustrative to build-specific. |
| Material details | Fuel salt composition, isotopic vectors, enrichment, impurity limits, graphite grade, structural alloys, reprocessing chemistry, online cleanup, source terms, or burnup/depletion details. |
| Operational claims | Claims about startup, shutdown, control response, power maneuvering, margins, accident response, maintenance, fuel processing, online cleanup, safeguards posture, or operating procedures for a real or benchmark-derived facility. |
| External contributions | Pull requests, issues, comments, attachments, imported files, partner data, generated patches, or examples submitted by people outside the trusted maintainer group. |
| Generated artifacts | Reports, plots, result bundles, solver input decks, external solver outputs, raw-derived solver outputs, external-code handoff files, notebooks, screenshots, mesh exports, or archives prepared for release. |
| Release packaging | README claims, documentation maps, tagged releases, GitHub artifacts, paper drafts, presentations, demos, or externally shared result bundles. |

## Content Classes

| Class | May be public? | Description |
| --- | --- | --- |
| Public benchmark and education content | Yes, after normal review | Information already public from open literature, public government pages, educational reduced-order equations, toy examples, and clearly labeled surrogate cases. Cite sources and keep maturity language visible. |
| Repository implementation metadata | Usually, after normal review | Open-source code, test fixtures, CLI behavior, QA status, and generated outputs from public configs, provided they do not add sensitive details beyond the public inputs. |
| Sensitive or proprietary information | No, unless explicitly cleared | Non-public vendor data, partner data, NDA material, restricted research notes, private emails, credentials, personal information, security weaknesses, or unpublished experimental records. |
| Controlled nuclear or dual-use technology | Hold for export-control review | Technical information that may be subject to DOE, NRC, BIS, ITAR, or other controls, including assistance, detailed design, production, operation, or use information for controlled nuclear or dual-use items. |
| Deployment-enabling information | Do not publish without explicit authorization | Details that materially enable construction, operation, optimization, procurement, safeguards bypass, security defeat, or fuel-cycle implementation of a real facility. |

## Public Content Rules

Public benchmark and education content is acceptable only when it stays inside
the public, non-proprietary scope of the repository.

- Cite public sources for benchmark values, operating points, and equations.
- Mark surrogate, dry-run, screening, and validation-blocked artifacts clearly.
- Prefer reduced-order, educational, or benchmark-harness descriptions over
  plant-specific design instructions.
- Keep generated artifacts tied to public case configs and bundle provenance.
- Remove credentials, local paths containing private names beyond normal repo
  paths, hidden metadata, and unrelated personal or organizational data.

## Hold Conditions

Hold the contribution or release when any condition below is true:

- The source is proprietary, NDA-bound, unclear, or cannot be cited publicly.
- A new geometry, material record, solver deck, or generated artifact appears to
  make the model more specific than its public benchmark or surrogate basis.
- The artifact includes exact plant, vendor, supply-chain, security, safeguards,
  deployment, fuel-cycle, or operational details not already public and reviewed.
- The contribution includes foreign-person transfer questions, technical
  assistance questions, or destination/end-user concerns.
- The artifact combines public information into a workflow that is materially
  more deployment-enabling than the individual public sources.
- The reviewer cannot distinguish educational benchmark content from controlled,
  proprietary, or sensitive information.

## Contribution Review Flow

1. Contributor self-screens the change using the review triggers and content
   classes above.
2. Contributor identifies the source class in the issue or pull request:
   `public_benchmark`, `education`, `repo_generated`, `third_party`,
   `proprietary`, `controlled_review`, or `unknown`.
3. Maintainer checks provenance, source citations, generated artifacts, and
   maturity language.
4. If any hold condition applies, maintainer removes the artifact from the
   release path and marks the work `controlled_review` or `needs:design`.
5. Export-control or legal reviewer decides whether the artifact can be public,
   must be redacted, must remain private, or requires a formal classification or
   license determination.
6. Maintainer records the decision in the issue, pull request, release notes, or
   nonconformance log before merge.

## Release Review Flow

Before a public release, demo, paper draft, presentation, or shared result
bundle:

1. List new or changed data, geometry, material details, external contributions,
   and generated artifacts.
2. Confirm each item is public benchmark/education content, repository-generated
   from public inputs, or explicitly cleared.
3. Verify reports and docs preserve maturity language for benchmark-blocked,
   surrogate, dry-run, and screening-backed results.
4. Remove or redact held artifacts from release packages.
5. Record the reviewer, date, scope, and decision in release notes or the
   relevant issue.
6. Open a `NCA-*` record for any unresolved deficiency that affects benchmark,
   QA, documentation, validation, export-control, or release confidence.

## Minimum Release Checklist

- Public source citations are present for new benchmark or operating-point data.
- Geometry exports are illustrative, benchmark-public, or explicitly cleared.
- Material details are public, surrogate, or explicitly cleared.
- External contributions have provenance and licensing reviewed.
- Generated artifacts do not include private inputs, credentials, hidden
  metadata, or held technical details.
- README, docs, reports, and release notes do not overstate validation maturity.
- Any hold, redaction, or accepted limitation is linked to an issue, PR, or
  nonconformance record.
