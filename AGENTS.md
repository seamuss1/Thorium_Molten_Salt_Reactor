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
