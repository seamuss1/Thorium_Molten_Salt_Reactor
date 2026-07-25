from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from thorium_reactor.depletion.chain import DepletionChain, DepletionReaction, load_depletion_chain

try:  # pragma: no cover - exercised when SciPy is installed in the runtime.
    import scipy.sparse as scipy_sparse
    from scipy.sparse.linalg import expm_multiply as scipy_expm_multiply
except Exception:
    scipy_sparse = None
    scipy_expm_multiply = None


DEPLETION_MATRIX_MODEL = "native_sparse_bateman_depletion_matrix_v1"
DEPLETION_NPZ_SCHEMA_VERSION = 1


@dataclass(slots=True)
class DepletionMatrixResult:
    matrix: Any
    backend: str
    nuclide_names: list[str]
    zone_names: list[str]
    feed_vector: np.ndarray
    removal_rates: np.ndarray
    shape: tuple[int, int]

    @property
    def size(self) -> int:
        return int(self.shape[0])


def build_depletion_matrix(
    chain: DepletionChain,
    *,
    zone_names: Sequence[str] = ("core",),
    reaction_rates_per_s: Mapping[str, Any] | None = None,
    removal_rates_per_s: Mapping[str, Any] | None = None,
    feed_atoms_per_s: Mapping[str, Any] | None = None,
    zone_transfers_per_s: Sequence[Mapping[str, Any]] | None = None,
) -> DepletionMatrixResult:
    nuclide_names = chain.nuclide_names
    if not nuclide_names:
        raise ValueError("Depletion chain must contain at least one nuclide.")
    zones = [str(zone) for zone in zone_names] or ["core"]
    nuclide_index = {name: index for index, name in enumerate(nuclide_names)}
    zone_index = {name: index for index, name in enumerate(zones)}
    size = len(nuclide_names) * len(zones)
    dense = np.zeros((size, size), dtype=float)
    feed = np.zeros(size, dtype=float)
    removals = np.zeros(size, dtype=float)
    reaction_rates = reaction_rates_per_s or {}

    for zone in zones:
        for parent in chain.nuclides:
            parent_idx = _flat_index(zone_index[zone], nuclide_index[parent.name], len(nuclide_names))
            decay_constant = _decay_constant(parent.half_life_s)
            if decay_constant > 0.0:
                dense[parent_idx, parent_idx] -= decay_constant
                for mode in parent.decay_modes:
                    _add_product(
                        dense, mode, decay_constant, parent_idx, zone_index[zone], nuclide_index, len(nuclide_names)
                    )
            for reaction in parent.reactions:
                rate = _reaction_rate(reaction_rates, zone, parent.name, reaction)
                if rate <= 0.0:
                    continue
                dense[parent_idx, parent_idx] -= rate
                if reaction.reaction_type.lower() == "fission":
                    for product, yield_fraction in reaction.fission_yields.items():
                        if product in nuclide_index:
                            product_idx = _flat_index(zone_index[zone], nuclide_index[product], len(nuclide_names))
                            dense[product_idx, parent_idx] += rate * float(yield_fraction)
                else:
                    _add_product(dense, reaction, rate, parent_idx, zone_index[zone], nuclide_index, len(nuclide_names))

    for zone in zones:
        for nuclide in nuclide_names:
            idx = _flat_index(zone_index[zone], nuclide_index[nuclide], len(nuclide_names))
            removal = _mapping_rate(removal_rates_per_s or {}, zone, nuclide)
            if removal > 0.0:
                dense[idx, idx] -= removal
                removals[idx] = removal
            feed[idx] = _mapping_rate(feed_atoms_per_s or {}, zone, nuclide)

    for transfer in zone_transfers_per_s or ():
        if not isinstance(transfer, Mapping):
            continue
        source = str(transfer.get("from", transfer.get("source", "")))
        target = str(transfer.get("to", transfer.get("target", "")))
        rate = float(transfer.get("rate_per_s", 0.0) or 0.0)
        if rate <= 0.0 or source not in zone_index or target not in zone_index:
            continue
        for nuclide in nuclide_names:
            source_idx = _flat_index(zone_index[source], nuclide_index[nuclide], len(nuclide_names))
            target_idx = _flat_index(zone_index[target], nuclide_index[nuclide], len(nuclide_names))
            dense[source_idx, source_idx] -= rate
            dense[target_idx, source_idx] += rate

    if scipy_sparse is not None:
        matrix = scipy_sparse.csr_matrix(dense)
        backend = "scipy_sparse_expm_multiply"
    else:
        matrix = dense
        backend = "numpy_dense_expm_fallback"
    return DepletionMatrixResult(
        matrix=matrix,
        backend=backend,
        nuclide_names=nuclide_names,
        zone_names=zones,
        feed_vector=feed,
        removal_rates=removals,
        shape=(size, size),
    )


