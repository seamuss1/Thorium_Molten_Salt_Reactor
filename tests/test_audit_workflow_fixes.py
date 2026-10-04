import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import thorium_reactor.cli as cli
import thorium_reactor.neutronics.workflows as workflows
from thorium_reactor.bundle_inputs import ensure_bundle_inputs, load_bundle_inputs
from thorium_reactor.config import ConfigError, load_case_config
from thorium_reactor.paths import create_result_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]


def _scratch_case(root: Path, *, with_benchmark: bool = False) -> Path:
    path = root / "configs/cases/example_pin/case.yaml"
    path.parent.mkdir(parents=True)
    raw = yaml.safe_load((REPO_ROOT / "configs/cases/example_pin/case.yaml").read_text(encoding="utf-8"))
    raw["validation_targets"].pop("keff_smoke_band")
    if with_benchmark:
        benchmark = root / raw["reactor"]["benchmark"]
        benchmark.parent.mkdir(parents=True)
        benchmark.write_text("title: Original benchmark\n", encoding="utf-8")
    else:
        raw["reactor"].pop("benchmark")
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize("snapshot", ["case_snapshot.yaml", "benchmark_snapshot.yaml"])
@pytest.mark.parametrize("change", ["modify", "delete"])
def test_snapshot_integrity_rejected_before_loading_or_regenerating(tmp_path, snapshot, change):
    case = _scratch_case(tmp_path, with_benchmark=True)
    config = load_case_config(case)
    bundle = create_result_bundle(tmp_path, config.name, "integrity")
    ensure_bundle_inputs(tmp_path, bundle, config)
    recorded = (bundle.root / "provenance.json").read_bytes()
    path = bundle.root / snapshot
    if change == "modify":
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    else:
        path.unlink()
    for operation in (load_bundle_inputs, ensure_bundle_inputs):
        with pytest.raises(ConfigError, match=snapshot):
            operation(tmp_path, bundle, config)
    assert (bundle.root / "provenance.json").read_bytes() == recorded
    if change == "delete":
        assert not path.exists()


def test_legacy_snapshot_without_hash_remains_readable(tmp_path):
    config = load_case_config(_scratch_case(tmp_path))
    bundle = create_result_bundle(tmp_path, config.name, "legacy")
    (bundle.root / "case_snapshot.yaml").write_bytes(config.path.read_bytes())
    bundle.write_json("provenance.json", {"input_snapshots": {"case": {"path": "case_snapshot.yaml"}}})
    assert load_bundle_inputs(tmp_path, bundle).config.name == config.name


@pytest.mark.parametrize("invalid_provenance", ["{", "[]"])
def test_unreadable_provenance_fails_with_integrity_error(tmp_path, invalid_provenance):
    config = load_case_config(_scratch_case(tmp_path))
    bundle = create_result_bundle(tmp_path, config.name, "corrupt")
    (bundle.root / "provenance.json").write_text(invalid_provenance, encoding="utf-8")
    with pytest.raises(ConfigError, match="provenance.json"):
        load_bundle_inputs(tmp_path, bundle, config)


def test_cli_rejects_changed_snapshot_in_complete_dry_run_bundle(tmp_path, capsys):
    _scratch_case(tmp_path)
    common = ["--repo-root", str(tmp_path)]
    selected = ["example_pin", "--run-id", "complete"]
    assert cli.main([*common, "run", *selected, "--no-solver"]) == 0
    assert cli.main([*common, "report", *selected]) == 0
    assert cli.main([*common, "verify-bundle", *selected]) == 0
    root = tmp_path / "results/example_pin/complete"
    snapshot = root / "case_snapshot.yaml"
    recorded = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    raw = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
    raw["reactor"]["design_power_mwth"] = 999.0
    snapshot.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() != recorded["input_snapshots"]["case"]["sha256"]
    assert cli.main([*common, "verify-bundle", *selected]) != 0
    assert cli.main([*common, "report", *selected]) != 0
    assert "case_snapshot.yaml: SHA-256 does not match" in capsys.readouterr().err
    assert cli._verify_bundle_evidence_contract(cli._load_existing_bundle(tmp_path, "example_pin", "complete"))
    assert json.loads((root / "provenance.json").read_text(encoding="utf-8")) == recorded


