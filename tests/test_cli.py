from thorium_reactor.cli import build_parser, resolve_benchmark_runtime


def test_cli_registers_all_commands() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["run", "example_pin", "--no-solver"])

    assert namespace.command == "run"
    assert namespace.case == "example_pin"
    assert namespace.no_solver is True


def test_cli_registers_benchmark_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["benchmark", "tmsr_lf1_core", "--docker-openmc"])

    assert namespace.command == "benchmark"
    assert namespace.case == "tmsr_lf1_core"
    assert namespace.docker_openmc is True


def test_benchmark_runtime_falls_back_to_docker_when_local_openmc_is_missing() -> None:
    runtime, message = resolve_benchmark_runtime(
        docker_requested=False,
        local_openmc_available=False,
        docker_status={"daemon_available": True, "message": None},
    )

    assert runtime == "docker"
    assert message is None


def test_benchmark_runtime_returns_guidance_when_no_solver_runtime_is_available() -> None:
    runtime, message = resolve_benchmark_runtime(
        docker_requested=False,
        local_openmc_available=False,
        docker_status={"daemon_available": False, "message": "Docker daemon unavailable."},
    )

    assert runtime == "error"
    assert message is not None
    assert "environment-openmc-linux.yml" in message
    assert "Docker daemon unavailable." in message


def test_cli_registers_transient_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["transient", "immersed_pool_reference", "--scenario", "load_follow_step"])

    assert namespace.command == "transient"
    assert namespace.case == "immersed_pool_reference"
    assert namespace.scenario == "load_follow_step"


def test_cli_registers_moose_and_scale_commands() -> None:
    parser = build_parser()
    moose = parser.parse_args(["moose", "immersed_pool_reference", "--run-external"])
    scale = parser.parse_args(["scale", "tmsr_lf1_core"])

    assert moose.command == "moose"
    assert moose.run_external is True
    assert scale.command == "scale"
    assert scale.run_external is False


def test_cli_registers_transient_sweep_command() -> None:
    parser = build_parser()
    namespace = parser.parse_args([
        "transient-sweep",
        "immersed_pool_reference",
        "--scenario",
        "partial_heat_sink_loss",
        "--samples",
        "1024",
        "--seed",
        "7",
        "--prefer-gpu",
    ])

    assert namespace.command == "transient-sweep"
    assert namespace.case == "immersed_pool_reference"
    assert namespace.scenario == "partial_heat_sink_loss"
    assert namespace.samples == 1024
    assert namespace.seed == 7
    assert namespace.prefer_gpu is True