def step_depletion(matrix_result: DepletionMatrixResult, inventory: np.ndarray, dt_s: float) -> np.ndarray:
    if dt_s < 0.0:
        raise ValueError("dt_s must be non-negative.")
    start = np.asarray(inventory, dtype=float)
    if start.shape != (matrix_result.size,):
        raise ValueError(f"Inventory vector must have shape {(matrix_result.size,)}.")
    if dt_s == 0.0:
        return start.copy()
    feed = matrix_result.feed_vector
    if scipy_sparse is not None and scipy_expm_multiply is not None and matrix_result.backend.startswith("scipy"):
        feed_column = scipy_sparse.csr_matrix(feed[:, None])
        zero = scipy_sparse.csr_matrix((1, 1))
        augmented = scipy_sparse.bmat([[matrix_result.matrix, feed_column], [None, zero]], format="csr")
        augmented_start = np.concatenate([start, [1.0]])
        stepped = scipy_expm_multiply(augmented * float(dt_s), augmented_start)
        return np.maximum(np.asarray(stepped[:-1], dtype=float), 0.0)

    dense = _as_dense(matrix_result.matrix)
    augmented = np.zeros((matrix_result.size + 1, matrix_result.size + 1), dtype=float)
    augmented[:-1, :-1] = dense
    augmented[:-1, -1] = feed
    augmented_start = np.concatenate([start, [1.0]])
    return np.maximum(_dense_expm_action(augmented, augmented_start, float(dt_s))[:-1], 0.0)


