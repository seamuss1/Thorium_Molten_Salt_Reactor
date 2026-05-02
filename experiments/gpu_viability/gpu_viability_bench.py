from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
SRC_ROOT = REPO_ROOT / "src"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".tmp" / "gpu-viability"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


ARC_PRO_B70_TARGET = {
    "name": "Intel Arc Pro B70",
    "vram_gb": 32.0,
    "memory_bandwidth_gb_s": 608.0,
    "fp32_tflops": 22.94,
    "xe_cores": 32,
    "xmx_engines": 256,
}

BACKEND_NAMES = ("torch-xpu", "dpnp", "cupy", "torch-cuda", "numpy", "torch-cpu")


@dataclass(frozen=True)
class PreparedTransient:
    case_name: str
    scenario: dict[str, Any]
    baseline: dict[str, Any]
    model_parameters: dict[str, Any]
    depletion: dict[str, Any]
    chemistry: dict[str, Any]
    uncertainty_model: dict[str, float]
    precursor_groups: list[dict[str, float | str]]
    loop_segments: list[dict[str, float | str]]


class BackendUnavailable(RuntimeError):
    pass


class BaseBackend:
    name: str
    device_label: str
    dtype_name: str

    def asarray(self, value: Any, *, dtype: str | None = None) -> Any:
        raise NotImplementedError

    def full(self, shape: tuple[int, ...], value: float) -> Any:
        raise NotImplementedError

    def zeros(self, shape: tuple[int, ...]) -> Any:
        raise NotImplementedError

    def ones(self, shape: tuple[int, ...]) -> Any:
        return self.full(shape, 1.0)

    def normal(self, *, mean: float, sigma: float, shape: tuple[int, ...], seed_offset: int) -> Any:
        raise NotImplementedError

    def clip(self, value: Any, lower: float, upper: float) -> Any:
        raise NotImplementedError

    def maximum(self, left: Any, right: Any) -> Any:
        raise NotImplementedError

    def minimum(self, left: Any, right: Any) -> Any:
        raise NotImplementedError

    def sum(self, value: Any, axis: int | None = None) -> Any:
        raise NotImplementedError

    def stack(self, values: list[Any], axis: int = 0) -> Any:
        raise NotImplementedError

    def roll(self, value: Any, *, shift: int, axis: int) -> Any:
        raise NotImplementedError

    def percentiles(self, value: Any, quantiles: tuple[float, float, float]) -> list[float]:
        raise NotImplementedError

    def max_scalar(self, value: Any) -> float:
        raise NotImplementedError

    def min_scalar(self, value: Any) -> float:
        raise NotImplementedError

    def scalar(self, value: Any) -> float:
        raise NotImplementedError

    def to_host_list(self, value: Any) -> list[float]:
        raise NotImplementedError

    def synchronize(self) -> None:
        return None

    def memory_allocated_bytes(self) -> int | None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device_label,
            "dtype": self.dtype_name,
        }


class NumpyLikeBackend(BaseBackend):
    def __init__(self, module_name: str, *, dtype_name: str, seed: int, device_label: str | None = None) -> None:
        self.xp = importlib.import_module(module_name)
        self.module_name = module_name
        self.name = module_name
        self.device_label = device_label or module_name
        self.dtype_name = dtype_name
        self.seed = seed
        self.dtype = getattr(self.xp, dtype_name)

        if module_name == "cupy":
            device_count = int(self.xp.cuda.runtime.getDeviceCount())
            if device_count <= 0:
                raise BackendUnavailable("CuPy imported, but no CUDA device is visible.")
            self.name = "cupy"
            self.device_label = str(self.xp.cuda.runtime.getDeviceProperties(0).get("name", b"cuda")).strip("b'")
        elif module_name == "dpnp":
            self.name = "dpnp"
            try:
                import dpctl  # type: ignore

                queue = dpctl.SyclQueue("gpu")
                self.device_label = queue.sycl_device.name
            except Exception:
                self.device_label = "oneapi-sycl"
        elif module_name == "numpy":
            self.name = "numpy"
            self.device_label = "cpu"

    def asarray(self, value: Any, *, dtype: str | None = None) -> Any:
        resolved_dtype = getattr(self.xp, dtype or self.dtype_name)
        return self.xp.asarray(value, dtype=resolved_dtype)

    def full(self, shape: tuple[int, ...], value: float) -> Any:
        return self.xp.full(shape, value, dtype=self.dtype)

    def zeros(self, shape: tuple[int, ...]) -> Any:
        return self.xp.zeros(shape, dtype=self.dtype)

    def normal(self, *, mean: float, sigma: float, shape: tuple[int, ...], seed_offset: int) -> Any:
        if self.module_name == "numpy":
            rng = self.xp.random.default_rng(self.seed + seed_offset)
            return self.xp.asarray(rng.normal(mean, sigma, size=shape), dtype=self.dtype)
        try:
            self.xp.random.seed(self.seed + seed_offset)
        except Exception:
            pass
        return self.xp.asarray(self.xp.random.normal(mean, sigma, size=shape), dtype=self.dtype)

    def clip(self, value: Any, lower: float, upper: float) -> Any:
        return self.xp.clip(value, lower, upper)

    def maximum(self, left: Any, right: Any) -> Any:
        return self.xp.maximum(left, right)

    def minimum(self, left: Any, right: Any) -> Any:
        return self.xp.minimum(left, right)

    def sum(self, value: Any, axis: int | None = None) -> Any:
        return self.xp.sum(value, axis=axis)

    def stack(self, values: list[Any], axis: int = 0) -> Any:
        return self.xp.stack(values, axis=axis)

    def roll(self, value: Any, *, shift: int, axis: int) -> Any:
        return self.xp.roll(value, shift=shift, axis=axis)

    def percentiles(self, value: Any, quantiles: tuple[float, float, float]) -> list[float]:
        q = self.xp.asarray([item * 100.0 for item in quantiles], dtype=self.dtype)
        raw = self.xp.percentile(value, q)
        return [float(item) for item in self.to_host_list(raw)]

    def max_scalar(self, value: Any) -> float:
        return self.scalar(self.xp.max(value))

    def min_scalar(self, value: Any) -> float:
        return self.scalar(self.xp.min(value))

    def scalar(self, value: Any) -> float:
        if hasattr(value, "get"):
            value = value.get()
        if hasattr(value, "item"):
            value = value.item()
        return float(value)

    def to_host_list(self, value: Any) -> list[float]:
        if hasattr(value, "get"):
            value = value.get()
        if hasattr(value, "asnumpy"):
            value = value.asnumpy()
        if hasattr(value, "tolist"):
            raw = value.tolist()
        else:
            raw = list(value)
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(item) for item in raw]

    def synchronize(self) -> None:
        if self.module_name == "cupy":
            self.xp.cuda.Device().synchronize()
        elif self.module_name == "dpnp":
            try:
                import dpctl  # type: ignore

                dpctl.SyclQueue("gpu").wait()
            except Exception:
                pass

    def memory_allocated_bytes(self) -> int | None:
        if self.module_name == "cupy":
            pool = self.xp.get_default_memory_pool()
            return int(pool.used_bytes())
        return None


