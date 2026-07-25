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


# --- Advection/decay verification (issue #17) ---------------------------------
#
# The two-region model integrates, by backward Euler,
#   dC_core/dt = S - (1/tau_core + lambda) C_core + (1/tau_loop) C_loop
#   dC_loop/dt = (1/tau_core) C_core - (1/tau_loop + lambda + k) C_loop
# where S is the source, lambda the decay constant, tau the residence times,
# and k the cleanup rate. The tests below are manufactured-solution checks of
# conservation, decay, residence time, and flow edge cases.


def _single_group(decay_constant_s: float) -> list:
    return normalize_precursor_groups([{"name": "g", "decay_constant_s": decay_constant_s, "yield_fraction": 1.0}])


def test_precursor_inventory_is_conserved_without_decay_or_cleanup() -> None:
    # With lambda -> 0, cleanup = 0, and no source, total core+loop inventory is
    # a conserved quantity; transport only redistributes it between regions.
    groups = _single_group(1.0e-9)
    state = build_initial_precursor_state(
        groups=groups,
        core_residence_time_s=1.0,
        loop_residence_time_s=6.0,
        cleanup_rate_s=0.0,
        transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
    )
    initial_total = summarize_precursor_state(state, groups)["total_inventory"]

    for _ in range(20):
        state = step_precursor_state(
            state=state,
            groups=groups,
            power_fraction=0.0,  # no source
            flow_fraction=1.0,
            dt_s=0.5,
            core_residence_time_s=1.0,
            loop_residence_time_s=6.0,
            cleanup_rate_s=0.0,
            transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
        )
    final_total = summarize_precursor_state(state, groups)["total_inventory"]

    assert final_total == pytest.approx(initial_total, rel=1.0e-4)


def test_precursor_decay_reduces_inventory_faster_for_larger_constant() -> None:
    # Source removed (power=0): the group with the larger decay constant must
    # lose inventory faster, isolating the decay term.
    def _decayed_total(decay_constant_s: float) -> float:
        groups = _single_group(decay_constant_s)
        state = build_initial_precursor_state(
            groups=groups,
            core_residence_time_s=1.0,
            loop_residence_time_s=6.0,
            cleanup_rate_s=0.0,
            transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
        )
        start = summarize_precursor_state(state, groups)["total_inventory"]
        for _ in range(10):
            state = step_precursor_state(
                state=state,
                groups=groups,
                power_fraction=0.0,
                flow_fraction=1.0,
                dt_s=0.5,
                core_residence_time_s=1.0,
                loop_residence_time_s=6.0,
                cleanup_rate_s=0.0,
                transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
            )
        return summarize_precursor_state(state, groups)["total_inventory"] / start

    slow_retained = _decayed_total(0.02)
    fast_retained = _decayed_total(0.5)

    assert 0.0 < fast_retained < slow_retained < 1.0


def test_precursor_steady_state_is_a_fixed_point_under_matched_stepping() -> None:
    # Stepping the steady state at the conditions it was built for must leave the
    # inventory essentially unchanged: the source exactly balances decay+transport.
    groups = normalize_precursor_groups(None)
    params = {"core_residence_time_s": 1.5, "loop_residence_time_s": 7.0, "cleanup_rate_s": 0.0}
    state = build_initial_precursor_state(groups=groups, transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL, **params)
    before = summarize_precursor_state(state, groups)["total_inventory"]

    stepped = step_precursor_state(
        state=state,
        groups=groups,
        power_fraction=1.0,
        flow_fraction=1.0,
        dt_s=1.0,
        transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
        **params,
    )
    after = summarize_precursor_state(stepped, groups)["total_inventory"]

    assert after == pytest.approx(before, rel=1.0e-6)


def test_precursor_lower_flow_retains_more_delayed_source_in_core() -> None:
    # Advection edge case: reducing the flow fraction lengthens residence times,
    # so fewer precursors are swept into the loop and the transport loss falls.
    groups = normalize_precursor_groups(None)
    params = {"core_residence_time_s": 1.0, "loop_residence_time_s": 6.0, "cleanup_rate_s": 0.0}

    def _loss_at_flow(flow_fraction: float) -> float:
        state = build_initial_precursor_state(
            groups=groups, transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL, **params
        )
        stepped = step_precursor_state(
            state=state,
            groups=groups,
            power_fraction=1.0,
            flow_fraction=flow_fraction,
            dt_s=2.0,
            transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
            **params,
        )
        return summarize_precursor_state(stepped, groups)["precursor_transport_loss_fraction"]

    assert _loss_at_flow(0.1) < _loss_at_flow(1.0)


def test_precursor_longer_loop_residence_increases_transport_loss() -> None:
    # Residence-time check: a longer external-loop residence time leaves more
    # delayed-neutron source stranded outside the core at steady state.
    groups = normalize_precursor_groups(None)

    def _loss_for_loop(loop_residence_time_s: float) -> float:
        state = build_initial_precursor_state(
            groups=groups,
            core_residence_time_s=1.0,
            loop_residence_time_s=loop_residence_time_s,
            cleanup_rate_s=0.0,
            transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
        )
        return summarize_precursor_state(state, groups)["precursor_transport_loss_fraction"]

    assert _loss_for_loop(3.0) < _loss_for_loop(12.0)


def test_precursor_cleanup_removes_loop_inventory() -> None:
    # Cleanup acts only on the loop region: enabling it must lower total steady
    # inventory versus the no-cleanup baseline, isolating the source-sink term.
    groups = normalize_precursor_groups(None)

    def _total_for_cleanup(cleanup_rate_s: float) -> float:
        state = build_initial_precursor_state(
            groups=groups,
            core_residence_time_s=1.0,
            loop_residence_time_s=6.0,
            cleanup_rate_s=cleanup_rate_s,
            transport_model=TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
        )
        return summarize_precursor_state(state, groups)["total_inventory"]

    assert _total_for_cleanup(1.0e-2) < _total_for_cleanup(0.0)
