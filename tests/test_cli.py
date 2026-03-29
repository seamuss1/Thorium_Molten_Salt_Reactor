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