class TorchBackend(BaseBackend):
    def __init__(self, *, device: str, dtype_name: str, seed: int) -> None:
        self.torch = importlib.import_module("torch")
        self.seed = seed
        self.dtype_name = dtype_name
        self.dtype = {
            "float32": self.torch.float32,
            "float64": self.torch.float64,
            "float16": self.torch.float16,
            "bfloat16": self.torch.bfloat16,
        }[dtype_name]

        if device == "xpu":
            try:
                importlib.import_module("intel_extension_for_pytorch")
            except Exception:
                pass
            if not hasattr(self.torch, "xpu") or not self.torch.xpu.is_available():
                raise BackendUnavailable("PyTorch XPU is not available. Install an Intel XPU-capable PyTorch/IPEX build.")
            self.device = self.torch.device("xpu")
            self.name = "torch-xpu"
            try:
                self.device_label = self.torch.xpu.get_device_name(0)
            except Exception:
                self.device_label = "xpu"
        elif device == "cuda":
            if not self.torch.cuda.is_available():
                raise BackendUnavailable("PyTorch CUDA is not available.")
            self.device = self.torch.device("cuda")
            self.name = "torch-cuda"
            self.device_label = self.torch.cuda.get_device_name(0)
        else:
            self.device = self.torch.device("cpu")
            self.name = "torch-cpu"
            self.device_label = "cpu"

        self.torch.manual_seed(seed)

    def asarray(self, value: Any, *, dtype: str | None = None) -> Any:
        resolved_dtype = self.dtype if dtype is None else {
            "float32": self.torch.float32,
            "float64": self.torch.float64,
            "float16": self.torch.float16,
            "bfloat16": self.torch.bfloat16,
        }[dtype]
        return self.torch.tensor(value, dtype=resolved_dtype, device=self.device)

    def full(self, shape: tuple[int, ...], value: float) -> Any:
        return self.torch.full(shape, value, dtype=self.dtype, device=self.device)

    def zeros(self, shape: tuple[int, ...]) -> Any:
        return self.torch.zeros(shape, dtype=self.dtype, device=self.device)

    def normal(self, *, mean: float, sigma: float, shape: tuple[int, ...], seed_offset: int) -> Any:
        self.torch.manual_seed(self.seed + seed_offset)
        return self.torch.normal(mean=mean, std=sigma, size=shape, dtype=self.dtype, device=self.device)

    def clip(self, value: Any, lower: float, upper: float) -> Any:
        return self.torch.clamp(value, min=lower, max=upper)

    def maximum(self, left: Any, right: Any) -> Any:
        if not self.torch.is_tensor(right):
            right = self.torch.tensor(right, dtype=self.dtype, device=self.device)
        return self.torch.maximum(left, right)

    def minimum(self, left: Any, right: Any) -> Any:
        if not self.torch.is_tensor(right):
            right = self.torch.tensor(right, dtype=self.dtype, device=self.device)
        return self.torch.minimum(left, right)

    def sum(self, value: Any, axis: int | None = None) -> Any:
        return self.torch.sum(value) if axis is None else self.torch.sum(value, dim=axis)

    def stack(self, values: list[Any], axis: int = 0) -> Any:
        return self.torch.stack(values, dim=axis)

    def roll(self, value: Any, *, shift: int, axis: int) -> Any:
        return self.torch.roll(value, shifts=shift, dims=axis)

    def percentiles(self, value: Any, quantiles: tuple[float, float, float]) -> list[float]:
        flat = value.flatten()
        try:
            q = self.torch.tensor(quantiles, dtype=self.dtype, device=self.device)
            raw = self.torch.quantile(flat, q)
            return self.to_host_list(raw)
        except Exception:
            ordered = self.torch.sort(flat).values
            count = int(ordered.numel())
            resolved: list[float] = []
            for quantile in quantiles:
                position = quantile * max(count - 1, 0)
                lower_index = int(math.floor(position))
                upper_index = int(math.ceil(position))
                if lower_index == upper_index:
                    resolved.append(self.scalar(ordered[lower_index]))
                else:
                    fraction = position - lower_index
                    lower = self.scalar(ordered[lower_index])
                    upper = self.scalar(ordered[upper_index])
                    resolved.append(lower + (upper - lower) * fraction)
            return resolved

    def max_scalar(self, value: Any) -> float:
        return self.scalar(self.torch.max(value))

    def min_scalar(self, value: Any) -> float:
        return self.scalar(self.torch.min(value))

    def scalar(self, value: Any) -> float:
        if self.torch.is_tensor(value):
            return float(value.detach().cpu().item())
        return float(value)

    def to_host_list(self, value: Any) -> list[float]:
        if self.torch.is_tensor(value):
            raw = value.detach().cpu().flatten().tolist()
        elif isinstance(value, list):
            raw = value
        else:
            raw = [value]
        return [float(item) for item in raw]

    def synchronize(self) -> None:
        if self.device.type == "xpu":
            self.torch.xpu.synchronize()
        elif self.device.type == "cuda":
            self.torch.cuda.synchronize()

    def memory_allocated_bytes(self) -> int | None:
        if self.device.type == "xpu":
            try:
                return int(self.torch.xpu.memory_allocated())
            except Exception:
                return None
        if self.device.type == "cuda":
            return int(self.torch.cuda.memory_allocated())
        return None


def create_backend(name: str, *, dtype: str, seed: int) -> BaseBackend:
    if name == "numpy":
        return NumpyLikeBackend("numpy", dtype_name=dtype, seed=seed)
    if name == "cupy":
        return NumpyLikeBackend("cupy", dtype_name=dtype, seed=seed)
    if name == "dpnp":
        return NumpyLikeBackend("dpnp", dtype_name=dtype, seed=seed)
    if name == "torch-xpu":
        return TorchBackend(device="xpu", dtype_name=dtype, seed=seed)
    if name == "torch-cuda":
        return TorchBackend(device="cuda", dtype_name=dtype, seed=seed)
    if name == "torch-cpu":
        return TorchBackend(device="cpu", dtype_name=dtype, seed=seed)
    raise BackendUnavailable(f"Unsupported backend: {name}")


def resolve_auto_backend(*, dtype: str, seed: int) -> BaseBackend:
    for name in ("torch-xpu", "dpnp", "cupy", "numpy"):
        try:
            return create_backend(name, dtype=dtype, seed=seed)
        except Exception:
            continue
    raise BackendUnavailable("No supported array backend is available.")


