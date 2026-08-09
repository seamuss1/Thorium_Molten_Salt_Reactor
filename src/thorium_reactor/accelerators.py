from __future__ import annotations

import importlib
import math
import os
import sys
from dataclasses import dataclass
from typing import Any

SUPPORTED_ARRAY_BACKENDS = ("python", "numpy", "torch-cpu", "torch-xpu")
VECTOR_ARRAY_BACKENDS = ("numpy", "torch-cpu", "torch-xpu")
SUPPORTED_DTYPES = ("float16", "bfloat16", "float32", "float64")
DEFAULT_DTYPE = "float32"
REFERENCE_DTYPE = "float64"

# torch.quantile refuses inputs above 2**24 elements; above that we take the
# explicit sort path instead of discovering the limit through an exception.
TORCH_QUANTILE_MAX_ELEMENTS = 2**24


class BackendUnavailable(RuntimeError):
    pass


def configure_torch_environment() -> None:
    """Install the Intel oneAPI/OpenMP defaults torch needs, before torch loads.

    ``libiomp`` reads ``KMP_DUPLICATE_LIB_OK`` when the DLL is loaded, and the
    level-zero variables are read when the SYCL runtime initializes. Setting
    them after ``import torch`` is a no-op, and on Windows the duplicate-OpenMP
    check then aborts the interpreter outright (``OMP: Error #15``, exit 3)
    rather than raising something recoverable.

    So these are installed at import time of this module, for every torch
    device rather than only for XPU -- ``torch-cpu`` loads the same libiomp.
    Values already present in the environment always win, so an operator can
    still opt into fallback behaviour deliberately.
    """
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("PYTORCH_ENABLE_XPU_FALLBACK", "0")
    os.environ.setdefault("SYCL_CACHE_PERSISTENT", "1")
    os.environ.setdefault("ZE_ENABLE_PCI_ID_DEVICE_ORDER", "1")


configure_torch_environment()


def torch_environment_is_safe() -> tuple[bool, str | None]:
    """Report whether torch was imported before we could set the OpenMP guard.

    If another module imported torch first, ``configure_torch_environment`` came
    too late and constructing a torch backend may abort the process. We cannot
    recover from that abort, so callers surface it as a diagnostic instead.
    """
    if "torch" not in sys.modules:
        return True, None
    if _truthy_environment(os.environ.get("KMP_DUPLICATE_LIB_OK")):
        return True, None
    return False, (
        "torch was imported before thorium_reactor.accelerators, so KMP_DUPLICATE_LIB_OK "
        "could not be set in time. Set KMP_DUPLICATE_LIB_OK=TRUE in the environment, or "
        "import thorium_reactor.accelerators before torch."
    )


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


def validate_dtype(dtype: str) -> str:
    if dtype not in SUPPORTED_DTYPES:
        raise BackendUnavailable(f"Unsupported array dtype: {dtype!r}. Choose one of {', '.join(SUPPORTED_DTYPES)}.")
    return dtype


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

    def percentiles_batch(self, values: list[Any], quantiles: tuple[float, float, float]) -> list[list[float]]:
        """Percentiles for several arrays with a single host transfer.

        The per-step history reduction needs five percentile triples. Doing them
        one at a time costs five device synchronizations per time step; batching
        them costs one.
        """
        return [self.percentiles(value, quantiles) for value in values]

    def amax(self, value: Any) -> Any:
        """Maximum as a *device* scalar, without transferring to the host.

        Lets a caller fold a running extremum across many steps and pay a
        single synchronization at the end instead of one per step.
        """
        raise NotImplementedError

    def amin(self, value: Any) -> Any:
        """Minimum as a device scalar. See :meth:`amax`."""
        raise NotImplementedError

    def max_scalar(self, value: Any) -> float:
        return self.scalar(self.amax(value))

    def min_scalar(self, value: Any) -> float:
        return self.scalar(self.amin(value))

    def scalar(self, value: Any) -> float:
        raise NotImplementedError

    def to_host_list(self, value: Any) -> list[float]:
        raise NotImplementedError

    def synchronize(self) -> None:
        return None

    def memory_allocated_bytes(self) -> int | None:
        return None

    def max_memory_allocated_bytes(self) -> int | None:
        return None

    def reset_peak_memory_stats(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device_label,
            "dtype": self.dtype_name,
        }


