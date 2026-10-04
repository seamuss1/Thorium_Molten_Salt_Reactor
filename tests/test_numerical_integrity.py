"""Independent numerical regressions for issues #99 through #104."""

import copy
import itertools
import math
from pathlib import Path

import numpy as np
import pytest

from thorium_reactor.config import load_case_config
from thorium_reactor.physics_core import (
    _solve_multigroup_eigenvalue,
    _solve_ring_advection_diffusion_decay,
    build_physics_core_summary,
)
from thorium_reactor.transient import (
    _build_transient_baseline,
    _integrate_transient,
    _resolve_model_parameters,
    build_chemistry_assumptions,
    build_depletion_assumptions,
)
from thorium_reactor.transient_sweep import _integrate_transient_ensemble, _resolve_uncertainty_model
from thorium_reactor.transport import TransportFieldSpec, build_rz_mesh, solve_transport_fields

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    cfg = load_case_config(ROOT / "configs/cases/immersed_pool_reference/case.yaml")
    cfg.data["physics_core"] = {
        "neutronics": {"group_count": 2, "deterministic_methods": ["diffusion"]},
        "thermal_hydraulics": {"axial_nodes": 4},
    }
    return cfg


def transient_args(config, minimal_summary, event_time, step=1, duration=2.5):
    parameters = _resolve_model_parameters({})
    for key in parameters:
        if "worth" in key or "feedback" in key or "penalty" in key:
            parameters[key] = 0.0
    return {
        "baseline": _build_transient_baseline(config, minimal_summary),
        "scenario": {
            "duration_s": duration,
            "time_step_s": step,
            "events": [{"time_s": event_time, "reactivity_step_pcm": 100}],
        },
        "model_parameters": parameters,
        "depletion": build_depletion_assumptions(config),
        "chemistry": build_chemistry_assumptions(config),
    }


@pytest.mark.parametrize("event_time", [0, 0.35, 2.5])
@pytest.mark.parametrize("step", [1.0, 5.0, 0.3])
def test_scalar_exact_time_and_analytic_response(config, minimal_summary, event_time, step):
    args = transient_args(config, minimal_summary, event_time, step)
    history, metrics = _integrate_transient(**args)
    assert history[0]["time_s"] == 0
    assert history[0]["power_fraction"] == 1
    assert history[0]["fuel_temp_c"] == args["baseline"]["hot_leg_temp_c"]
    assert history[0]["corrosion_index"] == args["baseline"]["chemistry"]["corrosion_index"]
    assert history[-1]["time_s"] == metrics["duration_s"] == 2.5
    assert metrics["time_step_s"] == step
    assert event_time in [row["time_s"] for row in history]
    p = args["model_parameters"]
    decay_product = 1.0
    previous_time = 0.0
    for row in history:
        dt = row["time_s"] - previous_time
        if previous_time >= event_time:
            decay_product *= 1 - dt / max(p["power_response_time_s"], dt)
        expected = 1 + 100 / p["reactivity_to_power_scale_pcm"] * (1 - decay_product)
        previous_time = row["time_s"]
        assert row["power_fraction"] == pytest.approx(expected, abs=6e-7)
    assert history[-1]["control_reactivity_pcm"] == 100


@pytest.mark.parametrize("backend", ["python", "numpy", "torch-cpu", "torch-xpu"])
@pytest.mark.parametrize("event_time", [0, 0.35, 2.5])
def test_sweep_exact_boundaries_and_analytic_response(config, minimal_summary, backend, event_time):
    if backend == "torch-xpu":
        import os

        if os.environ.get("RUN_XPU_NUMERICAL_TESTS") != "1":
            pytest.skip("Set RUN_XPU_NUMERICAL_TESTS=1 on a supported XPU host.")
    if backend.startswith("torch"):
        pytest.importorskip("torch")
        from thorium_reactor.accelerators import BackendUnavailable, create_array_backend

        try:
            probe = create_array_backend(backend, dtype="float32" if backend == "torch-xpu" else "float64", seed=1)
            probe.full((1,), 1.0)
            probe.synchronize()
        except (BackendUnavailable, RuntimeError) as exc:
            pytest.skip(str(exc))
    args = transient_args(config, minimal_summary, event_time)
    uncertainty = dict.fromkeys(_resolve_uncertainty_model({}), 0.0)
    history, metrics, *_ = _integrate_transient_ensemble(
        **args,
        samples=32,
        seed=1,
        uncertainty_model=uncertainty,
        backend_name=backend,
        dtype="float32" if backend == "torch-xpu" else "float64",
    )
    assert history[0]["power_fraction_p50"] == 1
    assert history[-1]["time_s"] == 2.5
    assert event_time in [row["time_s"] for row in history]
    p = args["model_parameters"]
    decay_product = 1.0
    for left, right in itertools.pairwise(history):
        if left["time_s"] >= event_time:
            dt = right["time_s"] - left["time_s"]
            decay_product *= 1 - dt / max(p["power_response_time_s"], dt)
    expected = 1 + 100 / p["reactivity_to_power_scale_pcm"] * (1 - decay_product)
    assert history[-1]["power_fraction_p50"] == pytest.approx(expected, abs=6e-7)
    assert metrics["final_total_reactivity_pcm_p50"] == 100


