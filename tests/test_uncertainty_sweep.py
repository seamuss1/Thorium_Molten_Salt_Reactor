from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from thorium_reactor.config import ConfigError, load_case_config, load_yaml
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.uncertainty.sweep import (
    apply_sample_to_case,
    build_uncertainty_samples,
    run_uncertainty_sweep_case,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _config():
    return load_case_config(REPO_ROOT / "configs" / "cases" / "msre_first_criticality" / "case.yaml")


def _benchmark():
    return load_yaml(REPO_ROOT / "benchmarks" / "msre_first_criticality" / "benchmark.yaml")


def _fake_openmc_executor(config, bundle, benchmark, sample, provenance):
    z = sample["standardized"]
    keff = (
        1.0001
        + 0.00018 * float(z.get("fuel_salt_density", 0.0))
        + 0.00006 * float(z.get("graphite_density", 0.0))
        + 0.00024 * float(z.get("u235_atom_fraction", 0.0))
        - 0.00004 * float(z.get("fuel_annulus_thickness", 0.0))
    )
    summary = {
        "case": config.name,
        "result_dir": str(bundle.root),
        "neutronics": {"status": "completed", "openmc_available": True},
        "metrics": {"keff": round(keff, 8), "keff_std_dev": 0.00001},
        "benchmark_quality": {
            "quality_score": 0.0,
            "quality_stage": "benchmark_blocked",
            "benchmark_ready": False,
            "passed_gate_count": 0,
            "failed_gate_count": 1,
            "gates": [
                {
                    "id": "uncertainty_propagated",
                    "status": "fail",
                    "message": "Benchmark uncertainty coverage is not yet fully quantified.",
                }
            ],
            "promotion_blockers": ["Benchmark uncertainty coverage is not yet fully quantified."],
        },
    }
    bundle.write_json("summary.json", summary)
    return summary


def test_uncertainty_samples_are_deterministic_and_include_oat_points() -> None:
    config = _config()
    inputs = load_yaml(REPO_ROOT / config.data["uncertainty_sweep"]["inputs"])
    from thorium_reactor.uncertainty.sweep import _resolve_enabled_parameters

    parameters = _resolve_enabled_parameters(inputs, config.data["uncertainty_sweep"])

    first = build_uncertainty_samples(parameters, samples=4, seed=5)
    second = build_uncertainty_samples(parameters, samples=4, seed=5)

    assert first == second
    assert first[0]["id"] == "nominal"
    assert first[1]["kind"] == "oat_minus"
    assert len(first) == 1 + 2 * len(parameters) + 4


def test_apply_sample_perturbs_materials_and_preserves_radial_stack() -> None:
    config = _config()
    inputs = load_yaml(REPO_ROOT / config.data["uncertainty_sweep"]["inputs"])
    from thorium_reactor.uncertainty.sweep import _resolve_enabled_parameters

    parameters = _resolve_enabled_parameters(inputs, config.data["uncertainty_sweep"])
    sample = build_uncertainty_samples(parameters, samples=1, seed=3)[1]

    perturbed = apply_sample_to_case(config.data, parameters, sample)

    assert perturbed is not config.data
    assert (
        perturbed["materials"]["fuel_salt"]["density"]["values"]
        != config.data["materials"]["fuel_salt"]["density"]["values"]
    )
    layers = perturbed["geometry"]["layers"]
    for index in range(1, len(layers)):
        assert float(layers[index]["outer_radius"]) > float(layers[index].get("inner_radius", 0.0))
        assert float(layers[index]["inner_radius"]) >= float(layers[index - 1]["outer_radius"])


def test_uncertainty_sweep_writes_artifacts_and_keeps_gate_blocked(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")

    summary = run_uncertainty_sweep_case(
        REPO_ROOT,
        config,
        bundle,
        _benchmark(),
        samples=4,
        seed=11,
        executor=_fake_openmc_executor,
    )

    assert summary["uncertainty_sweep"]["status"] == "completed"
    assert summary["uncertainty_sweep"]["coverage_status"] == "assumption_backed"
    assert summary["uncertainty_sweep"]["input_interval_width_pcm"] > 0.0
    assert summary["benchmark_quality"]["benchmark_ready"] is False
    assert summary["benchmark_quality"]["gates"][0]["status"] == "fail"
    keff_residual = next(item for item in summary["benchmark_residuals"]["items"] if item["metric"] == "keff")
    assert keff_residual["input_uncertainty_pcm"] > 0.0
    assert (bundle.root / "uncertainty_manifest.json").exists()
    assert (bundle.root / "uncertainty_samples.json").exists()
    assert (bundle.root / "uncertainty_results.json").exists()
    assert (bundle.root / "uncertainty_budget.json").exists()
    assert (bundle.root / "uncertainty_summary.json").exists()
    assert (bundle.plots_dir / "uncertainty_keff_interval.svg").exists()
    assert (bundle.plots_dir / "uncertainty_tornado.svg").exists()
    assert not (REPO_ROOT / "configs" / "cases" / "msre_first_criticality" / "case_snapshot.yaml").exists()


def test_uncertainty_sweep_require_source_backed_rejects_current_harness(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")

    with pytest.raises(ConfigError, match="not source-backed"):
        run_uncertainty_sweep_case(
            REPO_ROOT,
            config,
            bundle,
            _benchmark(),
            samples=2,
            require_source_backed=True,
            executor=_fake_openmc_executor,
        )


def test_uncertainty_results_can_resume_completed_samples(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(
        REPO_ROOT,
        config,
        bundle,
        _benchmark(),
        samples=2,
        seed=17,
        executor=_fake_openmc_executor,
    )

    summary = run_uncertainty_sweep_case(
        REPO_ROOT,
        config,
        bundle,
        _benchmark(),
        samples=2,
        seed=17,
        resume=True,
        executor=_fake_openmc_executor,
    )

    results = json.loads((bundle.root / "uncertainty_results.json").read_text(encoding="utf-8"))["results"]
    assert any(result["status"] == "skipped_existing" for result in results)
    assert summary["uncertainty_sweep"]["completed_sample_count"] == len(results)


@pytest.mark.parametrize("changed_input", ["seed", "case", "benchmark", "uncertainty_inputs"])
def test_resume_rejects_changed_inputs_without_relabeling_results(tmp_path: Path, changed_input: str) -> None:
    config = _config()
    benchmark = _benchmark()
    inputs = load_yaml(REPO_ROOT / config.data["uncertainty_sweep"]["inputs"])
    inputs_path = tmp_path / "uncertainty_inputs.yaml"
    inputs_path.write_text(yaml.safe_dump(inputs), encoding="utf-8")
    config.data["uncertainty_sweep"]["inputs"] = str(inputs_path)
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(REPO_ROOT, config, bundle, benchmark, samples=2, seed=17, executor=_fake_openmc_executor)
    before = {path.name: path.read_bytes() for path in bundle.root.glob("uncertainty_*.json")}
    seed = 17
    if changed_input == "seed":
        seed = 19
    elif changed_input == "case":
        config.reactor["design_power_mwth"] = 999.0
    elif changed_input == "benchmark":
        benchmark["description"] = "Changed benchmark reference"
    else:
        inputs["parameters"][0]["sigma_fraction"] = 0.3
        inputs_path.write_text(yaml.safe_dump(inputs), encoding="utf-8")

    calls = []

    def execute(*args):
        calls.append(args[3]["id"])
        return _fake_openmc_executor(*args)

    with pytest.raises(ConfigError, match="resume inputs differ"):
        run_uncertainty_sweep_case(
            REPO_ROOT, config, bundle, benchmark, samples=2, seed=seed, resume=True, executor=execute
        )
    assert not calls
    assert {path.name: path.read_bytes() for path in bundle.root.glob("uncertainty_*.json")} == before


def test_resume_can_extend_an_unchanged_sweep(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=17, executor=_fake_openmc_executor
    )
    calls = []

    def execute(*args):
        calls.append(args[3]["id"])
        return _fake_openmc_executor(*args)

    summary = run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=4, seed=17, resume=True, executor=execute
    )
    assert calls == ["sobol_0003", "sobol_0004"]
    assert summary["uncertainty_sweep"]["status"] == "completed"


def test_resume_after_rerun_without_benchmark_uses_current_snapshot(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=1, seed=17, executor=_fake_openmc_executor
    )
    run_uncertainty_sweep_case(REPO_ROOT, config, bundle, {}, samples=1, seed=17, executor=_fake_openmc_executor)
    calls = []

    def execute(*args):
        calls.append(args[3]["id"])
        return _fake_openmc_executor(*args)

    summary = run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, {}, samples=1, seed=17, resume=True, executor=execute
    )
    assert summary["uncertainty_sweep"]["status"] == "completed"
    assert not calls


def test_resume_rejects_modified_sample_snapshot(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=17, executor=_fake_openmc_executor
    )
    path = bundle.root / "samples" / "sobol_0001" / "case_snapshot.yaml"
    snapshot = load_yaml(path)
    snapshot["reactor"]["design_power_mwth"] = 999.0
    path.write_text(yaml.safe_dump(snapshot), encoding="utf-8")
    with pytest.raises(ConfigError, match="Saved inputs differ for uncertainty sample sobol_0001"):
        run_uncertainty_sweep_case(
            REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=17, resume=True, executor=_fake_openmc_executor
        )


def test_resume_retries_failed_sample_even_when_it_has_old_metrics(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=17, executor=_fake_openmc_executor
    )
    path = bundle.root / "samples" / "sobol_0001" / "summary.json"
    failed = json.loads(path.read_text(encoding="utf-8"))
    failed["neutronics"]["status"] = "failed"
    path.write_text(json.dumps(failed), encoding="utf-8")
    calls = []

    def execute(*args):
        calls.append(args[3]["id"])
        return _fake_openmc_executor(*args)

    run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=17, resume=True, executor=execute
    )
    assert calls == ["sobol_0001"]


def test_failed_rerun_cannot_relabel_prior_completed_results(tmp_path: Path) -> None:
    config = _config()
    bundle = create_result_bundle(tmp_path, config.name, "uq")
    run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=17, executor=_fake_openmc_executor
    )

    def fail_before_writing(*args):
        raise RuntimeError("Solver failed before producing new results")

    failed = run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=19, executor=fail_before_writing
    )
    assert failed["uncertainty_sweep"]["status"] == "incomplete"
    sample_summary = json.loads((bundle.root / "samples/sobol_0001/summary.json").read_text(encoding="utf-8"))
    assert sample_summary["neutronics"]["status"] == "failed"
    assert not sample_summary["metrics"]
    calls = []

    def execute(*args):
        calls.append(args[3]["id"])
        return _fake_openmc_executor(*args)

    completed = run_uncertainty_sweep_case(
        REPO_ROOT, config, bundle, _benchmark(), samples=2, seed=19, resume=True, executor=execute
    )
    assert completed["uncertainty_sweep"]["status"] == "completed"
    assert len(calls) == completed["uncertainty_sweep"]["sample_count"]
