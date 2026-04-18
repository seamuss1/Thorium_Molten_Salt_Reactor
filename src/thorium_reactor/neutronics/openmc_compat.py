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
            " This Windows runtime supports reduced-order local workflows, but solver-backed OpenMC runs require "
            "Docker or a supported Linux/OpenMC environment created from `environment-openmc-linux.yml`."
        )
    else:
        message += " Solver-backed runs require an environment with OpenMC installed, such as `environment-openmc-linux.yml`."
    if command_name == "benchmark":
        message += " Benchmark runs require the solver-backed path and can use `reactor benchmark <case> --docker-openmc` when Docker is available."
    return message


def require_openmc():
    if openmc is None:
        raise RuntimeError(missing_openmc_runtime_message())
    return openmc
