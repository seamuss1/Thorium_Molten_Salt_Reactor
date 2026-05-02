from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Sequence


SUPPORTED_ARRAY_BACKENDS = ("python", "numpy", "torch-cpu", "torch-xpu")
VECTOR_ARRAY_BACKENDS = ("numpy", "torch-cpu", "torch-xpu")
DEFAULT_DTYPE = "float32"
GPU_SAMPLE_THRESHOLD = 32_768

ArrayNamespace = Any


class BackendUnavailable(RuntimeError):
    pass


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


@dataclass(frozen=True)
class BackendSelection:
    requested: str
    selected: str
    reason: str
    dtype: str = DEFAULT_DTYPE


class ArrayBackend:
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


class PythonReferenceBackend:
    name = "python"
    device_label = "cpu"
    dtype_name = "float64"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device_label,
            "dtype": self.dtype_name,
        }

    def memory_allocated_bytes(self) -> int | None:
        return None


class NumpyBackend(ArrayBackend):
    def __init__(self, *, dtype_name: str, seed: int) -> None:
        self.xp = importlib.import_module("numpy")
        self.name = "numpy"
        self.device_label = "cpu"
        self.dtype_name = dtype_name
        self.seed = seed
        self.dtype = getattr(self.xp, dtype_name)

    def asarray(self, value: Any, *, dtype: str | None = None) -> Any:
        resolved_dtype = getattr(self.xp, dtype or self.dtype_name)
        return self.xp.asarray(value, dtype=resolved_dtype)

    def full(self, shape: tuple[int, ...], value: float) -> Any:
        return self.xp.full(shape, value, dtype=self.dtype)

    def zeros(self, shape: tuple[int, ...]) -> Any:
        return self.xp.zeros(shape, dtype=self.dtype)

    def normal(self, *, mean: float, sigma: float, shape: tuple[int, ...], seed_offset: int) -> Any:
        rng = self.xp.random.default_rng(self.seed + seed_offset)
        return self.xp.asarray(rng.normal(mean, sigma, size=shape), dtype=self.dtype)

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
        raw = self.xp.percentile(value, self.xp.asarray([item * 100.0 for item in quantiles], dtype=self.dtype))
        return [float(item) for item in self.to_host_list(raw)]

    def max_scalar(self, value: Any) -> float:
        return self.scalar(self.xp.max(value))

    def min_scalar(self, value: Any) -> float:
        return self.scalar(self.xp.min(value))

    def scalar(self, value: Any) -> float:
        if hasattr(value, "item"):
            value = value.item()
        return float(value)

    def to_host_list(self, value: Any) -> list[float]:
        raw = value.tolist() if hasattr(value, "tolist") else list(value)
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(item) for item in raw]


class TorchBackend(ArrayBackend):
    def __init__(self, *, device: str, dtype_name: str, seed: int) -> None:
        if device == "xpu" and "PYTORCH_ENABLE_XPU_FALLBACK" not in os.environ:
            os.environ["PYTORCH_ENABLE_XPU_FALLBACK"] = "0"
        if device == "xpu":
            os.environ.setdefault("SYCL_CACHE_PERSISTENT", "1")
            os.environ.setdefault("ZE_ENABLE_PCI_ID_DEVICE_ORDER", "1")
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
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
                raise BackendUnavailable("PyTorch XPU is not available.")
            self.device = self.torch.device("xpu")
            self.name = "torch-xpu"
            try:
                self.device_label = self.torch.xpu.get_device_name(0)
            except Exception:
                self.device_label = "xpu"
        elif device == "cpu":
            self.device = self.torch.device("cpu")
            self.name = "torch-cpu"
            self.device_label = "cpu"
        else:
            raise BackendUnavailable(f"Unsupported torch device: {device}")

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
            return self.to_host_list(self.torch.quantile(flat, q))
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

    def memory_allocated_bytes(self) -> int | None:
        if self.device.type == "xpu":
            try:
                return int(self.torch.xpu.memory_allocated())
            except Exception:
                return None
        return None


def create_array_backend(name: str, *, dtype: str = DEFAULT_DTYPE, seed: int = 42) -> ArrayBackend | PythonReferenceBackend:
    if name == "python":
        return PythonReferenceBackend()
    if name == "numpy":
        return NumpyBackend(dtype_name=dtype, seed=seed)
    if name == "torch-cpu":
        return TorchBackend(device="cpu", dtype_name=dtype, seed=seed)
    if name == "torch-xpu":
        return TorchBackend(device="xpu", dtype_name=dtype, seed=seed)
    raise BackendUnavailable(f"Unsupported array backend: {name}")


def resolve_runtime_backend(
    requested: str,
    *,
    samples: int,
    dtype: str = DEFAULT_DTYPE,
    seed: int = 42,
) -> BackendSelection:
    requested = requested.strip().lower()
    if requested not in (*SUPPORTED_ARRAY_BACKENDS, "auto"):
        raise BackendUnavailable(f"Unsupported array backend: {requested}")
    if requested != "auto":
        create_array_backend(requested, dtype=dtype, seed=seed)
        return BackendSelection(requested=requested, selected=requested, reason="explicit backend requested", dtype=dtype)

    if samples >= GPU_SAMPLE_THRESHOLD:
        try:
            create_array_backend("torch-xpu", dtype=dtype, seed=seed)
            return BackendSelection(
                requested=requested,
                selected="torch-xpu",
                reason=f"samples >= {GPU_SAMPLE_THRESHOLD} and torch-xpu is available",
                dtype=dtype,
            )
        except Exception:
            pass
    try:
        create_array_backend("numpy", dtype=dtype, seed=seed)
        return BackendSelection(
            requested=requested,
            selected="numpy",
            reason="numpy CPU vector backend is available",
            dtype=dtype,
        )
    except Exception:
        return BackendSelection(
            requested=requested,
            selected="python",
            reason="falling back to pure Python reference backend",
            dtype="float64",
        )


