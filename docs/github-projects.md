# GitHub Projects Workflow

This repository uses GitHub Issues as the durable work record and GitHub Projects as the operating dashboard for triage, implementation, review, and release planning.

## Project

Create one linked Project named:

```text
Thorium Reactor Workboard
```

Recommended visibility for this public repository is public, unless draft planning items should remain private.

The authenticated token available in this workspace can read the repository but cannot create Projects. GitHub returned:

```text
Resource not accessible by personal access token
```

To create the Project through the API, the token needs GitHub Projects write access. In the GitHub UI, create it from the repository or the user Projects page, then link it to `seamuss1/Thorium_Molten_Salt_Reactor`.

## Repo-Backed Setup

This repository includes:

- `.github/ISSUE_TEMPLATE/*.yml` issue forms for bugs, features, engineering tasks, validation/research work, and docs/science notes
- `.github/PULL_REQUEST_TEMPLATE.md` for issue linkage and verification notes
- `.github/labels.json` as the label manifest
- `.github/workflows/sync-labels.yml` as a manual label sync workflow

After these files are merged, run the `Sync labels` workflow from the Actions tab to create or update the labels used by the issue forms. The local workspace token could read labels but could not create them, returning `Resource not accessible by personal access token`.

## Status Field

Use a single-select `Status` field with these states:

| Status | Meaning | Exit rule |
| --- | --- | --- |
| Inbox | New issue or PR, not yet trusted | It has been read and classified |
| Needs Info | Missing reproduction, decision, source, or acceptance detail | Required information is added |
| Needs Design | Valid need, but approach or scope is unsettled | Design choice and acceptance criteria are written |
| Triaged | Valid work, classified, not yet pickup-ready | Scope is small enough and verification is clear |
| Ready | An engineer can start without another planning pass | Work starts or it is deliberately deferred |
| In Progress | Someone is actively changing code/docs/artifacts | PR is opened |
| Review | PR exists and is waiting on review, CI, or manual verification | PR is merged, revised, or closed |
| Done | Work is merged, verified, or deliberately closed | Issue is closed with a reason |

Keep `Ready` small. It should contain the next highest-value work, not the whole backlog.

## Custom Fields

| Field | Type | Values |
| --- | --- | --- |
| Priority | Single select | P0, P1, P2, P3 |
| Area | Single select | web, cli, cases, geometry, physics, transport, depletion, reporting, benchmarks, qa, docs, infra, integrations |
| Size | Single select | S, M, L |
| Validation Impact | Single select | none, docs, artifact, benchmark, physics, safety |
| Target Date | Date | Optional for release or validation deadlines |

Use milestones for release targets. Use fields for operating metadata.

## Views

Create these saved views:

| View | Layout | Filter / Grouping |
| --- | --- | --- |
| Triage Board | Board | Columns by `Status`; show Inbox, Needs Info, Needs Design, Triaged |
| Ready Queue | Table | `Status:Ready`; sort by Priority then Size |
| Current Work | Board | Columns by `Status`; show Ready, In Progress, Review, Done |
| Web Lab | Table | `Area:web` |
| Validation | Table | `Area:benchmarks` or `type:validation` |
| Release Plan | Roadmap or table | Group by milestone or Target Date |

## Labels

Create these labels in the repository. Labels classify work; the Project `Status` field tracks lifecycle.

### Type

```text
type:bug
type:feature
type:task
type:docs
type:refactor
type:validation
```

### Area

```text
area:web
area:cli
area:cases
area:geometry
area:physics
area:transport
area:depletion
area:reporting
area:benchmarks
area:qa
area:docs
area:infra
area:integrations
```

### Priority And Size

```text
priority:p0
priority:p1
priority:p2
priority:p3
size:S
size:M
size:L
```

### Workflow Flags

```text
needs:triage
needs:info
needs:design
needs:repro
blocked
```

## Automations

Enable these built-in Project workflows:

| Trigger | Action |
| --- | --- |
| Item added to project | Set `Status` to Inbox |
| Issue or PR reopened | Set `Status` to Inbox |
| Pull request opened | Set `Status` to Review |
| Pull request merged | Set `Status` to Done |
| Item closed as completed | Archive after 30 days |

Use an auto-add workflow for this repository:

```text
repo:seamuss1/Thorium_Molten_Salt_Reactor is:issue,pr
```

If the board gets noisy, narrow auto-add to issues and PRs with `needs:triage`, `type:*`, or specific `area:*` labels.

## Issue Readiness

Move an issue to `Ready` only when it has:

- Clear problem or desired outcome
- Acceptance criteria
- Area and type labels
- Priority or explicit "later" decision
- Expected size
- Verification plan
- No unresolved blocker

For large work, create a parent issue and use sub-issues for independently reviewable slices.

## PR Discipline

Every non-trivial PR should link to an issue:

```text
Closes #123
```

Use `Fixes #123` for defects and `Resolves #123` for non-bug work if that reads better. The issue should close only when merged into the default branch and verification is done.
