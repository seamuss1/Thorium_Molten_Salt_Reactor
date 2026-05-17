from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from thorium_reactor.config import load_case_config
from thorium_reactor.paths import create_result_bundle
from thorium_reactor.transport import TransportFieldSpec, build_rz_mesh, run_transport_case, solve_transport_fields


def test_rz_mesh_has_finite_axis_cell_volumes() -> None:
    mesh = build_rz_mesh(radial_cells=4, axial_cells=5, radius_m=0.2, height_m=0.5)

    assert mesh.radial_edges_m[0] == 0.0
    assert mesh.cell_volumes_m3[:, 0].min() > 0.0
    assert np.isfinite(mesh.cell_volumes_m3).all()


def test_rkdg_decay_only_matches_analytic_solution() -> None:
    mesh = build_rz_mesh(radial_cells=1, axial_cells=1, radius_m=0.1, height_m=0.2)
    spec = TransportFieldSpec("test_decay", "verification", decay_constant_s=0.2, yield_fraction=1.0)
    initial = np.full((1, 1, 1), 3.0)

    result = solve_transport_fields(
        mesh,
        [spec],
        duration_s=0.5,
        time_step_s=0.01,
        initial_fields=initial,
        velocity_z_m_s=0.0,
        diffusion_coefficient_m2_s=0.0,
    )

    assert result.field_values[0, 0, 0] == pytest.approx(3.0 * math.exp(-0.1), rel=1e-7)


def test_rkdg_conserves_mass_without_source_sink_or_outflow() -> None:
    mesh = build_rz_mesh(radial_cells=3, axial_cells=4, radius_m=0.2, height_m=0.5)
    spec = TransportFieldSpec("passive", "verification", decay_constant_s=0.0, yield_fraction=1.0)
    initial = np.arange(12, dtype=float).reshape(1, 4, 3) + 1.0

    result = solve_transport_fields(
        mesh,
        [spec],
        duration_s=1.0,
        time_step_s=0.1,
        initial_fields=initial,
        velocity_z_m_s=0.0,
        diffusion_coefficient_m2_s=0.0,
    )

    assert result.summary["conservation_residual"] < 1e-12
    np.testing.assert_allclose(result.field_values, initial)


def test_rkdg_positivity_floor_handles_severe_flow_reduction() -> None:
    mesh = build_rz_mesh(radial_cells=2, axial_cells=5, radius_m=0.1, height_m=0.5)
    spec = TransportFieldSpec("advected", "verification", decay_constant_s=0.05, yield_fraction=1.0)
    initial = np.ones((1, 5, 2))

    result = solve_transport_fields(
        mesh,
        [spec],
        duration_s=0.1,
        time_step_s=0.005,
        initial_fields=initial,
        velocity_z_m_s=4.0,
        diffusion_coefficient_m2_s=0.0,
        positivity_floor=0.0,
    )

    assert np.min(result.field_values) >= 0.0
    assert np.isfinite(result.field_values).all()


def test_transport_case_writes_native_artifacts(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_case_config(repo_root / "configs" / "cases" / "immersed_pool_reference" / "case.yaml")
    config.data["transport_solver"] = {
        "radial_cells": 3,
        "axial_cells": 4,
        "duration_s": 0.05,
        "time_step_s": 0.01,
        "velocity_z_m_s": 0.01,
        "diffusion_coefficient_m2_s": 0.0,
    }
    bundle = create_result_bundle(tmp_path, config.name, "transport-test")
    summary = {"metrics": {}, "physics_core": {"neutronics": {"power_shape": [1.0, 1.2, 1.0]}}}

    transport = run_transport_case(config, bundle, summary)

    assert transport["status"] == "completed"
    assert transport["mesh"]["radial_cells"] == 3
    assert (bundle.root / "transport_mesh.json").exists()
    assert (bundle.root / "transport_summary.json").exists()
    assert (bundle.root / "transport_solution.npz").exists()
    assert summary["metrics"]["transport_rkdg_conservation_residual"] >= 0.0
