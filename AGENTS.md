# AGENTS

## Runtime

- Use the repo-local runtime in `.runtime-env` for all Python work in this repository.
- Do not rely on system `python`, `pytest`, or global conda installs.
- On this Windows host, PowerShell script execution is restricted. Use the `.cmd` wrappers as the default entrypoints.
- Open a fresh configured shell with:

```powershell
.\scripts\Enter-PytbknShell.cmd
```

- If `.runtime-env` is missing, bootstrap it with the checked-in micromamba tool:

```powershell
.\scripts\Enter-PytbknShell.cmd -Bootstrap
```

This sets `PYTHONPATH=src`, points temp/cache directories into the repo, and exposes:

- `python`
- `pytest`
- `reactor`

## Running The Repo

Interactive PowerShell after bootstrapping:

```powershell
python -m thorium_reactor.cli build example_pin
python -m thorium_reactor.cli run example_pin --no-solver
reactor render tmsr_lf1_core
reactor report example_pin
```

One-shot wrapper without entering an interactive shell:

```powershell
.\scripts\Run-Reactor.cmd build example_pin
.\scripts\Run-Reactor.cmd run example_pin --no-solver
.\scripts\Run-Reactor.cmd render tmsr_lf1_core
```

## Web Interface

- The browser lab interface is a single-port FastAPI + React app at `http://localhost:18488`.
- Start it with the Windows wrapper:

```powershell
.\scripts\Run-Web.cmd
```

- The wrapper builds `web/ui/dist` when needed, then starts the Docker Compose `web` service. Use `.\scripts\Run-Web.cmd -SkipUiBuild` only when the production UI build is already current.
- Keep the normal browser runtime on one public port. Do not reintroduce a separate required browser-facing frontend port; Vite is only an optional hot-reload tool while editing `web/ui`, and it proxies `/api` to `http://localhost:18488`.
- For frontend changes, run commands from `web/ui` with `npm.cmd`, especially:

```powershell
npm.cmd run build
npm.cmd run test
```

- Browser-launched simulation runs must write isolated bundles under `results/<case>/<run_id>/` and must not modify canonical `configs/cases/*/case.yaml`.
- The web job allowlist is intentionally limited to `build`, `run --no-solver`, `transient`, `transient-sweep`, `validate`, `render`, and `report`. Solver-backed OpenMC benchmarks and external integrations can be shown as artifacts, but should not be browser-launchable unless the safety model is deliberately expanded.
- The Science view indexes `README.md` and `docs/*.md`. Use H1 headings for readable titles, and write formulas as Markdown math with `$...$` or `$$...$$` so the React/KaTeX renderer can display them.
- After web UI changes, verify `http://localhost:18488/api/health` and click through Dashboard, Cases, Builder, Runs, Science, and 3D in the browser.

## Testing

Run the full suite:

```powershell
.\scripts\Run-Tests.cmd
```

Run focused tests:

```powershell
.\scripts\Run-Tests.cmd tests\test_flow.py -q
.\scripts\Run-Tests.cmd tests\test_geometry.py tests\test_reporting.py
```

Interactive after bootstrapping:

```powershell
pytest
pytest tests\test_flow.py -q
python -m pytest tests\test_config_and_build.py
```

## Notes

- The default Windows workflow is geometry, reporting, reduced-order flow, and dry-run neutronics. Solver-backed OpenMC runs still require a supported host or Docker path documented in `README.md`.
- Keep generated caches under `.tmp` and `.pip-cache`.
