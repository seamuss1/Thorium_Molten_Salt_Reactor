"""Regressions for the accelerated transient-sweep path.

Every test here pins a defect found by auditing a real end-to-end GPU run of
``flagship_grid_msr`` on an Intel Arc Pro B70. The XPU-specific ones skip when
no device is present; everything else runs anywhere.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import build_minimal_summary

from thorium_reactor import accelerators
from thorium_reactor.accelerators import (
    BackendUnavailable,
    create_array_backend,
    estimate_state_bytes,
    resolve_runtime_backend,
)
from thorium_reactor.config import load_case_config
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.precursors import (
    LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL,
    TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
)
from thorium_reactor.sidecar_schemas import SidecarValidationError, validate_transient_sweep
from thorium_reactor.transient_sweep import (
    NumericalHealthError,
    _build_perturbations,
    _resolve_requested_backend,
    build_transient_sweep_payload,
    run_transient_sweep_case,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
IMMERSED_POOL = REPO_ROOT / "configs" / "cases" / "immersed_pool_reference" / "case.yaml"


def _xpu_available() -> bool:
    try:
        create_array_backend("torch-xpu", dtype="float32", seed=1)
    except Exception:
        return False
    return True


requires_xpu = pytest.mark.skipif(not _xpu_available(), reason="no Intel XPU device available")
hardware = pytest.mark.hardware


def _sweep(backend: str, *, transport_model: str | None = None, dtype: str = "float64", samples: int = 64) -> dict:
    config = load_case_config(IMMERSED_POOL)
    if transport_model is not None:
        config.data["transient"]["precursor_transport_model"] = transport_model
    return build_transient_sweep_payload(
        config,
        build_minimal_summary(),
        scenario_name="partial_heat_sink_loss",
        samples=samples,
        seed=13,
        backend=backend,
        dtype=dtype,
    )


# --------------------------------------------------------------------------
# torch import ordering
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_torch_cpu_backend_does_not_abort_the_interpreter() -> None:
    """``--backend torch-cpu`` used to kill the process outright.

    The OpenMP guard was installed only on the XPU branch, after torch may
    already have loaded, so the CPU branch tripped ``OMP: Error #15`` and exited
    3 -- an abort no ``except`` clause can catch.
    """
    pytest.importorskip("torch")
    script = (
        "from thorium_reactor.accelerators import create_array_backend\n"
        "print(create_array_backend('torch-cpu', dtype='float32', seed=1).describe()['name'])\n"
    )
    environment = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment, cwd=str(REPO_ROOT)
    )

    assert result.returncode == 0, f"torch-cpu aborted: {result.stdout}{result.stderr}"
    assert "torch-cpu" in result.stdout


def test_configure_torch_environment_sets_the_openmp_guard() -> None:
    accelerators.configure_torch_environment()

    assert __import__("os").environ["KMP_DUPLICATE_LIB_OK"] == "TRUE"


# --------------------------------------------------------------------------
# backend selection contract
# --------------------------------------------------------------------------


def test_prefer_gpu_alone_still_means_auto() -> None:
    assert _resolve_requested_backend("auto", prefer_gpu=True) == "auto"
    assert _resolve_requested_backend("auto", prefer_gpu=False) == "auto"


def test_prefer_gpu_conflicting_with_an_explicit_backend_is_rejected() -> None:
    """The alias used to be an unconditional no-op, so this combination lied."""
    with pytest.raises(BackendUnavailable, match="deprecated"):
        _resolve_requested_backend("numpy", prefer_gpu=True)


def test_explicit_backend_is_honoured_verbatim() -> None:
    assert _resolve_requested_backend("torch-cpu", prefer_gpu=False) == "torch-cpu"


def test_unknown_backend_and_dtype_are_rejected() -> None:
    with pytest.raises(BackendUnavailable):
        resolve_runtime_backend("cuda", samples=64)
    with pytest.raises(BackendUnavailable):
        resolve_runtime_backend("numpy", samples=64, dtype="float8")


def test_python_backend_selection_reports_float64() -> None:
    """The reference integrator is always float64 regardless of --dtype."""
    selection = resolve_runtime_backend("python", samples=64, dtype="float32")

    assert selection.dtype == "float64"


def test_undersized_ensembles_are_flagged_in_the_selection_reason() -> None:
    selection = resolve_runtime_backend("auto", samples=1024)

    if selection.selected == "torch-xpu":
        assert "floor" in selection.reason


# --------------------------------------------------------------------------
# physics: the transport model must reach every backend
# --------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["numpy", "torch-cpu"])
def test_precursor_transport_model_changes_results_on_vector_backends(backend: str) -> None:
    """The vectorized integrator used to hard-code the loop-segment model.

    Selecting ``two_region`` silently changed nothing on numpy/torch, which are
    the only backends ``auto`` ever picks.
    """
    pytest.importorskip("torch") if backend.startswith("torch") else None
    two_region = _sweep(backend, transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL)
    loop_segment = _sweep(backend, transport_model=LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL)

    assert (
        two_region["metrics"]["final_total_reactivity_pcm_p50"]
        != loop_segment["metrics"]["final_total_reactivity_pcm_p50"]
    )


@pytest.mark.parametrize(
    "transport_model",
    [TWO_REGION_PRECURSOR_TRANSPORT_MODEL, LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL],
)
def test_numpy_matches_the_python_reference_for_every_transport_model(transport_model: str) -> None:
    reference = _sweep("python", transport_model=transport_model)
    vector = _sweep("numpy", transport_model=transport_model)

    for key in (
        "final_core_delayed_neutron_source_fraction_p50",
        "minimum_core_delayed_neutron_source_fraction_p05",
        "peak_power_fraction_p95",
        "peak_fuel_temperature_c_p95",
    ):
        assert vector["metrics"][key] == pytest.approx(reference["metrics"][key], abs=5.0e-4)
    assert vector["metrics"]["final_total_reactivity_pcm_p50"] == pytest.approx(
        reference["metrics"]["final_total_reactivity_pcm_p50"], abs=5.0e-3
    )


def test_baseline_precursor_annotations_agree_between_backends() -> None:
    """These summaries feed the report, so both paths must mean the same thing."""
    reference = _sweep("python")
    vector = _sweep("numpy")

    for key in (
        "initial_core_precursor_fraction",
        "initial_core_delayed_neutron_source_absolute_fraction",
        "initial_precursor_transport_loss_fraction",
    ):
        assert vector["baseline"][key] == pytest.approx(reference["baseline"][key], abs=5.0e-3)


# --------------------------------------------------------------------------
# ensemble identity
# --------------------------------------------------------------------------


def test_streaming_perturbations_match_the_reference_stream() -> None:
    """The vector path streams parameters to avoid holding 11 Python lists.

    It must still consume the RNG in the same order, or CPU and GPU runs stop
    being the same ensemble.
    """
    from thorium_reactor.transient_sweep import _iter_perturbation_arrays, _resolve_uncertainty_model

    model = _resolve_uncertainty_model({})
    expected = _build_perturbations(128, 7, model)
    streamed = dict(_iter_perturbation_arrays(128, 7, model))

    assert list(streamed) == list(expected)
    for key, values in expected.items():
        assert streamed[key] == values


# --------------------------------------------------------------------------
# failing numerics must not be publishable
# --------------------------------------------------------------------------


def test_failed_numerical_checks_raise_instead_of_being_written(monkeypatch) -> None:
    """A "failed" status used to be recorded next to the metrics, exit code 0."""
    import thorium_reactor.transient_sweep as module

    original = module._integrate_transient_ensemble

    def failing(**kwargs):
        history, metrics, label, report, performance, checks = original(**kwargs)
        checks = {**checks, "status": "failed", "failures": ["power_fraction_bounded_over_trajectory"]}
        return history, metrics, label, report, performance, checks

    monkeypatch.setattr(module, "_integrate_transient_ensemble", failing)

    with pytest.raises(NumericalHealthError, match="power_fraction_bounded_over_trajectory"):
        _sweep("numpy")


def test_numerical_checks_screen_the_whole_trajectory() -> None:
    payload = _sweep("numpy")
    checks = payload["numerical_checks"]["checks"]

    assert checks["history_finite_at_every_step"] is True
    assert checks["power_fraction_bounded_over_trajectory"] is True
    assert payload["numerical_checks"]["trajectory"]["steps_observed"] == payload["metrics"]["history_points"]


# --------------------------------------------------------------------------
# bundle contract
# --------------------------------------------------------------------------


def test_history_path_is_recorded_relative_to_the_repository(tmp_path: Path) -> None:
    config = load_case_config(IMMERSED_POOL)
    bundle = create_result_bundle(tmp_path, config.name, "run")
    summary = build_minimal_summary()

    run_transient_sweep_case(
        config, bundle, summary, scenario_name="partial_heat_sink_loss", samples=32, backend="numpy"
    )

    history_path = summary["transient_sweep"]["history_path"]
    assert not Path(history_path).is_absolute()
    assert history_path.endswith("transient_sweep.json")
    assert str(tmp_path) not in history_path


def test_summary_records_the_device_and_precision() -> None:
    payload = _sweep("numpy", dtype="float32")
    from thorium_reactor.transient_sweep import transient_sweep_summary

    summary = transient_sweep_summary(payload, history_path="results/x/y/transient_sweep.json")

    assert summary["backend"] == "numpy"
    assert summary["requested_backend"] == "numpy"
    assert summary["device"] == "cpu"
    assert summary["dtype"] == "float32"


def test_transient_sweep_sidecar_accepts_a_real_payload() -> None:
    payload = _sweep("numpy")

    assert validate_transient_sweep(payload) is payload


def test_transient_sweep_sidecar_rejects_a_backend_that_disagrees_with_its_report() -> None:
    payload = copy.deepcopy(_sweep("numpy"))
    payload["backend"] = "torch-xpu"

    with pytest.raises(SidecarValidationError, match="disagrees"):
        validate_transient_sweep(payload)


def test_transient_sweep_sidecar_rejects_published_failed_checks() -> None:
    payload = copy.deepcopy(_sweep("numpy"))
    payload["numerical_checks"]["status"] = "failed"

    with pytest.raises(SidecarValidationError, match="numerical checks"):
        validate_transient_sweep(payload)


# --------------------------------------------------------------------------
# memory budget
# --------------------------------------------------------------------------


def test_state_estimate_scales_with_samples_and_precision() -> None:
    small = estimate_state_bytes(samples=1000, dtype="float32", groups=6, loop_segments=4)
    large = estimate_state_bytes(samples=2000, dtype="float32", groups=6, loop_segments=4)
    wide = estimate_state_bytes(samples=1000, dtype="float64", groups=6, loop_segments=4)

    assert large == 2 * small
    assert wide == 2 * small


@requires_xpu
@hardware
def test_oversized_ensemble_is_refused_before_integrating() -> None:
    from thorium_reactor.accelerators import check_memory_budget

    backend = create_array_backend("torch-xpu", dtype="float32", seed=1)
    with pytest.raises(BackendUnavailable, match="device memory"):
        check_memory_budget(backend, samples=2_000_000_000, dtype="float32", groups=6, loop_segments=4)


# --------------------------------------------------------------------------
# GPU parity, when a device is present
# --------------------------------------------------------------------------


@requires_xpu
@hardware
def test_xpu_matches_numpy_on_the_same_ensemble() -> None:
    cpu = _sweep("numpy", dtype="float32", samples=256)
    gpu = _sweep("torch-xpu", dtype="float32", samples=256)

    assert gpu["backend"] == "torch-xpu"
    for key in ("peak_power_fraction_p95", "peak_fuel_temperature_c_p95", "final_power_fraction_p50"):
        assert gpu["metrics"][key] == pytest.approx(cpu["metrics"][key], rel=1.0e-5)


@requires_xpu
@hardware
def test_xpu_run_records_its_device_and_peak_memory() -> None:
    payload = _sweep("torch-xpu", dtype="float32", samples=256)

    assert payload["backend_report"]["details"]["device"]
    assert payload["backend_report"]["available"] is True
    assert payload["runtime_performance"]["backend_peak_memory_allocated_bytes"] is not None
    assert payload["backend_report"]["memory_budget"]["status"] == "ok"


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def test_report_lists_every_varied_parameter(tmp_path: Path) -> None:
    """The report used to truncate to the first eight of eleven parameters."""
    from thorium_reactor.reporting.reports import generate_report

    config = load_case_config(REPO_ROOT / "configs" / "cases" / "flagship_grid_msr" / "case.yaml")
    payload = build_transient_sweep_payload(
        config,
        build_minimal_summary(),
        scenario_name="flagship_load_follow_recovery",
        samples=32,
        seed=1,
        backend="numpy",
    )
    varied = payload["ensemble_definition"]["varied_parameters"]
    assert len(varied) > 8, "flagship ensemble should vary more than the old cap"

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"case": config.name, "transient_sweep": {**payload, "status": "completed"}}), encoding="utf-8"
    )
    report = generate_report(config.name, config.data, summary_path, None, None, None, {})

    assert f"Varied parameter count: `{len(varied)}`" in report
    for parameter in varied:
        assert f"Varied `{parameter['parameter']}`" in report


# --------------------------------------------------------------------------
# Review findings on PR #92 (codex gpt-5.6-sol)
# --------------------------------------------------------------------------


def test_trajectory_extrema_are_exact_not_percentile_bands() -> None:
    """An excursion in <5% of samples must not hide behind the p05 band.

    The first cut fed percentile bands into the health screen, so a negative
    or non-finite excursion confined to a minority of samples could recover
    before the final step and pass.
    """
    reference = _sweep("python")
    vector = _sweep("numpy")

    for key in ("power_fraction_min", "power_fraction_max", "temperature_min_c", "temperature_max_c"):
        assert vector["numerical_checks"]["trajectory"][key] == pytest.approx(
            reference["numerical_checks"]["trajectory"][key], rel=1e-6
        )
    # The band is strictly inside the true extrema, which is what makes bands
    # unsafe for this check -- assert we are not reporting the band.
    trajectory = vector["numerical_checks"]["trajectory"]
    worst_band_low = min(row["power_fraction_p05"] for row in vector["history"])
    assert trajectory["power_fraction_min"] <= worst_band_low


def test_trajectory_extrema_cover_graphite_and_coolant() -> None:
    """Only fuel temperature was tracked; a graphite/coolant excursion escaped."""
    payload = _sweep("numpy")
    trajectory = payload["numerical_checks"]["trajectory"]
    coolest_fuel_band = min(row["fuel_temp_c_p05"] for row in payload["history"])

    # Graphite and coolant start below the fuel hot leg, so covering them must
    # pull the tracked minimum below anything the fuel-only band could report.
    assert trajectory["temperature_min_c"] < coolest_fuel_band


def test_failed_stage_is_not_reported_as_a_completed_run(tmp_path: Path) -> None:
    """A CLI stage that raised leaves an earlier summary.json behind.

    That used to read as a clean success in the web UI, because status was
    inferred purely from which artifacts existed.
    """
    from thorium_reactor.web.repository import infer_status_from_files

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")
    assert infer_status_from_files(run_dir) == "completed"

    (run_dir / "stage_manifest.json").write_text(
        json.dumps(
            {"stages": [{"stage": "run", "status": "completed"}, {"stage": "transient-sweep", "status": "failed"}]}
        ),
        encoding="utf-8",
    )
    assert infer_status_from_files(run_dir) == "failed"


def test_corrupt_status_file_does_not_pin_a_run_live_forever(tmp_path: Path) -> None:
    """Treating any unparseable status as "running" left the SSE stream open.

    A torn read during an atomic write is transient and the retry absorbs it; a
    genuinely corrupt file is permanent, so it must fall through to inference
    rather than reporting the run live forever.
    """
    from thorium_reactor.web.repository import read_status_payload

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "job_status.json").write_text("{ this is not json", encoding="utf-8")

    assert read_status_payload(run_dir) == {}

    (run_dir / "job_status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    assert read_status_payload(run_dir)["status"] == "running"


def test_missing_status_file_reads_as_empty(tmp_path: Path) -> None:
    from thorium_reactor.web.repository import read_status_payload

    assert read_status_payload(tmp_path) == {}


@pytest.mark.hardware
@requires_xpu
def test_clamp_shortcut_preserves_nan_semantics() -> None:
    """clamp(x, min=nan) leaves x alone; maximum(x, nan) propagates nan.

    The scalar fast path must not silently swallow a corrupt threshold.
    """
    import torch

    backend = create_array_backend("torch-xpu", dtype="float32", seed=1)
    values = backend.asarray([1.0, 2.0, 3.0])

    assert torch.isnan(backend.maximum(values, float("nan"))).all()
    assert torch.isnan(backend.minimum(values, float("nan"))).all()
    # A finite bound still takes the cheap path and behaves like maximum.
    assert backend.to_host_list(backend.maximum(values, 2.0)) == [2.0, 2.0, 3.0]
    assert backend.to_host_list(backend.minimum(values, 2.0)) == [1.0, 2.0, 2.0]


@pytest.mark.hardware
@requires_xpu
def test_half_precision_percentiles_do_not_raise() -> None:
    """float16/bfloat16 are advertised dtypes; torch.quantile rejects them."""
    for dtype in ("float16", "bfloat16"):
        backend = create_array_backend("torch-xpu", dtype=dtype, seed=1)
        values = backend.asarray([float(index) for index in range(256)])

        bands = backend.percentiles(values, (0.05, 0.5, 0.95))

        assert len(bands) == 3
        assert bands[0] <= bands[1] <= bands[2]


def test_memory_budget_uses_the_segments_the_integrator_allocates() -> None:
    """Two-region collapses to one segment, so budgeting the configured count
    would reject an ensemble that actually fits."""
    from thorium_reactor.transient_sweep import _effective_loop_segments

    baseline = {"precursor_loop_segments": [{"id": f"s{index}"} for index in range(4)]}
    two_region = {"precursor_transport_model": TWO_REGION_PRECURSOR_TRANSPORT_MODEL}
    loop_segment = {"precursor_transport_model": LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL}

    assert len(_effective_loop_segments(two_region, baseline)) == 1
    assert len(_effective_loop_segments(loop_segment, baseline)) == 4