class PythonReferenceBackend:
    """Marker for the pure-Python reference integrator.

    Deliberately not an :class:`ArrayBackend`: the reference integrator works on
    Python lists and never calls the array contract. It is always float64.
    """

    name = "python"
    device_label = "cpu"
    dtype_name = REFERENCE_DTYPE

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device_label,
            "dtype": self.dtype_name,
        }

    def memory_allocated_bytes(self) -> int | None:
        return None

    def max_memory_allocated_bytes(self) -> int | None:
        return None


class NumpyBackend(ArrayBackend):
    def __init__(self, *, dtype_name: str, seed: int) -> None:
        self.xp = importlib.import_module("numpy")
        self.name = "numpy"
        self.device_label = "cpu"
        self.dtype_name = validate_dtype(dtype_name)
        self.seed = seed
        self.dtype = getattr(self.xp, self.dtype_name)

    def asarray(self, value: Any, *, dtype: str | None = None) -> Any:
        resolved_dtype = getattr(self.xp, validate_dtype(dtype) if dtype else self.dtype_name)
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
        return self.xp.stack([self.xp.asarray(value, dtype=self.dtype) for value in values], axis=axis)

    def roll(self, value: Any, *, shift: int, axis: int) -> Any:
        return self.xp.roll(value, shift=shift, axis=axis)

    def percentiles(self, value: Any, quantiles: tuple[float, float, float]) -> list[float]:
        raw = self.xp.percentile(value, self.xp.asarray([item * 100.0 for item in quantiles], dtype=self.dtype))
        return [float(item) for item in self.to_host_list(raw)]

    def amax(self, value: Any) -> Any:
        return self.xp.max(value)

    def amin(self, value: Any) -> Any:
        return self.xp.min(value)

    def scalar(self, value: Any) -> float:
        if hasattr(value, "item"):
            value = value.item()
        return float(value)

    def to_host_list(self, value: Any) -> list[float]:
        # Flattened, matching TorchBackend, so callers can index the result the
        # same way on either backend.
        if hasattr(value, "reshape"):
            raw = self.xp.asarray(value).reshape(-1).tolist()
        else:
            raw = value if isinstance(value, list) else [value]
        if isinstance(raw, (int, float)):
            return [float(raw)]
        return [float(item) for item in raw]


