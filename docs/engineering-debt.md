# Engineering Debt

Known structural problems in this repository, with the diagnosis each one
rests on. This replaces the earlier phased refactor plan and its Phase 0.5
verification record: those described work that has since been done, and a
schedule that drifted out of date faster than the findings did.

File and line references are indicative, not pinned. Re-verify before acting.

## Standing rules

These are the rules the cleanup work was judged against. They are worth
keeping whether or not anything below gets done.

1. **No defensive reads below the interfaces.** `.get(key, default)` belongs at
   IO boundaries. There are ~3,200 `.get(` calls in the package — roughly one
   defensive dict read every nine lines.
2. **No `hasattr`/`getattr`-with-default polymorphism.** Normalize types at the
   boundary instead.
3. **No self-verifying output.** Never re-parse text or JSON you just produced;
   assert on the in-memory structure.
4. **No new abstraction without net deletion** in the same change — protocols,
   registries, base classes and "declarative specs" included.
5. **Delete, don't deprecate.** Single repo, no external consumers.
   `--prefer-gpu` is the cautionary tale: it survived as a deprecated alias
   long enough that its only two reachable outcomes became "no effect" and
   "error".
6. **`except Exception: pass` and `except BaseException` are banned.** Every
   swallow needs a logged reason.
7. **File-size tripwire.** New files over ~800 lines, or functions over ~80
   lines, need stated justification.
8. **No speculative config surface.** An env var, CLI flag or `case.yaml` key
   lands only with a consumer and a test in the same change.

## The QA gate is load-bearing

`qa/requirements.yaml` is not documentation. It declares an
`implementation_area` file list and a `verification_tests` list of specific
test functions per requirement, and `qa.py` checks that every declared path
exists and every declared test function is defined. That runs in CI.

Any change that deletes or renames a declared file or test function must edit
`qa/requirements.yaml` and `qa/requirements_traceability_matrix.csv` in the
same commit. `tests/test_qa.py` also asserts at least 10 requirements and that
matrix rows equal the requirement count. There are exactly 10, so removing
another requirement means revisiting that test.

## Open items

### 1. `summary.json` is a shared mutable global persisted to disk

The dominant structural problem. `summary.json` has writers across several
modules (`cli.py`, `neutronics/workflows.py`, `depletion/matrix.py`,
`economics/finance.py`, `transport/rzdg.py`, `uncertainty/sweep.py`), and
convergence is achieved by repetition: `generate_report` is called twice in a
row in three CLI branches, and `run_case` runs its benchmark-evidence and
validation block twice. Three separate defenses exist only to cope with
`summary["artifact_status"]` going stale.

The fix is an in-memory `RunResult` assembled across stages and serialized once
per stage end, which then allows deleting `_persist_refreshed_artifact_status`,
the embedded `summary["artifact_status"]` copy, and the doubled
`generate_report` calls.

### 2. Layering violations force that mutation

`neutronics/workflows.py` imports `reporting.plots`, and `reports.py` computes
domain state — readiness tiers, a reactor taxonomy keyed off case-name `if`
chains, result claims, the limitations matrix — then writes it back into
`summary.json`. Because presentation computes domain facts, the pipeline has to
re-run presentation to converge.

Worth enforcing with a small pytest contract test over `ast.parse` imports:
physics and domain modules must not import `reporting.*` or `web.*`;
`reporting.*` must not write into bundles; `web.*` may import only the
designated pipeline API surface.

### 3. The self-verifying output loop

The report builders emit `None`/`N/A`, a series of regex passes scrub the
rendered text (`reports.py` has 14 `re.sub` calls), and
`build_presentation_qa` greps the result to check the scrubbing.
Three mechanisms, ~200 LOC, to work around builders that should not emit the
missing lines in the first place. Fixing the builders lets the scrubber,
`build_presentation_qa` and `presentation_qa.json` all go.

This is the one QA layer that is redundant. The other four are not:
`benchmark_evidence` produces, `evidence` consumes, `sidecar_schemas` checks
shape, and `qa` gates repo requirements in CI. Keep those.

### 4. `dict[str, Any]` is the dominant data type

Config validation is 28 hand-written imperative validators in `config.py`, and
retains nothing: every consumer re-reads `config.data` with scattered inline
defaults. `config` is typed `CaseConfig` at some sites and `Any` at many more,
bridged by `hasattr`/`getattr` duck-typing in nine modules — the fossil of an
unfinished migration, of which `literature_models.py` is the clearest specimen.

Order matters: normalize the typing first (everything below the interfaces
takes `CaseConfig`, delete the duck-typing branches), then introduce a schema.
Doing it the other way round leaves the dual representation in place forever,
which is how the nine fossil sites got there.

### 5. Atomic bundle writes

Reachable data loss, not a refactor. The web process polls bundle files while
the CLI writes them, so a torn `summary.json` is possible today;
`web/repository.py` swallows `JSONDecodeError` and renders it as an empty run.
All bundle writes should be temp-file plus `os.replace`.

`write_metrics` also builds CSV by string concatenation without escaping, and
should use the `csv` module.

### 6. Duplicated numeric helpers, with a correctness caveat

`_round_float` exists in 10 copies: seven rounding to 6 digits, one to 9, and two
taking the digit count as a parameter. `_coerce_float` has three bodies with *different* semantics — one
does not check finiteness and can emit `inf`/`nan` into JSON, which is a latent
bug to fix on its own rather than a tidy-up. Also duplicated: `_utc_now`,
`_darcy_friction_factor` (identical physics in two places, so fixes will not
propagate), `_clamp`, `_write_npz_sidecars`, `_repo_root_from_bundle`.

Unifying the rounding changes serialized output, so this needs a golden-bundle
fixture to exist first and a reviewed diff when it lands.

