# AGENTS

## Runtime

Everything runs in Docker Compose. There is no repo-local Python interpreter
to bootstrap and no `reactor` console script on the host PATH.

- `docker-compose.yml` is the release definition; `docker-compose.dev.yml`
  overlays a bind mount of the checkout onto `/workspace` so edits take effect
  without a rebuild.
- Three services: `app` (CLI and tests), `web` (browser lab), and `openmc`
  (solver-backed benchmark runs).
- `PYTHONPATH=/workspace/src` is set in the image.
- On this Windows host PowerShell script execution is restricted, so the `.cmd`
  wrappers are the default entrypoints. Each one just invokes its `.ps1`
  sibling with `-ExecutionPolicy Bypass`.

Open a container shell:

```powershell
.\scripts\Enter-PytbknShell.cmd
```

Add `-Bootstrap` to build the `app` image first:

```powershell
.\scripts\Enter-PytbknShell.cmd -Bootstrap
```

Inside that shell, `python -m thorium_reactor.cli` and `python -m pytest` work
directly.

For POSIX hosts, or to skip the wrappers, use Compose directly:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build app python -m thorium_reactor.cli --help
```

A local editable install also works for development without Docker, and is
what CI uses:

```bash
pip install -e ".[dev]"
```

## Running The Repo

One-shot wrapper, which picks the Compose service from the verb (`openmc` for
`benchmark` and for `run` without `--no-solver`, otherwise `app`):

```powershell
.\scripts\Run-Reactor.cmd build example_pin
```

```powershell
.\scripts\Run-Reactor.cmd run example_pin --no-solver
```

```powershell
.\scripts\Run-Reactor.cmd render tmsr_lf1_core
```

```powershell
.\scripts\Run-Reactor.cmd report example_pin
```

Commands: `qa`, `build`, `run`, `validate`, `report`, `render`, `benchmark`,
`verify-bundle`, `transient`, `transient-sweep`, `uncertainty-sweep`,
`transport`, `deplete`, `economics`.

## Web Interface

- The browser lab interface is a single-port FastAPI + React app at
  `http://localhost:18488`.
- Start it with the Windows wrapper:

```powershell
.\scripts\Run-Web.cmd
```

- The wrapper builds `web/ui/dist` when needed, then starts the Docker Compose
  `web` service. Use `.\scripts\Run-Web.cmd -SkipUiBuild` only when the
  production UI build is already current.
- Keep the normal browser runtime on one public port. Do not reintroduce a
  separate required browser-facing frontend port; Vite is only an optional
  hot-reload tool while editing `web/ui`, and it proxies `/api` to
  `http://localhost:18488`.
- For frontend changes, run commands from `web/ui` with `npm.cmd`, especially:

```powershell
npm.cmd run build
```

```powershell
npm.cmd run test
```

- Browser-launched simulation runs must write isolated bundles under
  `results/<case>/<run_id>/` and must not modify canonical
  `configs/cases/*/case.yaml`.
- The web job allowlist (`ALLOWED_PHASES` in `src/thorium_reactor/web/jobs.py`)
  is intentionally limited to `build`, `run`, `transient`, `transient-sweep`,
  `validate`, `render`, and `report`; the `run` phase is always launched with
  `--no-solver`. Solver-backed OpenMC benchmarks can be shown as artifacts, but
  should not be browser-launchable unless the safety model is deliberately
  expanded.
- Every `/api` route requires a verified identity through one router-level
  dependency. `GET /api/health` is the single deliberate public exception, so
  liveness can answer before an identity exists. Keep it that way: add new
  public routes to that one allowlist rather than per-route checks.
- The Science view indexes `README.md` and `docs/*.md`. Use H1 headings for
  readable titles, and write formulas as Markdown math with `$...$` or
  `$$...$$` so the React/KaTeX renderer can display them.
