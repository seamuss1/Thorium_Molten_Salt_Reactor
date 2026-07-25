# Repository Refactor Plan (v2)

Status: proposed (2026-07-24). Covers the whole repository: the
`thorium_reactor` Python package, the FastAPI + React web stack, tests, CI,
Docker, scripts, and repo hygiene.

v2 supersedes the first draft after an independent adversarial review pass
(separate model, instructed to attack the plan and verify claims against the
code). The review confirmed the diagnosis, corrected several counts, killed
four proposals that would have added net code, and — most importantly —
reordered the plan around one principle the first draft missed:

> **The cheapest thing to put behind a quality gate is less code.** Roughly
> 2,000–2,500 LOC (~8% of the package) has no consumer at all. Deletion
> precedes restructuring; every later phase gets smaller and safer.

File/line references are as of commit `56859f0`.

---

## Guiding diagnosis

The codebase is a clean DAG (no circular imports) with real strengths —
snapshot-based reproducibility, an evidence contract, a careful proxy-auth
model — but four systemic problems drive almost all local smells:

1. **`summary.json` is a shared mutable global persisted to disk.** It has
   **19 writers across 8 modules** (`cli.py` ×10, `neutronics/workflows.py`
   ×3, plus `depletion/matrix.py:226`, `economics/finance.py:380`,
   `integrations.py:221`, `runtime_benchmark.py:163`, `transport/rzdg.py:322`,
   `uncertainty/sweep.py:174`). Convergence is achieved by repetition:
   `generate_report` is called twice in a row in three CLI branches, and
   `run_case` runs its benchmark-evidence/validation block twice
   (`workflows.py:533-598`). Three separate defenses exist just to cope with
   `summary["artifact_status"]` going stale.
2. **Layering violations force that mutation.** The workflow layer imports
   plotting (`neutronics/workflows.py` → `reporting.plots`), and the report
   renderer computes domain state (readiness tiers, a reactor taxonomy with
   hardcoded case names at `reports.py:2398-2437`) and writes it back into
   `summary.json`. Because presentation computes domain facts, the pipeline
   must re-run presentation to converge.
3. **The dominant data type is `dict[str, Any]`** (700+ annotations,
   **3,267 `.get(` calls** — one defensive dict read every 9 lines of
   package code). Config validation (38 hand-written validators, ~830 lines
   in `config.py`) retains nothing; every consumer re-reads `config.data`
   with scattered inline defaults. `config` is typed `CaseConfig` at 40 sites
   and `Any` at 91, bridged by `hasattr`/`getattr` duck-typing in nine
   modules — the fossil of a previous unfinished migration
   (`literature_models.py:253` is the clearest specimen).
