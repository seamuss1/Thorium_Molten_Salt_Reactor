import pytest

from thorium_reactor.precursors import (
    build_initial_precursor_state,
    normalize_precursor_groups,
    step_precursor_state,
    summarize_precursor_state,
)


def test_precursor_groups_normalize_to_relative_yields() -> None:
    groups = normalize_precursor_groups(
        [
            {"name": "slow", "decay_constant_s": 0.02, "yield_fraction": 0.25},
            {"name": "fast", "decay_constant_s": 1.2, "yield_fraction": 0.75},
        ]
    )

    assert groups[0]["relative_yield_fraction"] == 0.25
    assert groups[1]["relative_yield_fraction"] == 0.75


def test_two_region_precursor_state_tracks_core_source_and_loop_loss() -> None:
    groups = normalize_precursor_groups(None)
    state = build_initial_precursor_state(
        groups=groups,
        core_residence_time_s=1.0,
        loop_residence_time_s=7.0,
        cleanup_rate_s=0.0,
    )
    initial = summarize_precursor_state(state, groups, steady_state=state["steady_state"])

    assert initial["core_delayed_neutron_source_fraction"] == pytest.approx(1.0, abs=2.0e-6)
    assert 0.0 < initial["precursor_transport_loss_fraction"] < 1.0

    updated = step_precursor_state(
        state=state,
        groups=groups,
        power_fraction=1.2,
        flow_fraction=1.0,
        dt_s=2.0,
        core_residence_time_s=1.0,
        loop_residence_time_s=7.0,
        cleanup_rate_s=0.0,
    )
    summary = summarize_precursor_state(updated, groups, steady_state=state["steady_state"])

    assert summary["precursor_total_fraction"] > 1.0
    assert summary["core_delayed_neutron_source_fraction"] > 1.0