def run_depletion_case(config: Any, bundle: Any, summary: dict[str, Any]) -> dict[str, Any]:
    settings = _depletion_settings(config)
    chain_path = _resolve_chain_path(config, settings)
    chain = load_depletion_chain(chain_path, source_format=settings.get("chain_format"))
    zones = _zone_names(settings)
    matrix_result = build_depletion_matrix(
        chain,
        zone_names=zones,
        reaction_rates_per_s=settings.get("reaction_rates_per_s"),
        removal_rates_per_s=settings.get("removal_rates_per_s"),
        feed_atoms_per_s=settings.get("feed_atoms_per_s"),
        zone_transfers_per_s=settings.get("zone_transfers_per_s"),
    )
    initial = _initial_inventory_vector(config, chain, matrix_result, settings)
    dt_s = _time_step_seconds(settings)
    steps = max(int(settings.get("steps", 1)), 1)

    history_records = []
    inventory = initial
    history_records.append(_history_record(matrix_result, inventory, time_s=0.0, step=0))
    for step in range(1, steps + 1):
        inventory = step_depletion(matrix_result, inventory, dt_s)
        history_records.append(_history_record(matrix_result, inventory, time_s=step * dt_s, step=step))

    matrix_path = bundle.root / "depletion_matrix.npz"
    _write_matrix_npz(matrix_path, matrix_result)
    chain_payload = chain.to_json()
    bundle.write_json("depletion_chain.json", chain_payload)
    history_payload = {
        "model": DEPLETION_MATRIX_MODEL,
        "time_unit": "s",
        "records": history_records,
    }
    bundle.write_json("depletion_history.json", history_payload)
    balance = _atom_balance_diagnostic(matrix_result, initial, inventory)
    initial_total = float(np.sum(initial))
    final_total = float(np.sum(inventory))
    depletion_summary = {
        "status": "completed",
        "model": DEPLETION_MATRIX_MODEL,
        "backend": matrix_result.backend,
        "chain_source": str(chain_path),
        "chain_format": chain.source_format,
        "chain_name": chain.name,
        "isotope_count": len(matrix_result.nuclide_names),
        "zone_count": len(matrix_result.zone_names),
        "zones": matrix_result.zone_names,
        "matrix_shape": list(matrix_result.shape),
        "matrix_nonzero_entries": _nonzero_count(matrix_result.matrix),
        "time_step_s": _round_float(dt_s),
        "time_step_days": _round_float(dt_s / 86400.0),
        "steps": steps,
        "initial_total_atoms": _round_float(initial_total),
        "final_total_atoms": _round_float(final_total),
        "inventory_delta_atoms": _round_float(final_total - initial_total),
        "inventory_delta_fraction": _round_float((final_total - initial_total) / max(initial_total, 1.0)),
        "feed_total_atoms": _round_float(float(np.sum(matrix_result.feed_vector)) * dt_s * steps),
        "removal_rate_weighted_initial_atoms_per_s": _round_float(float(np.sum(matrix_result.removal_rates * initial))),
        "atom_balance_residual": _round_optional_float(balance["residual"]),
        "atom_balance_basis": balance["basis"],
        "atom_balance_status": balance["status"],
        "net_source_sink_rate_initial_atoms_per_s": _round_float(balance["net_source_sink_rate_initial_atoms_per_s"]),
        "artifacts": {
            "chain_path": "depletion_chain.json",
            "summary_path": "depletion_summary.json",
            "history_path": "depletion_history.json",
            "matrix_path": "depletion_matrix.npz",
            "matrix_schema_path": "depletion_matrix.schema.json",
            "matrix_human_summary_path": "depletion_matrix.summary.md",
        },
        "coupling_status": "native_constant_rate_matrix_not_yet_predictor_corrector_coupled",
    }
    bundle.write_json("depletion_summary.json", depletion_summary)
    summary["depletion_matrix"] = depletion_summary
    metrics = summary.setdefault("metrics", {})
    metrics["depletion_matrix_isotope_count"] = depletion_summary["isotope_count"]
    metrics["depletion_matrix_zone_count"] = depletion_summary["zone_count"]
    metrics["depletion_matrix_atom_balance_residual"] = depletion_summary["atom_balance_residual"]
    metrics["depletion_matrix_inventory_delta_fraction"] = depletion_summary["inventory_delta_fraction"]
    bundle.write_json("summary.json", summary)
    bundle.write_metrics(metrics)
    return depletion_summary


def _add_product(
    dense: np.ndarray,
    reaction: DepletionReaction,
    rate: float,
    parent_idx: int,
    zone_idx: int,
    nuclide_index: Mapping[str, int],
    nuclide_count: int,
) -> None:
    if reaction.target in nuclide_index:
        product_idx = _flat_index(zone_idx, nuclide_index[str(reaction.target)], nuclide_count)
        dense[product_idx, parent_idx] += rate * float(reaction.branching_ratio)


def _flat_index(zone_idx: int, nuclide_idx: int, nuclide_count: int) -> int:
    return zone_idx * nuclide_count + nuclide_idx


def _decay_constant(half_life_s: float | None) -> float:
    if half_life_s is None or half_life_s <= 0.0:
        return 0.0
    return math.log(2.0) / half_life_s


def _reaction_rate(
    rates: Mapping[str, Any],
    zone: str,
    parent: str,
    reaction: DepletionReaction,
) -> float:
    by_zone = rates.get(zone) if isinstance(rates, Mapping) else None
    if isinstance(by_zone, Mapping):
        by_parent = by_zone.get(parent)
        if isinstance(by_parent, Mapping):
            value = by_parent.get(reaction.reaction_type)
            if value is not None:
                return float(value)
        if by_parent is not None and not isinstance(by_parent, Mapping):
            return float(by_parent)
    by_parent = rates.get(parent) if isinstance(rates, Mapping) else None
    if isinstance(by_parent, Mapping):
        value = by_parent.get(reaction.reaction_type)
        if value is not None:
            return float(value)
    if by_parent is not None and not isinstance(by_parent, Mapping):
        return float(by_parent)
    return float(reaction.default_rate_per_s)


