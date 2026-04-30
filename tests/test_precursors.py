import pytest

from thorium_reactor.precursors import (
    TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
    build_initial_precursor_state,
    normalize_precursor_groups,
    precursor_loop_segment_summary,
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
        transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
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
        transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
    )
    summary = summarize_precursor_state(updated, groups, steady_state=state["steady_state"])

    assert summary["precursor_total_fraction"] > 1.0
    assert summary["core_delayed_neutron_source_fraction"] > 1.0


def test_loop_segment_precursor_state_reports_external_segment_sources() -> None:
    groups = normalize_precursor_groups(None)
    loop_segments = [
        {"id": "hot_leg", "residence_fraction": 0.45, "cleanup_weight": 0.2},
        {"id": "heat_exchanger", "residence_fraction": 0.35, "cleanup_weight": 1.5},
        {"id": "pump_return", "residence_fraction": 0.20, "cleanup_weight": 0.4},
    ]
    state = build_initial_precursor_state(
        groups=groups,
        core_residence_time_s=1.0,
        loop_residence_time_s=8.0,
        cleanup_rate_s=1.0e-4,
        loop_segments=loop_segments,
    )

    summary = summarize_precursor_state(state, groups, steady_state=state["steady_state"])
    segments = precursor_loop_segment_summary(state, groups)

    assert summary["loop_segment_count"] == 3
    assert len(segments) == 3
    assert segments[0]["id"] == "hot_leg"
    assert sum(float(segment["residence_fraction"]) for segment in segments) == pytest.approx(1.0)
    assert 0.0 < summary["peak_loop_segment_delayed_neutron_source_fraction"] < 1.0

    updated = step_precursor_state(
        state=state,
        groups=groups,
        power_fraction=0.8,
        flow_fraction=0.55,
        dt_s=2.0,
        core_residence_time_s=1.0,
        loop_residence_time_s=8.0,
        cleanup_rate_s=1.0e-4,
        loop_segments=loop_segments,
    )
    updated_summary = summarize_precursor_state(updated, groups, steady_state=state["steady_state"])

    assert updated_summary["loop_segment_count"] == 3
    assert updated_summary["total_inventory"] > 0.0
