from thorium_reactor.cli import build_parser


def test_cli_registers_all_commands() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["run", "example_pin", "--no-solver"])

    assert namespace.command == "run"
    assert namespace.case == "example_pin"
    assert namespace.no_solver is True
