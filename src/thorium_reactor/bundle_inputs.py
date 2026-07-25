from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thorium_reactor.config import CaseConfig, load_case_config, load_yaml, resolve_benchmark_path
from thorium_reactor.paths import ResultBundle
from thorium_reactor.runtime_context import build_runtime_context

CASE_SNAPSHOT_NAME = "case_snapshot.yaml"
BENCHMARK_SNAPSHOT_NAME = "benchmark_snapshot.yaml"
PROVENANCE_NAME = "provenance.json"
SNAPSHOT_SCHEMA_VERSION = 1
PROVENANCE_GENERATOR = "thorium_reactor.bundle_inputs.ensure_bundle_inputs"
PROVENANCE_GENERATOR_VERSION = 2
SNAPSHOT_SOURCE = "bundled snapshot"
FALLBACK_SOURCE = "fallback live repo files"
NOT_CONFIGURED_SOURCE = "not configured"


@dataclass(slots=True)
class BundleInputs:
    config: CaseConfig
    benchmark: dict[str, Any]
    provenance: dict[str, Any]


def ensure_bundle_inputs(repo_root: Path, bundle: ResultBundle, live_config: CaseConfig) -> BundleInputs:
    case_snapshot_path = bundle.root / CASE_SNAPSHOT_NAME
    benchmark_snapshot_path = bundle.root / BENCHMARK_SNAPSHOT_NAME
    provenance_path = bundle.root / PROVENANCE_NAME
    if not case_snapshot_path.exists():
        case_snapshot_path.write_text(live_config.path.read_text(encoding="utf-8"), encoding="utf-8")
    live_benchmark_path = resolve_benchmark_path(repo_root, live_config.data)
    if live_benchmark_path and live_benchmark_path.exists() and not benchmark_snapshot_path.exists():
        benchmark_snapshot_path.write_text(live_benchmark_path.read_text(encoding="utf-8"), encoding="utf-8")
    input_snapshots = _input_snapshot_records(repo_root, case_snapshot_path, benchmark_snapshot_path)
    runtime = build_runtime_context(command=["bundle-inputs", live_config.name, bundle.run_id], cwd=repo_root)
    if provenance_path.exists():
        stored = _load_provenance(provenance_path)
        changed = False
        for key, value in {
            "generator": PROVENANCE_GENERATOR,
            "generator_version": PROVENANCE_GENERATOR_VERSION,
            "input_snapshots": input_snapshots,
            "runtime": runtime,
            "git": runtime.get("git"),
            "dependency_hash": runtime.get("dependency_hash"),
            "dependency_summary": runtime.get("dependencies", {}),
        }.items():
            if key not in stored:
                stored[key] = value
                changed = True
        if changed:
            bundle.write_json(PROVENANCE_NAME, stored)
    else:
        bundle.write_json(
            PROVENANCE_NAME,
            {
                "case_name": live_config.name,
                "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "dependency_hash": runtime.get("dependency_hash"),
                "dependency_summary": runtime.get("dependencies", {}),
                "generator": PROVENANCE_GENERATOR,
                "generator_version": PROVENANCE_GENERATOR_VERSION,
                "git": runtime.get("git"),
                "input_snapshots": input_snapshots,
                "run_id": bundle.run_id,
                "runtime": runtime,
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "source_benchmark_path": _display_path(repo_root, live_benchmark_path),
                "source_case_path": _display_path(repo_root, live_config.path),
                "used_snapshot": True,
            },
        )
    return load_bundle_inputs(repo_root, bundle, live_config)


def load_bundle_inputs(repo_root: Path, bundle: ResultBundle, live_config: CaseConfig) -> BundleInputs:
    stored_provenance = _load_provenance(bundle.root / PROVENANCE_NAME)
    case_snapshot_path = bundle.root / CASE_SNAPSHOT_NAME
    benchmark_snapshot_path = bundle.root / BENCHMARK_SNAPSHOT_NAME

    if case_snapshot_path.exists():
        config = load_case_config(case_snapshot_path)
        case_source = SNAPSHOT_SOURCE
    else:
        config = live_config
        case_source = FALLBACK_SOURCE

    benchmark_source = NOT_CONFIGURED_SOURCE
    benchmark: dict[str, Any] = {}
    benchmark_origin = stored_provenance.get("source_benchmark_path")
    if benchmark_snapshot_path.exists():
        benchmark = load_yaml(benchmark_snapshot_path)
        benchmark_source = SNAPSHOT_SOURCE
    else:
        live_benchmark_path = resolve_benchmark_path(repo_root, config.data)
        if live_benchmark_path and live_benchmark_path.exists():
            benchmark = load_yaml(live_benchmark_path)
            benchmark_source = FALLBACK_SOURCE
            benchmark_origin = _display_path(repo_root, live_benchmark_path)

    input_snapshots = stored_provenance.get("input_snapshots")
    if not isinstance(input_snapshots, dict):
        input_snapshots = _input_snapshot_records(repo_root, case_snapshot_path, benchmark_snapshot_path)

    return BundleInputs(
        config=config,
        benchmark=benchmark,
        provenance={
            "bundle_created_utc": stored_provenance.get("created_utc"),
            "case": {
                "origin_path": stored_provenance.get("source_case_path") or _display_path(repo_root, live_config.path),
                "snapshot_path": CASE_SNAPSHOT_NAME if case_snapshot_path.exists() else None,
                "source": case_source,
            },
            "case_name": config.name,
            "dependency_hash": stored_provenance.get("dependency_hash"),
            "dependency_summary": stored_provenance.get("dependency_summary", {}),
            "generator": stored_provenance.get("generator", PROVENANCE_GENERATOR),
            "generator_version": stored_provenance.get("generator_version", PROVENANCE_GENERATOR_VERSION),
            "git": stored_provenance.get("git", {}),
            "input_snapshots": input_snapshots,
            "run_id": bundle.run_id,
            "runtime": stored_provenance.get("runtime", {}),
            "schema_version": stored_provenance.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
            "source_benchmark_path": benchmark_origin,
            "source_case_path": stored_provenance.get("source_case_path") or _display_path(repo_root, live_config.path),
            "used_snapshot": case_source == SNAPSHOT_SOURCE or benchmark_source == SNAPSHOT_SOURCE,
            "benchmark": {
                "origin_path": benchmark_origin,
                "snapshot_path": BENCHMARK_SNAPSHOT_NAME if benchmark_snapshot_path.exists() else None,
                "source": benchmark_source,
            },
        },
    )


def _load_provenance(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _display_path(repo_root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved)


def _input_snapshot_records(repo_root: Path, case_snapshot_path: Path, benchmark_snapshot_path: Path) -> dict[str, Any]:
    snapshots: dict[str, Any] = {
        "case": _snapshot_record(repo_root, case_snapshot_path),
    }
    if benchmark_snapshot_path.exists():
        snapshots["benchmark"] = _snapshot_record(repo_root, benchmark_snapshot_path)
    else:
        snapshots["benchmark"] = None
    return snapshots


def _snapshot_record(repo_root: Path, path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    return {
        "path": _display_path(repo_root, path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
