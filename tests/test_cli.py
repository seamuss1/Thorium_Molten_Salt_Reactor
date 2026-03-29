from thorium_reactor.cli import build_parser


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