def _mapping_rate(mapping: Mapping[str, Any], zone: str, nuclide: str) -> float:
    by_zone = mapping.get(zone) if isinstance(mapping, Mapping) else None
    if isinstance(by_zone, Mapping) and nuclide in by_zone:
        return max(float(by_zone[nuclide]), 0.0)
    if nuclide in mapping:
        return max(float(mapping[nuclide]), 0.0)
    return 0.0


def _dense_expm_action(matrix: np.ndarray, vector: np.ndarray, dt_s: float) -> np.ndarray:
    scaled = matrix * dt_s
    try:
        values, vectors = np.linalg.eig(scaled)
        inverse = np.linalg.inv(vectors)
        result = vectors @ (np.exp(values) * (inverse @ vector))
        result = np.real_if_close(result, tol=1000)
        if np.iscomplexobj(result):
            real_norm = float(np.linalg.norm(np.real(result), ord=np.inf))
            imag_norm = float(np.linalg.norm(np.imag(result), ord=np.inf))
            if imag_norm > max(1.0e-6, 1.0e-8 * max(real_norm, 1.0)):
                return _rk4_action(matrix, vector, dt_s)
            result = np.real(result)
        return np.asarray(result, dtype=float)
    except np.linalg.LinAlgError:
        return _rk4_action(matrix, vector, dt_s)


def _rk4_action(matrix: np.ndarray, vector: np.ndarray, dt_s: float) -> np.ndarray:
    steps = max(int(math.ceil(dt_s / 60.0)), 1)
    h = dt_s / steps
    y = vector.astype(float, copy=True)
    for _ in range(steps):
        k1 = matrix @ y
        k2 = matrix @ (y + 0.5 * h * k1)
        k3 = matrix @ (y + 0.5 * h * k2)
        k4 = matrix @ (y + h * k3)
        y = y + h * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
    return y


def _as_dense(matrix: Any) -> np.ndarray:
    if hasattr(matrix, "toarray"):
        return np.asarray(matrix.toarray(), dtype=float)
    return np.asarray(matrix, dtype=float)


def _write_matrix_npz(path: Path, matrix_result: DepletionMatrixResult) -> None:
    if scipy_sparse is not None and hasattr(matrix_result.matrix, "tocoo"):
        matrix = matrix_result.matrix.tocsr()
        arrays = {
            "matrix_format": np.array(["csr"]),
            "data": np.asarray(matrix.data, dtype=float),
            "indices": np.asarray(matrix.indices, dtype=np.int64),
            "indptr": np.asarray(matrix.indptr, dtype=np.int64),
            "shape": np.asarray(matrix.shape, dtype=np.int64),
            "backend": np.array([matrix_result.backend]),
            "nuclide_names": np.array(matrix_result.nuclide_names),
            "zone_names": np.array(matrix_result.zone_names),
            "feed_vector": matrix_result.feed_vector,
            "removal_rates": matrix_result.removal_rates,
        }
    else:
        arrays = {
            "matrix_format": np.array(["dense"]),
            "dense_matrix": _as_dense(matrix_result.matrix),
            "shape": np.asarray(matrix_result.shape, dtype=np.int64),
            "backend": np.array([matrix_result.backend]),
            "nuclide_names": np.array(matrix_result.nuclide_names),
            "zone_names": np.array(matrix_result.zone_names),
            "feed_vector": matrix_result.feed_vector,
            "removal_rates": matrix_result.removal_rates,
        }
    np.savez_compressed(path, **arrays)
    schema = _depletion_npz_schema(matrix_result, arrays)
    _write_npz_sidecars(
        path,
        schema,
        title="Depletion Matrix NPZ",
        notes=[
            "Matrix columns map parent nuclide-zone inventory to product nuclide-zone rates.",
            "Flat inventory order is zone-major: zone index, then nuclide index.",
            "Closed-chain conservation is assessed from matrix column sums; open systems use feed/removal vectors.",
        ],
    )