class TorchBackend(ArrayBackend):
    def __init__(self, *, device: str, dtype_name: str, seed: int) -> None:
        if device not in {"cpu", "xpu"}:
            raise BackendUnavailable(f"Unsupported torch device: {device}")
        self.dtype_name = validate_dtype(dtype_name)

        # Must happen before the first torch import in the process; see the
        # module docstring on configure_torch_environment.
        configure_torch_environment()
        safe, message = torch_environment_is_safe()
        if not safe:
            raise BackendUnavailable(message or "Unsafe torch import order.")

        self.torch = importlib.import_module("torch")
        self.seed = seed
        self.dtype = {
            "float32": self.torch.float32,
            "float64": self.torch.float64,
            "float16": self.torch.float16,
            "bfloat16": self.torch.bfloat16,
        }[self.dtype_name]

        if device == "xpu":
            self.ipex_import_error: str | None = None
            try:
                importlib.import_module("intel_extension_for_pytorch")
            except ImportError as exc:
                # Modern torch XPU builds do not need IPEX; record why it was
                # absent rather than hiding it behind "XPU is not available".
                self.ipex_import_error = f"{type(exc).__name__}: {exc}"
            if not hasattr(self.torch, "xpu") or not self.torch.xpu.is_available():
                detail = f" (intel_extension_for_pytorch: {self.ipex_import_error})" if self.ipex_import_error else ""
                raise BackendUnavailable(f"PyTorch XPU is not available.{detail}")
            self.device = self.torch.device("xpu")
            self.name = "torch-xpu"
            try:
                self.device_label = self.torch.xpu.get_device_name(0)
            except Exception:
                self.device_label = "xpu"
        else:
            self.device = self.torch.device("cpu")
            self.name = "torch-cpu"
            self.device_label = "cpu"

        # A local generator, so constructing a backend does not perturb the
        # global torch RNG that other code in this process may depend on.
        self.generator = self.torch.Generator(device=self.device)
        self.generator.manual_seed(seed)

    def _torch_dtype(self, dtype: str | None) -> Any:
        if dtype is None:
            return self.dtype
        return {
            "float32": self.torch.float32,
            "float64": self.torch.float64,
            "float16": self.torch.float16,
            "bfloat16": self.torch.bfloat16,
        }[validate_dtype(dtype)]

    def asarray(self, value: Any, *, dtype: str | None = None) -> Any:
        resolved_dtype = self._torch_dtype(dtype)
        if self.torch.is_tensor(value):
            return value.to(device=self.device, dtype=resolved_dtype)
        return self.torch.tensor(value, dtype=resolved_dtype, device=self.device)

    def full(self, shape: tuple[int, ...], value: float) -> Any:
        return self.torch.full(shape, value, dtype=self.dtype, device=self.device)

    def zeros(self, shape: tuple[int, ...]) -> Any:
        return self.torch.zeros(shape, dtype=self.dtype, device=self.device)

    def normal(self, *, mean: float, sigma: float, shape: tuple[int, ...], seed_offset: int) -> Any:
        generator = self.torch.Generator(device=self.device)
        generator.manual_seed(self.seed + seed_offset)
        return self.torch.normal(
            mean=mean, std=sigma, size=shape, dtype=self.dtype, device=self.device, generator=generator
        )

    def clip(self, value: Any, lower: float, upper: float) -> Any:
        return self.torch.clamp(value, min=lower, max=upper)

    def maximum(self, left: Any, right: Any) -> Any:
        # clamp takes a Python scalar directly. torch.maximum does not, and
        # wrapping the scalar in a tensor costs an allocation plus a
        # host->device copy on every call -- hundreds of thousands of them over
        # a sweep. clamp is only equivalent for a *finite* bound, though:
        # torch.maximum(x, nan) propagates nan, while clamp(x, min=nan) leaves
        # x untouched, so a non-finite bound must take the tensor path or a
        # corrupt threshold would be silently ignored.
        scalar_bound = self._finite_scalar(right if not self.torch.is_tensor(right) else None)
        if scalar_bound is not None:
            return self.torch.clamp(left, min=scalar_bound)
        scalar_bound = self._finite_scalar(left if not self.torch.is_tensor(left) else None)
        if scalar_bound is not None:
            return self.torch.clamp(right, min=scalar_bound)
        return self.torch.maximum(self.asarray(left), self.asarray(right))

    def minimum(self, left: Any, right: Any) -> Any:
        scalar_bound = self._finite_scalar(right if not self.torch.is_tensor(right) else None)
        if scalar_bound is not None:
            return self.torch.clamp(left, max=scalar_bound)
        scalar_bound = self._finite_scalar(left if not self.torch.is_tensor(left) else None)
        if scalar_bound is not None:
            return self.torch.clamp(right, max=scalar_bound)
        return self.torch.minimum(self.asarray(left), self.asarray(right))

    @staticmethod
    def _finite_scalar(value: Any) -> float | None:
        if value is None:
            return None
        try:
            resolved = float(value)
        except (TypeError, ValueError):
            return None
        return resolved if math.isfinite(resolved) else None

    def sum(self, value: Any, axis: int | None = None) -> Any:
        return self.torch.sum(value) if axis is None else self.torch.sum(value, dim=axis)

    def stack(self, values: list[Any], axis: int = 0) -> Any:
        return self.torch.stack([self.asarray(value) for value in values], dim=axis)

    def roll(self, value: Any, *, shift: int, axis: int) -> Any:
        return self.torch.roll(value, shifts=shift, dims=axis)

    def _quantile_tensor(self, value: Any, quantiles: tuple[float, float, float]) -> Any:
        """Quantiles as a device tensor, without transferring anything to the host."""
        flat = value.flatten()
        if flat.dtype not in (self.torch.float32, self.torch.float64):
            # torch.quantile and torch.sort interpolation accept only float or
            # double; half precisions are advertised by the backend, so upcast
            # rather than surfacing a dtype error from deep in the reduction.
            flat = flat.to(self.torch.float32)
        if int(flat.numel()) <= TORCH_QUANTILE_MAX_ELEMENTS:
            q = self.torch.tensor(quantiles, dtype=flat.dtype, device=self.device)
            return self.torch.quantile(flat, q)
        # torch.quantile refuses larger inputs; sort and interpolate on device.
        ordered = self.torch.sort(flat).values
        count = int(ordered.numel())
        picks = []
        for quantile in quantiles:
            position = quantile * max(count - 1, 0)
            lower_index = int(math.floor(position))
            upper_index = min(int(math.ceil(position)), max(count - 1, 0))
            fraction = position - lower_index
            lower = ordered[lower_index]
            picks.append(lower if lower_index == upper_index else lower + (ordered[upper_index] - lower) * fraction)
        return self.torch.stack(picks)

    def percentiles(self, value: Any, quantiles: tuple[float, float, float]) -> list[float]:
        return self.to_host_list(self._quantile_tensor(value, quantiles))

    def percentiles_batch(self, values: list[Any], quantiles: tuple[float, float, float]) -> list[list[float]]:
        if not values:
            return []
        # One host transfer for every series in the step, instead of one each.
        stacked = self.torch.stack([self._quantile_tensor(value, quantiles) for value in values])
        flat = self.to_host_list(stacked)
        width = len(quantiles)
        return [flat[index * width : (index + 1) * width] for index in range(len(values))]

    def amax(self, value: Any) -> Any:
        return self.torch.max(value)

    def amin(self, value: Any) -> Any:
        return self.torch.min(value)

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

    def max_memory_allocated_bytes(self) -> int | None:
        if self.device.type == "xpu":
            try:
                return int(self.torch.xpu.max_memory_allocated())
            except Exception:
                return None
        return None

    def reset_peak_memory_stats(self) -> None:
        if self.device.type == "xpu":
            try:
                self.torch.xpu.reset_peak_memory_stats()
            except Exception:
                return None
        return None

    def device_total_memory_bytes(self) -> int | None:
        if self.device.type == "xpu":
            try:
                return int(self.torch.xpu.get_device_properties(0).total_memory)
            except Exception:
                return None
        return None