4. **There is no automated quality gate.** No ruff/mypy config exists, PR CI
   runs the Python suite only via a full uncached Docker build (90-minute
   timeout, downloading a PyTorch XPU wheel per build for hardware GitHub
   runners don't have), and neither the frontend build nor its tests run in
   CI. Backend/frontend types are hand-mirrored and have drifted in three
   verified places.

**The slop signature here is structural, not verbal.** The package has ~23
comment lines and 69 docstring lines against 26,508 code lines — it is
*under*-documented. The excess is defensive ceremony (`.get` chains,
`isinstance` re-guards on already-validated data, self-verifying output QA)
and speculative surface (features whose only consumer is their own test
suite). The fix is deletion and typing, plus *adding* the missing docstrings —
not prose trimming.

---

## Architecture rule (not a re-packaging)

The first draft proposed moving 55 modules into a five-package layered
architecture. The review killed this: the DAG is already acyclic, only a
handful of specific edges are wrong, and a package migration adds churn plus a
compatibility-shim layer without deleting anything.

Instead: **keep the layering as a rule, enforced by a ~20-line pytest contract
test over `ast.parse` imports**:

- physics/domain modules must not import `reporting.*` or `web.*`;
- `reporting.*` must not write into bundles (single-writer rule, Phase 3);
- `web.*` may import only the designated pipeline API surface.

The two edges to break first (Phase 3): `neutronics/workflows.py` →
`reporting.plots`, and `reports.py` writing `summary.json`. If, after the
behavior fixes land, the module layout still feels wrong, move files then —
with earned information.

---

## Phase 0 — Safety net (do first, no behavior changes)

~1–2 days. Nothing else is safe without a gate.

1. **New `ci.yml`** on every PR, no Docker:
   - `pip install -e ".[dev]"` on `ubuntu-latest`;
     `pytest -m "not slow and not hardware"`.
   - `ruff check` + `ruff format --check`.
   - `mypy src/thorium_reactor` (permissive baseline).
   - Frontend job: `npm ci && npm run build && npm test` in `web/ui`.
   - Keep `msre-openmc-benchmark.yml` as the deep/dispatch workflow; drop its
     redundant re-run of four test files; add GHA Docker layer caching.
2. **Tool config in `pyproject.toml`**: `[tool.ruff]`, `[tool.mypy]`,
   `[tool.coverage]`, pytest `markers = ["slow", "hardware", "integration"]`;
   remove the dead `cache_dir` / `-p no:cacheprovider` contradiction.
3. **Dependency declaration fixes**: declare `pydantic` (imported at
   `web/schemas.py:5`, currently only transitive); remove the redundant `web`
   extra; add a `render-blender` extra for the `bpy`/`mathutils` imports; add
   `pytest-cov` and publish a coverage baseline.
4. **Mark slow/hardware tests**: `test_gpu_viability_experiments.py` →
   `hardware`; the 4-subprocess rate-limit test
   (`test_web_backend.py:458`) and the git-subprocess tests in
   `test_runtime_context.py` → `slow`.

Acceptance: green PR gate (lint, types, Python tests, frontend build+tests)
in under ~5 minutes.

---

## Phase 0.5 — Delete first (~6,200 LOC, and NOT low risk)

New in v2. Deleting these first shrinks every later phase — the first draft
was unknowingly scheduling three separate refactor treatments for
`integrations.py` alone.

> **Superseded in scope by
> [`refactor-phase-0-5-verification.md`](refactor-phase-0-5-verification.md).**
> Every target was re-traced at execution time as this plan requires. **None
> came back safe to simply delete — all eight are `PARTIAL`.** Read that
> document before touching any item below; it carries the corrected file
> lists, the execution order, and five corrections to this plan. The
> load-bearing surprise: `qa/requirements.yaml` names implementation files
> and test functions that CI verifies still exist, so several of these
> deletions break CI unless the QA artifacts are edited in the same commit.
> Two claims below are outright wrong — `.runtime-env/`/`.mamba/` are live,
> not dead, and dropping the `physics_core` validators removes real
> validation rather than dead code.

1. **The external-tool integration subsystem (~800 LOC).**
   `integrations.py` (570 LOC): all five `INTEGRATION_DEFINITIONS` are
   `export_only`; the rendered `*_handoff.json` decks are read back by
   nothing (the report prints the path as a string); the only production
   importer is `cli.py`; the web UI never mentions these tools. The five
   `run_<tool>_integration` wrappers are identical one-line delegations.
   Delete: `integrations.py`, the five CLI branches (`cli.py:514-602`) and
   their subcommand parsers, `docker/{moltres,saltproc,thermochimica}-runner.Dockerfile`
   (which install none of those tools), their three compose services, and the
   corresponding test blocks. If deck export is a real requirement, keep
   `run_named_integration` + one renderer (~450 LOC still removed).
2. **`msd_tp.py` (693 LOC) is dead by default.** Its data package
   `thorium_reactor.data.msd_tp` does not exist in the tree; its env-var
   escape hatch is set nowhere; zero of the 9 shipped cases select the
   `msd_tp*` property providers; its only exercise is a synthetic test
   fixture. Delete (with `docs/msd-tp-thermophysical-data.md` and the
   conftest fixture), or move to `experiments/` as an unshipped provider.
3. **`runtime_benchmark.py` (232 LOC) + the dev-tooling third of
   `accelerators.py` (~200 LOC).** `runtime_benchmark.json` has no reader
   except the raw-file download list. Removing it orphans the subprocess
   probe machinery, `main()`, `_PythonArrayNamespace`, `percentile_band`, and
   `get_array_namespace` in `accelerators.py` (whose only remaining caller is
   the GPU experiment, itself leaving in Phase 1). This also removes 5 of the
   repo's 6 silent `except: pass` handlers and both `except BaseException`
   sites (`accelerators.py:392,415`). Keep the backend-resolution core that
   `transient_sweep.py` uses.
4. **Dead config/env/flag surface** (small LOC, high signal):
   `THORIUM_REACTOR_DEVICE`, `THORIUM_REACTOR_WEB_PHASE_TIMEOUT_S`,
   `PYTBKN_ENV`, the `REPO_ROOT`/.runtime-env ffmpeg fallback in
   `exporters.py:528-535`, `loop_segments[].decay_heat_fraction`
   (validated, set by no case, read by nothing).
   **`--prefer-gpu`: delete, don't finish deprecating** — its behavioral
   branch is a tautology (`transient_sweep.py:120-122`), yet
   `web/schemas.py:101` defaults it true so every web sweep emits a
   deprecated flag. Remove the flag, the schema field, and the UI toggle
   (`Builder.tsx:94`).
5. **Unreachable `physics_core` config validators (~50 LOC).**
   `config.py:768-819` guards a `physics_core:` section that zero shipped
   cases set; every sub-key already has a hardcoded default at point of use,
   including a legacy-alias double fallback (`axial_nodes`/`core_nodes`)
   where neither name is ever set.
6. **`tests/test_container_workflow.py` — delete the file.** All 40 lines
   are source-string assertions against PowerShell and Dockerfiles (including
   `assert 'return "openmc"' in run_reactor` and a pinned XPU wheel URL). It
   would fail on Phase 1's changes anyway; `docker compose config` checks
   more than it does.
7. **Confirmed-dead functions (~120 LOC):**
   `capabilities.case_supports_capability`,
   `accelerators.{get_array_namespace,to_numpy,percentile_band}` (covered by
   item 3), `neutronics/openmc_compat.require_openmc`,
   `web/repository.iter_json_lines`,
   `flow/properties._evaluate_property_spec`.
8. **`scripts/dev_preview_server.py` (152 LOC)** — hand-rolled drift-prone
   copy of the FastAPI surface documenting a deleted runtime;
   `uvicorn --reload` + the Vite dev proxy cover it.
9. **`docker-compose.openmc.yml`** — stale strict subset; nothing references
   it.
10. **`GET /api/health`** has zero callers (not in `api.ts`, tests, or a
    compose healthcheck). Either delete it or make it real by wiring a
    compose `healthcheck` — it becomes the public-allowlist entry in
    Phase 5.1 only if it exists for a reason.

Acceptance: suite green; `grep` proves no dangling references; CHANGELOG
entry lists what left and why (with the orphan-branch name if anything is
parked rather than deleted).

---

## Phase 1 — Repo hygiene and single-source dependencies

~1–2 days.

1. **Purge the 2022 archive scratch (15 MB, 25% of tracked files).** Keep the
   scripts, README, XML inputs, one representative plot; delete the 51
   tracked `.h5` (40 are per-particle restarts; `statepoint.1000.h5` is
   9.2 MB), the `.ppm`s, the tracked Vim swap file, the zero-byte h5. Then
   remove the `!archive/legacy_openmc_2022/**` un-ignore (`.gitignore:159`).
   `git filter-repo` optional, needs a flag day; plain delete stops the
   working-tree cost now.
2. **Extract `experiments/gpu_viability/` (~2,740 LOC)** to its own repo or
   an orphan branch: its README disclaims validity, its runners hard-require
   the deleted `.runtime-env`, it's specific to one GPU. Goes with it: the
   torch-XPU wheel install and GPU env vars in `app-runner.Dockerfile`, and
   `tests/test_gpu_viability_experiments.py`.
3. **Rewrite `.gitignore`**: drop stock-template sections (~90 lines), the
   duplicate `.pytest_cache/`, the dangerous global `lib/` and `*.xml`
   ignores, and the truly dead `.pip-cache/` and `.tools/` entries.
   **Keep `.runtime-env/` and `.mamba/`** — verification found both are live
   and multi-GB on the dev host, and `.runtime-env` is the interpreter the
   test suite actually runs under. (v1 called them dead leftovers; that was
   wrong, and acting on it would have started tracking gigabytes.)
4. **Single-source dependencies.** All Dockerfiles switch to
   `pip install -e ".[dev]"` (also finally puts the `reactor` console script
   in containers); `environment*.yml` shrink to
   `python + pip + openmc/ffmpeg + -e ".[dev]"`; drop `matplotlib` from
   images. **Provenance coupling (flagged by review):** the probed-package
   list at `runtime_context.py:270-278` includes `matplotlib`, so image
   changes alter `dependency_hash` in `provenance.json`. Land all of Phase 1
   *before* generating the Phase 2 golden fixture.
5. **Scripts consolidation:** one parameterized `.cmd` shim instead of five
   copies; move `Run-Reactor.ps1`'s remaining verb→service routing into the
   CLI (much of it dies with Phase 0.5.1); dedupe the npm build block in
   `Build-Web-UI.ps1`/`Run-Web.ps1`; make `Run-Web.ps1` rebuild on stale
   `dist/`, not only absent; add a `Makefile`/`justfile` for POSIX
   contributors; pin Docker base images; add `.dockerignore`.
6. **De-hardcode the owner identity** (`web/permissions.py:26`,
   `Run-Web.ps1`, compose default) → required env var + `.env.example`.
   Reconcile the opposite access defaults (compose: required; local wrapper:
   disabled).
7. **Rewrite `AGENTS.md`** (its runtime story — micromamba `.runtime-env`,
   `reactor` binary — is wrong end-to-end); split human onboarding into
   `CONTRIBUTING.md`.

---

## Phase 2 — Foundations (shared seams before surgery)

~2–3 days. **Generate the golden-bundle fixture at the end of this phase,
after Phase 1's provenance-affecting changes** (review finding): a snapshot
test running `run --no-solver` + `report` on `example_pin`, diffing the
bundle against a checked-in fixture, timestamps excluded.

1. **`errors.py`**: root `ThoriumReactorError`; rebase the five existing
   exceptions. Migrate the 95 bare `ValueError`s opportunistically.
2. **Bundle IO**: give `ResultBundle` a read side — one `read_json(name,
   policy)` replaces the 20 inline `json.loads(...read_text())` occurrences
   in `cli.py` and the five competing helpers. **All writes atomic** (temp +
   `os.replace`) — the web process polls these files while the CLI writes
   them; today a torn `summary.json` silently renders as an empty run
   (`web/repository.py:1109` swallows `JSONDecodeError` → `{}`). Fix
   `write_metrics` to use the `csv` module (unescaped concat today).
3. **Constants seam (merged task, per review):** one module owns
   `DEFAULT_TRANSIENT_SWEEP_SAMPLES` and the shared physics constants/spec
   that both `transient_sweep` integrators use — so `web/schemas.py:7` and
   `cli.py:49` stop importing the 1,453-line module (which transitively
   probes torch at import), and the scalar/vectorized integrator pair
   references one spec. (First draft had this split across two phases.)
4. **Deduplicate numeric helpers — with a correctness caveat.**
   `_round_float`: **10 copies in 3 regimes** (7× 6-digit, 2× 10-digit, 1×
   9-digit). `_coerce_float`: 3 bodies with **different semantics** —
   `benchmarking.py:1146` doesn't check finiteness and can emit `inf`/`nan`
   into JSON; that's a latent bug to fix on its own, not a tidy-up. Plus
   `_utc_now` ×3, `_darcy_friction_factor` ×2 (identical physics, fixes
   won't propagate), `_clamp` ×2, `_write_npz_sidecars` ×2,
   `_repo_root_from_bundle` ×2 (keep the guarded one). **Because unifying
   rounding changes serialized output, land this *after* the golden fixture
   exists and regenerate with a reviewed diff** (review caught the
   contradiction with "byte-identical" acceptance).
5. **Logging** (zero uses today). Logs to stderr — the web job runner treats
   CLI stdout as the event protocol, so the stdout stream must stay clean
   (made explicit in Phase 3). Replace exception swallows with logged
   warnings: the real numbers are 30 broad handlers, 6 silent `pass` (5 in
   `accelerators.py` — mostly deleted in Phase 0.5.3), 2 `except
   BaseException` (swallow Ctrl-C), and the two `except OSError: pass` data-loss
   sites in `reports.py:61,1639`.
6. **Ship `py.typed`. Write the missing docstrings** for public entry points
   (45 of 55 modules have none; `config.py` has 852 LOC and one docstring).
   *(v1 proposed an `__all__`/façade policy here; review killed it as
   ceremony on an under-documented codebase.)*
7. **Add the import-contract pytest test** (Architecture rule above).

---

## Phase 3 — Invert the pipeline

The heart of the refactor. ~1–2 weeks, as separate PRs, under the golden
fixture. **Scope note from review: single-writer `summary.json` means
touching all 8 writer modules, not 3** — `depletion/matrix`,
`economics/finance`, `transport/rzdg`, `uncertainty/sweep` (and
`runtime_benchmark`/`integrations`, already deleted) each mutate summary
today.

1. **Extract domain logic out of `reports.py`.** Move
   `_classify_design_readiness`, `_classify_reactor_case` (make taxonomy a
   declared `case.yaml` field instead of case-name `if` chains),
   result-claims and limitations-matrix computation into a domain module.
   Pipeline computes once and stores; `reports.py` only renders.
2. **Remove plotting from `run_case`**; deduplicate its doubled
   benchmark-evidence/validation block (`workflows.py:533-598`).
3. **Single-writer `summary.json`** via an in-memory `RunResult` assembled
   across stages, serialized once per stage end. Delete the compensating
   machinery: `_persist_refreshed_artifact_status`, the embedded
   `summary["artifact_status"]` copy, the doubled `generate_report` calls.
4. **Kill the self-verifying output loop (promoted to its own item, per
   review).** The report builders emit `None`/`N/A`, six regex passes scrub
   the rendered text (`reports.py:2313-2370`), and `build_presentation_qa`
   greps the result to check the scrubbing. Fix the builders to not emit
   missing lines; delete the scrubber, `build_presentation_qa`, and
   `presentation_qa.json` in one PR (~200+ LOC, three mechanisms).
5. **CLI command registry.** Replace the 643-line `if/elif` `main()` with
   per-command handlers behind a registry; hoist the shared stage spine
   (discover → load → bundle → snapshot → body → finish); make the silent
   snapshot rebinding of `config` (`cli.py:211`) explicit. Collapse the five
   load-or-run preambles. Stop importing private `_build_visualization_state`
   cross-package.
6. **Split the remaining god modules — only after test coverage exists
   (hard prerequisite, see Phase 7.3):**
   - `flow/primary_system.py` (2,201 lines, zero classes) → network /
     heat-exchange / thermal-march / inventory modules; move
     `build_depletion_assumptions` out of `transient.py`.
   - `geometry/exporters.py` (1,923) → svg / mesh / gltf / raster / video /
     blender; move the ~350-line Blender program out of the f-string into a
     package-data template so it's lintable.
   - `reporting/reports.py` (2,481) → section modules, after items 1 and 4
     shrink it.
7. **Sidecar ownership table.** One module owns each sidecar's schema and
   sole writer. Fix the provenance divergence (web bundles write 8 keys,
   CLI bundles 13 — route `prepare_run_bundle` through `bundle_inputs`);
   tighten `sidecar_schemas` to catch it.
8. **Performance fixes:** fingerprint bundle files by size/mtime instead of
   SHA-256 of every byte (note: the before/after snapshot pair in
   `cli.py:215`/`:1011` is *intentional* — optimize `_artifact_fingerprint`,
   don't remove a call); pass the benchmark dict into `validate_case`
   instead of re-running `build_case`; make `latest_result_bundle` use
   run-id timestamps, not mtime.

---

## Phase 4 — Typed models at the seams

~1 week interleaved. **Order inverted from v1 (review finding):** normalize
first, then schema — otherwise the dual representation lives forever, as the
nine `hasattr(config, ...)`/`getattr(config, "data", ...)` fossil sites
prove the last migration did.

1. **Kill `config: Any` first.** Mechanical PR: everything below the
   interfaces takes `CaseConfig`, period; delete the duck-typing branches in
   `state_store`, `property_audit`, `depletion/matrix`, `transport/rzdg`,
   `flow/primary_system`, `modeling`, `literature_models`.
2. **Then `CaseSchema`** (pydantic or dataclass tree) replacing the ~33
   surviving imperative validators; defaults live in the schema — one place
   answers "what's the default turbine efficiency?" instead of ~90 `.get`
   sites. **No compatibility escape hatch**: `config.data` access is removed
   package-wide in the same series, enforced by a grep-ratchet.
3. **Type the metrics/summary spine** (`RunResult`/`SummaryDoc`) for keys the
   CLI, web sections, and reports consume; a typo becomes a mypy error, not
   a `KeyError` three stages later. Bare dicts stay acceptable inside pure
   numerics.
4. **Ratchet mypy** package-by-package; annotate the 7 public functions with
   untyped `bundle` params; standardize `frozen=True, slots=True`.

---

## Phase 5 — Web backend

~1 week. *(v1's router split and declarative section spec are cut — see
"What v2 dropped".)*

1. **Fail-closed auth — do immediately, it's a security bug.** Only
   `/api/me`, `POST /api/runs`, and the two admin routes check identity;
   `list_cases`, `get_case`, `validate_draft`, `list_runs`, `get_run`,
   `stream_events`, **`get_artifact`**, `list_docs`, `get_doc` are anonymous
   even with `THORIUM_REACTOR_ACCESS_REQUIRED=1`. Add a global dependency
   with an explicit public allowlist. Keep `app.py` as one file (179 lines
   does not need five router modules); register the SPA mount lazily.
2. **Split `repository.py` (1,290 lines)** into cases / runs / events
   (co-located with the NDJSON writer from `jobs.py`) / artifacts / docs /
   presenters / util. **Keep the 11 `_add_*_section` methods as plain
   methods** in `presenters/sections.py` — they are already ~85% declarative
   tables of tuples; a spec DSL covering their fallback chains and
   conditional prose would be longer than the 503 lines it replaces. The
   rename-safety win comes from a ~15-line test asserting each section
   yields ≥1 metric on the golden bundle (and, after Phase 4.3, from typed
   summary keys).
3. **Hot paths:** memoize the doc index (`GET /api/cases` re-reads the whole
   docs corpus per case); cache the artifact allow-set per
   `(run_dir, mtime)`; cache `list_runs` between the UI's 5-second polls.
   Merge the two near-identical status inferers; derive
   `_editable_parameters` from the case schema (Phase 4.2) instead of a
   65-line hand list.
4. **Job runner: inject, don't abstract.** Pass the runner callable into
   `create_app` — that alone deletes the `THORIUM_REACTOR_WEB_FAKE_JOBS`
   production branch and `_run_fake_job`'s fabricated summaries. (v1's
   `JobRunner` Protocol is cut: there will only ever be one real
   implementation.) Independently valuable and not blocked on any interface
   work: a job registry enabling a cancel endpoint; startup reconciliation
   (bundles orphaned as "running" by a restart show running forever);
   process-group kill on timeout (bare `process.kill()` leaves grandchildren);
   bound or replace `_SEQUENCE_CACHE`.
5. **Unstack the triple catch-all** (review C8): `app.py:91` →
   `jobs.py:66` → `repository.py:113` each catch `Exception`; a repository
   `KeyError` surfaces as "your YAML is invalid" and 500s masquerade as
   400s. One boundary handler, typed domain errors below it — pairs with
   moving `HTTPException` out of `permissions.py` (`normalize_email`,
   `RateLimitStore.claim`) and wrapping claim/release in a context manager.
6. **Path-sanitization cleanup, carefully:** sanitization is applied three
   times over (`web/repository.py` pre-sanitizes, `paths.py` re-sanitizes,
   then re-checks `is_relative_to`). Keep exactly one enforcement point *and
   test it* — these are security-sensitive; thin deliberately, not randomly.
7. **Harden SSE:** heartbeat, disconnect detection, cache headers, defined
   behavior for connecting to a finished run.

---

## Phase 6 — Frontend

~3–5 days. **No dependency on Phase 5 (review corrected v1): can start
day one** — `openapi-typescript` reads `/api/openapi.json` regardless of
route organization.

1. **Generate `types.ts` from OpenAPI**, drift-checked in CI. Kills the three
   live drifts (`RunEvent.artifact`, `SimulationDraft.draft_yaml`,
   `output_sections` optionality) by construction.
2. **`api.ts` overhaul:** query-key factory; typed error class (401/429
   currently indistinguishable from 400); `encodeURIComponent` on path
   segments; SSE into a `useRunEvents` hook.
3. **Derive server-owned constants from the API:** the Builder phase list
   duplicates `ALLOWED_PHASES` (and omits `build`); the sweep-sample default
   duplicates the Python constant (Phase 2.3's seam).
4. **Collapse duplication:** `<QueryPanel>` for the ~10 loading/error
   ladders; one `format.ts` (four client formatters + one server-side —
   decide once where formatting lives); `useGroupedParameters`; one
   definition of "latest run" (three exist).
5. **Split `runData.ts`**; Vite manual chunks so the dashboard doesn't ship
   three.js/echarts/katex.
6. **Add ESLint** (a disable-comment exists; ESLint doesn't) and component
   tests (jsdom + Testing Library: SSE invalidation, Viewer selection, Admin
   gate). **Audit `styles.css`** (2,863 lines for 7 pages) for dead
   selectors; expect 30–50% removable.

---

## Phase 7 — Test suite health

Interleaved, with one hard edge:

1. ~~Fix `test_container_workflow.py`~~ → **deleted in Phase 0.5.6** (the
   whole file is source-string assertions; there's no behavior to preserve).
2. **Shared `conftest.py` fixtures**: repo root, tmp case factory, golden
   bundle — removes per-file `REPO_ROOT` redefinitions and the
   fake-`pyproject.toml` hacks (decouple `discover_repo_root` from the
   packaging file with a dedicated sentinel).
3. **Coverage as a prerequisite, not a follow-up:** `test_geometry.py` is
   106 lines covering 3,652 LOC; there is no `test_primary_system.py` or
   `test_exporters.py`. **Phase 3.6's splits are blocked until
   characterization tests exist for those modules** (sequencing table
   updated accordingly). Also add tests for `chemistry`, `modeling`,
   `state_store`, `transient` (699 lines, none), `depletion/chain`.
4. **Split `test_reporting.py`** (1,567 lines) along the reports-module
   split.
5. **Coverage ratchet in CI** (fail on drop), plus the two grep-ratchets:
   `.get(` count (baseline 3,267) and `config.data` access (Phase 4.2) may
   only decrease.

---

## Phase 8 — Docs (trimmed)

v1's `docs/` re-foldering is cut (13 files is not a navigation problem, and
it forced a web-indexer rewrite). What remains:

1. **Generate the `docs/README.md` index** (hand-maintained today, will
   drift); add a link checker to CI.
2. **Collapse the four overlapping setup narratives** (`README.md`,
   `AGENTS.md`, `web/README.md`, `docs/browser-front-end.md`) into one, with
   links; merge `github-projects.md` into `CONTRIBUTING.md`.

---

## Code reduction ledger

Running total of net deletions, by phase (conservative):

| Phase | What | ~LOC removed |
| --- | --- | --- |
| 0.5 | integrations subsystem, msd_tp, runtime_benchmark + accelerators tooling, dead env/flags, unreachable validators, dead functions, dev_preview_server, test_container_workflow | 2,900 |
| 1 | GPU experiments (to own repo), archive binaries, gitignore/template cruft, script dedup | 2,900 + 15 MB |
| 2 | numeric-helper dedup, JSON-read helpers | 250 |
| 3 | doubled report/validation calls, presentation-QA loop + regex scrubber, artifact-status compensators, CLI preambles, config validators for deleted features | 700 |
| 4 | 38→schema validators (~830→~300), duck-typing branches, `.get` chains as typing lands | 800+ |
| 5 | fake-jobs branch, status-inferer merge, `_editable_parameters` hand list, sanitization dedup | 250 |
| 6 | formatter/ladder/memo dedup, dead CSS (30–50% of 2,863) | 1,000–1,600 |
| **Total** | | **~8,800–9,400 LOC (~25%+), 15 MB binaries, 3 Docker images, 5 CLI verbs** |

### Tier-3 decisions still owed by the maintainer

1. **Quota/rate-limit subsystem** (~330 LOC in `permissions.py` + Admin UI):
   if the app is effectively single-user behind Cloudflare Access, it's
   ceremony. (Review note: the auth core itself is well built — this is
   about the quota accounting, not identity.)
2. **`property_audit.py` → merge into `flow/properties.py`** (~60 LOC): the
   report currently prints two independently computed "Source backing"
   values for the same salt (`reports.py:403` vs `:428`). Its only unique
   contribution dies with msd_tp.
3. **QA/evidence layers — corrected from v1:** the review verified these are
   *not* four copies of one check (`benchmark_evidence` produces,
   `evidence` consumes, `sidecar_schemas` checks shape, `qa` gates repo
   requirements in CI). Keep all four; the redundant *fifth* layer
   (presentation QA) dies in Phase 3.4. v1's "fold the checkers into one"
   is withdrawn.

### Standing anti-slop rules (review policy; enforce where possible)

1. **No defensive reads below the interfaces.** `.get(key, default)` only at
   IO boundaries; CI grep-ratchet from baseline 3,267, monotonically down.
2. **No `hasattr`/`getattr`-with-default polymorphism** (18 + 9 fossil
   sites); normalize types at the boundary.
3. **No self-verifying output** — never re-parse text/JSON you just produced;
   assert on the in-memory structure.
4. **No new abstraction without net deletion** in the same PR (protocols,
   registries, base classes, "declarative specs" included — two of v1's own
   proposals failed this test).
5. **Delete, don't deprecate** — single repo, no external consumers;
   `--prefer-gpu` is the cautionary tale.
6. **`except Exception: pass` and `except BaseException` are banned**; every
   swallow needs a logged reason.
7. **File-size tripwire:** new files >~800 lines or functions >~80 lines need
   stated justification.
8. **No speculative config surface:** an env var, flag, or `case.yaml` key
   lands only with a consumer and a test in the same PR.

---

## Sequencing summary

| Phase | Content | Effort | Hard dependencies |
| --- | --- | --- | --- |
| 0 | CI gate, lint/type config, dep fixes, markers | 1–2 d | — |
| 0.5 | Delete: integrations, msd_tp, runtime_benchmark, dead surface | 1–2 d | 0 |
| 1 | Archive purge, GPU-experiment extraction, dep single-sourcing, scripts, AGENTS.md | 1–2 d | 0 |
| 2 | errors, atomic bundle IO, constants seam, numutil, logging, docstrings, import-contract test; **golden fixture last, after Phase 1** | 2–3 d | 1 (fixture) |
| 3 | Pipeline inversion (8 writer modules), presentation-QA deletion, CLI registry; god-module splits **blocked on 7.3 coverage** | 1–2 wk | 2; 3.6 ⇐ 7.3 |
| 4 | Normalize config typing (4.1 before schema), summary spine, mypy ratchet | ~1 wk | 2 |
| 5 | Auth fail-closed (immediate), repository split, injected job runner, catch-all unstacking | ~1 wk | 5.1: none |
| 6 | Generated types, api layer, dedup, ESLint, CSS audit | 3–5 d | **none** |
| 7 | Fixtures, characterization coverage (gates 3.6), ratchets | interleaved | — |
| 8 | Generated docs index, merged setup narrative | 1 d | — |

**Immediate out-of-band bug fixes** (not refactors):

- Fail-closed web auth (5.1).
- Atomic bundle writes (2.2) — torn reads reachable today.
- `pydantic` undeclared (0.3).
- `benchmarking.py:1146` `_coerce_float` can emit `inf`/`nan` into JSON.
- Web bundles write weaker provenance than CLI bundles (3.7).
- Delete `--prefer-gpu` end to end (0.5.4).
- The 9-digit `_round_float` outlier in `flow/properties.py:359` — confirm
  intended precision before unifying.

## What v2 dropped from v1, and why (adversarial-review outcomes)

- **Five-package architecture migration** → replaced by an enforced import
  rule; the DAG is already acyclic and only specific edges are wrong.
- **APIRouter split of the 179-line `app.py`** → net more code; the security
  fix doesn't need it; it wasn't a real prerequisite for OpenAPI typegen.
- **Declarative section spec** → the 11 methods are already mostly tables;
  a spec covering their fallbacks/prose is a mini-interpreter longer than
  what it replaces. A 15-line golden-section test delivers the safety win.
- **`JobRunner` Protocol** → one real implementation forever; inject a
  callable instead.
- **`docs/` re-foldering** → 13 files; would force a web-indexer rewrite for
  no navigation gain.
- **`__all__`/façade policy** → ceremony on an under-documented codebase;
  write the missing docstrings instead.
- **"Fold the QA checkers into one"** → withdrawn; review showed the four
  layers check different things and are load-bearing. The fifth
  (presentation QA) still dies.
- Corrected counts: 19 summary writers (not 12); 10 `_round_float` copies in
  3 regimes (not 8); 30 broad handlers / 6 silent (not 13); before/after
  bundle snapshots are intentional (optimize the fingerprint, not the call
  count); `starlette` is not actually imported; tool Dockerfiles differ by
  one ENV line (still install nothing).

## Risks

- **Golden-fixture churn:** Phases 2.4/3/4 intentionally change serialized
  output (rounding unification, provenance completeness, single-writer
  summary). Regenerate per PR with a reviewed diff, never wholesale; never
  regenerate before Phase 1 lands (provenance coupling).
- **Deletion false positives:** Phase 0.5 items were verified by reference
  tracing, but anything parked (msd_tp, GPU experiments) goes to an orphan
  branch, not oblivion, for one release cycle.
- **Security-sensitive dedup:** path sanitization and auth changes need
  their own focused review + tests; don't batch them with mechanical PRs.
- **Windows-first reality:** primary dev host is Windows with restricted
  PowerShell; keep the `.cmd` shims working through every phase; add a
  Windows CI leg before deleting any of them.