def test_transient_timestep_refinement_converges(config, minimal_summary):
    errors = []
    for step in [0.2, 0.1, 0.05]:
        args = transient_args(config, minimal_summary, 0.35, step)
        history, _ = _integrate_transient(**args)
        p = args["model_parameters"]
        expected = 1 + 100 / p["reactivity_to_power_scale_pcm"] * (1 - math.exp(-2.15 / p["power_response_time_s"]))
        errors.append(abs(history[-1]["power_fraction"] - expected))
    assert errors[1] < errors[0] * 0.6
    assert errors[2] < errors[1] * 0.6


def test_prediction_independent_of_targets_and_frozen_calibration(config, minimal_summary):
    minimal_summary["bop"].update(primary_mass_flow_kg_s=37, primary_cp_kj_kgk=1.6)
    original = build_physics_core_summary(config, minimal_summary)["neutronics"]
    for target in config.validation_targets.values():
        if isinstance(target, dict) and target.get("metric") == "keff":
            target.update(min=10, max=20)
    assert build_physics_core_summary(config, minimal_summary)["neutronics"]["k_eff"] == original["k_eff"]
    config.data["physics_core"]["neutronics"]["calibration"] = {
        "factor": 2.0,
        "reference_id": "synthetic-test-reference",
        "scope": "fixed test fixture",
    }
    calibrated = build_physics_core_summary(config, minimal_summary)["neutronics"]
    assert calibrated["k_eff"] == original["k_eff"]
    assert calibrated["calibrated_k_eff"] == pytest.approx(2 * original["k_eff"], abs=2e-6)
    config.geometry["active_core_height_cm"] *= 2
    changed = build_physics_core_summary(config, minimal_summary)["neutronics"]
    assert changed["k_eff"] != original["k_eff"]
    assert changed["calibrated_k_eff"] == pytest.approx(2 * changed["k_eff"], abs=2e-6)
    assert changed["cross_sections"]["provenance"].startswith("synthetic")


def test_one_way_dependencies_are_explicit(config, minimal_summary):
    result = build_physics_core_summary(config, minimal_summary)
    assert result["coupling"]["mode"] == "one_way_screening"
    assert result["coupling"]["feedback_converged"] is False
    for key in ("neutronics_to_thermal_hydraulics", "decay_heat_to_thermal_hydraulics"):
        assert result["coupling"][key].startswith("not_implemented")
    config.data["physics_core"]["neutronics"]["cross_sections"] = {
        "absorption_cm_inv": [0.01, 0.01],
        "nu_fission_cm_inv": [0.02, 0.02],
    }
    changed = build_physics_core_summary(config, minimal_summary)
    assert changed["thermal_hydraulics"] == result["thermal_hydraulics"]
    assert changed["neutronics"]["k_eff"] != result["neutronics"]["k_eff"]


def test_manufactured_eigenproblem_and_nonconvergence(monkeypatch):
    xs = {
        "group_count": 1,
        "diffusion_coeff_cm": [1.0],
        "absorption_cm_inv": [0.1],
        "nu_fission_cm_inv": [0.2],
        "scatter_cm_inv": [[0.0]],
        "chi": [1.0],
    }
    nodes = [{"cell_length_m": 0.1} for _ in range(4)]
    result = _solve_multigroup_eigenvalue(xs, axial_nodes=nodes, method="diffusion")
    expected = 0.2 / (0.1 + 2 / 100 * (1 - math.cos(math.pi / 5)))
    assert result["k_eff"] == pytest.approx(expected, rel=1e-7)
    assert result["numerical_checks"]["relative_residual"] <= 1e-8
    monkeypatch.setattr(np.linalg, "eig", lambda a: (np.ones(4), np.eye(4)))
    with pytest.raises(RuntimeError, match="residual acceptance"):
        _solve_multigroup_eigenvalue(xs, axial_nodes=nodes, method="diffusion")