def backend_report_for_selection(selection: BackendSelection, *, seed: int = 42) -> dict[str, Any]:
    try:
        backend = create_array_backend(selection.selected, dtype=selection.dtype, seed=seed)
        details = backend.describe()
        available = True
        reason = selection.reason
    except BaseException as exc:
        details = None
        available = False
        reason = str(exc)
    return {
        "requested": selection.requested,
        "selected": selection.selected,
        "available": available,
        "reason": reason,
        "details": details,
        "environment": runtime_environment_report(),
    }


def probe_backend_in_current_process(name: str, dtype: str, seed: int) -> dict[str, Any]:
    try:
        backend = create_array_backend(name, dtype=dtype, seed=seed)
        return {
            "name": name,
            "available": True,
            "details": backend.describe(),
            "environment": runtime_environment_report(),
        }
    except BaseException as exc:
        return {
            "name": name,
            "available": False,
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "environment": runtime_environment_report(),
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


def probe_backend_in_subprocess(name: str, *, dtype: str = DEFAULT_DTYPE, seed: int = 42) -> dict[str, Any]:
    src_root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = src_root if not env.get("PYTHONPATH") else f"{src_root}{os.pathsep}{env['PYTHONPATH']}"
    command = [
        sys.executable,
        "-m",
        "thorium_reactor.accelerators",
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
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "available": False,
            "reason": f"backend probe timed out after {exc.timeout} seconds",
            "error_type": "TimeoutExpired",
            "environment": runtime_environment_report(),
        }
    parsed = _parse_probe_stdout(completed.stdout)
    if completed.returncode == 0 and parsed is not None:
        parsed["probe_isolated_process"] = True
        return parsed
    stderr_tail = "\n".join(completed.stderr.splitlines()[-8:]).strip()
    stdout_tail = "\n".join(completed.stdout.splitlines()[-8:]).strip()
    reason_parts = [f"backend probe exited with code {completed.returncode}"]
    if stderr_tail:
        reason_parts.append(f"stderr: {stderr_tail}")
    if stdout_tail and parsed is None:
        reason_parts.append(f"stdout: {stdout_tail}")
    return {
        "name": name,
        "available": False,
        "reason": " | ".join(reason_parts),
        "error_type": "SubprocessProbeFailed",
        "environment": runtime_environment_report(),
    }


def available_backend_report(
    *,
    dtype: str = DEFAULT_DTYPE,
    seed: int = 42,
    names: Sequence[str] = SUPPORTED_ARRAY_BACKENDS,
) -> list[dict[str, Any]]:
    return [probe_backend_in_subprocess(name, dtype=dtype, seed=seed) for name in names]


def get_array_namespace(*, prefer_gpu: bool = False) -> tuple[ArrayNamespace, str]:
    """Backward-compatible helper for older callers.

    New runtime paths should use ``create_array_backend`` and
    ``resolve_runtime_backend``. This helper intentionally returns a CPU
    namespace unless an explicit GPU-compatible namespace can be imported.
    """

    if prefer_gpu:
        try:  # pragma: no cover - depends on optional CUDA stack.
            import cupy as cp  # type: ignore

            if int(cp.cuda.runtime.getDeviceCount()) > 0:
                return cp, "cupy"
        except Exception:
            pass
    try:
        np = importlib.import_module("numpy")
        return np, "numpy"
    except Exception:
        return _PythonArrayNamespace(), "python"


def to_python_scalar(value: Any) -> float:
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except Exception:
            pass
    return float(value)


def to_numpy(array: Any) -> list[float]:
    if hasattr(array, "detach"):
        try:
            array = array.detach().cpu()
        except Exception:
            pass
    if hasattr(array, "get"):
        try:
            array = array.get()
        except Exception:
            pass
    if hasattr(array, "tolist"):
        raw = array.tolist()
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(value) for value in raw]
    if isinstance(array, list):
        return [float(value) for value in array]
    if isinstance(array, tuple):
        return [float(value) for value in array]
    return [float(array)]


def percentile_band(values: Any, xp: ArrayNamespace) -> tuple[float, float, float]:
    percentiles = xp.percentile(values, xp.asarray([5.0, 50.0, 95.0]))
    if hasattr(percentiles, "tolist"):
        raw = percentiles.tolist()
    else:
        raw = list(percentiles)
    return (
        to_python_scalar(raw[0]),
        to_python_scalar(raw[1]),
        to_python_scalar(raw[2]),
    )


class _PythonArrayNamespace:
    def asarray(self, values: Any) -> list[float]:
        if isinstance(values, list):
            return [float(value) for value in values]
        if isinstance(values, tuple):
            return [float(value) for value in values]
        return [float(values)]

    def percentile(self, values: Any, quantiles: Any) -> list[float]:
        ordered = sorted(self.asarray(values))
        return [_percentile(ordered, quantile / 100.0) for quantile in self.asarray(quantiles)]


def _percentile(ordered: list[float], quantile: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Thorium reactor array backend probe.")
    parser.add_argument("--probe-backend", choices=SUPPORTED_ARRAY_BACKENDS, required=True)
    parser.add_argument("--dtype", default=DEFAULT_DTYPE, choices=["float32", "float64", "float16", "bfloat16"])
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    print(json.dumps(probe_backend_in_current_process(args.probe_backend, args.dtype, args.seed), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
