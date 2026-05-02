from __future__ import annotations

from typing import Any


TWO_REGION_PRECURSOR_TRANSPORT_MODEL = "two_region_six_group_advection_decay"
LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL = "loop_segment_six_group_advection_decay"
SUPPORTED_PRECURSOR_TRANSPORT_MODELS = {
    TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
    LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL,
}
DEFAULT_PRECURSOR_TRANSPORT_MODEL = LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL
DEFAULT_DELAYED_NEUTRON_PRECURSOR_GROUPS: tuple[dict[str, float | str], ...] = (
    {"name": "group_1", "decay_constant_s": 0.0124, "yield_fraction": 0.000215},
    {"name": "group_2", "decay_constant_s": 0.0305, "yield_fraction": 0.001424},
    {"name": "group_3", "decay_constant_s": 0.111, "yield_fraction": 0.001274},
    {"name": "group_4", "decay_constant_s": 0.301, "yield_fraction": 0.002568},
    {"name": "group_5", "decay_constant_s": 1.14, "yield_fraction": 0.000748},
    {"name": "group_6", "decay_constant_s": 3.01, "yield_fraction": 0.000273},
)


def resolve_precursor_transport(transient_config: dict[str, Any]) -> dict[str, Any]:
    groups = normalize_precursor_groups(transient_config.get("delayed_neutron_precursor_groups"))
    requested_model = str(
        transient_config.get("precursor_transport_model", DEFAULT_PRECURSOR_TRANSPORT_MODEL)
    )
    transport_model = (
        requested_model
        if requested_model in SUPPORTED_PRECURSOR_TRANSPORT_MODELS
        else DEFAULT_PRECURSOR_TRANSPORT_MODEL
    )
    return {
        "precursor_transport_model": transport_model,
        "delayed_neutron_precursor_groups": groups,
        "delayed_neutron_group_count": len(groups),
        "delayed_neutron_total_yield_fraction": _round_float(
            sum(float(group["yield_fraction"]) for group in groups)
        ),
    }


def normalize_precursor_groups(raw_groups: Any | None) -> list[dict[str, float | str]]:
    groups = list(DEFAULT_DELAYED_NEUTRON_PRECURSOR_GROUPS if raw_groups is None else raw_groups)
    if not groups:
        raise ValueError("At least one delayed neutron precursor group is required.")

    normalized: list[dict[str, float | str]] = []
    total_yield = 0.0
    for index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise ValueError(f"Delayed neutron precursor group {index} must be a mapping.")
        decay_constant_s = float(group.get("decay_constant_s", 0.0))
        yield_fraction = float(group.get("yield_fraction", 0.0))
        if decay_constant_s <= 0.0:
            raise ValueError(f"Delayed neutron precursor group {index} must have positive decay_constant_s.")
        if yield_fraction < 0.0:
            raise ValueError(f"Delayed neutron precursor group {index} must have non-negative yield_fraction.")
        total_yield += yield_fraction
        normalized.append(
            {
                "name": str(group.get("name", f"group_{index}")),
                "decay_constant_s": decay_constant_s,
                "yield_fraction": yield_fraction,
            }
        )

    if total_yield <= 0.0:
        raise ValueError("Delayed neutron precursor groups must have a positive total yield_fraction.")

    for group in normalized:
        group["relative_yield_fraction"] = float(group["yield_fraction"]) / total_yield
    return normalized


