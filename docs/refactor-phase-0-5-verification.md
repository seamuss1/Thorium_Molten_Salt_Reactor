# Phase 0.5 deletion targets — execution-time verification

Status: verified 2026-07-24 against `290de48`, re-checked against the Phase 0
commits. This is the record the plan's rule demanded:

> **Verify before deleting.** Phase 0.5 items were verified consumer-free at
> commit `56859f0`; re-verify references at execution time before each
> deletion.

Eight targets were re-traced independently, plus a cross-target completeness
pass. **The headline result: not one target came back `SAFE_TO_DELETE`. All
eight are `PARTIAL`** — each needs edits elsewhere first, and several have
consumers the plan did not know about.

Deleting these in the order and scope the plan described would have broken
CI. The plan's "low risk" characterisation of Phase 0.5 was wrong; the work
is worth doing, but it is a careful multi-file refactor, not a delete.

## Verified scope

| Target | Verdict | LOC | Files to delete | Files to edit first |
| --- | --- | --- | --- | --- |
| External-tool integrations | PARTIAL | ~1,100 | 4 | **23** |
| `msd_tp.py` | PARTIAL | ~1,001 | 3 | 10 |
| `runtime_benchmark.py` + accelerators tail | PARTIAL | ~590 | 2 | 7 |
| GPU viability experiments (Phase 1) | PARTIAL | ~3,061 | 9 | 3 |
| Dead env/flag surface | PARTIAL | ~50 | 0 | **19** |
| `physics_core` config validators | PARTIAL | ~102 | 0 | 4 |
| Dead functions + `dev_preview_server` + `docker-compose.openmc.yml` | PARTIAL | ~231 | 3 | 6 |
| Archive binary purge (Phase 1) | PARTIAL | ~78 + 15 MB | 56 | 3 |

Total verified ≈ **6,200 LOC** — more than the plan's ~2,900 estimate for
Phase 0.5, because the plan under-counted the collateral (config validators,
state-store fields, report sections, case YAML blocks, docs).

## The blocker the plan completely missed: the QA gate is load-bearing

`qa/requirements.yaml` is not documentation. It declares, per requirement, an
`implementation_area` file list and a `verification_tests` list naming
specific test functions. `qa.py` then checks that **every declared path exists
and every declared test function is actually defined** in its file. That
validator runs in CI (`cli qa --format json`) and in `tests/test_qa.py`.

Consequences:

- Deleting `src/thorium_reactor/integrations.py` fails the QA gate, because
  `REQ-EXTERNAL-HANDOFF-EXPORTS` names it as an implementation area.
- Deleting `tests/test_container_workflow.py` fails the QA gate, because that
  requirement names one of its test functions as a verification test.
- `tests/test_qa.py` asserts `matrix rows == requirements total` **and**
  `total >= 10`. There are 11 requirements today, so removing one lands
  exactly on the floor; removing two breaks the suite. `qa/requirements.yaml`
  and `qa/requirements_traceability_matrix.csv` must be edited in lockstep.

Any deletion touching a declared implementation area or verification test
must edit the QA artifacts in the **same commit**.

## Corrections to the plan (and to the adversarial review)

1. **`tests/test_container_workflow.py` must be edited, not deleted.** The
   adversarial review said "delete the file, not replace it." That is wrong:
   the file is a declared QA verification test, and it also covers the `app`
   and `openmc` services that are staying. The three fake tool services and
   the XPU-wheel assertions come out; the file stays.
2. **`.runtime-env/` and `.mamba/` are NOT dead `.gitignore` entries.** The
   plan called them "leftovers of the abandoned micromamba runtime". Both
   exist and are large (~6.1 GB and ~2.6 GB). `.runtime-env` is the
   interpreter this repo's tests are actually run with on the dev host.
   **Removing those ignore lines would be actively harmful.** The `.gitignore`
   rewrite must keep them.