def test_two_cell_ring_analytic_solution():
    cells = [{"residence_time_s": 0.01, "source_fraction": 1}, {"residence_time_s": 100, "source_fraction": 0}]
    checks = {}
    inventory = _solve_ring_advection_diffusion_decay(
        cells, decay_constant_s=0.001, source_strength=1, diffusion_m2_s=0, cleanup_rate_s=0, diagnostics=checks
    )
    expected = np.linalg.solve([[100.001, -0.01], [-100, 0.011]], [1, 0])
    np.testing.assert_allclose(inventory, expected, rtol=1e-10)
    assert sum(inventory) == pytest.approx(1000)
    assert checks["production"] == pytest.approx(checks["decay"] + checks["removal"])


@pytest.mark.parametrize("flow", [0.0, 0.7])
def test_unequal_volume_ring_manufactured_uniform_concentration(flow):
    lengths = np.array([0.1, 0.7, 1.2])
    cells = [
        {
            "residence_time_s": length / flow if flow else math.inf,
            "length_m": length,
            "volume_m3": 2 * length,
            "face_area_m2": 2,
            "source_fraction": length / sum(lengths),
            "cleanup_weight": 1,
        }
        for length in lengths
    ]
    result = _solve_ring_advection_diffusion_decay(
        cells, decay_constant_s=0.3, source_strength=2.0, diffusion_m2_s=0.1, cleanup_rate_s=0.2
    )
    np.testing.assert_allclose(result, 2 * lengths, rtol=1e-12)


def test_ring_mesh_refinement_and_diffusion_scaling():
    errors = []
    for n in [16, 32, 64]:
        dx = 2 * math.pi / n
        x = (np.arange(n) + 0.5) * dx
        # C=2+cos(x), D=0.2, lambda=1, zero flow: source=2+1.2cos(x).
        cells = [
            {
                "residence_time_s": math.inf,
                "length_m": dx,
                "volume_m3": dx,
                "face_area_m2": 1,
                "source_fraction": (2 + 1.2 * math.cos(z)) * dx,
            }
            for z in x
        ]
        values = _solve_ring_advection_diffusion_decay(
            cells, decay_constant_s=1, source_strength=1, diffusion_m2_s=0.2, cleanup_rate_s=0
        )
        errors.append(np.max(np.abs(np.array(values) / dx - (2 + np.cos(x)))))
        scaled = copy.deepcopy(cells)
        for cell in scaled:
            cell["length_m"] *= 3
            cell["volume_m3"] *= 3
        scaled_values = _solve_ring_advection_diffusion_decay(
            scaled, decay_constant_s=1, source_strength=1, diffusion_m2_s=1.8, cleanup_rate_s=0
        )
        np.testing.assert_allclose(scaled_values, values, rtol=1e-12)
    assert errors[1] < errors[0] / 3.5
    assert errors[2] < errors[1] / 3.5


def test_ring_rejects_missing_geometry_and_failed_residual(monkeypatch):
    cells = [{"residence_time_s": 1, "source_fraction": 1}]
    kwargs = {"decay_constant_s": 1, "source_strength": 1, "cleanup_rate_s": 0}
    with pytest.raises(ValueError, match="geometry"):
        _solve_ring_advection_diffusion_decay(cells, diffusion_m2_s=1, **kwargs)
    monkeypatch.setattr(np.linalg, "solve", lambda a, b: np.zeros_like(b))
    with pytest.raises(RuntimeError, match="acceptance"):
        _solve_ring_advection_diffusion_decay(cells, diffusion_m2_s=0, **kwargs)


@pytest.mark.parametrize("amplitude", [1, 1e-20])
def test_rk_decay_balance_is_stage_consistent(amplitude):
    mesh = build_rz_mesh(radial_cells=1, axial_cells=1, radius_m=1, height_m=1)
    result = solve_transport_fields(
        mesh,
        [TransportFieldSpec("decay", "test", 1, 1)],
        duration_s=1,
        time_step_s=0.1,
        initial_fields=np.full((1, 1, 1), amplitude),
    )
    assert result.summary["conservation_residual"] < 1e-13
    assert result.summary["status"] == "completed"
    assert result.field_values.item() == pytest.approx(amplitude * math.exp(-1), rel=5e-5, abs=0)
    assert result.summary["initial_inventory"] > 0


def test_stiff_transport_subdivides_and_reports_floor_failure():
    mesh = build_rz_mesh(radial_cells=1, axial_cells=1, radius_m=1, height_m=1)
    kwargs = {"duration_s": 0.25, "time_step_s": 0.25, "initial_fields": np.ones((1, 1, 1))}
    result = solve_transport_fields(mesh, [TransportFieldSpec("decay", "test", 100, 1)], **kwargs)
    assert result.summary["timestep_subdivided"]
    assert result.summary["conservation_residual"] < 1e-13
    assert result.field_values.item() > 0
    failed = solve_transport_fields(mesh, [TransportFieldSpec("decay", "test", 100, 1)], positivity_floor=0.5, **kwargs)
    assert failed.summary["status"] == "failed"
    assert failed.summary["limiter_inventory_correction"] > 0
    assert failed.summary["corrected_conservation_residual"] < 1e-13


