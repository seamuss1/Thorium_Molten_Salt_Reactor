from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from thorium_reactor.accelerators import BackendUnavailable, create_array_backend
from thorium_reactor.config import load_case_config
from thorium_reactor.depletion import (
    DepletionChain,
    DepletionNuclide,
    DepletionReaction,
    build_depletion_matrix,
    load_depletion_chain,
    step_depletion,
)
from thorium_reactor.flow.reduced_order import build_reduced_order_flow_summary
from thorium_reactor.geometry.molten_salt_reactor import build_msr_flow_summary
from thorium_reactor.physics_core import (
    _feedback_coefficients,
    _solve_multigroup_eigenvalue,
    build_deterministic_neutronics_summary,
    build_finite_volume_precursor_transport,
    build_finite_volume_thermal_hydraulics,
    build_temperature_dependent_multigroup_xs,
)
from thorium_reactor.precursors import (
    LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL,
    TWO_REGION_PRECURSOR_TRANSPORT_MODEL,
    build_initial_precursor_state,
    normalize_precursor_groups,
    step_precursor_state,
    summarize_precursor_state,
)
from thorium_reactor.transient_sweep import (
    _annotate_vectorized_precursor_baseline,
    _initialize_precursors_vectorized,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("source_format", ["memory", "openmc"])
def test_capture_branches_share_parent_loss_and_conserve_atoms(tmp_path, source_format):
    chain = DepletionChain(
        "capture",
        "test",
        "memory",
        (
            DepletionNuclide(
                "A",
                reactions=(
                    DepletionReaction("(n,gamma)", target="B", branching_ratio=0.6),
                    DepletionReaction("(n,gamma)", target="C", branching_ratio=0.4),
                    DepletionReaction("(n,2n)", target="D"),
                ),
            ),
            DepletionNuclide("B"),
            DepletionNuclide("C"),
            DepletionNuclide("D"),
        ),
    )
    if source_format == "openmc":
        path = tmp_path / "branched.xml"
        path.write_text(
            '<depletion_chain><nuclide name="A">'
            '<reaction type="(n,gamma)" target="B" branching_ratio="0.6" />'
            '<reaction type="(n,gamma)" target="C" branching_ratio="0.4" />'
            '<reaction type="(n,2n)" target="D" />'
            '</nuclide><nuclide name="B"/><nuclide name="C"/><nuclide name="D"/></depletion_chain>',
            encoding="utf-8",
        )
        chain = load_depletion_chain(path, source_format="openmc")
    matrix = build_depletion_matrix(
        chain,
        zone_names=("core", "loop"),
        reaction_rates_per_s={"core": {"A": {"(n,gamma)": 0.1, "(n,2n)": 0.02}}},
    )
    final = step_depletion(matrix, np.array([100.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0]), 10.0)
    remaining = 100.0 * math.exp(-1.2)
    reacted = 100.0 - remaining
    assert final[:4] == pytest.approx([remaining, reacted * 0.06 / 0.12, reacted * 0.04 / 0.12, reacted * 0.02 / 0.12])
    assert final[4:] == pytest.approx([100.0, 0.0, 0.0, 0.0])
    assert final.sum() == pytest.approx(200.0)


def test_capture_branches_reject_conflicting_total_rates():
    chain = DepletionChain(
        "capture",
        "test",
        "memory",
        (
            DepletionNuclide(
                "A",
                reactions=(
                    DepletionReaction("capture", target="B", branching_ratio=0.6, default_rate_per_s=0.1),
                    DepletionReaction("capture", target="B", branching_ratio=0.4, default_rate_per_s=0.2),
                ),
            ),
            DepletionNuclide("B"),
        ),
    )
    with pytest.raises(ValueError, match="must share one total rate"):
        build_depletion_matrix(chain)


@pytest.mark.parametrize("case", ["immersed_pool_reference", "tmsr_lf1_core"])
def test_physics_core_preserves_active_geometry_and_residence_time(case):
    config = load_case_config(REPO_ROOT / "configs" / "cases" / case / "case.yaml")
    reduced = build_reduced_order_flow_summary(config, build_msr_flow_summary(config), 10.0)
    th = build_finite_volume_thermal_hydraulics(config, {"flow": {"reduced_order": reduced}}, {})
    active = reduced["active_flow"]
    nodes = th["axial_nodes"]
    assert th["porous_core_model"]["core_length_m"] == pytest.approx(config.geometry["active_core_height_cm"] * 0.01)
    assert sum(node["volume_m3"] for node in nodes) == pytest.approx(active["total_salt_volume_cm3"] * 1e-6)
    assert sum(node["cell_length_m"] / node["velocity_m_s"] for node in nodes) == pytest.approx(
        active["representative_residence_time_s"],
        abs=1e-6,
    )


def test_missing_core_volume_uses_active_height_before_vessel_height():
    config = load_case_config(REPO_ROOT / "configs/cases/immersed_pool_reference/case.yaml")
    th = build_finite_volume_thermal_hydraulics(config, {}, {})
    assert th["porous_core_model"]["core_length_m"] == pytest.approx(0.52)
    assert sum(node["volume_m3"] for node in th["axial_nodes"]) == pytest.approx(0.52 * 0.0001)


def _single_group():
    return normalize_precursor_groups([{"name": "slow", "decay_constant_s": 0.01, "yield_fraction": 0.0065}])


@pytest.mark.parametrize("cleanup_rate", [0.0, 1.0])
def test_public_physics_summary_beta_counts_cleanup_as_lost_precursors(cleanup_rate):
    config = SimpleNamespace(
        data={
            "transient": {"delayed_neutron_precursor_groups": _single_group()},
            "loop_segments": [{"id": "loop", "residence_fraction": 1.0, "cleanup_weight": 1.0}],
        },
        reactor={"cleanup_turnover_days": 1 / 86400, "cleanup_removal_efficiency": cleanup_rate},
        geometry={},
    )
    summary = {
        "flow": {
            "reduced_order": {
                "active_flow": {
                    "total_salt_volume_cm3": 1e6,
                    "total_volumetric_flow_m3_s": 1.0,
                }
            }
        },
        "primary_system": {"inventory": {"fuel_salt": {"total_m3": 2.0}}, "primary_volumetric_flow_m3_s": 1.0},
    }
    th = {
        "axial_nodes": [
            {
                "index": 0,
                "power_shape": 1.0,
                "cell_length_m": 1.0,
                "velocity_m_s": 1.0,
                "flow_area_m2": 1.0,
                "volume_m3": 1.0,
                "fuel_salt_temp_c": 650.0,
                "graphite_temp_c": 650.0,
            }
        ]
    }
    settings = {"neutronics": {"group_count": 2, "deterministic_methods": ["diffusion"]}}
    transport = build_finite_volume_precursor_transport(config, summary, settings, th)
    neutronics = build_deterministic_neutronics_summary(
        config,
        summary,
        settings,
        thermal_hydraulics=th,
        precursor_transport=transport,
    )
    # Exact steady two-compartment solution: source=1, both transport rates=1.
    determinant = 1.01 * (1.01 + cleanup_rate) - 1.0
    core_decay = 0.01 * (1.01 + cleanup_rate) / determinant
    loop_decay = 0.01 / determinant
    removal = cleanup_rate / determinant
    assert transport["core_delayed_neutron_source_absolute_fraction"] == pytest.approx(core_decay, abs=5e-7)
    assert transport["loop_delayed_neutron_source_absolute_fraction"] == pytest.approx(loop_decay, abs=5e-7)
    assert transport["cleanup_loss_fraction"] == pytest.approx(removal, abs=5e-7)
    assert transport["transport_loss_fraction"] == pytest.approx(loop_decay + removal, abs=5e-7)
    assert neutronics["beta_eff"] == pytest.approx(0.0065 * core_decay, abs=5e-7)
    assert neutronics["unweighted_beta_eff"] == pytest.approx(0.0065 * core_decay, abs=5e-7)
    assert sum(cell["delayed_neutron_source_absolute_fraction"] for cell in transport["cells"]) == pytest.approx(
        1.0 - removal,
        abs=1e-6,
    )


@pytest.mark.parametrize("model", [TWO_REGION_PRECURSOR_TRANSPORT_MODEL, LOOP_SEGMENT_PRECURSOR_TRANSPORT_MODEL])
def test_transient_absolute_sources_retain_nominal_production_during_storage(model):
    groups = _single_group()
    params = {
        "core_residence_time_s": 1.0,
        "loop_residence_time_s": 1.0,
        "cleanup_rate_s": 1.0,
        "transport_model": model,
    }
    state = build_initial_precursor_state(groups=groups, **params)
    initial = summarize_precursor_state(state, groups)
    determinant = 1.01 * 2.01 - 1.0
    core_decay = 0.01 * 2.01 / determinant
    assert initial["core_delayed_neutron_source_absolute_fraction"] == pytest.approx(core_decay, abs=5e-7)
    assert initial["precursor_transport_loss_fraction"] == pytest.approx(1.0 - core_decay, abs=5e-7)
    assert state["steady_state"]["core_delayed_neutron_source_absolute_fraction"] == pytest.approx(core_decay)
    updated = step_precursor_state(
        state=state, groups=groups, power_fraction=2.0, flow_fraction=1.0, dt_s=10.0, **params
    )
    result = summarize_precursor_state(updated, groups)
    assert result["nominal_precursor_production_rate"] == 1.0
    # Storage changes total disposition, but must not renormalize absolute source.
    actual_core_decay = 0.01 * sum(updated["core_inventories"])
    assert result["core_delayed_neutron_source_absolute_fraction"] == pytest.approx(actual_core_decay, abs=5e-7)
    assert (
        result["core_delayed_neutron_source_absolute_fraction"]
        > initial["core_delayed_neutron_source_absolute_fraction"]
    )
    assert result["core_delayed_neutron_source_disposition_fraction"] + result[
        "precursor_transport_loss_fraction"
    ] == pytest.approx(1.0)


@pytest.mark.parametrize("backend_name", ["numpy", "torch-cpu"])
def test_vectorized_precursor_baseline_matches_scalar_cleanup_fractions(backend_name):
    try:
        backend = create_array_backend(backend_name, dtype="float64", seed=1)
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    groups = _single_group()
    segments = [
        {"id": "hot", "residence_fraction": 0.25, "cleanup_weight": 0.5},
        {"id": "cold", "residence_fraction": 0.75, "cleanup_weight": 1.5},
    ]
    baseline = {"core_residence_time_s": 1.0, "loop_residence_time_s": 2.0}
    cleanup = [0.2, 1.0]
    core, loop, _ = _initialize_precursors_vectorized(
        backend,
        samples=2,
        groups=groups,
        loop_segments=segments,
        flow_fraction=backend.asarray([1.0, 1.0]),
        cleanup_rate_s=backend.asarray(cleanup),
        baseline=baseline,
    )
    _annotate_vectorized_precursor_baseline(
        baseline,
        backend=backend,
        groups=groups,
        loop_segments=segments,
        core_inventory=core,
        segment_inventory=loop,
        decay_vector=backend.asarray([0.01]),
    )
    scalar = [
        summarize_precursor_state(
            build_initial_precursor_state(
                groups=groups,
                core_residence_time_s=1.0,
                loop_residence_time_s=2.0,
                cleanup_rate_s=rate,
                loop_segments=segments,
            ),
            groups,
        )
        for rate in cleanup
    ]
    for key in ("core_delayed_neutron_source_absolute_fraction", "precursor_transport_loss_fraction"):
        assert baseline[f"initial_{key}"] == pytest.approx(sum(item[key] for item in scalar) / 2, abs=1e-6)


@pytest.mark.parametrize("calibration_factor", [1.0, 2.0])
def test_feedback_coefficients_match_reactivity_difference_for_noncritical_k(calibration_factor):
    config = SimpleNamespace(geometry={})
    nodes = [{"cell_length_m": 0.1}] * 4
    options = {"group_count": 11, "temperature_grid_c": [500.0, 650.0, 800.0], "settings": {}}

    def solve(fuel, graphite):
        xs = build_temperature_dependent_multigroup_xs(
            config,
            fuel_temperature_c=fuel,
            graphite_temperature_c=graphite,
            **options,
        )
        return _solve_multigroup_eigenvalue(xs, axial_nodes=nodes, method="diffusion")

    base = solve(650.0, 650.0)
    feedback = _feedback_coefficients(
        config,
        base_result=base,
        calibration_factor=calibration_factor,
        axial_nodes=nodes,
        average_fuel_temp_c=650.0,
        average_graphite_temp_c=650.0,
        method="diffusion",
        **options,
    )
    for name, fuel, graphite in [("fuel", 700.0, 650.0), ("graphite", 650.0, 700.0), ("uniform", 700.0, 700.0)]:
        hot = solve(fuel, graphite)
        expected = (1.0 / base["k_eff"] - 1.0 / hot["k_eff"]) * 1e5 / (50.0 * calibration_factor)
        assert feedback[f"{name}_temperature_pcm_per_c"] == pytest.approx(expected, abs=5e-7)