3. **Deleting the `physics_core` validators loses real validation.** The plan
   framed them as guarding a section no case sets. True — but at least two
   tests assert `ConfigError` is raised for invalid values, and deleting the
   validators makes malformed config silently accepted. This is a behaviour
   change, not dead-code removal. Either keep the validators or delete them
   together with their tests as a conscious reduction in strictness.
4. **`--prefer-gpu` deletion is a cross-stack change with a CI gate.**
   `web/ui/src/types.ts` declares `prefer_gpu` non-optional, so the frontend
   `tsc --noEmit` build fails unless the TypeScript changes land in the same
   commit as the Python ones.
5. **Torch is not purely experimental.** `cli.py` offers `--backend torch-xpu`
   on the production transient-sweep command, so removing the XPU wheel from
   the image is a supported-backend decision, not just experiment cleanup.

## What Phase 0 already retired

Several blockers the verifiers found were dissolved by the Phase 0 commits:

- The unused `accelerators` imports in `transient_sweep.py` — the only stated
  reason the `runtime_benchmark` deletion was `PARTIAL` — were removed by the
  ruff autofix. That target is now the cleanest of the eight.
- `tests/test_gpu_viability_experiments.py` is now marked `hardware`/`slow`,
  and CI runs `-m "not slow and not hardware"`, so it no longer gates CI.
- The duplicated pytest/QA steps were removed from the OpenMC workflow. The
  QA gate did not disappear — it **moved** to `ci.yml`, so QA blockers remain
  real, only their location changed.
- `ruff check` with `F401` in CI is now a mechanical safety net for exactly
  the leftover-import class of error these deletions risk.

## Recommended execution order

Ordered so that each step's blockers are already retired:

1. ~~**`runtime_benchmark.py` + accelerators tail**~~ — **done** (#90).
2. ~~**Dead functions, `dev_preview_server.py`, `docker-compose.openmc.yml`**~~
   — **done.** Re-verification at execution time showed the four functions
   each had exactly one reference (their own definition), and that
   `tests/test_container_workflow.py` reads only `docker-compose.yml`, never
   the `.openmc` variant. No QA artifact edit was needed after all: the QA
   requirement names `test_container_workflow.py`, which stays. The QA gate
   still reports `passed: true` with 11 requirements.
3. **`msd_tp.py`** — no production consumer; mind `tests/conftest.py`, whose
   only fixture serves it.
4. ~~**Dead env/flag surface incl. `--prefer-gpu`**~~ — **partly done.**
   `--prefer-gpu` is deleted end to end, Python and TypeScript in one commit
   because the frontend build type-checks against the schema.

   **The rest of this item is withdrawn.** Read in context, the remaining
   "dead env surface" is not slop:

   - `THORIUM_REACTOR_WEB_PHASE_TIMEOUT_S` (`web/jobs.py`) is a legitimate
     operator knob for job timeouts, with a sensible per-phase fallback.
     "Nothing sets it" is the normal state of an optional override, not
     evidence that it is dead.
   - `THORIUM_REACTOR_DEVICE` (`runtime_context.py`) is a one-line provenance
     extension point recording the execution hardware.
   - `PYTBKN_ENV` and the `REPO_ROOT`/`.runtime-env` entries in
     `_resolve_ffmpeg_binary` are fallbacks reached only when `shutil.which`
     fails, and `.runtime-env` is live (correction 2 above), so that branch
     is not vestigial either.

   That is about ten lines total, each with a default and no failure mode.
   Deleting a working escape hatch to save three lines is not a
   simplification. What they actually lack is documentation — none appears in
   any README or `.env.example`. Documenting them belongs with Phase 1.6.

   `loop_segments[].decay_heat_fraction` moves to the decision list: like the
   `physics_core` validators, removing it removes validation rather than dead
   code.
5. **GPU experiments extraction** (Phase 1) — to an orphan branch.
6. **External-tool integrations** — largest and most entangled; 23 files
   including QA artifacts, three case YAMLs, a report section, and a
   state-store field. Do it last, on its own, with the QA matrix edit.
7. **`physics_core` validators** — only after deciding item 3 above.

Archive purge and `.gitignore` (Phase 1) are independent and can go any time,
subject to correction 2.
