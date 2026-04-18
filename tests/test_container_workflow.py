from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_canonical_docker_compose_defines_required_services() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose.get("services", {})

    assert {"app", "openmc", "thermochimica", "saltproc", "moltres"} <= set(services)
    assert services["app"]["build"]["dockerfile"] == "docker/app-runner.Dockerfile"
    assert services["openmc"]["build"]["dockerfile"] == "docker/openmc-runner.Dockerfile"
    assert services["thermochimica"]["environment"]["THORIUM_REACTOR_RUNTIME_SERVICE"] == "thermochimica"
    assert services["saltproc"]["environment"]["THORIUM_REACTOR_TOOL_RUNTIME"] == "saltproc"
    assert services["moltres"]["environment"]["THORIUM_REACTOR_RUNTIME_IMAGE"] == "thorium-reactor-moltres:latest"


def test_windows_wrappers_delegate_to_docker_compose() -> None:
    run_reactor = (REPO_ROOT / "scripts" / "Run-Reactor.ps1").read_text(encoding="utf-8")
    run_tests = (REPO_ROOT / "scripts" / "Run-Tests.ps1").read_text(encoding="utf-8")
    enter_shell = (REPO_ROOT / "scripts" / "Enter-PytbknShell.ps1").read_text(encoding="utf-8")

    assert '@("compose", "run", "--rm", "--build"' in run_reactor
    assert 'return "openmc"' in run_reactor
    assert '& docker compose run --rm --build app python -m pytest' in run_tests
    assert '& docker compose run --rm --build app sh' in enter_shell