def load_prepared_transient(case_name: str, scenario_name: str | None, output_root: Path) -> PreparedTransient:
    from thorium_reactor.chemistry import build_chemistry_assumptions
    from thorium_reactor.config import load_case_config
    from thorium_reactor.literature_models import build_property_uncertainty_summary
    from thorium_reactor.neutronics.workflows import run_case
    from thorium_reactor.paths import create_result_bundle
    from thorium_reactor.precursors import normalize_loop_segments
    from thorium_reactor.transient import (
        _build_transient_baseline,
        _resolve_model_parameters,
        _resolve_scenario,
        build_depletion_assumptions,
    )
    from thorium_reactor.transient_sweep import _resolve_uncertainty_model

    case_path = REPO_ROOT / "configs" / "cases" / case_name / "case.yaml"
    config = load_case_config(case_path)
    bundle = create_result_bundle(output_root / "production-inputs", config.name, "gpu-viability-baseline")
    summary = run_case(config, bundle, solver_enabled=False)

    transient_config = config.data.get("transient", {})
    if not isinstance(transient_config, dict):
        transient_config = {}
    scenario = _resolve_scenario(transient_config, scenario_name)
    baseline = _build_transient_baseline(config, summary)
    model_parameters = _resolve_model_parameters(transient_config)
    depletion = build_depletion_assumptions(config)
    chemistry = build_chemistry_assumptions(config)
    property_uncertainty = build_property_uncertainty_summary(
        config,
        primary_delta_t_c=float(baseline.get("steady_state_delta_t_c", 0.0)),
    )
    uncertainty_model = _resolve_uncertainty_model(
        transient_config,
        property_uncertainty=property_uncertainty,
    )
    return PreparedTransient(
        case_name=config.name,
        scenario=scenario,
        baseline=baseline,
        model_parameters=model_parameters,
        depletion=depletion,
        chemistry=chemistry,
        uncertainty_model=uncertainty_model,
        precursor_groups=list(model_parameters["delayed_neutron_precursor_groups"]),
        loop_segments=normalize_loop_segments(baseline.get("precursor_loop_segments")),
    )


def build_perturbations(
    backend: BaseBackend,
    *,
    samples: int,
    seed: int,
    uncertainty_model: dict[str, float],
    rng_mode: str,
) -> dict[str, Any]:
    if rng_mode == "production":
        from thorium_reactor.transient_sweep import _build_perturbations

        raw = _build_perturbations(samples, seed, uncertainty_model)
        return {key: backend.asarray(value) for key, value in raw.items()}

    def bounded(key: str, mean: float, lower: float, upper: float, offset: int) -> Any:
        return backend.clip(
            backend.normal(
                mean=mean,
                sigma=float(uncertainty_model[key]),
                shape=(samples,),
                seed_offset=offset,
            ),
            lower,
            upper,
        )

    return {
        "event_reactivity_scale": bounded("event_reactivity_sigma_fraction", 1.0, 0.55, 1.55, 101),
        "flow_scale": bounded("flow_sigma_fraction", 1.0, 0.65, 1.45, 102),
        "heat_sink_scale": bounded("heat_sink_sigma_fraction", 1.0, 0.6, 1.45, 103),
        "cleanup_scale": bounded("cleanup_sigma_fraction", 1.0, 0.6, 1.6, 104),
        "temperature_feedback_scale": bounded("temperature_feedback_sigma_fraction", 1.0, 0.8, 1.2, 105),
        "precursor_worth_scale": bounded("precursor_worth_sigma_fraction", 1.0, 0.75, 1.25, 106),
        "xenon_worth_scale": bounded("xenon_worth_sigma_fraction", 1.0, 0.7, 1.3, 107),
        "sink_temp_bias_c": backend.normal(
            mean=0.0,
            sigma=float(uncertainty_model["sink_offset_sigma_c"]),
            shape=(samples,),
            seed_offset=108,
        ),
        "redox_bias_ev": backend.normal(
            mean=0.0,
            sigma=float(uncertainty_model["redox_setpoint_sigma_ev"]),
            shape=(samples,),
            seed_offset=109,
        ),
        "impurity_ingress_scale": bounded("impurity_ingress_sigma_fraction", 1.0, 0.5, 1.8, 110),
        "gas_stripping_scale": bounded("gas_stripping_sigma_fraction", 1.0, 0.85, 1.15, 111),
    }


def estimate_state_bytes(samples: int, groups: int, segments: int, dtype: str) -> int:
    dtype_bytes = {
        "float16": 2,
        "bfloat16": 2,
        "float32": 4,
        "float64": 8,
    }[dtype]
    persistent_scalars = 22
    precursor_slots = groups + groups * segments + groups + groups * segments
    scratch_multiplier = 2.5
    return int(samples * dtype_bytes * (persistent_scalars + precursor_slots) * scratch_multiplier)


def dtype_size_bytes(dtype: str) -> int:
    return {
        "float16": 2,
        "bfloat16": 2,
        "float32": 4,
        "float64": 8,
    }[dtype]


def choose_chunks(samples: int, groups: int, segments: int, dtype: str, target_vram_gb: float) -> list[tuple[int, int]]:
    estimated = estimate_state_bytes(samples, groups, segments, dtype)
    target_bytes = int(target_vram_gb * (1024**3))
    if estimated <= target_bytes:
        return [(0, samples)]
    chunk_count = max(2, math.ceil(estimated / max(target_bytes, 1)))
    chunk_size = math.ceil(samples / chunk_count)
    chunks = []
    start = 0
    while start < samples:
        stop = min(samples, start + chunk_size)
        chunks.append((start, stop))
        start = stop
    return chunks


def first_order_step(current: Any, target: Any, dt: float, tau_s: float) -> Any:
    tau = max(float(tau_s), dt)
    return current + (target - current) * (dt / tau)


def core_delayed_source(backend: BaseBackend, core_inventory: Any, decay: Any) -> Any:
    return backend.sum(core_inventory * decay, axis=1)


def initialize_precursors(
    backend: BaseBackend,
    *,
    samples: int,
    groups: list[dict[str, float | str]],
    loop_segments: list[dict[str, float | str]],
    flow_scale: Any,
    cleanup_scale: Any,
    baseline: dict[str, Any],
    depletion: dict[str, Any],
) -> tuple[Any, Any, Any]:
    group_count = len(groups)
    segment_count = len(loop_segments)
    core_inventory_by_group = []
    segment_inventory_by_group = []
    flow_fraction = backend.clip(flow_scale, 0.05, 1.5)
    cleanup_multiplier = backend.clip(cleanup_scale, 0.0, 2.5)
    cleanup_rate_s = (
        float(baseline["cleanup_removal_efficiency"])
        * cleanup_multiplier
        / max(float(baseline["cleanup_turnover_s"]), 1.0)
        + float(depletion["volatile_removal_efficiency"])
        / max(float(baseline["cleanup_turnover_s"]) * 6.0, 1.0)
    )
    core_transport_rate_s = flow_fraction / max(float(baseline["core_residence_time_s"]), 1.0e-12)

    residence = [float(segment["residence_fraction"]) for segment in loop_segments]
    cleanup_weights = [float(segment["cleanup_weight"]) for segment in loop_segments]

    for group in groups:
        decay = max(float(group["decay_constant_s"]), 1.0e-12)
        source_rate = float(group["relative_yield_fraction"])
        ratios = []
        previous_ratio = core_transport_rate_s
        segment_rates = []
        for index in range(segment_count):
            rate = flow_fraction / max(residence[index] * float(baseline["loop_residence_time_s"]), 1.0e-12)
            diagonal = rate + decay + cleanup_rate_s * cleanup_weights[index]
            ratio = previous_ratio / backend.maximum(diagonal, 1.0e-18)
            ratios.append(ratio)
            segment_rates.append(rate)
            previous_ratio = rate * ratio

        loop_return_term = segment_rates[-1] * ratios[-1]
        core_diagonal = core_transport_rate_s + decay
        core_inventory = backend.maximum(source_rate / backend.maximum(core_diagonal - loop_return_term, 1.0e-18), 0.0)
        segment_inventory = backend.stack([backend.maximum(core_inventory * ratio, 0.0) for ratio in ratios], axis=1)
        core_inventory_by_group.append(core_inventory)
        segment_inventory_by_group.append(segment_inventory)

    core_inventory_matrix = backend.stack(core_inventory_by_group, axis=1)
    segment_inventory_tensor = backend.stack(segment_inventory_by_group, axis=1)
    decay_vector = backend.asarray([float(group["decay_constant_s"]) for group in groups])
    steady_core_source = core_delayed_source(backend, core_inventory_matrix, decay_vector)
    return core_inventory_matrix, segment_inventory_tensor, steady_core_source