def create_array_backend(
    name: str, *, dtype: str = DEFAULT_DTYPE, seed: int = 42
) -> ArrayBackend | PythonReferenceBackend:
    if name == "python":
        return PythonReferenceBackend()
    if name == "numpy":
        return NumpyBackend(dtype_name=dtype, seed=seed)
    if name == "torch-cpu":
        return TorchBackend(device="cpu", dtype_name=dtype, seed=seed)
    if name == "torch-xpu":
        return TorchBackend(device="xpu", dtype_name=dtype, seed=seed)
    raise BackendUnavailable(f"Unsupported array backend: {name}")


#: Ensemble size below which GPU offload is not worth the device-init and
#: per-step launch overhead. Measured on an Intel Arc Pro B70 against
#: ``flagship_grid_stress``: 1.4x at 65,536 samples, 12.4x at 262,144, and
#: 49.6x at 1,048,576 -- the fixed per-step cost is what the ensemble has to
#: amortize, so small ensembles get an advisory in the selection reason.
GPU_EFFICIENT_SAMPLE_FLOOR = 131_072


def resolve_runtime_backend(
    requested: str,
    *,
    samples: int,
    dtype: str = DEFAULT_DTYPE,
    seed: int = 42,
) -> BackendSelection:
    """Pick a backend, and say honestly why.

    ``samples`` shapes the recorded reason: the vectorized integrator pays a
    fixed per-step launch and synchronization cost, so an ensemble below
    :data:`GPU_EFFICIENT_SAMPLE_FLOOR` gets little from the GPU and the
    selection says so. It is still honoured -- the GPU is never slower here,
    just under-used -- but the bundle records that the run was in that regime.
    """
    requested = requested.strip().lower()
    if requested not in (*SUPPORTED_ARRAY_BACKENDS, "auto"):
        raise BackendUnavailable(
            f"Unsupported array backend: {requested!r}. Choose one of auto, {', '.join(SUPPORTED_ARRAY_BACKENDS)}."
        )
    validate_dtype(dtype)

    if requested != "auto":
        # Constructing here surfaces an unavailable backend as an error the
        # caller can act on, rather than a silent downgrade.
        create_array_backend(requested, dtype=dtype, seed=seed)
        selected_dtype = REFERENCE_DTYPE if requested == "python" else dtype
        return BackendSelection(
            requested=requested,
            selected=requested,
            reason="explicit backend requested",
            dtype=selected_dtype,
        )

    undersized = (
        f" Note: {samples:,} samples is below the {GPU_EFFICIENT_SAMPLE_FLOOR:,}-sample floor where GPU offload"
        " amortizes its per-step launch overhead, so the device is under-used at this ensemble size."
        if samples < GPU_EFFICIENT_SAMPLE_FLOOR
        else ""
    )

    try:
        create_array_backend("torch-xpu", dtype=dtype, seed=seed)
        return BackendSelection(
            requested=requested,
            selected="torch-xpu",
            reason=f"auto mode prefers torch-xpu when available.{undersized}".strip(),
            dtype=dtype,
        )
    except Exception as exc:
        gpu_error: Exception = exc

    try:
        create_array_backend("numpy", dtype=dtype, seed=seed)
        return BackendSelection(
            requested=requested,
            selected="numpy",
            reason=f"torch-xpu unavailable ({gpu_error}); numpy CPU vector backend is available",
            dtype=dtype,
        )
    except Exception as exc:
        numpy_error: Exception = exc

    return BackendSelection(
        requested=requested,
        selected="python",
        reason=(
            f"falling back to pure Python reference backend: torch-xpu unavailable ({gpu_error}); "
            f"numpy unavailable ({numpy_error}). This is far slower and runs in float64."
        ),
        dtype=REFERENCE_DTYPE,
    )


