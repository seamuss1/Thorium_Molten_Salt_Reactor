from __future__ import annotations

import math
from typing import Any

ArrayNamespace = Any


def get_array_namespace(*, prefer_gpu: bool = False) -> tuple[ArrayNamespace, str]:
    """Return an array namespace and backend label.

    When ``prefer_gpu`` is true we try to use CuPy, but only if it imports cleanly
    *and* reports at least one visible CUDA device. Otherwise the function falls
    back to a lightweight pure-Python namespace, which keeps host-side workflows
    stable even when a local BLAS/NumPy runtime is unavailable.
    """

    if prefer_gpu:
        try:  # pragma: no cover - exercised only on GPU-capable environments.
            import cupy as cp  # type: ignore

            try:
                if int(cp.cuda.runtime.getDeviceCount()) > 0:
                    return cp, "cupy"
            except Exception:
                pass
        except Exception:
            pass
    return _PythonArrayNamespace(), "python"


def to_python_scalar(value: Any) -> float:
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except Exception:
            pass
    return float(value)


def to_numpy(array: Any) -> list[float]:
    if hasattr(array, "get"):
        try:
            array = array.get()
        except Exception:
            pass
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
        resolved = []
        for quantile in self.asarray(quantiles):
            resolved.append(_percentile(ordered, quantile / 100.0))
        return resolved


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