def step_precursors(
    backend: BaseBackend,
    *,
    core_inventory: Any,
    segment_inventory: Any,
    groups: list[dict[str, float | str]],
    loop_segments: list[dict[str, float | str]],
    power_fraction: Any,
    flow_fraction: Any,
    cleanup_rate_s: Any,
    dt: float,
    baseline: dict[str, Any],
) -> tuple[Any, Any]:
    group_next_core = []
    group_next_segments = []
    residence = [float(segment["residence_fraction"]) for segment in loop_segments]
    cleanup_weights = [float(segment["cleanup_weight"]) for segment in loop_segments]
    segment_count = len(loop_segments)
    core_transport_rate_s = flow_fraction / max(float(baseline["core_residence_time_s"]), 1.0e-12)

    for group_index, group in enumerate(groups):
        decay = max(float(group["decay_constant_s"]), 1.0e-12)
        source_rate = float(group["relative_yield_fraction"]) * backend.maximum(power_fraction, 0.0)
        affine_constants = []
        affine_slopes = []
        segment_rates = []
        prior_constant = backend.maximum(segment_inventory[:, group_index, 0], 0.0)
        prior_slope = dt * core_transport_rate_s
        for segment_index in range(segment_count):
            segment_rate = flow_fraction / max(
                residence[segment_index] * float(baseline["loop_residence_time_s"]),
                1.0e-12,
            )
            segment_rates.append(segment_rate)
            diagonal = 1.0 + dt * (
                segment_rate
                + decay
                + cleanup_rate_s * cleanup_weights[segment_index]
            )
            if segment_index > 0:
                previous_rate = segment_rates[segment_index - 1]
                prior_constant = (
                    backend.maximum(segment_inventory[:, group_index, segment_index], 0.0)
                    + dt * previous_rate * affine_constants[-1]
                )
                prior_slope = dt * previous_rate * affine_slopes[-1]
            affine_constants.append(prior_constant / backend.maximum(diagonal, 1.0e-18))
            affine_slopes.append(prior_slope / backend.maximum(diagonal, 1.0e-18))

        core_diagonal = 1.0 + dt * (core_transport_rate_s + decay)
        rhs_core = backend.maximum(core_inventory[:, group_index], 0.0) + dt * source_rate
        return_rate = dt * segment_rates[-1]
        next_core = (
            rhs_core + return_rate * affine_constants[-1]
        ) / backend.maximum(core_diagonal - return_rate * affine_slopes[-1], 1.0e-18)
        next_core = backend.maximum(next_core, 0.0)
        next_segments = backend.stack(
            [backend.maximum(affine_constants[index] + affine_slopes[index] * next_core, 0.0) for index in range(segment_count)],
            axis=1,
        )
        group_next_core.append(next_core)
        group_next_segments.append(next_segments)

    return backend.stack(group_next_core, axis=1), backend.stack(group_next_segments, axis=1)