def backend_report_for_selection(
    selection: BackendSelection,
    *,
    seed: int = 42,
    backend: ArrayBackend | PythonReferenceBackend | None = None,
) -> dict[str, Any]:
    """Describe the backend that will actually run.

    Pass ``backend`` to describe an already-constructed instance; that avoids
    building the device context a second time purely to write the report.
    """
    try:
        described = (
            backend
            if backend is not None
            else create_array_backend(selection.selected, dtype=selection.dtype, seed=seed)
        )
        details = described.describe()
        available = True
        reason = selection.reason
    except Exception as exc:
        details = None
        available = False
        reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return {
        "requested": selection.requested,
        "selected": selection.selected,
        "available": available,
        "reason": reason,
        "details": details,
        "environment": runtime_environment_report(),
    }


def estimate_state_bytes(*, samples: int, dtype: str, groups: int, loop_segments: int) -> int:
    """Approximate device bytes the vectorized integrator holds live.

    Roughly 30 per-sample state/temporary arrays, plus the precursor core
    inventory (groups) and segment inventory (groups x segments), plus the 11
    perturbation arrays that live for the whole run.
    """
    width = {"float16": 2, "bfloat16": 2, "float32": 4, "float64": 8}[validate_dtype(dtype)]
    per_sample_slots = 30 + 11 + groups + groups * loop_segments
    return int(samples) * per_sample_slots * width


def check_memory_budget(
    backend: ArrayBackend | PythonReferenceBackend,
    *,
    samples: int,
    dtype: str,
    groups: int,
    loop_segments: int,
    headroom_fraction: float = 0.8,
) -> dict[str, Any]:
    """Refuse an ensemble that cannot fit, instead of discovering it at step 1."""
    estimated = estimate_state_bytes(samples=samples, dtype=dtype, groups=groups, loop_segments=loop_segments)
    total = getattr(backend, "device_total_memory_bytes", lambda: None)()
    budget = {
        "estimated_state_bytes": estimated,
        "device_total_bytes": total,
        "headroom_fraction": headroom_fraction,
        "status": "ok",
    }
    if total is not None and estimated > total * headroom_fraction:
        budget["status"] = "insufficient"
        raise BackendUnavailable(
            f"Ensemble of {samples:,} samples needs about {estimated / 2**30:.1f} GiB of device memory, "
            f"which exceeds {headroom_fraction:.0%} of the {total / 2**30:.1f} GiB available on "
            f"{getattr(backend, 'device_label', 'the device')}. Reduce --samples or use --dtype float32."
        )
    return budget
