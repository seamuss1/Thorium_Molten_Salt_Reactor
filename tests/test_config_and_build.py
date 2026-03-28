from pathlib import Path

from thorium_reactor.config import load_case_config
from thorium_reactor.neutronics.workflows import build_case


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_case(name: str):
    return load_case_config(REPO_ROOT / "configs" / "cases" / name / "case.yaml")


def test_benchmark_paths_resolve_from_case_configs() -> None:
    config = _load_case("tmsr_lf1_core")

    assert config.benchmark_file is not None
    assert config.benchmark_file.exists()


def test_core_case_manifest_has_expected_channel_count() -> None:
    config = _load_case("tmsr_lf1_core")
    built = build_case(config)

    assert built.manifest["channel_count"] == 91
    assert built.manifest["cell_count"] == 456
    assert built.manifest["channel_variant_counts"] == {
        "fuel": 79,
        "control_guides": 6,
        "instrumentation_wells": 6,
    }
    assert built.manifest["geometry_kind"] == "ring_lattice_core"
    assert built.geometry_description["type"] == "detailed_molten_salt_reactor"


def test_example_pin_case_builds_without_solver() -> None:
    config = _load_case("example_pin")
    built = build_case(config)

    assert built.manifest["cell_count"] == 4
    assert built.geometry_description["type"] == "pin"
