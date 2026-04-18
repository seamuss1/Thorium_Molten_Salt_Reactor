from __future__ import annotations

import json
import math
import random
from typing import Any

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
    backend = "python"
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

    power_fraction = [1.0 for _ in range(samples)]
    steady_fuel_temp_c = float(baseline["hot_leg_temp_c"])
    steady_graphite_temp_c = float(baseline["average_primary_temp_c"])
    steady_coolant_temp_c = float(baseline["average_primary_temp_c"])
    steady_precursor_fraction = float(baseline["initial_core_precursor_fraction"])

    fuel_temp_c = [steady_fuel_temp_c for _ in range(samples)]
    graphite_temp_c = [steady_graphite_temp_c for _ in range(samples)]
    coolant_temp_c = [steady_coolant_temp_c for _ in range(samples)]
    precursor_total = [1.0 for _ in range(samples)]
    core_precursor_fraction = [steady_precursor_fraction for _ in range(samples)]
    xenon_fraction = [1.0 for _ in range(samples)]
    fissile_inventory_fraction = [float(depletion["initial_fissile_inventory_fraction"]) for _ in range(samples)]
    protactinium_inventory_fraction = [0.0 for _ in range(samples)]

    chemistry_baseline = baseline.get("chemistry", {})
    steady_redox_state_ev = float(chemistry_baseline.get("redox_state_ev", chemistry["initial_redox_state_ev"]))
    target_redox_state_ev = float(chemistry_baseline.get("target_redox_state_ev", chemistry["target_redox_state_ev"]))
    redox_state_ev = [steady_redox_state_ev for _ in range(samples)]
    impurity_fraction = [float(chemistry_baseline.get("impurity_fraction", 0.0)) for _ in range(samples)]
    corrosion_index = [float(chemistry_baseline.get("corrosion_index", 1.0)) for _ in range(samples)]

    temperature_feedback_scale = perturbations["temperature_feedback_scale"]
    precursor_worth_scale = perturbations["precursor_worth_scale"]
    xenon_worth_scale = perturbations["xenon_worth_scale"]
    event_reactivity_scale = perturbations["event_reactivity_scale"]
    flow_scale = perturbations["flow_scale"]
    heat_sink_scale = perturbations["heat_sink_scale"]
    cleanup_scale = perturbations["cleanup_scale"]
    sink_temp_bias_c = perturbations["sink_temp_bias_c"]
    redox_bias_ev = perturbations["redox_bias_ev"]
    impurity_ingress_scale = perturbations["impurity_ingress_scale"]
    gas_stripping_scale = perturbations["gas_stripping_scale"]

    fuel_temp_feedback_pcm_per_c = [
        float(model_parameters["fuel_temperature_feedback_pcm_per_c"]) * scale
        for scale in temperature_feedback_scale
    ]
    graphite_temp_feedback_pcm_per_c = [
        float(model_parameters["graphite_temperature_feedback_pcm_per_c"]) * scale
        for scale in temperature_feedback_scale
    ]
    coolant_temp_feedback_pcm_per_c = [
        float(model_parameters["coolant_temperature_feedback_pcm_per_c"]) * scale
        for scale in temperature_feedback_scale
    ]
    precursor_worth_pcm = [
        float(model_parameters["precursor_worth_pcm"]) * scale
        for scale in precursor_worth_scale
    ]
    xenon_worth_pcm_per_fraction = [
        float(model_parameters["xenon_worth_pcm_per_fraction"]) * scale
        for scale in xenon_worth_scale
    ]

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

        effective_flow_fraction = [_clip_value(controls["flow_fraction"] * scale, 0.05, 1.5) for scale in flow_scale]
        effective_heat_sink_fraction = [_clip_value(controls["heat_sink_fraction"] * scale, 0.0, 1.5) for scale in heat_sink_scale]
        cleanup_multiplier = [_clip_value(controls["cleanup_multiplier"] * scale, 0.0, 2.5) for scale in cleanup_scale]
        cleanup_rate_s = [
            float(baseline["cleanup_removal_efficiency"]) * multiplier / max(float(baseline["cleanup_turnover_s"]), 1.0)
            + float(depletion["volatile_removal_efficiency"]) / max(float(baseline["cleanup_turnover_s"]) * 6.0, 1.0)
            for multiplier in cleanup_multiplier
        ]
        gas_stripping_efficiency = [
            _clip_value(controls["gas_stripping_efficiency"] * scale, 0.0, 1.0)
            for scale in gas_stripping_scale
        ]

        thermal_load_ratio = [
            power_fraction[index]
            / max(effective_flow_fraction[index] * max(effective_heat_sink_fraction[index], 0.15), 0.05)
            for index in range(samples)
        ]
        fuel_target_c = [
            steady_fuel_temp_c
            + (thermal_load_ratio[index] - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.7
            + (controls["sink_temp_offset_c"] + sink_temp_bias_c[index]) * 0.25
            for index in range(samples)
        ]
        graphite_target_c = [
            steady_graphite_temp_c + (fuel_temp_c[index] - steady_fuel_temp_c) * 0.7
            for index in range(samples)
        ]
        coolant_target_c = [
            steady_coolant_temp_c
            + (thermal_load_ratio[index] - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.45
            + (controls["sink_temp_offset_c"] + sink_temp_bias_c[index]) * 0.55
            for index in range(samples)
        ]

        fuel_temp_c = _first_order_step_array(
            fuel_temp_c,
            fuel_target_c,
            dt,
            float(model_parameters["fuel_temperature_response_time_s"]),
        )
        graphite_temp_c = _first_order_step_array(
            graphite_temp_c,
            graphite_target_c,
            dt,
            float(model_parameters["graphite_temperature_response_time_s"]),
        )
        coolant_temp_c = _first_order_step_array(
            coolant_temp_c,
            coolant_target_c,
            dt,
            float(model_parameters["coolant_temperature_response_time_s"]),
        )

        target_core_fraction = [
            _clip_value(steady_precursor_fraction / max(effective_flow_fraction[index] ** 0.5, 0.2), 0.05, 0.98)
            for index in range(samples)
        ]
        core_precursor_fraction = _first_order_step_array(
            core_precursor_fraction,
            target_core_fraction,
            dt,
            float(model_parameters["precursor_transport_response_time_s"]),
        )
        precursor_total_target = [max(value, 0.0) for value in power_fraction]
        precursor_total = _first_order_step_array(
            precursor_total,
            precursor_total_target,
            dt,
            float(model_parameters["precursor_inventory_response_time_s"]),
        )
        precursor_total = [
            max(
                precursor_total[index]
                - cleanup_rate_s[index] * (1.0 - core_precursor_fraction[index]) * precursor_total[index] * dt,
                0.05,
            )
            for index in range(samples)
        ]

        xenon_target = [max(value, 0.0) for value in power_fraction]
        xenon_fraction = _first_order_step_array(
            xenon_fraction,
            xenon_target,
            dt,
            float(model_parameters["xenon_response_time_s"]),
        )
        xenon_fraction = [
            max(
                xenon_fraction[index]
                - cleanup_rate_s[index] * float(depletion["xenon_removal_fraction"]) * xenon_fraction[index] * dt,
                0.0,
            )
            for index in range(samples)
        ]

        breeding_gain_fraction_per_day = float(depletion["breeding_gain_fraction_per_day"])
        fissile_burn_fraction_per_day_full_power = float(depletion["fissile_burn_fraction_per_day_full_power"])
        minor_actinide_sink_fraction_per_day = float(depletion["minor_actinide_sink_fraction_per_day"])
        protactinium_holdup_days = max(float(depletion["protactinium_holdup_days"]), 0.05)
        protactinium_target_fraction = [
            breeding_gain_fraction_per_day * protactinium_holdup_days * value
            for value in power_fraction
        ]
        protactinium_inventory_fraction = _first_order_step_array(
            protactinium_inventory_fraction,
            protactinium_target_fraction,
            dt,
            protactinium_holdup_days * 86400.0,
        )
        fissile_inventory_fraction = [
            _clip_value(
                fissile_inventory_fraction[index]
                + (
                    breeding_gain_fraction_per_day * max(1.0 - protactinium_inventory_fraction[index], 0.0)
                    - fissile_burn_fraction_per_day_full_power * power_fraction[index]
                    - minor_actinide_sink_fraction_per_day
                )
                * dt_days,
                0.2,
                1.5,
            )
            for index in range(samples)
        ]

        redox_target_ev = [
            target_redox_state_ev
            + controls["redox_setpoint_shift_ev"]
            + redox_bias_ev[index]
            + impurity_fraction[index] * 0.03
            for index in range(samples)
        ]
        redox_state_ev = _first_order_step_array(
            redox_state_ev,
            redox_target_ev,
            dt,
            max(float(chemistry["redox_control_time_days"]) * 86400.0, dt),
        )
        impurity_ingress_fraction_per_day = [
            float(chemistry["oxidant_ingress_fraction_per_day"])
            * _clip_value(controls["impurity_ingress_multiplier"] * scale, 0.0, 4.0)
            for scale in impurity_ingress_scale
        ]
        impurity_capture_rate_per_day = [
            (float(chemistry["impurity_capture_efficiency"]) + gas_stripping_efficiency[index])
            / max(float(baseline["fuel_cycle"].get("cleanup_turnover_days", 14.0)), 0.25)
            for index in range(samples)
        ]
        impurity_fraction = [
            _clip_value(
                impurity_fraction[index]
                + (
                    impurity_ingress_fraction_per_day[index]
                    - impurity_capture_rate_per_day[index] * impurity_fraction[index]
                )
                * dt_days,
                0.0,
                0.05,
            )
            for index in range(samples)
        ]
        corrosion_index = [
            max(
                0.1,
                1.0
                + max(redox_state_ev[index] - target_redox_state_ev, 0.0) * float(chemistry["corrosion_acceleration_per_ev"])
                + impurity_fraction[index] * 400.0,
            )
            for index in range(samples)
        ]

        temperature_feedback_pcm = [
            fuel_temp_feedback_pcm_per_c[index] * (fuel_temp_c[index] - steady_fuel_temp_c)
            + graphite_temp_feedback_pcm_per_c[index] * (graphite_temp_c[index] - steady_graphite_temp_c)
            + coolant_temp_feedback_pcm_per_c[index] * (coolant_temp_c[index] - steady_coolant_temp_c)
            for index in range(samples)
        ]
        precursor_feedback_pcm = [
            precursor_worth_pcm[index] * (core_precursor_fraction[index] - steady_precursor_fraction)
            for index in range(samples)
        ]
        xenon_feedback_pcm = [
            xenon_worth_pcm_per_fraction[index] * (xenon_fraction[index] - 1.0)
            for index in range(samples)
        ]
        depletion_feedback_pcm = [
            float(model_parameters["depletion_reactivity_worth_pcm_per_fraction"]) * (fissile_inventory_fraction[index] - 1.0)
            + float(model_parameters["protactinium_penalty_pcm_per_fraction"]) * protactinium_inventory_fraction[index]
            for index in range(samples)
        ]
        chemistry_feedback_pcm = [
            float(model_parameters["chemistry_redox_worth_pcm_per_ev"]) * (redox_state_ev[index] - steady_redox_state_ev)
            + float(model_parameters["chemistry_impurity_worth_pcm_per_fraction"]) * impurity_fraction[index]
            for index in range(samples)
        ]
        control_reactivity_pcm = [
            controls["reactivity_pcm"] * event_reactivity_scale[index]
            for index in range(samples)
        ]
        total_reactivity_pcm = [
            control_reactivity_pcm[index]
            + temperature_feedback_pcm[index]
            + precursor_feedback_pcm[index]
            + xenon_feedback_pcm[index]
            + depletion_feedback_pcm[index]
            + chemistry_feedback_pcm[index]
            for index in range(samples)
        ]
        power_target = [
            _clip_value(
                1.0 + (total_reactivity_pcm[index] / max(float(model_parameters["reactivity_to_power_scale_pcm"]), 1.0)),
                0.02,
                float(model_parameters["max_power_fraction"]),
            )
            for index in range(samples)
        ]
        power_fraction = _first_order_step_array(
            power_fraction,
            power_target,
            dt,
            float(model_parameters["power_response_time_s"]),
        )

        power_band = _percentile_band(power_fraction)
        fuel_band = _percentile_band(fuel_temp_c)
        reactivity_band = _percentile_band(total_reactivity_pcm)
        corrosion_band = _percentile_band(corrosion_index)
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

        peak_power_fraction_max = max(peak_power_fraction_max, max(power_fraction))
        peak_fuel_temperature_c_max = max(peak_fuel_temperature_c_max, max(fuel_temp_c))
        peak_corrosion_index_max = max(peak_corrosion_index_max, max(corrosion_index))

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
    if prefer_gpu:
        metrics["requested_gpu_backend"] = True
    return history, metrics, backend


def _build_perturbations(samples: int, seed: int, uncertainty_model: dict[str, float]) -> dict[str, list[float]]:
    rng = random.Random(seed)
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
        "sink_temp_bias_c": [float(rng.gauss(0.0, uncertainty_model["sink_offset_sigma_c"])) for _ in range(samples)],
        "redox_bias_ev": [float(rng.gauss(0.0, uncertainty_model["redox_setpoint_sigma_ev"])) for _ in range(samples)],
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
    rng: random.Random,
    samples: int,
    *,
    mean: float,
    sigma: float,
    lower: float,
    upper: float,
) -> list[float]:
    return [_clip_value(float(rng.gauss(mean, sigma)), lower, upper) for _ in range(samples)]


def _first_order_step_array(current_value: list[float], target_value: list[float], dt: float, time_constant_s: float) -> list[float]:
    tau = max(time_constant_s, dt)
    fraction = dt / tau
    return [
        current_value[index] + (target_value[index] - current_value[index]) * fraction
        for index in range(len(current_value))
    ]


def _clip_value(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _percentile_band(values: list[float]) -> tuple[float, float, float]:
    ordered = sorted(float(value) for value in values)
    return (
        _percentile(ordered, 0.05),
        _percentile(ordered, 0.50),
        _percentile(ordered, 0.95),
    )


def _percentile(ordered: list[float], quantile: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