def run_vectorized_transient(
    backend: BaseBackend,
    prepared: PreparedTransient,
    *,
    samples: int,
    seed: int,
    rng_mode: str,
    summary_mode: str,
) -> dict[str, Any]:
    baseline = prepared.baseline
    scenario = prepared.scenario
    model_parameters = prepared.model_parameters
    depletion = prepared.depletion
    chemistry = prepared.chemistry
    groups = prepared.precursor_groups
    loop_segments = prepared.loop_segments
    group_count = len(groups)

    perturbations = build_perturbations(
        backend,
        samples=samples,
        seed=seed,
        uncertainty_model=prepared.uncertainty_model,
        rng_mode=rng_mode,
    )

    dt = max(float(scenario["time_step_s"]), 0.05)
    duration_s = max(float(scenario["duration_s"]), dt)
    step_count = int(round(duration_s / dt))
    controls = {
        "reactivity_pcm": 0.0,
        "flow_fraction": 1.0,
        "heat_sink_fraction": 1.0,
        "cleanup_multiplier": 1.0,
        "sink_temp_offset_c": 0.0,
        "redox_setpoint_shift_ev": 0.0,
        "impurity_ingress_multiplier": 1.0,
        "gas_stripping_efficiency": float(chemistry["gas_stripping_efficiency"]),
    }
    event_index = 0

    steady_fuel_temp_c = float(baseline["hot_leg_temp_c"])
    steady_graphite_temp_c = float(baseline["average_primary_temp_c"])
    steady_coolant_temp_c = float(baseline["average_primary_temp_c"])
    chemistry_baseline = baseline.get("chemistry", {})
    steady_redox_state_ev = float(chemistry_baseline.get("redox_state_ev", chemistry["initial_redox_state_ev"]))
    target_redox_state_ev = float(chemistry_baseline.get("target_redox_state_ev", chemistry["target_redox_state_ev"]))

    power_fraction = backend.ones((samples,))
    fuel_temp_c = backend.full((samples,), steady_fuel_temp_c)
    graphite_temp_c = backend.full((samples,), steady_graphite_temp_c)
    coolant_temp_c = backend.full((samples,), steady_coolant_temp_c)
    xenon_fraction = backend.ones((samples,))
    fissile_inventory_fraction = backend.full((samples,), float(depletion["initial_fissile_inventory_fraction"]))
    protactinium_inventory_fraction = backend.zeros((samples,))
    redox_state_ev = backend.full((samples,), steady_redox_state_ev)
    impurity_fraction = backend.full((samples,), float(chemistry_baseline.get("impurity_fraction", 0.0)))
    corrosion_index = backend.full((samples,), float(chemistry_baseline.get("corrosion_index", 1.0)))

    core_inventory, segment_inventory, steady_core_source = initialize_precursors(
        backend,
        samples=samples,
        groups=groups,
        loop_segments=loop_segments,
        flow_scale=perturbations["flow_scale"],
        cleanup_scale=perturbations["cleanup_scale"],
        baseline=baseline,
        depletion=depletion,
    )
    decay_vector = backend.asarray([float(group["decay_constant_s"]) for group in groups])
    core_delayed_neutron_source_fraction = core_delayed_source(backend, core_inventory, decay_vector) / backend.maximum(
        steady_core_source,
        1.0e-12,
    )

    fuel_temp_feedback_pcm_per_c = float(model_parameters["fuel_temperature_feedback_pcm_per_c"]) * perturbations[
        "temperature_feedback_scale"
    ]
    graphite_temp_feedback_pcm_per_c = float(model_parameters["graphite_temperature_feedback_pcm_per_c"]) * perturbations[
        "temperature_feedback_scale"
    ]
    coolant_temp_feedback_pcm_per_c = float(model_parameters["coolant_temperature_feedback_pcm_per_c"]) * perturbations[
        "temperature_feedback_scale"
    ]
    precursor_worth_pcm = float(model_parameters["precursor_worth_pcm"]) * perturbations["precursor_worth_scale"]
    xenon_worth_pcm_per_fraction = float(model_parameters["xenon_worth_pcm_per_fraction"]) * perturbations[
        "xenon_worth_scale"
    ]

    history: list[dict[str, float]] = []
    peak_power_fraction_max = 1.0
    peak_fuel_temperature_c_max = steady_fuel_temp_c
    peak_corrosion_index_max = float(chemistry_baseline.get("corrosion_index", 1.0))
    final_total_reactivity_pcm = backend.zeros((samples,))

    backend.synchronize()
    integrate_start = time.perf_counter()

    for step in range(step_count + 1):
        time_s = step * dt
        dt_days = dt / 86400.0
        while event_index < len(scenario["events"]) and float(scenario["events"][event_index]["time_s"]) <= time_s + 1.0e-9:
            event = scenario["events"][event_index]
            for source_key, target_key in (
                ("reactivity_step_pcm", "reactivity_pcm"),
                ("flow_fraction", "flow_fraction"),
                ("heat_sink_fraction", "heat_sink_fraction"),
                ("cleanup_multiplier", "cleanup_multiplier"),
                ("secondary_sink_temp_offset_c", "sink_temp_offset_c"),
                ("redox_setpoint_shift_ev", "redox_setpoint_shift_ev"),
                ("impurity_ingress_multiplier", "impurity_ingress_multiplier"),
                ("gas_stripping_efficiency", "gas_stripping_efficiency"),
            ):
                if source_key in event:
                    controls[target_key] = float(event[source_key])
            event_index += 1

        effective_flow_fraction = backend.clip(controls["flow_fraction"] * perturbations["flow_scale"], 0.05, 1.5)
        effective_heat_sink_fraction = backend.clip(
            controls["heat_sink_fraction"] * perturbations["heat_sink_scale"],
            0.0,
            1.5,
        )
        cleanup_multiplier = backend.clip(controls["cleanup_multiplier"] * perturbations["cleanup_scale"], 0.0, 2.5)
        cleanup_rate_s = (
            float(baseline["cleanup_removal_efficiency"])
            * cleanup_multiplier
            / max(float(baseline["cleanup_turnover_s"]), 1.0)
            + float(depletion["volatile_removal_efficiency"])
            / max(float(baseline["cleanup_turnover_s"]) * 6.0, 1.0)
        )
        gas_stripping_efficiency = backend.clip(
            controls["gas_stripping_efficiency"] * perturbations["gas_stripping_scale"],
            0.0,
            1.0,
        )

        thermal_load_ratio = power_fraction / backend.maximum(
            effective_flow_fraction * backend.maximum(effective_heat_sink_fraction, 0.15),
            0.05,
        )
        sink_bias = controls["sink_temp_offset_c"] + perturbations["sink_temp_bias_c"]
        fuel_target_c = steady_fuel_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.7 + sink_bias * 0.25
        graphite_target_c = steady_graphite_temp_c + (fuel_temp_c - steady_fuel_temp_c) * 0.7
        coolant_target_c = steady_coolant_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.45 + sink_bias * 0.55

        fuel_temp_c = first_order_step(fuel_temp_c, fuel_target_c, dt, float(model_parameters["fuel_temperature_response_time_s"]))
        graphite_temp_c = first_order_step(
            graphite_temp_c,
            graphite_target_c,
            dt,
            float(model_parameters["graphite_temperature_response_time_s"]),
        )
        coolant_temp_c = first_order_step(
            coolant_temp_c,
            coolant_target_c,
            dt,
            float(model_parameters["coolant_temperature_response_time_s"]),
        )

        core_inventory, segment_inventory = step_precursors(
            backend,
            core_inventory=core_inventory,
            segment_inventory=segment_inventory,
            groups=groups,
            loop_segments=loop_segments,
            power_fraction=power_fraction,
            flow_fraction=effective_flow_fraction,
            cleanup_rate_s=cleanup_rate_s,
            dt=dt,
            baseline=baseline,
        )
        core_delayed_neutron_source_fraction = core_delayed_source(backend, core_inventory, decay_vector) / backend.maximum(
            steady_core_source,
            1.0e-12,
        )

        xenon_fraction = first_order_step(
            xenon_fraction,
            backend.maximum(power_fraction, 0.0),
            dt,
            float(model_parameters["xenon_response_time_s"]),
        )
        xenon_fraction = backend.maximum(
            xenon_fraction - cleanup_rate_s * float(depletion["xenon_removal_fraction"]) * xenon_fraction * dt,
            0.0,
        )

        breeding_gain_fraction_per_day = float(depletion["breeding_gain_fraction_per_day"])
        fissile_burn_fraction_per_day_full_power = float(depletion["fissile_burn_fraction_per_day_full_power"])
        minor_actinide_sink_fraction_per_day = float(depletion["minor_actinide_sink_fraction_per_day"])
        protactinium_holdup_days = max(float(depletion["protactinium_holdup_days"]), 0.05)
        protactinium_target_fraction = breeding_gain_fraction_per_day * protactinium_holdup_days * power_fraction
        protactinium_inventory_fraction = first_order_step(
            protactinium_inventory_fraction,
            protactinium_target_fraction,
            dt,
            protactinium_holdup_days * 86400.0,
        )
        fissile_inventory_fraction = backend.clip(
            fissile_inventory_fraction
            + (
                breeding_gain_fraction_per_day * backend.maximum(1.0 - protactinium_inventory_fraction, 0.0)
                - fissile_burn_fraction_per_day_full_power * power_fraction
                - minor_actinide_sink_fraction_per_day
            )
            * dt_days,
            0.2,
            1.5,
        )

        redox_target_ev = (
            target_redox_state_ev
            + controls["redox_setpoint_shift_ev"]
            + perturbations["redox_bias_ev"]
            + impurity_fraction * 0.03
        )
        redox_state_ev = first_order_step(
            redox_state_ev,
            redox_target_ev,
            dt,
            max(float(chemistry["redox_control_time_days"]) * 86400.0, dt),
        )
        impurity_ingress_fraction_per_day = (
            float(chemistry["oxidant_ingress_fraction_per_day"])
            * backend.clip(controls["impurity_ingress_multiplier"] * perturbations["impurity_ingress_scale"], 0.0, 4.0)
        )
        impurity_capture_rate_per_day = (
            float(chemistry["impurity_capture_efficiency"]) + gas_stripping_efficiency
        ) / max(float(baseline["fuel_cycle"].get("cleanup_turnover_days", 14.0)), 0.25)
        impurity_fraction = backend.clip(
            impurity_fraction
            + (impurity_ingress_fraction_per_day - impurity_capture_rate_per_day * impurity_fraction) * dt_days,
            0.0,
            0.05,
        )
        corrosion_index = backend.maximum(
            1.0
            + backend.maximum(redox_state_ev - target_redox_state_ev, 0.0) * float(chemistry["corrosion_acceleration_per_ev"])
            + impurity_fraction * 400.0,
            0.1,
        )

        temperature_feedback_pcm = (
            fuel_temp_feedback_pcm_per_c * (fuel_temp_c - steady_fuel_temp_c)
            + graphite_temp_feedback_pcm_per_c * (graphite_temp_c - steady_graphite_temp_c)
            + coolant_temp_feedback_pcm_per_c * (coolant_temp_c - steady_coolant_temp_c)
        )
        precursor_feedback_pcm = precursor_worth_pcm * (core_delayed_neutron_source_fraction - 1.0)
        xenon_feedback_pcm = xenon_worth_pcm_per_fraction * (xenon_fraction - 1.0)
        depletion_feedback_pcm = (
            float(model_parameters["depletion_reactivity_worth_pcm_per_fraction"]) * (fissile_inventory_fraction - 1.0)
            + float(model_parameters["protactinium_penalty_pcm_per_fraction"]) * protactinium_inventory_fraction
        )
        chemistry_feedback_pcm = (
            float(model_parameters["chemistry_redox_worth_pcm_per_ev"]) * (redox_state_ev - steady_redox_state_ev)
            + float(model_parameters["chemistry_impurity_worth_pcm_per_fraction"]) * impurity_fraction
        )
        control_reactivity_pcm = controls["reactivity_pcm"] * perturbations["event_reactivity_scale"]
        final_total_reactivity_pcm = (
            control_reactivity_pcm
            + temperature_feedback_pcm
            + precursor_feedback_pcm
            + xenon_feedback_pcm
            + depletion_feedback_pcm
            + chemistry_feedback_pcm
        )
        power_target = backend.clip(
            1.0 + (final_total_reactivity_pcm / max(float(model_parameters["reactivity_to_power_scale_pcm"]), 1.0)),
            0.02,
            float(model_parameters["max_power_fraction"]),
        )
        power_fraction = first_order_step(power_fraction, power_target, dt, float(model_parameters["power_response_time_s"]))

        if summary_mode == "full":
            power_band = backend.percentiles(power_fraction, (0.05, 0.50, 0.95))
            fuel_band = backend.percentiles(fuel_temp_c, (0.05, 0.50, 0.95))
            reactivity_band = backend.percentiles(final_total_reactivity_pcm, (0.05, 0.50, 0.95))
            corrosion_band = backend.percentiles(corrosion_index, (0.05, 0.50, 0.95))
            core_delayed_source_band = backend.percentiles(core_delayed_neutron_source_fraction, (0.05, 0.50, 0.95))
            history.append(
                {
                    "time_s": round(float(time_s), 6),
                    "power_fraction_p05": round(power_band[0], 6),
                    "power_fraction_p50": round(power_band[1], 6),
                    "power_fraction_p95": round(power_band[2], 6),
                    "fuel_temp_c_p05": round(fuel_band[0], 6),
                    "fuel_temp_c_p50": round(fuel_band[1], 6),
                    "fuel_temp_c_p95": round(fuel_band[2], 6),
                    "total_reactivity_pcm_p05": round(reactivity_band[0], 6),
                    "total_reactivity_pcm_p50": round(reactivity_band[1], 6),
                    "total_reactivity_pcm_p95": round(reactivity_band[2], 6),
                    "core_delayed_neutron_source_fraction_p05": round(core_delayed_source_band[0], 6),
                    "core_delayed_neutron_source_fraction_p50": round(core_delayed_source_band[1], 6),
                    "core_delayed_neutron_source_fraction_p95": round(core_delayed_source_band[2], 6),
                    "corrosion_index_p05": round(corrosion_band[0], 6),
                    "corrosion_index_p50": round(corrosion_band[1], 6),
                    "corrosion_index_p95": round(corrosion_band[2], 6),
                }
            )

        peak_power_fraction_max = max(peak_power_fraction_max, backend.max_scalar(power_fraction))
        peak_fuel_temperature_c_max = max(peak_fuel_temperature_c_max, backend.max_scalar(fuel_temp_c))
        peak_corrosion_index_max = max(peak_corrosion_index_max, backend.max_scalar(corrosion_index))

    if summary_mode != "full":
        power_band = backend.percentiles(power_fraction, (0.05, 0.50, 0.95))
        fuel_band = backend.percentiles(fuel_temp_c, (0.05, 0.50, 0.95))
        reactivity_band = backend.percentiles(final_total_reactivity_pcm, (0.05, 0.50, 0.95))
        corrosion_band = backend.percentiles(corrosion_index, (0.05, 0.50, 0.95))
        core_delayed_source_band = backend.percentiles(core_delayed_neutron_source_fraction, (0.05, 0.50, 0.95))
        history.append(
            {
                "time_s": round(float(duration_s), 6),
                "power_fraction_p05": round(power_band[0], 6),
                "power_fraction_p50": round(power_band[1], 6),
                "power_fraction_p95": round(power_band[2], 6),
                "fuel_temp_c_p05": round(fuel_band[0], 6),
                "fuel_temp_c_p50": round(fuel_band[1], 6),
                "fuel_temp_c_p95": round(fuel_band[2], 6),
                "total_reactivity_pcm_p05": round(reactivity_band[0], 6),
                "total_reactivity_pcm_p50": round(reactivity_band[1], 6),
                "total_reactivity_pcm_p95": round(reactivity_band[2], 6),
                "core_delayed_neutron_source_fraction_p05": round(core_delayed_source_band[0], 6),
                "core_delayed_neutron_source_fraction_p50": round(core_delayed_source_band[1], 6),
                "core_delayed_neutron_source_fraction_p95": round(core_delayed_source_band[2], 6),
                "corrosion_index_p05": round(corrosion_band[0], 6),
                "corrosion_index_p50": round(corrosion_band[1], 6),
                "corrosion_index_p95": round(corrosion_band[2], 6),
            }
        )

    backend.synchronize()
    elapsed_s = time.perf_counter() - integrate_start
    final = history[-1]
    return {
        "backend": backend.describe(),
        "runtime_environment": runtime_environment_report(),
        "case": prepared.case_name,
        "scenario": scenario["name"],
        "samples": samples,
        "seed": seed,
        "rng_mode": rng_mode,
        "summary_mode": summary_mode,
        "duration_s": round(duration_s, 6),
        "time_step_s": round(dt, 6),
        "steps": step_count + 1,
        "groups": group_count,
        "loop_segments": len(loop_segments),
        "elapsed_s": round(elapsed_s, 6),
        "sample_steps_per_s": round((samples * (step_count + 1)) / max(elapsed_s, 1.0e-12), 3),
        "estimated_state_bytes": estimate_state_bytes(samples, group_count, len(loop_segments), backend.dtype_name),
        "backend_memory_allocated_bytes": backend.memory_allocated_bytes(),
        "peak_power_fraction_max": round(peak_power_fraction_max, 6),
        "peak_fuel_temperature_c_max": round(peak_fuel_temperature_c_max, 6),
        "peak_corrosion_index_max": round(peak_corrosion_index_max, 6),
        "final_power_fraction_p50": final["power_fraction_p50"],
        "final_power_fraction_p95": final["power_fraction_p95"],
        "final_total_reactivity_pcm_p50": final["total_reactivity_pcm_p50"],
        "final_total_reactivity_pcm_p95": final["total_reactivity_pcm_p95"],
        "final_core_delayed_neutron_source_fraction_p50": final["core_delayed_neutron_source_fraction_p50"],
        "history": history,
    }