@pytest.mark.parametrize("velocity", [-2, 2])
def test_combined_transport_and_outflow_conservation(velocity):
    mesh = build_rz_mesh(radial_cells=3, axial_cells=5, radius_m=1, height_m=1)
    result = solve_transport_fields(
        mesh,
        [TransportFieldSpec("mixed", "test", 2, 1)],
        duration_s=0.25,
        time_step_s=0.25,
        initial_fields=np.ones((1, 5, 3)),
        source_density=np.ones((1, 5, 3)),
        diffusion_coefficient_m2_s=0.4,
        cleanup_rate_s=10,
        velocity_z_m_s=velocity,
    )
    assert result.summary["status"] == "completed"
    assert result.summary["conservation_residual"] < 1e-13
    assert result.summary["outlet_integral"] > 0
    assert result.summary["cleanup_integral"] > 0


@pytest.mark.parametrize("order", [1, 2, 3, 0.5])
def test_unsupported_polynomial_orders_rejected(order):
    mesh = build_rz_mesh(radial_cells=1, axial_cells=1, radius_m=1, height_m=1)
    with pytest.raises(ValueError, match="polynomial_order=0"):
        solve_transport_fields(
            mesh, [TransportFieldSpec("x", "test", 1, 1)], duration_s=1, time_step_s=0.1, polynomial_order=order
        )


def test_rz_smooth_advection_has_first_order_spatial_convergence():
    errors = []
    for cells in (20, 40, 80):
        mesh = build_rz_mesh(radial_cells=1, axial_cells=cells, radius_m=1, height_m=1)
        z = mesh.axial_centers_m
        initial = np.exp(-(((z - 0.4) / 0.07) ** 2))[None, :, None]
        result = solve_transport_fields(
            mesh,
            [TransportFieldSpec("passive", "test", 0, 1)],
            duration_s=0.2,
            time_step_s=0.002,
            velocity_z_m_s=0.2,
            initial_fields=initial,
        )
        exact = np.exp(-(((z - 0.44) / 0.07) ** 2))
        errors.append(float(np.mean(np.abs(result.field_values[0, :, 0] - exact))))
        assert result.summary["dof_per_cell"] == 1
        assert result.summary["polynomial_order"] == 0
        assert result.summary["model"] == "native_rz_finite_volume_ssprk3_v2"
    assert errors[1] < errors[0] * 0.7
    assert errors[2] < errors[1] * 0.7


def test_ring_refinement_preserves_short_residence_times():
    from thorium_reactor.physics_core import _precursor_cells

    nodes = [{"index": 0, "cell_length_m": 1, "velocity_m_s": 1, "flow_area_m2": 2, "volume_m3": 2, "power_shape": 1}]
    for count in (4, 40, 400):
        cells = _precursor_cells(nodes, [], count, loop_residence_time_s=0.1, loop_length_m=0.2)
        loop = cells[1:]
        assert sum(cell["residence_time_s"] for cell in loop) == pytest.approx(0.1)
        assert sum(cell["volume_m3"] for cell in loop) == pytest.approx(0.2)
        assert sum(cell["length_m"] for cell in loop) == pytest.approx(0.2)


def test_configuration_accepts_short_final_step_and_rejects_fake_order():
    from thorium_reactor.config import ConfigError, _validate_optional_transport_solver_settings
    from thorium_reactor.transient import _resolve_scenario

    scenario = _resolve_scenario({"duration_s": 2.5, "time_step_s": 5}, None)
    assert scenario["duration_s"] == 2.5
    with pytest.raises(ConfigError, match="polynomial_order must be 0"):
        _validate_optional_transport_solver_settings(ROOT / "case.yaml", {"polynomial_order": 3})


def test_transport_failed_artifact_preserves_acceptance_and_corrections(config, tmp_path):
    import json

    from thorium_reactor.paths import create_result_bundle
    from thorium_reactor.transport import run_transport_case

    config.data["transport_solver"] = {"radial_cells": 1, "axial_cells": 1, "duration_s": 0.1, "positivity_floor": 1e6}
    bundle = create_result_bundle(tmp_path, config.name, "failed-transport")
    summary = {}
    result = run_transport_case(config, bundle, summary)
    assert result["status"] == "failed"
    persisted = json.loads((bundle.root / "transport_summary.json").read_text())
    assert persisted["numerical_acceptance"]["status"] == "failed"
    assert persisted["limiter_inventory_correction"] > 0
    schema = json.loads((bundle.root / "transport_solution.schema.json").read_text())
    assert schema["schema_version"] == 2
    assert schema["dof_per_cell"] == 1