- After web UI changes, verify `http://localhost:18488/api/health` and click
  through Dashboard, Cases, Builder, Runs, Science, and 3D in the browser.

## GitHub Issues And Projects

- Use the repo Project `Thorium Reactor Workboard`: `https://github.com/users/seamuss1/projects/1`.
- Issue lifecycle is: `Inbox` -> `Needs Info` / `Needs Design` -> `Triaged` -> `Ready` -> `In Progress` -> `Review` -> `Done`.
- Use Project `Status` for lifecycle. Use labels/fields for classification: `type:*`, `area:*`, `priority:p*`, `size:*`, `roadmap:*`, `blocked`, and `needs:design`.
- When adding an issue, use the closest issue template, include acceptance criteria and verification, add it to the Project, and set `Status` to `Inbox` unless it is already actionable.
- Move an issue to `Ready` only when scope, acceptance criteria, priority/size/area, verification, and blockers are clear.
- When starting work, set `Status` to `In Progress`, work from a branch, and keep the issue updated if scope or blockers change.
- Open PRs with `Closes #<issue>` or `Fixes #<issue>`, and set the issue/Project item to `Review`.
- Close issues only after acceptance criteria are met and verification is recorded. Prefer merge-linked auto-close; otherwise close manually with `completed`, `not planned`, or `duplicate`.
- Do not close parent/tracking issues until their required child issues are done or explicitly deferred.

## Testing

Run the full suite:

```powershell
.\scripts\Run-Tests.cmd
```

Run focused tests:

```powershell
.\scripts\Run-Tests.cmd tests\test_flow.py -q
```

```powershell
.\scripts\Run-Tests.cmd tests\test_geometry.py tests\test_reporting.py
```

Inside a container shell:

```powershell
python -m pytest
```

```powershell
python -m pytest tests\test_flow.py -q
```

Markers: `slow`, `hardware`, and `integration`. CI runs
`pytest -m "not slow and not hardware"`, so anything that spawns subprocesses,
takes more than a few seconds, or needs specific local hardware must be marked
or it will run on every PR.

## Quality Gates

CI (`.github/workflows/ci.yml`) runs four jobs, all of which must pass:

- `lint`: `ruff check src tests`, `ruff format --check src tests`, and `mypy`
  (configuration in `pyproject.toml`; the module override list is typing debt
  that may only ever shrink)
- `test`: `pytest -m "not slow and not hardware" --cov` plus
  `python -m thorium_reactor.cli qa --format json`
- `frontend`: `npm ci && npm run build && npm test` in `web/ui`, where `build`
  includes `tsc --noEmit`
- `windows-workflows`: `tests/test_web_wrappers.py` and
  `tests/test_audit_regressions.py` on `windows-latest`, which is the only leg
  that exercises the PowerShell wrapper contract (those tests skip on Linux)

The QA gate is load-bearing, not documentation. `qa/requirements.yaml` declares
an `implementation_area` file list and a `verification_tests` list of specific
test functions per requirement, and `qa.py` checks that every declared path
exists and every declared test function is defined. Deleting or renaming a
declared file or test function fails CI unless `qa/requirements.yaml` and
`qa/requirements_traceability_matrix.csv` are edited in the same commit.
`tests/test_qa.py` also asserts at least 10 requirements and that matrix rows
equal the requirement count; there are exactly 10, so removing another one
means revisiting that test.

## Notes

- The default Windows workflow is geometry, reporting, reduced-order flow, and
  dry-run neutronics. Solver-backed OpenMC runs still require the `openmc`
  Compose service or a supported host, documented in `README.md`.
- Keep generated caches under `.tmp` and `.pip-cache`.
- `.gitignore` anchors the stray-solver-output patterns (`/*.xml`, `/*.h5`,
  `/*.png`, ...) to the repository root with a leading slash, because OpenMC
  writes those into its working directory. Do not make them repo-wide again:
  that silently swallows new XML inputs and documentation images.
