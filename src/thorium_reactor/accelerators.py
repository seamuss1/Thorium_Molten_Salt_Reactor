from __future__ import annotations

from typing import Any

import numpy as np


ArrayNamespace = Any


def get_array_namespace(*, prefer_gpu: bool = False) -> tuple[ArrayNamespace, str]:
    """Return an array namespace and backend label.

    The project always has NumPy available. When ``prefer_gpu`` is true we try to
    use CuPy, but only if it imports cleanly *and* reports at least one visible
    CUDA device. Otherwise the function silently falls back to NumPy.
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
    return np, "numpy"


def to_python_scalar(value: Any) -> float:
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except Exception:
            pass
    return float(value)


def to_numpy(array: Any) -> np.ndarray:
    if hasattr(array, "get"):
        try:
            return np.asarray(array.get())
        except Exception:
            pass
    return np.asarray(array)


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
