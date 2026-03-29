import json
import shutil
import uuid
from pathlib import Path

import yaml

from thorium_reactor.bundle_inputs import ensure_bundle_inputs, load_bundle_inputs
from thorium_reactor.config import load_case_config
from thorium_reactor.neutronics.workflows import validate_case
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.reporting.reports import generate_report


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = REPO_ROOT / "configs" / "cases" / "example_pin" / "case.yaml"
SOURCE_BENCHMARK = REPO_ROOT / "benchmarks" / "tmsr_lf1" / "benchmark.yaml"
ORIGINAL_BENCHMARK_TITLE = "TMSR-LF1-inspired surrogate benchmark"


def test_new_bundle_captures_case_and_benchmark_snapshots() -> None:
    scratch_root = _create_scratch_repo()
    try:
        config = load_case_config(scratch_root / "configs" / "cases" / "example_pin" / "case.yaml")
        bundle = create_result_bundle(scratch_root, config.name, "snapshot-create")

        inputs = ensure_bundle_inputs(scratch_root, bundle, config)

        assert (bundle.root / "case_snapshot.yaml").exists()
        assert (bundle.root / "benchmark_snapshot.yaml").exists()
        provenance = json.loads((bundle.root / "provenance.json").read_text(encoding="utf-8"))
        assert provenance["case_name"] == "example_pin"
        assert provenance["run_id"] == "snapshot-create"
        assert provenance["source_case_path"] == "configs/cases/example_pin/case.yaml"
        assert provenance["source_benchmark_path"] == "benchmarks/tmsr_lf1/benchmark.yaml"
        assert provenance["schema_version"] == 1
        assert provenance["used_snapshot"] is True
        assert inputs.provenance["case"]["source"] == "bundled snapshot"
        assert inputs.provenance["benchmark"]["source"] == "bundled snapshot"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_prefers_bundled_case_snapshot_after_repo_case_changes() -> None:
    scratch_root = _create_scratch_repo()
    case_path = scratch_root / "configs" / "cases" / "example_pin" / "case.yaml"
    try:
        config = load_case_config(case_path)
        bundle = create_result_bundle(scratch_root, config.name, "report-repro")
        inputs = ensure_bundle_inputs(scratch_root, bundle, config)
        bundle.write_json(
            "summary.json",
            {
                "case": inputs.config.name,
                "result_dir": str(bundle.root),
                "neutronics": {"status": "dry-run"},
                "metrics": {"expected_cells": 4},
                "input_provenance": inputs.provenance,
            },
        )

        _mutate_case(case_path, name="Mutated Report Name", design_power_mwth=999.0)
        mutated_live_config = load_case_config(case_path)
        resolved = load_bundle_inputs(scratch_root, bundle, mutated_live_config)
        report = generate_report(
            resolved.config.name,
            resolved.config.data,
            bundle.root / "summary.json",
            None,
            None,
            resolved.benchmark,
            provenance=resolved.provenance,
        )

        assert "# Example Pin Smoke Test" in report
        assert "Mutated Report Name" not in report
        assert "- Design thermal power (MWth): `1.0`" in report
        assert "`999.0`" not in report
        assert "- Case definition: `bundled snapshot`" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_validation_prefers_bundled_benchmark_snapshot_after_repo_benchmark_changes() -> None:
    scratch_root = _create_scratch_repo()
    case_path = scratch_root / "configs" / "cases" / "example_pin" / "case.yaml"
    benchmark_path = scratch_root / "benchmarks" / "tmsr_lf1" / "benchmark.yaml"
    try:
        config = load_case_config(case_path)
        bundle = create_result_bundle(scratch_root, config.name, "validation-repro")
        inputs = ensure_bundle_inputs(scratch_root, bundle, config)
        summary = {
            "case": inputs.config.name,
            "result_dir": str(bundle.root),
            "neutronics": {"status": "dry-run"},
            "metrics": {"expected_cells": 4},
            "input_provenance": inputs.provenance,
        }
        bundle.write_json("summary.json", summary)

        _mutate_benchmark(benchmark_path, title="Mutated Benchmark Title")
        mutated_live_config = load_case_config(case_path)
        resolved = load_bundle_inputs(scratch_root, bundle, mutated_live_config)
        result = validate_case(
            resolved.config,
            bundle,
            summary=summary,
            benchmark=resolved.benchmark,
            provenance=resolved.provenance,
        )

        messages = {check["name"]: check["message"] for check in result["checks"]}
        assert messages["benchmark_metadata_loaded"] == ORIGINAL_BENCHMARK_TITLE
        assert result["provenance"]["benchmark"]["source"] == "bundled snapshot"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_legacy_bundle_falls_back_to_live_repo_inputs() -> None:
    scratch_root = _create_scratch_repo()
    try:
        config = load_case_config(scratch_root / "configs" / "cases" / "example_pin" / "case.yaml")
        bundle = create_result_bundle(scratch_root, config.name, "legacy-fallback")
        inputs = load_bundle_inputs(scratch_root, bundle, config)
        summary_path = bundle.write_json(
            "summary.json",
            {
                "case": config.name,
                "result_dir": str(bundle.root),
                "neutronics": {"status": "dry-run"},
                "metrics": {"expected_cells": 4},
            },
        )

        report = generate_report(
            inputs.config.name,
            inputs.config.data,
            summary_path,
            None,
            None,
            inputs.benchmark,
            provenance=inputs.provenance,
        )
        validation = validate_case(
            inputs.config,
            bundle,
            summary=json.loads(summary_path.read_text(encoding="utf-8")),
            benchmark=inputs.benchmark,
            provenance=inputs.provenance,
        )

        assert not (bundle.root / "case_snapshot.yaml").exists()
        assert not (bundle.root / "benchmark_snapshot.yaml").exists()
        assert inputs.provenance["case"]["source"] == "fallback live repo files"
        assert inputs.provenance["benchmark"]["source"] == "fallback live repo files"
        assert "- Case definition: `fallback live repo files`" in report
        assert validation["provenance"]["benchmark"]["source"] == "fallback live repo files"
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_report_and_validation_surface_input_provenance() -> None:
    scratch_root = _create_scratch_repo()
    case_path = scratch_root / "configs" / "cases" / "example_pin" / "case.yaml"
    try:
        config = load_case_config(case_path)
        bundle = create_result_bundle(scratch_root, config.name, "provenance-visible")
        inputs = ensure_bundle_inputs(scratch_root, bundle, config)
        summary = {
            "case": inputs.config.name,
            "result_dir": str(bundle.root),
            "neutronics": {"status": "dry-run"},
            "metrics": {"expected_cells": 4},
            "input_provenance": inputs.provenance,
        }
        bundle.write_json("summary.json", summary)
        validation = validate_case(
            inputs.config,
            bundle,
            summary=summary,
            benchmark=inputs.benchmark,
            provenance=inputs.provenance,
        )
        report = generate_report(
            inputs.config.name,
            inputs.config.data,
            bundle.root / "summary.json",
            bundle.root / "validation.json",
            None,
            inputs.benchmark,
            provenance=inputs.provenance,
        )

        assert json.loads((bundle.root / "provenance.json").read_text(encoding="utf-8"))["used_snapshot"] is True
        assert validation["provenance"]["benchmark"]["source"] == "bundled snapshot"
        assert "## Input Provenance" in report
        assert "- Benchmark metadata: `bundled snapshot`" in report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def _create_scratch_repo() -> Path:
    scratch_root = REPO_ROOT / ".tmp" / "test-bundle-snapshots" / uuid.uuid4().hex
    (scratch_root / "configs" / "cases" / "example_pin").mkdir(parents=True, exist_ok=True)
    (scratch_root / "benchmarks" / "tmsr_lf1").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_CASE, scratch_root / "configs" / "cases" / "example_pin" / "case.yaml")
    shutil.copy2(SOURCE_BENCHMARK, scratch_root / "benchmarks" / "tmsr_lf1" / "benchmark.yaml")
    return scratch_root


def _mutate_case(case_path: Path, *, name: str, design_power_mwth: float) -> None:
    payload = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    payload["reactor"]["name"] = name
    payload["reactor"]["design_power_mwth"] = design_power_mwth
    case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _mutate_benchmark(benchmark_path: Path, *, title: str) -> None:
    payload = yaml.safe_load(benchmark_path.read_text(encoding="utf-8"))
    payload["title"] = title
    benchmark_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
