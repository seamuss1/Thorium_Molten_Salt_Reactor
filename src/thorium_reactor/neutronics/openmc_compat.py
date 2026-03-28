from __future__ import annotations

try:
    import openmc  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    openmc = None


def require_openmc():
    if openmc is None:
        raise RuntimeError(
            "OpenMC is not installed in the active environment. "
            "Install the conda environment from environment.yml to enable solver-backed runs."
        )
    return openmc