def _depletion_npz_schema(matrix_result: DepletionMatrixResult, arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    return {
        "schema_version": DEPLETION_NPZ_SCHEMA_VERSION,
        "generator": "thorium_reactor.depletion.matrix._write_matrix_npz",
        "artifact": "depletion_matrix.npz",
        "model": DEPLETION_MATRIX_MODEL,
        "coordinate_conventions": {
            "inventory_order": ["zone", "nuclide"],
            "matrix_orientation": "rows are produced nuclide-zone states; columns are depleted parent states",
            "zones": matrix_result.zone_names,
            "nuclides": matrix_result.nuclide_names,
        },
        "normalization": {
            "matrix_units": "1/s",
            "feed_vector_units": "atoms/s",
            "removal_rates_units": "1/s",
            "conservation": "closed systems should have near-zero matrix column sums with zero feed vector",
        },
        "arrays": {
            name: {
                "shape": list(value.shape),
                "dtype": str(value.dtype),
                "units": _depletion_units(name),
                "description": _depletion_array_description(name),
            }
            for name, value in arrays.items()
        },
    }


def _write_npz_sidecars(path: Path, schema: dict[str, Any], *, title: str, notes: Sequence[str]) -> None:
    path.with_suffix(".schema.json").write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    lines = [f"# {title}", "", f"- Artifact: `{path.name}`", f"- Schema version: `{schema['schema_version']}`"]
    lines.extend(f"- {note}" for note in notes)
    lines.extend(["", "## Arrays", ""])
    for name, spec in schema["arrays"].items():
        lines.append(
            f"- `{name}`: shape=`{spec['shape']}`, dtype=`{spec['dtype']}`, "
            f"units=`{spec['units']}`, {spec['description']}"
        )
    path.with_suffix(".summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _depletion_units(name: str) -> str:
    return {
        "matrix_format": "label",
        "data": "1/s",
        "indices": "index",
        "indptr": "index",
        "shape": "count",
        "dense_matrix": "1/s",
        "backend": "label",
        "nuclide_names": "label",
        "zone_names": "label",
        "feed_vector": "atoms/s",
        "removal_rates": "1/s",
    }[name]


def _depletion_array_description(name: str) -> str:
    return {
        "matrix_format": "storage layout marker",
        "data": "CSR nonzero matrix values",
        "indices": "CSR column indices for data",
        "indptr": "CSR row pointer offsets",
        "shape": "matrix shape",
        "dense_matrix": "dense depletion matrix",
        "backend": "runtime backend used to assemble and step the matrix",
        "nuclide_names": "nuclide labels aligned with the flat inventory vector",
        "zone_names": "zone labels aligned with the flat inventory vector",
        "feed_vector": "external feed source term for each flat inventory state",
        "removal_rates": "explicit removal sink rate for each flat inventory state",
    }[name]


def _nonzero_count(matrix: Any) -> int:
    if hasattr(matrix, "nnz"):
        return int(matrix.nnz)
    return int(np.count_nonzero(matrix))


def _atom_balance_diagnostic(
    matrix_result: DepletionMatrixResult,
    initial: np.ndarray,
    final: np.ndarray,
) -> dict[str, float | str | None]:
    dense = _as_dense(matrix_result.matrix)
    column_sums = np.sum(dense, axis=0)
    closed_matrix = np.allclose(column_sums, 0.0, atol=1.0e-14) and np.allclose(matrix_result.feed_vector, 0.0)
    scale = max(float(np.sum(np.abs(initial))), float(np.sum(np.abs(final))), 1.0)
    net_source_sink_rate = float(np.sum(dense @ initial) + np.sum(matrix_result.feed_vector))
    if closed_matrix:
        return {
            "residual": abs(float(np.sum(final) - np.sum(initial))) / scale,
            "basis": "closed_chain_total_atoms_conserved",
            "status": "closed_chain",
            "net_source_sink_rate_initial_atoms_per_s": net_source_sink_rate,
        }
    return {
        "residual": None,
        "basis": "not_applicable_open_system",
        "status": "open_system_sources_or_sinks_present",
        "net_source_sink_rate_initial_atoms_per_s": net_source_sink_rate,
    }


def _history_record(
    matrix_result: DepletionMatrixResult, inventory: np.ndarray, *, time_s: float, step: int
) -> dict[str, Any]:
    by_zone = {}
    nuclide_count = len(matrix_result.nuclide_names)
    for zone_index, zone in enumerate(matrix_result.zone_names):
        start = zone_index * nuclide_count
        values = inventory[start : start + nuclide_count]
        by_zone[zone] = {
            "total_atoms": _round_float(float(np.sum(values))),
            "inventory_atoms": {
                name: _round_float(float(values[index]))
                for index, name in enumerate(matrix_result.nuclide_names)
                if abs(float(values[index])) > 0.0
            },
        }
    return {
        "step": step,
        "time_s": _round_float(time_s),
        "time_days": _round_float(time_s / 86400.0),
        "total_atoms": _round_float(float(np.sum(inventory))),
        "zones": by_zone,
    }


def _depletion_settings(config: Any) -> dict[str, Any]:
    data = getattr(config, "data", {})
    settings = data.get("depletion_solver")
    if isinstance(settings, Mapping):
        return dict(settings)
    depletion = data.get("depletion", {})
    if isinstance(depletion, Mapping) and isinstance(depletion.get("native_matrix"), Mapping):
        return dict(depletion["native_matrix"])
    return {}


def _resolve_chain_path(config: Any, settings: Mapping[str, Any]) -> Path:
    repo_root = config.path.parents[3]
    raw = settings.get("chain_path", "resources/depletion/tiny_thorium_chain.yaml")
    path = Path(str(raw))
    if not path.is_absolute():
        path = repo_root / path
    return path


def _zone_names(settings: Mapping[str, Any]) -> list[str]:
    zones = settings.get("zones", ["core"])
    if not isinstance(zones, list) or not zones:
        return ["core"]
    names = []
    for index, zone in enumerate(zones, start=1):
        if isinstance(zone, Mapping):
            names.append(str(zone.get("name", f"zone_{index}")))
        else:
            names.append(str(zone))
    return names


def _initial_inventory_vector(
    config: Any,
    chain: DepletionChain,
    matrix_result: DepletionMatrixResult,
    settings: Mapping[str, Any],
) -> np.ndarray:
    nuclide_defaults = {nuclide.name: float(nuclide.initial_atoms) for nuclide in chain.nuclides}
    material_defaults = _material_inventory_defaults(config, chain.nuclide_names)
    configured = settings.get("initial_inventory_atoms", {})
    configured_mapping = configured if isinstance(configured, Mapping) else {}
    vector = np.zeros(matrix_result.size, dtype=float)
    nuclide_count = len(matrix_result.nuclide_names)
    for zone_index, zone in enumerate(matrix_result.zone_names):
        zone_inventory = configured_mapping.get(zone, configured_mapping)
        for nuclide_index, nuclide in enumerate(matrix_result.nuclide_names):
            value = None
            if isinstance(zone_inventory, Mapping):
                value = zone_inventory.get(nuclide)
            if value is None:
                value = material_defaults.get(nuclide, nuclide_defaults.get(nuclide, 0.0))
            vector[_flat_index(zone_index, nuclide_index, nuclide_count)] = max(float(value), 0.0)
    return vector


def _material_inventory_defaults(config: Any, nuclide_names: Sequence[str]) -> dict[str, float]:
    fuel = getattr(config, "materials", {}).get("fuel_salt", {})
    raw_nuclides = fuel.get("nuclides", []) if isinstance(fuel, Mapping) else []
    requested = set(nuclide_names)
    defaults: dict[str, float] = {}
    for item in raw_nuclides if isinstance(raw_nuclides, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", ""))
        if name in requested:
            defaults[name] = max(float(item.get("ao", 0.0)), 0.0) * 1.0e24
    return defaults


def _time_step_seconds(settings: Mapping[str, Any]) -> float:
    if "time_step_s" in settings:
        return max(float(settings["time_step_s"]), 0.0)
    if "matrix_timestep_days" in settings:
        return max(float(settings["matrix_timestep_days"]), 0.0) * 86400.0
    return max(float(settings.get("time_step_days", 1.0)), 0.0) * 86400.0


def _round_float(value: Any, digits: int = 10) -> float:
    return round(float(value), digits)


def _round_optional_float(value: Any, digits: int = 10) -> float | None:
    if value is None:
        return None
    return _round_float(value, digits)