### 7. No logging

Zero logging calls in the package. Logs must go to stderr, because the web job
runner treats CLI stdout as its event protocol. There are ~30 broad exception
handlers, a small number of silent `pass` swallows, and two `except OSError:
pass` data-loss sites in `reports.py`.

### 8. Test coverage gaps that block the god-module splits

`flow/primary_system.py` (~2,200 lines, zero classes),
`geometry/exporters.py` (~1,930, including a ~350-line Blender program inside
an f-string, so it is not lintable) and `reporting/reports.py` (~2,600) all
want splitting, but `test_geometry.py` is ~100 lines covering several thousand
LOC and there is no `test_primary_system.py` or `test_exporters.py`.
Characterization tests are a hard prerequisite. Also missing: tests for
`chemistry`, `modeling`, `state_store`, `transient` and `depletion/chain`.

### 9. Web backend

- `repository.py` is ~1,670 lines and wants splitting into cases / runs /
  events / artifacts / docs / presenters. Keep the `_add_*_section` methods as
  plain methods: they are already mostly declarative tables, and a spec DSL
  covering their fallback chains would be longer than what it replaces.
- Hot paths: `GET /api/cases` re-reads the whole docs corpus per case; the
  artifact allow-set is recomputed per request; `list_runs` is uncached against
  a 5-second UI poll.
- `THORIUM_REACTOR_WEB_FAKE_JOBS` is a test seam in production code that
  fabricates summaries. Injecting the runner callable into `create_app` removes
  the branch and `_run_fake_job` together.
- Job runner: no cancel endpoint; bundles orphaned as "running" by a restart
  show running forever; `process.kill()` on timeout leaves grandchildren;
  `_SEQUENCE_CACHE` is unbounded.
- Three stacked catch-alls (`app.py` → `jobs.py` → `repository.py`) each catch
  `Exception`, so a repository `KeyError` can surface as "your YAML is invalid"
  and 500s can masquerade as 400s.
- Path sanitization is applied three times over (`web/repository.py`
  pre-sanitizes, `paths.py` re-sanitizes, then re-checks `is_relative_to`).
  Thinning this is security-sensitive: keep exactly one enforcement point and
  test it.
- SSE has no heartbeat, no disconnect detection, no cache headers, and no
  defined behavior for connecting to an already-finished run.

### 10. Frontend

- `types.ts` is hand-mirrored from the backend models and has drifted before.
  Generate it from `/api/openapi.json` and drift-check in CI.
- Server-owned constants are duplicated client-side: `phaseOptions` in
  `Builder.tsx` hardcodes six of the seven phases in `ALLOWED_PHASES`, omitting
  `build`.
- `api.ts` has no typed error class, so 401 and 429 are indistinguishable from
  400; path segments are not `encodeURIComponent`-ed.
- ~10 copies of the same loading/error ladder; four client formatters plus one
  server-side; three definitions of "latest run".
- No ESLint, though a disable comment exists. `styles.css` is ~2,900 lines for
  7 pages and wants a dead-selector audit.
- Vite ships three.js, echarts and katex to the dashboard; needs manual chunks.

### 11. Docs and packaging

- `docs/README.md` is a hand-maintained index and is already incomplete. Either
  generate it or add a link checker.
- Four overlapping setup narratives: `README.md`, `AGENTS.md`, `web/README.md`
  and `docs/browser-front-end.md`.
- No `py.typed`. Most modules have no docstring; `config.py` is ~1,050 LOC with
  one.

## Decisions owed by the maintainer

1. **Quota/rate-limit subsystem** (~330 LOC in `permissions.py` plus the Admin
   UI). If the app is effectively single-user behind Cloudflare Access, the
   quota accounting is ceremony. The identity core itself is well built; this
   is only about the accounting.
2. **`property_audit.py`** computes a "Source backing" value independently of
   the one `flow/properties.py` computes, and the report prints both for the
   same salt. Merging them is ~60 LOC. Note that both can only return
   `configured` unless an MSD-TP provider is actually wired up, since
   `direct_record` and `redlich_kister_estimate` are the only source kinds that
   count and only `msd_tp.py` produces them.
3. **`msd_tp.py`** is deliberately unwired, not abandoned: no shipped case
   selects it and the default data package does not exist, because MSD-TP
   redistribution rights are unconfirmed (see
   `docs/msd-tp-thermophysical-data.md`). It is user-enablable via `data_dir`
   or `THORIUM_REACTOR_MSD_TP_DATA_DIR`, and its consumers in
   `literature_models.py` are current. Either ship data and wire a case to it,
   or accept that its only exercise is a synthetic fixture.
4. **`physics_core` config validators** guard a `physics_core:` section no
   shipped case sets, and every sub-key has a hardcoded default at point of
   use. But two tests assert `ConfigError` for invalid values, so deleting the
   validators makes malformed config silently accepted. That is a deliberate
   reduction in strictness, not dead-code removal.

## Already done

For context, so these are not re-proposed:

- CI quality gate, lint/type/coverage config, pytest markers.
- Fail-closed web auth: every `/api` route now requires a verified identity
  through one router-level dependency, with `GET /api/health` as the single
  deliberate public exception.
- Deleted: the external-tool integration subsystem and its three relabeled
  Compose services, `--prefer-gpu`, `runtime_benchmark.py` and the accelerator
  dev-tooling tail, `scripts/dev_preview_server.py`,
  `docker-compose.openmc.yml`, the GPU viability experiment tree, five
  consumer-free functions, and two dead config fossils.
- Purged 15 MB of 2022 archive solver scratch and rewrote `.gitignore`,
  including anchoring the stray-solver-output patterns to the repository root.