def run_backend_once(
    prepared: PreparedTransient,
    *,
    backend_name: str,
    samples: int,
    seed: int,
    dtype: str,
    rng_mode: str,
    summary_mode: str,
) -> dict[str, Any]:
    backend = resolve_auto_backend(dtype=dtype, seed=seed) if backend_name == "auto" else create_backend(backend_name, dtype=dtype, seed=seed)
    return run_vectorized_transient(
        backend,
        prepared,
        samples=samples,
        seed=seed,
        rng_mode=rng_mode,
        summary_mode=summary_mode,
    )


def run_memory_probe(backend: BaseBackend, *, elements: int, iterations: int) -> dict[str, Any]:
    elements = max(int(elements), 1024)
    iterations = max(int(iterations), 1)
    a = backend.full((elements,), 1.0)
    b = backend.full((elements,), 2.0)
    c = backend.full((elements,), 0.5)
    backend.synchronize()
    start = time.perf_counter()
    for _ in range(iterations):
        a = b + 1.61803398875 * c
        b = c + 0.61803398875 * a
        c = a - 0.25 * b
    backend.synchronize()
    elapsed = time.perf_counter() - start
    bytes_touched = elements * dtype_size_bytes(backend.dtype_name) * 9 * iterations
    return {
        "kind": "vector_triad_bandwidth",
        "elements": elements,
        "iterations": iterations,
        "elapsed_s": round(elapsed, 6),
        "estimated_gb_s": round(bytes_touched / max(elapsed, 1.0e-12) / 1.0e9, 3),
        "checksum": round(backend.scalar(a[0]), 6),
    }


