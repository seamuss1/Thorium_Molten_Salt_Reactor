from __future__ import annotations

import json
from typing import Any

import numpy as np

from thorium_reactor.accelerators import get_array_namespace, percentile_band, to_python_scalar
from thorium_reactor.capabilities import BALANCE_OF_PLANT, THERMAL_NETWORK, validate_case_capability
from thorium_reactor.chemistry import build_chemistry_assumptions
from thorium_reactor.transient import (
    _build_transient_baseline,
    _resolve_model_parameters,
    _resolve_scenario,
    _round_float,
    build_depletion_assumptions,
)


DEFAULT_TRANSIENT_SWEEP_MODEL = "reduced_order_transient_proxy_ensemble"


def run_transient_sweep_case(
    config: Any,
    bundle,
    summary: dict[str, Any],
    *,
    scenario_name: str | None = None,
    samples: int = 512,
    seed: int = 42,
    prefer_gpu: bool = False,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_case_capability(config, BALANCE_OF_PLANT)
    validate_case_capability(config, THERMAL_NETWORK)

    transient_config = config.data.get("transient", {})
    if not isinstance(transient_config, dict):
        transient_config = {}
    scenario = _resolve_scenario(transient_config, scenario_name)
    baseline = _build_transient_baseline(config, summary)
    model_parameters = _resolve_model_parameters(transient_config)
    depletion = build_depletion_assumptions(config)
    chemistry = build_chemistry_assumptions(config)
    uncertainty_model = _resolve_uncertainty_model(transient_config)

    history, metrics, backend = _integrate_transient_ensemble(
        baseline=baseline,
        scenario=scenario,
        model_parameters=model_parameters,
        depletion=depletion,
        chemistry=chemistry,
        samples=max(int(samples), 32),
        seed=int(seed),
        prefer_gpu=prefer_gpu,
        uncertainty_model=uncertainty_model,
    )

    payload = {
        "case": config.name,
        "model": DEFAULT_TRANSIENT_SWEEP_MODEL,
        "backend": backend,
        "samples": max(int(samples), 32),
        "seed": int(seed),
        "scenario": scenario,
        "baseline": baseline,
        "depletion": depletion,
        "chemistry": chemistry,
        "model_parameters": model_parameters,
        "uncertainty_model": uncertainty_model,
        "metrics": metrics,
        "history": history,
    }
    if provenance:
        payload["provenance"] = json.loads(json.dumps(provenance))

    transient_path = bundle.write_json("transient_sweep.json", payload)
    summary.setdefault("transient_sweep", {})
    summary["transient_sweep"] = {
        "status": "completed",
        "model": DEFAULT_TRANSIENT_SWEEP_MODEL,
        "backend": backend,
        "samples": max(int(samples), 32),
        "seed": int(seed),
        "scenario_name": scenario["name"],
        "duration_s": metrics["duration_s"],
        "time_step_s": metrics["time_step_s"],
        "event_count": metrics["event_count"],
        "history_path": str(transient_path),
        "peak_power_fraction_p95": metrics["peak_power_fraction_p95"],
        "peak_power_fraction_max": metrics["peak_power_fraction_max"],
        "peak_fuel_temperature_c_p95": metrics["peak_fuel_temperature_c_p95"],
        "peak_fuel_temperature_c_max": metrics["peak_fuel_temperature_c_max"],
        "final_power_fraction_p50": metrics["final_power_fraction_p50"],
        "final_power_fraction_p95": metrics["final_power_fraction_p95"],
        "final_total_reactivity_pcm_p50": metrics["final_total_reactivity_pcm_p50"],
        "final_total_reactivity_pcm_p95": metrics["final_total_reactivity_pcm_p95"],
        "peak_corrosion_index_p95": metrics["peak_corrosion_index_p95"],
    }
    summary.setdefault("metrics", {})
    summary["metrics"]["transient_sweep_peak_power_fraction_p95"] = metrics["peak_power_fraction_p95"]
    summary["metrics"]["transient_sweep_peak_fuel_temperature_c_p95"] = metrics["peak_fuel_temperature_c_p95"]
    summary["metrics"]["transient_sweep_final_reactivity_pcm_p50"] = metrics["final_total_reactivity_pcm_p50"]
    return payload


def _resolve_uncertainty_model(transient_config: dict[str, Any]) -> dict[str, float]:
    ensemble = transient_config.get("ensemble", {})
    if not isinstance(ensemble, dict):
        ensemble = {}
    uncertainties = ensemble.get("uncertainties", {})
    if not isinstance(uncertainties, dict):
        uncertainties = {}
    return {
        "event_reactivity_sigma_fraction": float(uncertainties.get("event_reactivity_sigma_fraction", 0.12)),
        "flow_sigma_fraction": float(uncertainties.get("flow_sigma_fraction", 0.08)),
        "heat_sink_sigma_fraction": float(uncertainties.get("heat_sink_sigma_fraction", 0.09)),
        "cleanup_sigma_fraction": float(uncertainties.get("cleanup_sigma_fraction", 0.12)),
        "temperature_feedback_sigma_fraction": float(
            uncertainties.get("temperature_feedback_sigma_fraction", 0.06)
        ),
        "precursor_worth_sigma_fraction": float(uncertainties.get("precursor_worth_sigma_fraction", 0.08)),
        "xenon_worth_sigma_fraction": float(uncertainties.get("xenon_worth_sigma_fraction", 0.10)),
        "sink_offset_sigma_c": float(uncertainties.get("sink_offset_sigma_c", 4.0)),
        "redox_setpoint_sigma_ev": float(uncertainties.get("redox_setpoint_sigma_ev", 0.003)),
        "impurity_ingress_sigma_fraction": float(uncertainties.get("impurity_ingress_sigma_fraction", 0.18)),
        "gas_stripping_sigma_fraction": float(uncertainties.get("gas_stripping_sigma_fraction", 0.05)),
    }


def _integrate_transient_ensemble(
    *,
    baseline: dict[str, Any],
    scenario: dict[str, Any],
    model_parameters: dict[str, float],
    depletion: dict[str, Any],
    chemistry: dict[str, Any],
    samples: int,
    seed: int,
    prefer_gpu: bool,
    uncertainty_model: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    xp, backend = get_array_namespace(prefer_gpu=prefer_gpu)
    perturbations = _build_perturbations(samples, seed, uncertainty_model)

    dt = max(float(scenario["time_step_s"]), 0.05)
    duration_s = max(float(scenario["duration_s"]), dt)
    step_count = int(round(duration_s / dt))

    controls = {
        "reactivity_pcm": 0.0,
        "flow_fraction": 1.0,
        "heat_sink_fraction": 1.0,
        "cleanup_multiplier": 1.0,
        "sink_temp_offset_c": 0.0,
        "redox_setpoint_shift_ev": 0.0,
        "impurity_ingress_multiplier": 1.0,
        "gas_stripping_efficiency": float(chemistry["gas_stripping_efficiency"]),
    }
    event_index = 0

    ones = xp.ones(samples, dtype=xp.float64)
    power_fraction = ones.copy()
    steady_fuel_temp_c = float(baseline["hot_leg_temp_c"])
    steady_graphite_temp_c = float(baseline["average_primary_temp_c"])
    steady_coolant_temp_c = float(baseline["average_primary_temp_c"])
    steady_precursor_fraction = float(baseline["initial_core_precursor_fraction"])

    fuel_temp_c = xp.full(samples, steady_fuel_temp_c, dtype=xp.float64)
    graphite_temp_c = xp.full(samples, steady_graphite_temp_c, dtype=xp.float64)
    coolant_temp_c = xp.full(samples, steady_coolant_temp_c, dtype=xp.float64)
    precursor_total = ones.copy()
    core_precursor_fraction = xp.full(samples, steady_precursor_fraction, dtype=xp.float64)
    xenon_fraction = ones.copy()
    fissile_inventory_fraction = xp.full(
        samples,
        float(depletion["initial_fissile_inventory_fraction"]),
        dtype=xp.float64,
    )
    protactinium_inventory_fraction = xp.zeros(samples, dtype=xp.float64)

    chemistry_baseline = baseline.get("chemistry", {})
    steady_redox_state_ev = float(chemistry_baseline.get("redox_state_ev", chemistry["initial_redox_state_ev"]))
    target_redox_state_ev = float(chemistry_baseline.get("target_redox_state_ev", chemistry["target_redox_state_ev"]))
    redox_state_ev = xp.full(samples, steady_redox_state_ev, dtype=xp.float64)
    impurity_fraction = xp.full(samples, float(chemistry_baseline.get("impurity_fraction", 0.0)), dtype=xp.float64)
    corrosion_index = xp.full(
        samples,
        float(chemistry_baseline.get("corrosion_index", 1.0)),
        dtype=xp.float64,
    )

    temperature_feedback_scale = xp.asarray(perturbations["temperature_feedback_scale"])
    precursor_worth_scale = xp.asarray(perturbations["precursor_worth_scale"])
    xenon_worth_scale = xp.asarray(perturbations["xenon_worth_scale"])
    event_reactivity_scale = xp.asarray(perturbations["event_reactivity_scale"])
    flow_scale = xp.asarray(perturbations["flow_scale"])
    heat_sink_scale = xp.asarray(perturbations["heat_sink_scale"])
    cleanup_scale = xp.asarray(perturbations["cleanup_scale"])
    sink_temp_bias_c = xp.asarray(perturbations["sink_temp_bias_c"])
    redox_bias_ev = xp.asarray(perturbations["redox_bias_ev"])
    impurity_ingress_scale = xp.asarray(perturbations["impurity_ingress_scale"])
    gas_stripping_scale = xp.asarray(perturbations["gas_stripping_scale"])

    fuel_temp_feedback_pcm_per_c = float(model_parameters["fuel_temperature_feedback_pcm_per_c"]) * temperature_feedback_scale
    graphite_temp_feedback_pcm_per_c = (
        float(model_parameters["graphite_temperature_feedback_pcm_per_c"]) * temperature_feedback_scale
    )
    coolant_temp_feedback_pcm_per_c = (
        float(model_parameters["coolant_temperature_feedback_pcm_per_c"]) * temperature_feedback_scale
    )
    precursor_worth_pcm = float(model_parameters["precursor_worth_pcm"]) * precursor_worth_scale
    xenon_worth_pcm_per_fraction = float(model_parameters["xenon_worth_pcm_per_fraction"]) * xenon_worth_scale

    history: list[dict[str, Any]] = []
    peak_power_fraction_max = 1.0
    peak_fuel_temperature_c_max = steady_fuel_temp_c
    peak_corrosion_index_max = float(chemistry_baseline.get("corrosion_index", 1.0))

    for step in range(step_count + 1):
        time_s = step * dt
        dt_days = dt / 86400.0
        while event_index < len(scenario["events"]) and float(scenario["events"][event_index]["time_s"]) <= time_s + 1.0e-9:
            event = scenario["events"][event_index]
            for source_key, target_key in (
                ("reactivity_step_pcm", "reactivity_pcm"),
                ("flow_fraction", "flow_fraction"),
                ("heat_sink_fraction", "heat_sink_fraction"),
                ("cleanup_multiplier", "cleanup_multiplier"),
                ("secondary_sink_temp_offset_c", "sink_temp_offset_c"),
                ("redox_setpoint_shift_ev", "redox_setpoint_shift_ev"),
                ("impurity_ingress_multiplier", "impurity_ingress_multiplier"),
                ("gas_stripping_efficiency", "gas_stripping_efficiency"),
            ):
                if source_key in event:
                    controls[target_key] = float(event[source_key])
            event_index += 1

        effective_flow_fraction = _clip(xp, controls["flow_fraction"] * flow_scale, 0.05, 1.5)
        effective_heat_sink_fraction = _clip(xp, controls["heat_sink_fraction"] * heat_sink_scale, 0.0, 1.5)
        cleanup_multiplier = _clip(xp, controls["cleanup_multiplier"] * cleanup_scale, 0.0, 2.5)
        cleanup_rate_s = (
            float(baseline["cleanup_removal_efficiency"]) * cleanup_multiplier / max(float(baseline["cleanup_turnover_s"]), 1.0)
        )
        cleanup_rate_s = cleanup_rate_s + float(depletion["volatile_removal_efficiency"]) / max(
            float(baseline["cleanup_turnover_s"]) * 6.0,
            1.0,
        )
        gas_stripping_efficiency = _clip(
            xp,
            controls["gas_stripping_efficiency"] * gas_stripping_scale,
            0.0,
            1.0,
        )

        thermal_load_ratio = power_fraction / xp.maximum(
            effective_flow_fraction * xp.maximum(effective_heat_sink_fraction, 0.15),
            0.05,
        )
        fuel_target_c = steady_fuel_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.7
        fuel_target_c = fuel_target_c + (controls["sink_temp_offset_c"] + sink_temp_bias_c) * 0.25
        graphite_target_c = steady_graphite_temp_c + (fuel_temp_c - steady_fuel_temp_c) * 0.7
        coolant_target_c = steady_coolant_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.45
        coolant_target_c = coolant_target_c + (controls["sink_temp_offset_c"] + sink_temp_bias_c) * 0.55

        fuel_temp_c = _first_order_step_array(
            xp,
            fuel_temp_c,
            fuel_target_c,
            dt,
            float(model_parameters["fuel_temperature_response_time_s"]),
        )
        graphite_temp_c = _first_order_step_array(
            xp,
            graphite_temp_c,
            graphite_target_c,
            dt,
            float(model_parameters["graphite_temperature_response_time_s"]),
        )
        coolant_temp_c = _first_order_step_array(
            xp,
            coolant_temp_c,
            coolant_target_c,
            dt,
            float(model_parameters["coolant_temperature_response_time_s"]),
        )

        target_core_fraction = _clip(
            xp,
            steady_precursor_fraction / xp.maximum(effective_flow_fraction**0.5, 0.2),
            0.05,
            0.98,
        )
        core_precursor_fraction = _first_order_step_array(
            xp,
            core_precursor_fraction,
            target_core_fraction,
            dt,
            float(model_parameters["precursor_transport_response_time_s"]),
        )
        precursor_total_target = xp.maximum(power_fraction, 0.0)
        precursor_total = _first_order_step_array(
            xp,
            precursor_total,
            precursor_total_target,
            dt,
            float(model_parameters["precursor_inventory_response_time_s"]),
        )
        precursor_total = xp.maximum(
            precursor_total - cleanup_rate_s * (1.0 - core_precursor_fraction) * precursor_total * dt,
            0.05,
        )

        xenon_target = xp.maximum(power_fraction, 0.0)
        xenon_fraction = _first_order_step_array(
            xp,
            xenon_fraction,
            xenon_target,
            dt,
            float(model_parameters["xenon_response_time_s"]),
        )
        xenon_fraction = xp.maximum(
            xenon_fraction - cleanup_rate_s * float(depletion["xenon_removal_fraction"]) * xenon_fraction * dt,
            0.0,
        )

        breeding_gain_fraction_per_day = float(depletion["breeding_gain_fraction_per_day"])
        fissile_burn_fraction_per_day_full_power = float(depletion["fissile_burn_fraction_per_day_full_power"])
        minor_actinide_sink_fraction_per_day = float(depletion["minor_actinide_sink_fraction_per_day"])
        protactinium_holdup_days = max(float(depletion["protactinium_holdup_days"]), 0.05)
        protactinium_target_fraction = breeding_gain_fraction_per_day * protactinium_holdup_days * power_fraction
        protactinium_inventory_fraction = _first_order_step_array(
            xp,
            protactinium_inventory_fraction,
            protactinium_target_fraction,
            dt,
            protactinium_holdup_days * 86400.0,
        )
        fissile_inventory_fraction = fissile_inventory_fraction + (
            breeding_gain_fraction_per_day * xp.maximum(1.0 - protactinium_inventory_fraction, 0.0)
            - fissile_burn_fraction_per_day_full_power * power_fraction
            - minor_actinide_sink_fraction_per_day
        ) * dt_days
        fissile_inventory_fraction = _clip(xp, fissile_inventory_fraction, 0.2, 1.5)

        redox_target_ev = target_redox_state_ev + controls["redox_setpoint_shift_ev"] + redox_bias_ev
        redox_target_ev = redox_target_ev + impurity_fraction * 0.03
        redox_state_ev = _first_order_step_array(
            xp,
            redox_state_ev,
            redox_target_ev,
            dt,
            max(float(chemistry["redox_control_time_days"]) * 86400.0, dt),
        )
        impurity_ingress_fraction_per_day = float(chemistry["oxidant_ingress_fraction_per_day"]) * _clip(
            xp,
            controls["impurity_ingress_multiplier"] * impurity_ingress_scale,
            0.0,
            4.0,
        )
        impurity_capture_rate_per_day = (
            float(chemistry["impurity_capture_efficiency"]) + gas_stripping_efficiency
        ) / max(float(baseline["fuel_cycle"].get("cleanup_turnover_days", 14.0)), 0.25)
        impurity_fraction = impurity_fraction + (
            impurity_ingress_fraction_per_day - impurity_capture_rate_per_day * impurity_fraction
        ) * dt_days
        impurity_fraction = _clip(xp, impurity_fraction, 0.0, 0.05)
        redox_deviation_ev = redox_state_ev - target_redox_state_ev
        corrosion_index = xp.maximum(
            0.1,
            1.0
            + xp.maximum(redox_deviation_ev, 0.0) * float(chemistry["corrosion_acceleration_per_ev"])
            + impurity_fraction * 400.0,
        )

        temperature_feedback_pcm = (
            fuel_temp_feedback_pcm_per_c * (fuel_temp_c - steady_fuel_temp_c)
            + graphite_temp_feedback_pcm_per_c * (graphite_temp_c - steady_graphite_temp_c)
            + coolant_temp_feedback_pcm_per_c * (coolant_temp_c - steady_coolant_temp_c)
        )
        precursor_feedback_pcm = precursor_worth_pcm * (core_precursor_fraction - steady_precursor_fraction)
        xenon_feedback_pcm = xenon_worth_pcm_per_fraction * (xenon_fraction - 1.0)
        depletion_feedback_pcm = (
            float(model_parameters["depletion_reactivity_worth_pcm_per_fraction"]) * (fissile_inventory_fraction - 1.0)
            + float(model_parameters["protactinium_penalty_pcm_per_fraction"]) * protactinium_inventory_fraction
        )
        chemistry_feedback_pcm = (
            float(model_parameters["chemistry_redox_worth_pcm_per_ev"]) * (redox_state_ev - steady_redox_state_ev)
            + float(model_parameters["chemistry_impurity_worth_pcm_per_fraction"]) * impurity_fraction
        )
        control_reactivity_pcm = controls["reactivity_pcm"] * event_reactivity_scale
        total_reactivity_pcm = (
            control_reactivity_pcm
            + temperature_feedback_pcm
            + precursor_feedback_pcm
            + xenon_feedback_pcm
            + depletion_feedback_pcm
            + chemistry_feedback_pcm
        )
        power_target = _clip(
            xp,
            1.0 + (total_reactivity_pcm / max(float(model_parameters["reactivity_to_power_scale_pcm"]), 1.0)),
            0.02,
            float(model_parameters["max_power_fraction"]),
        )
        power_fraction = _first_order_step_array(
            xp,
            power_fraction,
            power_target,
            dt,
            float(model_parameters["power_response_time_s"]),
        )

        power_band = percentile_band(power_fraction, xp)
        fuel_band = percentile_band(fuel_temp_c, xp)
        reactivity_band = percentile_band(total_reactivity_pcm, xp)
        corrosion_band = percentile_band(corrosion_index, xp)
        history.append(
            {
                "time_s": _round_float(time_s),
                "power_fraction_p05": _round_float(power_band[0]),
                "power_fraction_p50": _round_float(power_band[1]),
                "power_fraction_p95": _round_float(power_band[2]),
                "fuel_temp_c_p05": _round_float(fuel_band[0]),
                "fuel_temp_c_p50": _round_float(fuel_band[1]),
                "fuel_temp_c_p95": _round_float(fuel_band[2]),
                "total_reactivity_pcm_p05": _round_float(reactivity_band[0]),
                "total_reactivity_pcm_p50": _round_float(reactivity_band[1]),
                "total_reactivity_pcm_p95": _round_float(reactivity_band[2]),
                "corrosion_index_p05": _round_float(corrosion_band[0]),
                "corrosion_index_p50": _round_float(corrosion_band[1]),
                "corrosion_index_p95": _round_float(corrosion_band[2]),
            }
        )

        peak_power_fraction_max = max(peak_power_fraction_max, to_python_scalar(xp.max(power_fraction)))
        peak_fuel_temperature_c_max = max(peak_fuel_temperature_c_max, to_python_scalar(xp.max(fuel_temp_c)))
        peak_corrosion_index_max = max(peak_corrosion_index_max, to_python_scalar(xp.max(corrosion_index)))

    metrics = {
        "duration_s": _round_float(duration_s),
        "time_step_s": _round_float(dt),
        "history_points": len(history),
        "event_count": len(scenario["events"]),
        "samples": int(samples),
        "backend": backend,
        "peak_power_fraction_p95": _round_float(max(item["power_fraction_p95"] for item in history)),
        "peak_power_fraction_max": _round_float(peak_power_fraction_max),
        "final_power_fraction_p50": history[-1]["power_fraction_p50"],
        "final_power_fraction_p95": history[-1]["power_fraction_p95"],
        "peak_fuel_temperature_c_p95": _round_float(max(item["fuel_temp_c_p95"] for item in history)),
        "peak_fuel_temperature_c_max": _round_float(peak_fuel_temperature_c_max),
        "final_total_reactivity_pcm_p50": history[-1]["total_reactivity_pcm_p50"],
        "final_total_reactivity_pcm_p95": history[-1]["total_reactivity_pcm_p95"],
        "peak_corrosion_index_p95": _round_float(max(item["corrosion_index_p95"] for item in history)),
        "peak_corrosion_index_max": _round_float(peak_corrosion_index_max),
    }
    return history, metrics, backend


def _build_perturbations(samples: int, seed: int, uncertainty_model: dict[str, float]) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {
        "event_reactivity_scale": _bounded_normal(rng, samples, mean=1.0, sigma=uncertainty_model["event_reactivity_sigma_fraction"], lower=0.55, upper=1.55),
        "flow_scale": _bounded_normal(rng, samples, mean=1.0, sigma=uncertainty_model["flow_sigma_fraction"], lower=0.65, upper=1.45),
        "heat_sink_scale": _bounded_normal(rng, samples, mean=1.0, sigma=uncertainty_model["heat_sink_sigma_fraction"], lower=0.6, upper=1.45),
        "cleanup_scale": _bounded_normal(rng, samples, mean=1.0, sigma=uncertainty_model["cleanup_sigma_fraction"], lower=0.6, upper=1.6),
        "temperature_feedback_scale": _bounded_normal(
            rng,
            samples,
            mean=1.0,
            sigma=uncertainty_model["temperature_feedback_sigma_fraction"],
            lower=0.8,
            upper=1.2,
        ),
        "precursor_worth_scale": _bounded_normal(
            rng,
            samples,
            mean=1.0,
            sigma=uncertainty_model["precursor_worth_sigma_fraction"],
            lower=0.75,
            upper=1.25,
        ),
        "xenon_worth_scale": _bounded_normal(
            rng,
            samples,
            mean=1.0,
            sigma=uncertainty_model["xenon_worth_sigma_fraction"],
            lower=0.7,
            upper=1.3,
        ),
        "sink_temp_bias_c": rng.normal(0.0, uncertainty_model["sink_offset_sigma_c"], samples).astype(np.float64),
        "redox_bias_ev": rng.normal(0.0, uncertainty_model["redox_setpoint_sigma_ev"], samples).astype(np.float64),
        "impurity_ingress_scale": _bounded_normal(
            rng,
            samples,
            mean=1.0,
            sigma=uncertainty_model["impurity_ingress_sigma_fraction"],
            lower=0.5,
            upper=1.8,
        ),
        "gas_stripping_scale": _bounded_normal(
            rng,
            samples,
            mean=1.0,
            sigma=uncertainty_model["gas_stripping_sigma_fraction"],
            lower=0.85,
            upper=1.15,
        ),
    }


def _bounded_normal(
    rng: np.random.Generator,
    samples: int,
    *,
    mean: float,
    sigma: float,
    lower: float,
    upper: float,
) -> np.ndarray:
    return np.clip(rng.normal(mean, sigma, samples), lower, upper).astype(np.float64)


def _first_order_step_array(xp: Any, current_value: Any, target_value: Any, dt: float, time_constant_s: float) -> Any:
    tau = max(time_constant_s, dt)
    return current_value + (target_value - current_value) * (dt / tau)


def _clip(xp: Any, value: Any, lower: float, upper: float) -> Any:
    return xp.minimum(upper, xp.maximum(lower, value))