@pytest.mark.parametrize("live_state", ["invalid", "missing", "renamed"])
def test_archived_bundle_commands_use_snapshot_without_live_config(tmp_path, live_state):
    case = _scratch_case(tmp_path)
    common = ["--repo-root", str(tmp_path)]
    selected = ["example_pin", "--run-id", "archive"]
    assert cli.main([*common, "run", *selected, "--no-solver"]) == 0
    if live_state == "invalid":
        case.write_text("invalid: input\n", encoding="utf-8")
    elif live_state == "missing":
        case.unlink()
    else:
        raw = yaml.safe_load(case.read_text(encoding="utf-8"))
        raw["name"] = "different_logical_name"
        case.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert cli.main([*common, "validate", *selected]) == 0
    assert cli.main([*common, "report", *selected]) == 0
    assert cli.main([*common, "report", "example_pin"]) == 0
    assert cli.main([*common, "verify-bundle", *selected]) == 0
    assert cli.main([*common, "run", *selected, "--reuse-run-id", "--no-solver"]) == 0


def test_cli_resolves_case_directory_alias_for_read_reuse_and_extend(tmp_path):
    case = _scratch_case(tmp_path)
    raw = yaml.safe_load(case.read_text(encoding="utf-8"))
    raw["name"] = "different_logical_name"
    case.write_text(yaml.safe_dump(raw), encoding="utf-8")
    common = ["--repo-root", str(tmp_path)]
    selected = ["example_pin", "--run-id", "alias"]
    assert cli.main([*common, "run", *selected, "--no-solver"]) == 0
    root = tmp_path / "results/different_logical_name/alias"
    assert root.is_dir()
    assert cli.main([*common, "report", *selected]) == 0
    assert cli.main([*common, "report", "example_pin"]) == 0
    assert cli.main([*common, "validate", *selected]) == 0
    assert cli.main([*common, "verify-bundle", *selected]) == 0
    assert cli.main([*common, "run", *selected, "--reuse-run-id", "--no-solver"]) == 0
    assert cli.main([*common, "moose", *selected]) == 0
    manifest = json.loads((root / "stage_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"][-1]["stage"] == "moose"
    assert not (tmp_path / "results/example_pin").exists()


@pytest.mark.parametrize("run_id", [None, "legacy"])
def test_legacy_alias_bundle_retains_live_config_fallback(tmp_path, run_id):
    case = _scratch_case(tmp_path)
    raw = yaml.safe_load(case.read_text(encoding="utf-8"))
    raw["name"] = "different_logical_name"
    case.write_text(yaml.safe_dump(raw), encoding="utf-8")
    expected = create_result_bundle(tmp_path, "different_logical_name", "legacy")
    bundle, live_config = cli._load_case_bundle(tmp_path, "example_pin", run_id)
    assert bundle.root == expected.root
    inputs = load_bundle_inputs(tmp_path, bundle, live_config)
    assert inputs.config.name == "different_logical_name"
    assert inputs.config.path == case


@pytest.mark.parametrize("status", ["failed", "completed_without_statepoint", "completed"])
def test_local_benchmark_exit_matches_solver_result(tmp_path, monkeypatch, status):
    _scratch_case(tmp_path)

    def fake_run(config, bundle, **kwargs):
        summary = {"case": config.name, "neutronics": {"status": status}, "metrics": {}}
        bundle.write_json("summary.json", summary)
        return summary

    monkeypatch.setattr(cli, "openmc", object())
    monkeypatch.setattr(cli, "run_case", fake_run)
    monkeypatch.setattr(cli, "generate_summary_plots", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "generate_validation_plot", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "generate_report", lambda *args, **kwargs: "# Report\n")
    code = cli.main(["--repo-root", str(tmp_path), "benchmark", "example_pin", "--run-id", "benchmark"])
    assert (code == 0) == (status == "completed")
    manifest = json.loads((tmp_path / "results/example_pin/benchmark/stage_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"][-1]["status"] == ("completed" if status == "completed" else "failed")


@pytest.mark.parametrize("command", cli.INTEGRATION_COMMANDS)
@pytest.mark.parametrize("status", ["failed", "input_deck_exported_missing_runtime", "completed"])
def test_external_cli_exit_matches_explicit_execution_result(tmp_path, monkeypatch, command, status):
    _scratch_case(tmp_path)

    def fake_integration(config, bundle, **kwargs):
        assert kwargs["execute"] is True
        bundle.write_json("summary.json", {"case": config.name, "neutronics": {"status": "dry-run"}, "metrics": {}})
        return {"status": status, "returncode": 7 if status == "failed" else 0}

    monkeypatch.setattr(cli, f"run_{command}_integration", fake_integration)
    code = cli.main(["--repo-root", str(tmp_path), command, "example_pin", "--run-id", "external", "--run-external"])
    assert (code == 0) == (status == "completed")
    manifest = json.loads((tmp_path / "results/example_pin/external/stage_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"][-1]["status"] == ("completed" if status == "completed" else "failed")


def test_export_only_external_workflow_still_succeeds(tmp_path):
    _scratch_case(tmp_path)
    assert cli.main(["--repo-root", str(tmp_path), "moose", "example_pin", "--run-id", "export"]) == 0


class _Region:
    def __init__(self, contains):
        self.contains = contains

    def __and__(self, other):
        return _Region(lambda point: self.contains(point) and other.contains(point))

    def __or__(self, other):
        return _Region(lambda point: self.contains(point) or other.contains(point))


class _Surface:
    def __init__(self, distance):
        self.distance = distance

    def __neg__(self):
        return _Region(lambda point: self.distance(point) < 0.0)

    def __pos__(self):
        return _Region(lambda point: self.distance(point) > 0.0)


@pytest.fixture
def symbolic_openmc(monkeypatch):
    def cylinder(r, x0=0.0, y0=0.0):
        return _Surface(lambda point: (point[0] - x0) ** 2 + (point[1] - y0) ** 2 - r**2)

    double = SimpleNamespace(
        ZCylinder=cylinder,
        XPlane=lambda x0, **kwargs: _Surface(lambda point: point[0] - x0),
        YPlane=lambda y0, **kwargs: _Surface(lambda point: point[1] - y0),
        ZPlane=lambda z0, **kwargs: _Surface(lambda point: point[2] - z0),
        Cell=lambda name: SimpleNamespace(name=name, fill=None),
        Universe=lambda cells: SimpleNamespace(cells=cells),
        Geometry=lambda universe: universe,
        Materials=list,
        Model=SimpleNamespace,
    )
    monkeypatch.setattr(workflows, "openmc", double)
    monkeypatch.setattr(workflows, "_create_materials", lambda config: {name: name for name in config.materials})
    monkeypatch.setattr(workflows, "_create_settings", lambda config: None)
    monkeypatch.setattr(workflows, "_create_tallies", lambda config, lookup: None)


@pytest.mark.parametrize("kind", ["pin", "ring", "detailed"])
def test_openmc_honors_inner_radii_with_nonoverlapping_void_cells(symbolic_openmc, kind):
    case_name = "example_pin" if kind == "pin" else "tmsr_lf1_core"
    config = load_case_config(REPO_ROOT / "configs/cases" / case_name / "case.yaml")
    if kind == "pin":
        config.geometry["layers"][2]["inner_radius"] = 0.45
        gap_radius, material_radius, material = 0.425, 0.455, "zirconium"
    else:
        if kind == "ring":
            config.geometry.pop("style")
        # The stock core has a 0.0001 cm gap before the fuel annulus.
        gap_radius, material_radius, material = 0.25005, 0.3, "fuel_salt"
    built = workflows.build_case(config, benchmark={})
    cells = built.model.geometry.cells
    gaps = [cell for cell in cells if cell.region.contains((gap_radius, 0.0, 0.0))]
    material_cells = [cell for cell in cells if cell.region.contains((material_radius, 0.0, 0.0))]
    assert len(gaps) == 1 and gaps[0].fill is None
    assert len(material_cells) == 1 and material_cells[0].fill == material
    assert len(cells) == built.manifest["openmc_cell_count"]
    assert len(cells) - built.manifest["cell_count"] == built.manifest["openmc_void_gap_cell_count"]
    assert built.manifest["openmc_void_gap_cell_count"] > 0
    if kind == "detailed":
        # The new void cells must remain bounded to the active axial interval.
        assert not gaps[0].region.contains((gap_radius, 0.0, 1000.0))