def run_porous_field_probe(
    backend: BaseBackend,
    *,
    cells: int,
    iterations: int,
    groups: int,
) -> dict[str, Any]:
    side = max(int(math.sqrt(max(cells, 4096))), 64)
    ny = side
    nx = side
    iterations = max(int(iterations), 1)
    groups = max(int(groups), 1)
    temperature = backend.full((ny, nx), 650.0)
    fluid_temperature = backend.full((ny, nx), 630.0)
    precursor = backend.full((groups, ny, nx), 1.0 / groups)
    decay = backend.asarray([0.0124, 0.0305, 0.111, 0.301, 1.14, 3.01][:groups])
    if groups > 6:
        decay = backend.asarray([0.0124 + 0.05 * index for index in range(groups)])
    decay3 = decay[:, None, None]

    def laplace(field: Any, axis_offset: int = 0) -> Any:
        return (
            backend.roll(field, shift=1, axis=axis_offset)
            + backend.roll(field, shift=-1, axis=axis_offset)
            + backend.roll(field, shift=1, axis=axis_offset + 1)
            + backend.roll(field, shift=-1, axis=axis_offset + 1)
            - 4.0 * field
        )

    backend.synchronize()
    start = time.perf_counter()
    for _ in range(iterations):
        precursor_source = backend.maximum((temperature - 620.0) / 300.0, 0.0)
        precursor = backend.maximum(
            precursor
            + 0.03 * laplace(precursor, axis_offset=1)
            + 0.004 * precursor_source
            - 0.01 * decay3 * precursor,
            0.0,
        )
        delayed_source = backend.sum(precursor * decay3, axis=0)
        fluid_temperature = (
            fluid_temperature
            + 0.08 * laplace(fluid_temperature)
            + 0.02 * (temperature - fluid_temperature)
        )
        temperature = (
            temperature
            + 0.04 * laplace(temperature)
            - 0.015 * (temperature - fluid_temperature)
            + 0.02 * delayed_source
        )
    backend.synchronize()
    elapsed = time.perf_counter() - start
    cell_updates = ny * nx * iterations
    bytes_per_cell = dtype_size_bytes(backend.dtype_name) * (32 + groups * 28)
    return {
        "kind": "porous_core_stencil_precursor_transport",
        "grid": [ny, nx],
        "groups": groups,
        "iterations": iterations,
        "elapsed_s": round(elapsed, 6),
        "cell_updates_per_s": round(cell_updates / max(elapsed, 1.0e-12), 3),
        "estimated_gb_s": round(cell_updates * bytes_per_cell / max(elapsed, 1.0e-12) / 1.0e9, 3),
        "temperature_center_c": round(backend.scalar(temperature[ny // 2, nx // 2]), 6),
    }


def run_kernel_probes(
    backend: BaseBackend,
    *,
    elements: int,
    cells: int,
    iterations: int,
    groups: int,
) -> list[dict[str, Any]]:
    return [
        run_memory_probe(backend, elements=elements, iterations=iterations),
        run_porous_field_probe(backend, cells=cells, iterations=iterations, groups=groups),
    ]


def run_validation(prepared: PreparedTransient, *, samples: int, seed: int, output_root: Path) -> dict[str, Any]:
    from thorium_reactor.config import load_case_config
    from thorium_reactor.neutronics.workflows import run_case
    from thorium_reactor.paths import create_result_bundle
    from thorium_reactor.transient_sweep import run_transient_sweep_case

    config = load_case_config(REPO_ROOT / "configs" / "cases" / prepared.case_name / "case.yaml")
    bundle = create_result_bundle(output_root / "production-validation", prepared.case_name, f"validation-{samples}")
    summary = run_case(config, bundle, solver_enabled=False)
    start = time.perf_counter()
    payload = run_transient_sweep_case(
        config,
        bundle,
        summary,
        scenario_name=str(prepared.scenario["name"]),
        samples=samples,
        seed=seed,
        prefer_gpu=False,
    )
    elapsed = time.perf_counter() - start
    return {
        "samples": samples,
        "elapsed_s": round(elapsed, 6),
        "backend": payload["backend"],
        "peak_power_fraction_p95": payload["metrics"]["peak_power_fraction_p95"],
        "final_power_fraction_p50": payload["metrics"]["final_power_fraction_p50"],
        "final_total_reactivity_pcm_p50": payload["metrics"]["final_total_reactivity_pcm_p50"],
        "final_core_delayed_neutron_source_fraction_p50": payload["metrics"][
            "final_core_delayed_neutron_source_fraction_p50"
        ],
    }


def parse_sample_grid(raw: str | None, fallback: int) -> list[int]:
    if not raw:
        return [fallback]
    values = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(max(int(item), 32))
    return values or [fallback]


def _truthy_environment(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in {"", "0", "false", "off", "no"}


def runtime_environment_report() -> dict[str, Any]:
    fallback = os.environ.get("PYTORCH_ENABLE_XPU_FALLBACK")
    return {
        "ONEAPI_DEVICE_SELECTOR": os.environ.get("ONEAPI_DEVICE_SELECTOR"),
        "SYCL_CACHE_PERSISTENT": os.environ.get("SYCL_CACHE_PERSISTENT"),
        "ZE_ENABLE_PCI_ID_DEVICE_ORDER": os.environ.get("ZE_ENABLE_PCI_ID_DEVICE_ORDER"),
        "PYTORCH_ENABLE_XPU_FALLBACK": fallback,
        "pytorch_xpu_fallback_enabled": _truthy_environment(fallback),
        "KMP_DUPLICATE_LIB_OK": os.environ.get("KMP_DUPLICATE_LIB_OK"),
    }


def probe_backend_in_current_process(name: str, dtype: str, seed: int) -> dict[str, Any]:
    try:
        backend = create_backend(name, dtype=dtype, seed=seed)
        return {"name": name, "available": True, "details": backend.describe()}
    except BaseException as exc:
        return {
            "name": name,
            "available": False,
            "reason": str(exc),
            "error_type": type(exc).__name__,
        }


def _parse_probe_stdout(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def probe_backend_in_subprocess(name: str, dtype: str, seed: int) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--probe-backend",
        name,
        "--dtype",
        dtype,
        "--seed",
        str(seed),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "available": False,
            "reason": f"backend probe timed out after {exc.timeout} seconds",
            "error_type": "TimeoutExpired",
        }

    parsed = _parse_probe_stdout(completed.stdout)
    if completed.returncode == 0 and parsed is not None:
        return parsed

    stderr_tail = "\n".join(completed.stderr.splitlines()[-8:]).strip()
    stdout_tail = "\n".join(completed.stdout.splitlines()[-8:]).strip()
    reason_parts = [
        f"backend probe exited with code {completed.returncode}",
    ]
    if stderr_tail:
        reason_parts.append(f"stderr: {stderr_tail}")
    if stdout_tail and parsed is None:
        reason_parts.append(f"stdout: {stdout_tail}")
    return {
        "name": name,
        "available": False,
        "reason": " | ".join(reason_parts),
        "error_type": "SubprocessProbeFailed",
    }


def available_backend_report(dtype: str, seed: int) -> list[dict[str, Any]]:
    report = []
    for name in BACKEND_NAMES:
        report.append(probe_backend_in_subprocess(name, dtype, seed))
    return report


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GPU viability benchmark for reactor transient sweeps.")
    parser.add_argument("--case", default="immersed_pool_reference", help="Case name under configs/cases.")
    parser.add_argument("--scenario", default="partial_heat_sink_loss", help="Transient scenario name.")
    parser.add_argument("--backend", default="auto", choices=["auto", "torch-xpu", "dpnp", "cupy", "torch-cuda", "numpy", "torch-cpu"])
    parser.add_argument("--profile-all", action="store_true", help="Run every available backend.")
    parser.add_argument("--samples", type=int, default=262144, help="Ensemble samples for a single run.")
    parser.add_argument("--sample-grid", default=None, help="Comma-separated sample counts for profile-all runs.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dtype", default="float32", choices=["float32", "float64", "float16", "bfloat16"])
    parser.add_argument("--rng-mode", default="device", choices=["device", "production"], help="Use device RNG for speed or production RNG for faithfulness.")
    parser.add_argument("--summary-mode", default="full", choices=["full", "final"], help="Full stores per-step p05/p50/p95 history; final only summarizes at the end.")
    parser.add_argument("--target-vram-gb", type=float, default=28.0, help="Soft VRAM budget for chunk planning.")
    parser.add_argument("--kernel-probes", default="basic", choices=["none", "basic"], help="Run standalone memory and porous-core stencil probes.")
    parser.add_argument("--probe-elements", type=int, default=8_388_608, help="Elements for the vector bandwidth probe.")
    parser.add_argument("--probe-cells", type=int, default=262_144, help="Approximate cells for the porous-core stencil probe.")
    parser.add_argument("--probe-iterations", type=int, default=50, help="Iterations for standalone kernel probes.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--list-backends", action="store_true")
    parser.add_argument("--probe-backend", choices=BACKEND_NAMES, help=argparse.SUPPRESS)
    parser.add_argument("--validate-production", action="store_true", help="Run the existing Python sweep for a small comparison.")
    parser.add_argument("--validation-samples", type=int, default=256)
    args = parser.parse_args(argv)

    if args.probe_backend:
        print(json.dumps(probe_backend_in_current_process(args.probe_backend, args.dtype, args.seed), sort_keys=True))
        return 0

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if args.list_backends:
        payload = {
            "host": host_report(),
            "arc_pro_b70_target": ARC_PRO_B70_TARGET,
            "backends": available_backend_report(args.dtype, args.seed),
        }
        print(json.dumps(payload, indent=2))
        return 0

    prepared = load_prepared_transient(args.case, args.scenario, output_root)
    sample_grid = parse_sample_grid(args.sample_grid, args.samples)
    chunks = choose_chunks(
        max(sample_grid),
        len(prepared.precursor_groups),
        len(prepared.loop_segments),
        args.dtype,
        args.target_vram_gb,
    )
    if len(chunks) > 1:
        print(
            "Requested sample count exceeds the configured VRAM budget. "
            "This script reports the chunk plan but still runs each requested sample count as a single benchmark."
        )

    backend_names = [args.backend]
    if args.profile_all:
        backend_names = [item["name"] for item in available_backend_report(args.dtype, args.seed) if item["available"]]

    results = []
    failures = []
    for backend_name in backend_names:
        for samples in sample_grid:
            try:
                print(f"running backend={backend_name} samples={samples} dtype={args.dtype} rng={args.rng_mode}")
                backend = resolve_auto_backend(dtype=args.dtype, seed=args.seed) if backend_name == "auto" else create_backend(
                    backend_name,
                    dtype=args.dtype,
                    seed=args.seed,
                )
                result = run_vectorized_transient(
                    backend,
                    prepared,
                    samples=max(samples, 32),
                    seed=args.seed,
                    rng_mode=args.rng_mode,
                    summary_mode=args.summary_mode,
                )
                if args.kernel_probes != "none":
                    result["kernel_probes"] = run_kernel_probes(
                        backend,
                        elements=args.probe_elements,
                        cells=args.probe_cells,
                        iterations=args.probe_iterations,
                        groups=len(prepared.precursor_groups),
                    )
                results.append(result)
                print(
                    f"  {result['backend']['name']} {result['sample_steps_per_s']:.3f} sample-steps/s "
                    f"in {result['elapsed_s']:.3f}s"
                )
            except BaseException as exc:
                failure = {"backend": backend_name, "samples": samples, "error": repr(exc)}
                failures.append(failure)
                print(f"  failed: {failure['error']}")

    validation = None
    if args.validate_production:
        print(f"running production validation samples={args.validation_samples}")
        validation = run_validation(
            prepared,
            samples=max(args.validation_samples, 32),
            seed=args.seed,
            output_root=output_root,
        )

    payload = {
        "host": host_report(),
        "arc_pro_b70_target": ARC_PRO_B70_TARGET,
        "case": prepared.case_name,
        "scenario": prepared.scenario,
        "dtype": args.dtype,
        "rng_mode": args.rng_mode,
        "summary_mode": args.summary_mode,
        "target_vram_gb": args.target_vram_gb,
        "chunk_plan_for_max_sample": [{"start": start, "stop": stop} for start, stop in chunks],
        "results": results,
        "failures": failures,
        "production_validation": validation,
    }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = output_root / f"gpu_viability_{prepared.case_name}_{stamp}.json"
    write_json(output_path, payload)
    print(f"wrote {output_path}")
    return 0 if results else 2


def host_report() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "repo_root": str(REPO_ROOT),
        "environment": runtime_environment_report(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
