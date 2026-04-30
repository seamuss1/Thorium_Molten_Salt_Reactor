from __future__ import annotations

from typing import Any


DEFAULT_PRECURSOR_TRANSPORT_MODEL = "two_region_six_group_advection_decay"
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
    return {
        "precursor_transport_model": str(
            transient_config.get("precursor_transport_model", DEFAULT_PRECURSOR_TRANSPORT_MODEL)
        ),
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
) -> dict[str, Any]:
    core_tau_s = max(float(core_residence_time_s), 1.0e-6)
    loop_tau_s = max(float(loop_residence_time_s), 1.0e-6)
    cleanup_rate = max(float(cleanup_rate_s), 0.0)
    core_inventories: list[float] = []
    loop_inventories: list[float] = []

    for group in groups:
        core_inventory, loop_inventory = _steady_state_group_inventory(
            source_rate=float(group["relative_yield_fraction"]),
            decay_constant_s=float(group["decay_constant_s"]),
            core_residence_time_s=core_tau_s,
            loop_residence_time_s=loop_tau_s,
            cleanup_rate_s=cleanup_rate,
        )
        core_inventories.append(core_inventory)
        loop_inventories.append(loop_inventory)

    state = {
        "core_inventories": core_inventories,
        "loop_inventories": loop_inventories,
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
) -> dict[str, Any]:
    effective_flow_fraction = max(float(flow_fraction), 0.05)
    core_tau_s = max(float(core_residence_time_s) / effective_flow_fraction, 1.0e-6)
    loop_tau_s = max(float(loop_residence_time_s) / effective_flow_fraction, 1.0e-6)
    dt = max(float(dt_s), 0.0)
    cleanup_rate = max(float(cleanup_rate_s), 0.0)

    new_core: list[float] = []
    new_loop: list[float] = []
    for index, group in enumerate(groups):
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
        new_core.append(core_inventory)
        new_loop.append(loop_inventory)

    return {
        "core_inventories": new_core,
        "loop_inventories": new_loop,
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


def _round_float(value: float) -> float:
    return round(float(value), 6)