def build_initial_precursor_state(
    *,
    groups: list[dict[str, float | str]],
    core_residence_time_s: float,
    loop_residence_time_s: float,
    cleanup_rate_s: float,
    transport_model: str = DEFAULT_PRECURSOR_TRANSPORT_MODEL,
    loop_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    core_tau_s = max(float(core_residence_time_s), 1.0e-6)
    loop_tau_s = max(float(loop_residence_time_s), 1.0e-6)
    cleanup_rate = max(float(cleanup_rate_s), 0.0)
    segment_specs = normalize_loop_segments(loop_segments)
    core_inventories: list[float] = []
    loop_inventories: list[float] = []
    loop_segment_inventories: list[list[float]] = []

    for group in groups:
        if transport_model == LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL:
            core_inventory, segment_inventory = _steady_state_segmented_group_inventory(
                source_rate=float(group["relative_yield_fraction"]),
                decay_constant_s=float(group["decay_constant_s"]),
                core_residence_time_s=core_tau_s,
                loop_residence_time_s=loop_tau_s,
                cleanup_rate_s=cleanup_rate,
                loop_segments=segment_specs,
            )
            loop_inventory = sum(segment_inventory)
        else:
            core_inventory, loop_inventory = _steady_state_group_inventory(
                source_rate=float(group["relative_yield_fraction"]),
                decay_constant_s=float(group["decay_constant_s"]),
                core_residence_time_s=core_tau_s,
                loop_residence_time_s=loop_tau_s,
                cleanup_rate_s=cleanup_rate,
            )
            segment_inventory = [loop_inventory]
        core_inventories.append(core_inventory)
        loop_inventories.append(loop_inventory)
        loop_segment_inventories.append(segment_inventory)

    state = {
        "core_inventories": core_inventories,
        "loop_inventories": loop_inventories,
        "loop_segment_inventories": loop_segment_inventories,
        "loop_segments": segment_specs,
        "transport_model": transport_model,
    }
    core_inventory = sum(core_inventories)
    loop_inventory = sum(loop_inventories)
    total_inventory = core_inventory + loop_inventory
    core_delayed_source = sum(
        float(group["decay_constant_s"]) * core_inventories[index]
        for index, group in enumerate(groups)
    )
    loop_delayed_source = sum(
        float(group["decay_constant_s"]) * loop_inventories[index]
        for index, group in enumerate(groups)
    )
    total_delayed_source = core_delayed_source + loop_delayed_source
    state["steady_state"] = {
        "total_inventory": total_inventory,
        "core_inventory": core_inventory,
        "core_precursor_fraction": core_inventory / max(total_inventory, 1.0e-12),
        "core_delayed_neutron_source": core_delayed_source,
        "total_delayed_neutron_source": total_delayed_source,
        "core_delayed_neutron_source_absolute_fraction": (
            core_delayed_source / max(total_delayed_source, 1.0e-12)
        ),
        "precursor_transport_loss_fraction": loop_delayed_source / max(total_delayed_source, 1.0e-12),
        "loop_segment_count": len(segment_specs) if transport_model == LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL else 1,
    }
    return state


def step_precursor_state(
    *,
    state: dict[str, Any],
    groups: list[dict[str, float | str]],
    power_fraction: float,
    flow_fraction: float,
    dt_s: float,
    core_residence_time_s: float,
    loop_residence_time_s: float,
    cleanup_rate_s: float,
    transport_model: str = DEFAULT_PRECURSOR_TRANSPORT_MODEL,
    loop_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    effective_flow_fraction = max(float(flow_fraction), 0.05)
    core_tau_s = max(float(core_residence_time_s) / effective_flow_fraction, 1.0e-6)
    loop_tau_s = max(float(loop_residence_time_s) / effective_flow_fraction, 1.0e-6)
    dt = max(float(dt_s), 0.0)
    cleanup_rate = max(float(cleanup_rate_s), 0.0)
    model = str(state.get("transport_model", transport_model))
    segment_specs = normalize_loop_segments(
        loop_segments if loop_segments is not None else state.get("loop_segments")
    )

    new_core: list[float] = []
    new_loop: list[float] = []
    new_segments: list[list[float]] = []
    for index, group in enumerate(groups):
        if model == LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL:
            prior_segments = state.get("loop_segment_inventories", [])
            if index < len(prior_segments):
                loop_segment_inventory = [float(value) for value in prior_segments[index]]
            else:
                loop_segment_inventory = [float(state["loop_inventories"][index])]
            core_inventory, segment_inventory = _step_segmented_group_inventory(
                core_inventory=float(state["core_inventories"][index]),
                loop_segment_inventory=loop_segment_inventory,
                source_rate=float(group["relative_yield_fraction"]) * max(float(power_fraction), 0.0),
                decay_constant_s=float(group["decay_constant_s"]),
                core_residence_time_s=core_tau_s,
                loop_residence_time_s=loop_tau_s,
                cleanup_rate_s=cleanup_rate,
                dt_s=dt,
                loop_segments=segment_specs,
            )
            loop_inventory = sum(segment_inventory)
        else:
            core_inventory, loop_inventory = _step_group_inventory(
                core_inventory=float(state["core_inventories"][index]),
                loop_inventory=float(state["loop_inventories"][index]),
                source_rate=float(group["relative_yield_fraction"]) * max(float(power_fraction), 0.0),
                decay_constant_s=float(group["decay_constant_s"]),
                core_residence_time_s=core_tau_s,
                loop_residence_time_s=loop_tau_s,
                cleanup_rate_s=cleanup_rate,
                dt_s=dt,
            )
            segment_inventory = [loop_inventory]
        new_core.append(core_inventory)
        new_loop.append(loop_inventory)
        new_segments.append(segment_inventory)

    return {
        "core_inventories": new_core,
        "loop_inventories": new_loop,
        "loop_segment_inventories": new_segments,
        "loop_segments": segment_specs,
        "transport_model": model,
        "steady_state": state["steady_state"],
    }


def summarize_precursor_state(
    state: dict[str, Any],
    groups: list[dict[str, float | str]],
    *,
    steady_state: dict[str, float] | None = None,
) -> dict[str, float]:
    reference = steady_state if steady_state is not None else state.get("steady_state")
    core_inventories = [float(value) for value in state["core_inventories"]]
    loop_inventories = [float(value) for value in state["loop_inventories"]]

    core_inventory = sum(core_inventories)
    loop_inventory = sum(loop_inventories)
    total_inventory = core_inventory + loop_inventory
    core_delayed_source = sum(
        float(group["decay_constant_s"]) * core_inventories[index]
        for index, group in enumerate(groups)
    )
    loop_delayed_source = sum(
        float(group["decay_constant_s"]) * loop_inventories[index]
        for index, group in enumerate(groups)
    )
    total_delayed_source = core_delayed_source + loop_delayed_source
    segment_sources = _segment_delayed_sources(state, groups)

    if reference:
        steady_total_inventory = max(float(reference["total_inventory"]), 1.0e-12)
        steady_core_source = max(float(reference["core_delayed_neutron_source"]), 1.0e-12)
    else:
        steady_total_inventory = max(total_inventory, 1.0e-12)
        steady_core_source = max(core_delayed_source, 1.0e-12)

    return {
        "core_inventory": _round_float(core_inventory),
        "loop_inventory": _round_float(loop_inventory),
        "total_inventory": _round_float(total_inventory),
        "core_precursor_fraction": _round_float(core_inventory / max(total_inventory, 1.0e-12)),
        "precursor_total_fraction": _round_float(total_inventory / steady_total_inventory),
        "core_delayed_neutron_source": _round_float(core_delayed_source),
        "loop_delayed_neutron_source": _round_float(loop_delayed_source),
        "total_delayed_neutron_source": _round_float(total_delayed_source),
        "core_delayed_neutron_source_fraction": _round_float(core_delayed_source / steady_core_source),
        "core_delayed_neutron_source_absolute_fraction": _round_float(
            core_delayed_source / max(total_delayed_source, 1.0e-12)
        ),
        "precursor_transport_loss_fraction": _round_float(
            loop_delayed_source / max(total_delayed_source, 1.0e-12)
        ),
        "loop_segment_count": int(len(state.get("loop_segments") or [])),
        "peak_loop_segment_delayed_neutron_source_fraction": _round_float(
            max(segment_sources) / max(total_delayed_source, 1.0e-12) if segment_sources else 0.0
        ),
    }


def precursor_group_summary(
    state: dict[str, Any],
    groups: list[dict[str, float | str]],
) -> list[dict[str, float | str]]:
    summary: list[dict[str, float | str]] = []
    for index, group in enumerate(groups):
        core_inventory = float(state["core_inventories"][index])
        loop_inventory = float(state["loop_inventories"][index])
        total_inventory = core_inventory + loop_inventory
        summary.append(
            {
                "name": str(group["name"]),
                "decay_constant_s": _round_float(float(group["decay_constant_s"])),
                "yield_fraction": _round_float(float(group["yield_fraction"])),
                "relative_yield_fraction": _round_float(float(group["relative_yield_fraction"])),
                "core_inventory": _round_float(core_inventory),
                "loop_inventory": _round_float(loop_inventory),
                "core_inventory_fraction": _round_float(core_inventory / max(total_inventory, 1.0e-12)),
            }
        )
    return summary


def precursor_loop_segment_summary(
    state: dict[str, Any],
    groups: list[dict[str, float | str]],
) -> list[dict[str, float | str]]:
    segments = normalize_loop_segments(state.get("loop_segments"))
    segment_sources = _segment_delayed_sources(state, groups)
    segment_inventories = _segment_inventories(state)
    total_source = sum(segment_sources)
    total_inventory = sum(segment_inventories)
    summary: list[dict[str, float | str]] = []
    for index, segment in enumerate(segments):
        summary.append(
            {
                "id": str(segment["id"]),
                "residence_fraction": _round_float(float(segment["residence_fraction"])),
                "cleanup_weight": _round_float(float(segment["cleanup_weight"])),
                "inventory": _round_float(segment_inventories[index]),
                "inventory_fraction": _round_float(segment_inventories[index] / max(total_inventory, 1.0e-12)),
                "delayed_neutron_source": _round_float(segment_sources[index]),
                "delayed_neutron_source_fraction": _round_float(
                    segment_sources[index] / max(total_source, 1.0e-12)
                ),
            }
        )
    return summary


def dominant_loop_segment_source(
    state: dict[str, Any],
    groups: list[dict[str, float | str]],
) -> dict[str, float | str]:
    segments = normalize_loop_segments(state.get("loop_segments"))
    segment_sources = _segment_delayed_sources(state, groups)
    if not segment_sources:
        return {
            "id": "external_loop",
            "delayed_neutron_source_fraction": 0.0,
            "loop_delayed_neutron_source_fraction": 0.0,
        }

    dominant_index, dominant_source = max(
        enumerate(segment_sources),
        key=lambda item: item[1],
    )
    core_source = sum(
        float(group["decay_constant_s"]) * float(state["core_inventories"][index])
        for index, group in enumerate(groups)
    )
    loop_source = sum(segment_sources)
    total_source = core_source + loop_source
    return {
        "id": str(segments[dominant_index]["id"]),
        "delayed_neutron_source_fraction": _round_float(dominant_source / max(total_source, 1.0e-12)),
        "loop_delayed_neutron_source_fraction": _round_float(dominant_source / max(loop_source, 1.0e-12)),
    }


def normalize_loop_segments(loop_segments: Any | None) -> list[dict[str, float | str]]:
    if not isinstance(loop_segments, list) or not loop_segments:
        return [{"id": "external_loop", "residence_fraction": 1.0, "cleanup_weight": 1.0}]

    raw_segments = [segment for segment in loop_segments if isinstance(segment, dict)]
    if not raw_segments:
        return [{"id": "external_loop", "residence_fraction": 1.0, "cleanup_weight": 1.0}]

    residence_values = []
    for segment in raw_segments:
        residence_fraction = segment.get("residence_fraction", segment.get("volume_fraction", 0.0))
        try:
            residence_values.append(max(float(residence_fraction), 0.0))
        except (TypeError, ValueError):
            residence_values.append(0.0)
    residence_total = sum(residence_values)
    if residence_total <= 0.0:
        residence_values = [1.0 for _ in raw_segments]
        residence_total = float(len(raw_segments))

    normalized: list[dict[str, float | str]] = []
    for index, segment in enumerate(raw_segments):
        cleanup_weight = segment.get("cleanup_weight", segment.get("cleanup_fraction", 1.0))
        try:
            cleanup_weight_value = max(float(cleanup_weight), 0.0)
        except (TypeError, ValueError):
            cleanup_weight_value = 1.0
        segment_id = str(segment.get("id") or segment.get("name") or f"loop_segment_{index + 1}")
        normalized.append(
            {
                "id": segment_id,
                "residence_fraction": residence_values[index] / residence_total,
                "cleanup_weight": cleanup_weight_value,
            }
        )
    return normalized


def _steady_state_group_inventory(
    *,
    source_rate: float,
    decay_constant_s: float,
    core_residence_time_s: float,
    loop_residence_time_s: float,
    cleanup_rate_s: float,
) -> tuple[float, float]:
    core_transport_rate_s = 1.0 / max(core_residence_time_s, 1.0e-12)
    loop_transport_rate_s = 1.0 / max(loop_residence_time_s, 1.0e-12)
    decay = max(decay_constant_s, 1.0e-12)
    cleanup = max(cleanup_rate_s, 0.0)

    core_diagonal = core_transport_rate_s + decay
    loop_diagonal = loop_transport_rate_s + decay + cleanup
    determinant = max(core_diagonal * loop_diagonal - core_transport_rate_s * loop_transport_rate_s, 1.0e-18)
    core_inventory = source_rate * loop_diagonal / determinant
    loop_inventory = source_rate * core_transport_rate_s / determinant
    return max(core_inventory, 0.0), max(loop_inventory, 0.0)


def _steady_state_segmented_group_inventory(
    *,
    source_rate: float,
    decay_constant_s: float,
    core_residence_time_s: float,
    loop_residence_time_s: float,
    cleanup_rate_s: float,
    loop_segments: list[dict[str, float | str]],
) -> tuple[float, list[float]]:
    segments = normalize_loop_segments(loop_segments)
    core_transport_rate_s = 1.0 / max(core_residence_time_s, 1.0e-12)
    decay = max(decay_constant_s, 1.0e-12)
    segment_transport_rates = _segment_transport_rates(loop_residence_time_s, segments)
    segment_diagonals = [
        segment_transport_rates[index] + decay + cleanup_rate_s * float(segment["cleanup_weight"])
        for index, segment in enumerate(segments)
    ]

    ratios: list[float] = []
    previous_ratio = core_transport_rate_s
    for index, diagonal in enumerate(segment_diagonals):
        ratio = previous_ratio / max(diagonal, 1.0e-18)
        ratios.append(ratio)
        previous_ratio = segment_transport_rates[index] * ratio

    loop_return_term = segment_transport_rates[-1] * ratios[-1] if ratios else 0.0
    core_diagonal = core_transport_rate_s + decay
    core_inventory = max(source_rate / max(core_diagonal - loop_return_term, 1.0e-18), 0.0)
    segment_inventory = [max(core_inventory * ratio, 0.0) for ratio in ratios]
    return core_inventory, segment_inventory


def _step_group_inventory(
    *,
    core_inventory: float,
    loop_inventory: float,
    source_rate: float,
    decay_constant_s: float,
    core_residence_time_s: float,
    loop_residence_time_s: float,
    cleanup_rate_s: float,
    dt_s: float,
) -> tuple[float, float]:
    core_transport_rate_s = 1.0 / max(core_residence_time_s, 1.0e-12)
    loop_transport_rate_s = 1.0 / max(loop_residence_time_s, 1.0e-12)
    decay = max(decay_constant_s, 1.0e-12)
    cleanup = max(cleanup_rate_s, 0.0)
    dt = max(dt_s, 0.0)

    matrix_a = 1.0 + dt * (core_transport_rate_s + decay)
    matrix_b = -dt * loop_transport_rate_s
    matrix_c = -dt * core_transport_rate_s
    matrix_d = 1.0 + dt * (loop_transport_rate_s + decay + cleanup)
    rhs_core = max(core_inventory, 0.0) + dt * max(source_rate, 0.0)
    rhs_loop = max(loop_inventory, 0.0)
    determinant = max(matrix_a * matrix_d - matrix_b * matrix_c, 1.0e-18)

    next_core = (rhs_core * matrix_d - matrix_b * rhs_loop) / determinant
    next_loop = (matrix_a * rhs_loop - matrix_c * rhs_core) / determinant
    return max(next_core, 0.0), max(next_loop, 0.0)


def _step_segmented_group_inventory(
    *,
    core_inventory: float,
    loop_segment_inventory: list[float],
    source_rate: float,
    decay_constant_s: float,
    core_residence_time_s: float,
    loop_residence_time_s: float,
    cleanup_rate_s: float,
    dt_s: float,
    loop_segments: list[dict[str, float | str]],
) -> tuple[float, list[float]]:
    segments = normalize_loop_segments(loop_segments)
    prior_segments = list(loop_segment_inventory[: len(segments)])
    if len(prior_segments) < len(segments):
        prior_segments.extend([0.0 for _ in range(len(segments) - len(prior_segments))])

    core_transport_rate_s = 1.0 / max(core_residence_time_s, 1.0e-12)
    decay = max(decay_constant_s, 1.0e-12)
    dt = max(dt_s, 0.0)
    segment_transport_rates = _segment_transport_rates(loop_residence_time_s, segments)
    segment_diagonals = [
        1.0
        + dt * (
            segment_transport_rates[index]
            + decay
            + cleanup_rate_s * float(segment["cleanup_weight"])
        )
        for index, segment in enumerate(segments)
    ]

    affine_constants: list[float] = []
    affine_slopes: list[float] = []
    prior_constant = max(prior_segments[0], 0.0) if prior_segments else 0.0
    prior_slope = dt * core_transport_rate_s
    for index, diagonal in enumerate(segment_diagonals):
        if index > 0:
            prior_constant = max(prior_segments[index], 0.0) + dt * segment_transport_rates[index - 1] * affine_constants[-1]
            prior_slope = dt * segment_transport_rates[index - 1] * affine_slopes[-1]
        affine_constants.append(prior_constant / max(diagonal, 1.0e-18))
        affine_slopes.append(prior_slope / max(diagonal, 1.0e-18))

    core_diagonal = 1.0 + dt * (core_transport_rate_s + decay)
    rhs_core = max(core_inventory, 0.0) + dt * max(source_rate, 0.0)
    return_rate = dt * segment_transport_rates[-1]
    next_core = (
        rhs_core + return_rate * affine_constants[-1]
    ) / max(core_diagonal - return_rate * affine_slopes[-1], 1.0e-18)
    next_segments = [
        max(affine_constants[index] + affine_slopes[index] * next_core, 0.0)
        for index in range(len(segments))
    ]
    return max(next_core, 0.0), next_segments


def _segment_transport_rates(
    loop_residence_time_s: float,
    loop_segments: list[dict[str, float | str]],
) -> list[float]:
    return [
        1.0 / max(float(segment["residence_fraction"]) * max(loop_residence_time_s, 1.0e-12), 1.0e-12)
        for segment in normalize_loop_segments(loop_segments)
    ]


def _segment_delayed_sources(state: dict[str, Any], groups: list[dict[str, float | str]]) -> list[float]:
    segment_inventories_by_group = state.get("loop_segment_inventories")
    if not isinstance(segment_inventories_by_group, list):
        return []
    segment_count = len(normalize_loop_segments(state.get("loop_segments")))
    sources = [0.0 for _ in range(segment_count)]
    for group_index, group in enumerate(groups):
        if group_index >= len(segment_inventories_by_group):
            continue
        for segment_index, inventory in enumerate(segment_inventories_by_group[group_index][:segment_count]):
            sources[segment_index] += float(group["decay_constant_s"]) * float(inventory)
    return sources


def _segment_inventories(state: dict[str, Any]) -> list[float]:
    segment_inventories_by_group = state.get("loop_segment_inventories")
    if not isinstance(segment_inventories_by_group, list):
        return []
    segment_count = len(normalize_loop_segments(state.get("loop_segments")))
    totals = [0.0 for _ in range(segment_count)]
    for group_inventories in segment_inventories_by_group:
        if not isinstance(group_inventories, list):
            continue
        for segment_index, inventory in enumerate(group_inventories[:segment_count]):
            totals[segment_index] += float(inventory)
    return totals


def _round_float(value: float) -> float:
    return round(float(value), 6)
