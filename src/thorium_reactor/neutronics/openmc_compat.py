from __future__ import annotations

import platform

try:
    import openmc  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    openmc = None


def missing_openmc_runtime_message(command_name: str = "run", *, system_name: str | None = None) -> str:
    system = (system_name or platform.system()).strip().lower()
    message = "OpenMC is not installed in the active environment."
    if system.startswith("win"):
        message += (
            " This repository now defaults to the Docker Compose workflow, so solver-backed OpenMC runs should use "
            "`docker compose run --rm openmc ...` or the `Run-Reactor.cmd` wrapper. A supported "
            "`environment-openmc-linux.yml` environment remains available as a best-effort fallback."
        )
    else:
        message += (
            " Solver-backed runs require either the Docker Compose `openmc` service or an environment with OpenMC "
            "installed, such as `environment-openmc-linux.yml`. A typical container command is "
            "`docker compose run --rm openmc python -m thorium_reactor.cli run <case>`."
        )
    if command_name == "benchmark":
        message += (
            " Benchmark runs require the solver-backed path and can use `reactor benchmark <case> --docker-openmc` "
            "or `docker compose run --rm openmc python -m thorium_reactor.cli benchmark <case>`."
        )
    return message


def require_openmc():
    if openmc is None:
        raise RuntimeError(missing_openmc_runtime_message())
    return openmc
