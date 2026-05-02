from __future__ import annotations

import json
import math
import random
import time
from typing import Any

from thorium_reactor.accelerators import (
    ArrayBackend,
    BackendUnavailable,
    DEFAULT_DTYPE,
    VECTOR_ARRAY_BACKENDS,
    available_backend_report,
    backend_report_for_selection,
    create_array_backend,
    resolve_runtime_backend,
    runtime_environment_report,
)
from thorium_reactor.capabilities import BALANCE_OF_PLANT, THERMAL_NETWORK, validate_case_capability
from thorium_reactor.chemistry import build_chemistry_assumptions
from thorium_reactor.literature_models import build_property_uncertainty_summary
from thorium_reactor.precursors import (
    build_initial_precursor_state,
    normalize_loop_segments,
    precursor_group_summary,
    precursor_loop_segment_summary,
    step_precursor_state,
    summarize_precursor_state,
)
from thorium_reactor.transient import (
    _build_transient_baseline,
    _precursor_cleanup_rate_s,
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
    backend: str = "auto",
    dtype: str = DEFAULT_DTYPE,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_transient_sweep_payload(
        config,
        summary,
        scenario_name=scenario_name,
        samples=samples,
        seed=seed,
        prefer_gpu=prefer_gpu,
        backend=backend,
        dtype=dtype,
        provenance=provenance,
    )
    if provenance:
        payload["provenance"] = json.loads(json.dumps(provenance))

    transient_path = bundle.write_json("transient_sweep.json", payload)
    summary["transient_sweep"] = transient_sweep_summary(payload, history_path=str(transient_path))
    summary.setdefault("metrics", {})
    summary["metrics"]["transient_sweep_peak_power_fraction_p95"] = payload["metrics"]["peak_power_fraction_p95"]
    summary["metrics"]["transient_sweep_peak_fuel_temperature_c_p95"] = payload["metrics"]["peak_fuel_temperature_c_p95"]
    summary["metrics"]["transient_sweep_final_reactivity_pcm_p50"] = payload["metrics"]["final_total_reactivity_pcm_p50"]
    return payload


def build_transient_sweep_payload(
    config: Any,
    summary: dict[str, Any],
    *,
    scenario_name: str | None = None,
    samples: int = 512,
    seed: int = 42,
    prefer_gpu: bool = False,
    backend: str = "auto",
    dtype: str = DEFAULT_DTYPE,
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
    property_uncertainty = build_property_uncertainty_summary(
        config,
        primary_delta_t_c=float(baseline.get("steady_state_delta_t_c", 0.0)),
    )
    uncertainty_model = _resolve_uncertainty_model(
        transient_config,
        property_uncertainty=property_uncertainty,
    )
    sample_count = max(int(samples), 32)
    requested_backend = "auto" if backend == "auto" else backend
    if prefer_gpu and backend == "auto":
        requested_backend = "auto"

    (
        history,
        metrics,
        backend_label,
        backend_report,
        runtime_performance,
        numerical_checks,
    ) = _integrate_transient_ensemble(
        baseline=baseline,
        scenario=scenario,
        model_parameters=model_parameters,
        depletion=depletion,
        chemistry=chemistry,
        samples=sample_count,
        seed=int(seed),
        prefer_gpu=prefer_gpu,
        uncertainty_model=uncertainty_model,
        backend_name=requested_backend,
        dtype=dtype,
    )

    payload = {
        "case": config.name,
        "model": DEFAULT_TRANSIENT_SWEEP_MODEL,
        "backend": backend_label,
        "samples": sample_count,
        "seed": int(seed),
        "scenario": scenario,
        "baseline": baseline,
        "depletion": depletion,
        "chemistry": chemistry,
        "property_uncertainty": property_uncertainty,
        "model_parameters": model_parameters,
        "uncertainty_model": uncertainty_model,
        "metrics": metrics,
        "history": history,
        "backend_report": backend_report,
        "runtime_performance": runtime_performance,
        "numerical_checks": numerical_checks,
    }
    if provenance:
        payload["provenance"] = json.loads(json.dumps(provenance))
    return payload


def transient_sweep_summary(payload: dict[str, Any], *, history_path: str) -> dict[str, Any]:
    metrics = payload["metrics"]
    property_uncertainty = payload["property_uncertainty"]
    return {
        "status": "completed",
        "model": DEFAULT_TRANSIENT_SWEEP_MODEL,
        "backend": payload["backend"],
        "samples": payload["samples"],
        "seed": payload["seed"],
        "scenario_name": payload["scenario"]["name"],
        "duration_s": metrics["duration_s"],
        "time_step_s": metrics["time_step_s"],
        "event_count": metrics["event_count"],
        "history_path": history_path,
        "peak_power_fraction_p95": metrics["peak_power_fraction_p95"],
        "peak_power_fraction_max": metrics["peak_power_fraction_max"],
        "peak_fuel_temperature_c_p95": metrics["peak_fuel_temperature_c_p95"],
        "peak_fuel_temperature_c_max": metrics["peak_fuel_temperature_c_max"],
        "final_power_fraction_p50": metrics["final_power_fraction_p50"],
        "final_power_fraction_p95": metrics["final_power_fraction_p95"],
        "final_total_reactivity_pcm_p50": metrics["final_total_reactivity_pcm_p50"],
        "final_total_reactivity_pcm_p95": metrics["final_total_reactivity_pcm_p95"],
        "final_core_delayed_neutron_source_fraction_p50": metrics[
            "final_core_delayed_neutron_source_fraction_p50"
        ],
        "minimum_core_delayed_neutron_source_fraction_p05": metrics[
            "minimum_core_delayed_neutron_source_fraction_p05"
        ],
        "peak_corrosion_index_p95": metrics["peak_corrosion_index_p95"],
        "core_outlet_temperature_uncertainty_95_c": property_uncertainty[
            "core_outlet_temperature_uncertainty_95_c"
        ],
        "backend_report": payload["backend_report"],
        "runtime_performance": payload["runtime_performance"],
        "numerical_checks": payload["numerical_checks"],
    }


def _resolve_uncertainty_model(
    transient_config: dict[str, Any],
    *,
    property_uncertainty: dict[str, Any] | None = None,
) -> dict[str, float]:
    ensemble = transient_config.get("ensemble", {})
    if not isinstance(ensemble, dict):
        ensemble = {}
    uncertainties = ensemble.get("uncertainties", {})
    if not isinstance(uncertainties, dict):
        uncertainties = {}
    property_uncertainty = property_uncertainty or {}
    flow_sigma = float(property_uncertainty.get("flow_uncertainty_95_fraction", 0.08)) / 1.96
    heat_transfer_sigma = float(property_uncertainty.get("heat_transfer_uncertainty_95_fraction", 0.09)) / 1.96
    return {
        "event_reactivity_sigma_fraction": float(uncertainties.get("event_reactivity_sigma_fraction", 0.12)),
        "flow_sigma_fraction": float(uncertainties.get("flow_sigma_fraction", max(0.08, flow_sigma))),
        "heat_sink_sigma_fraction": float(uncertainties.get("heat_sink_sigma_fraction", max(0.09, heat_transfer_sigma))),
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
    model_parameters: dict[str, Any],
    depletion: dict[str, Any],
    chemistry: dict[str, Any],
    samples: int,
    seed: int,
    prefer_gpu: bool,
    uncertainty_model: dict[str, float],
    backend_name: str,
    dtype: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    selection = resolve_runtime_backend(
        backend_name,
        samples=samples,
        dtype=dtype,
        seed=seed,
    )
    if selection.selected == "python":
        return _integrate_transient_ensemble_reference(
            baseline=baseline,
            scenario=scenario,
            model_parameters=model_parameters,
            depletion=depletion,
            chemistry=chemistry,
            samples=samples,
            seed=seed,
            prefer_gpu=prefer_gpu,
            uncertainty_model=uncertainty_model,
            backend_report=backend_report_for_selection(selection, seed=seed),
        )
    if selection.selected not in VECTOR_ARRAY_BACKENDS:
        raise BackendUnavailable(f"Backend {selection.selected} cannot run the vectorized transient sweep.")
    vector_backend = create_array_backend(selection.selected, dtype=dtype, seed=seed)
    assert isinstance(vector_backend, ArrayBackend)
    return _integrate_transient_ensemble_vectorized(
        array_backend=vector_backend,
        backend_report=backend_report_for_selection(selection, seed=seed),
        baseline=baseline,
        scenario=scenario,
        model_parameters=model_parameters,
        depletion=depletion,
        chemistry=chemistry,
        samples=samples,
        seed=seed,
        uncertainty_model=uncertainty_model,
    )


def _integrate_transient_ensemble_reference(
    *,
    baseline: dict[str, Any],
    scenario: dict[str, Any],
    model_parameters: dict[str, Any],
    depletion: dict[str, Any],
    chemistry: dict[str, Any],
    samples: int,
    seed: int,
    prefer_gpu: bool,
    uncertainty_model: dict[str, float],
    backend_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    integrate_start = time.perf_counter()
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

    fuel_temp_c = [steady_fuel_temp_c for _ in range(samples)]
    graphite_temp_c = [steady_graphite_temp_c for _ in range(samples)]
    coolant_temp_c = [steady_coolant_temp_c for _ in range(samples)]
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

    precursor_groups = model_parameters["delayed_neutron_precursor_groups"]
    precursor_states: list[dict[str, Any]] = []
    initial_precursor_summaries: list[dict[str, float]] = []
    for index in range(samples):
        initial_flow_fraction = _clip_value(flow_scale[index], 0.05, 1.5)
        initial_cleanup_rate_s = _precursor_cleanup_rate_s(
            baseline=baseline,
            depletion=depletion,
            cleanup_multiplier=_clip_value(cleanup_scale[index], 0.0, 2.5),
        )
        state = build_initial_precursor_state(
            groups=precursor_groups,
            core_residence_time_s=float(baseline["core_residence_time_s"]) / initial_flow_fraction,
            loop_residence_time_s=float(baseline["loop_residence_time_s"]) / initial_flow_fraction,
            cleanup_rate_s=initial_cleanup_rate_s,
            transport_model=str(model_parameters["precursor_transport_model"]),
            loop_segments=baseline.get("precursor_loop_segments"),
        )
        precursor_states.append(state)
        initial_precursor_summaries.append(
            summarize_precursor_state(state, precursor_groups, steady_state=state["steady_state"])
        )
    baseline["initial_core_precursor_fraction"] = _round_float(
        sum(item["core_precursor_fraction"] for item in initial_precursor_summaries) / samples
    )
    baseline["initial_core_delayed_neutron_source_absolute_fraction"] = _round_float(
        sum(item["core_delayed_neutron_source_absolute_fraction"] for item in initial_precursor_summaries) / samples
    )
    baseline["initial_precursor_transport_loss_fraction"] = _round_float(
        sum(item["precursor_transport_loss_fraction"] for item in initial_precursor_summaries) / samples
    )
    baseline["delayed_neutron_precursor_groups"] = precursor_group_summary(
        precursor_states[0],
        precursor_groups,
    )
    baseline["precursor_loop_segment_summary"] = precursor_loop_segment_summary(
        precursor_states[0],
        precursor_groups,
    )
    core_delayed_neutron_source_fraction = [
        float(item["core_delayed_neutron_source_fraction"]) for item in initial_precursor_summaries
    ]

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

        precursor_summaries: list[dict[str, float]] = []
        for index in range(samples):
            precursor_states[index] = step_precursor_state(
                state=precursor_states[index],
                groups=precursor_groups,
                power_fraction=power_fraction[index],
                flow_fraction=effective_flow_fraction[index],
                dt_s=dt,
                core_residence_time_s=float(baseline["core_residence_time_s"]),
                loop_residence_time_s=float(baseline["loop_residence_time_s"]),
                cleanup_rate_s=cleanup_rate_s[index],
                transport_model=str(model_parameters["precursor_transport_model"]),
                loop_segments=baseline.get("precursor_loop_segments"),
            )
            precursor_summaries.append(
                summarize_precursor_state(
                    precursor_states[index],
                    precursor_groups,
                    steady_state=precursor_states[index]["steady_state"],
                )
            )
        core_delayed_neutron_source_fraction = [
            float(item["core_delayed_neutron_source_fraction"]) for item in precursor_summaries
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
            precursor_worth_pcm[index] * (core_delayed_neutron_source_fraction[index] - 1.0)
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
        core_delayed_source_band = _percentile_band(core_delayed_neutron_source_fraction)
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
                "core_delayed_neutron_source_fraction_p05": _round_float(core_delayed_source_band[0]),
                "core_delayed_neutron_source_fraction_p50": _round_float(core_delayed_source_band[1]),
                "core_delayed_neutron_source_fraction_p95": _round_float(core_delayed_source_band[2]),
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
        "final_core_delayed_neutron_source_fraction_p50": history[-1][
            "core_delayed_neutron_source_fraction_p50"
        ],
        "minimum_core_delayed_neutron_source_fraction_p05": _round_float(
            min(item["core_delayed_neutron_source_fraction_p05"] for item in history)
        ),
        "peak_corrosion_index_p95": _round_float(max(item["corrosion_index_p95"] for item in history)),
        "peak_corrosion_index_max": _round_float(peak_corrosion_index_max),
    }
    if prefer_gpu:
        metrics["requested_gpu_backend"] = True
    elapsed_s = time.perf_counter() - integrate_start
    runtime_performance = {
        "elapsed_s": _round_float(elapsed_s),
        "sample_steps_per_s": _round_float((samples * max(len(history), 1)) / max(elapsed_s, 1.0e-12)),
        "backend_memory_allocated_bytes": None,
    }
    numerical_checks = _reference_numerical_checks(
        metrics=metrics,
        power_fraction=power_fraction,
        fuel_temp_c=fuel_temp_c,
        graphite_temp_c=graphite_temp_c,
        coolant_temp_c=coolant_temp_c,
        fissile_inventory_fraction=fissile_inventory_fraction,
        protactinium_inventory_fraction=protactinium_inventory_fraction,
        corrosion_index=corrosion_index,
        precursor_states=precursor_states,
    )
    metrics["numerical_health"] = numerical_checks["status"]
    metrics["runtime_elapsed_s"] = runtime_performance["elapsed_s"]
    metrics["sample_steps_per_s"] = runtime_performance["sample_steps_per_s"]
    return history, metrics, backend, backend_report, runtime_performance, numerical_checks


def _integrate_transient_ensemble_vectorized(
    *,
    array_backend: ArrayBackend,
    backend_report: dict[str, Any],
    baseline: dict[str, Any],
    scenario: dict[str, Any],
    model_parameters: dict[str, Any],
    depletion: dict[str, Any],
    chemistry: dict[str, Any],
    samples: int,
    seed: int,
    uncertainty_model: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any], str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    backend = array_backend
    perturbations = _build_backend_perturbations(backend, samples, seed, uncertainty_model)

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

    power_fraction = backend.full((samples,), 1.0)
    steady_fuel_temp_c = float(baseline["hot_leg_temp_c"])
    steady_graphite_temp_c = float(baseline["average_primary_temp_c"])
    steady_coolant_temp_c = float(baseline["average_primary_temp_c"])
    fuel_temp_c = backend.full((samples,), steady_fuel_temp_c)
    graphite_temp_c = backend.full((samples,), steady_graphite_temp_c)
    coolant_temp_c = backend.full((samples,), steady_coolant_temp_c)
    xenon_fraction = backend.full((samples,), 1.0)
    fissile_inventory_fraction = backend.full((samples,), float(depletion["initial_fissile_inventory_fraction"]))
    protactinium_inventory_fraction = backend.zeros((samples,))

    chemistry_baseline = baseline.get("chemistry", {})
    steady_redox_state_ev = float(chemistry_baseline.get("redox_state_ev", chemistry["initial_redox_state_ev"]))
    target_redox_state_ev = float(chemistry_baseline.get("target_redox_state_ev", chemistry["target_redox_state_ev"]))
    redox_state_ev = backend.full((samples,), steady_redox_state_ev)
    impurity_fraction = backend.full((samples,), float(chemistry_baseline.get("impurity_fraction", 0.0)))
    corrosion_index = backend.full((samples,), float(chemistry_baseline.get("corrosion_index", 1.0)))

    temperature_feedback_scale = perturbations["temperature_feedback_scale"]
    fuel_temp_feedback_pcm_per_c = float(model_parameters["fuel_temperature_feedback_pcm_per_c"]) * temperature_feedback_scale
    graphite_temp_feedback_pcm_per_c = float(model_parameters["graphite_temperature_feedback_pcm_per_c"]) * temperature_feedback_scale
    coolant_temp_feedback_pcm_per_c = float(model_parameters["coolant_temperature_feedback_pcm_per_c"]) * temperature_feedback_scale
    precursor_worth_pcm = float(model_parameters["precursor_worth_pcm"]) * perturbations["precursor_worth_scale"]
    xenon_worth_pcm_per_fraction = float(model_parameters["xenon_worth_pcm_per_fraction"]) * perturbations["xenon_worth_scale"]

    groups = list(model_parameters["delayed_neutron_precursor_groups"])
    loop_segments = normalize_loop_segments(baseline.get("precursor_loop_segments"))
    initial_flow_fraction = backend.clip(perturbations["flow_scale"], 0.05, 1.5)
    initial_cleanup_rate_s = (
        float(baseline["cleanup_removal_efficiency"])
        * backend.clip(perturbations["cleanup_scale"], 0.0, 2.5)
        / max(float(baseline["cleanup_turnover_s"]), 1.0)
        + float(depletion["volatile_removal_efficiency"])
        / max(float(baseline["cleanup_turnover_s"]) * 6.0, 1.0)
    )
    core_inventory, segment_inventory, steady_core_source = _initialize_precursors_vectorized(
        backend,
        samples=samples,
        groups=groups,
        loop_segments=loop_segments,
        flow_fraction=initial_flow_fraction,
        cleanup_rate_s=initial_cleanup_rate_s,
        baseline=baseline,
    )
    decay_vector = backend.asarray([float(group["decay_constant_s"]) for group in groups])
    _annotate_vectorized_precursor_baseline(
        baseline,
        backend=backend,
        groups=groups,
        loop_segments=loop_segments,
        core_inventory=core_inventory,
        segment_inventory=segment_inventory,
        decay_vector=decay_vector,
    )

    history: list[dict[str, Any]] = []
    peak_power_fraction_max = 1.0
    peak_fuel_temperature_c_max = steady_fuel_temp_c
    peak_corrosion_index_max = float(chemistry_baseline.get("corrosion_index", 1.0))
    final_total_reactivity_pcm = backend.zeros((samples,))
    core_delayed_neutron_source_fraction = _core_delayed_source(backend, core_inventory, decay_vector) / backend.maximum(
        steady_core_source,
        1.0e-12,
    )

    backend.synchronize()
    integrate_start = time.perf_counter()
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

        effective_flow_fraction = backend.clip(controls["flow_fraction"] * perturbations["flow_scale"], 0.05, 1.5)
        effective_heat_sink_fraction = backend.clip(controls["heat_sink_fraction"] * perturbations["heat_sink_scale"], 0.0, 1.5)
        cleanup_multiplier = backend.clip(controls["cleanup_multiplier"] * perturbations["cleanup_scale"], 0.0, 2.5)
        cleanup_rate_s = (
            float(baseline["cleanup_removal_efficiency"]) * cleanup_multiplier / max(float(baseline["cleanup_turnover_s"]), 1.0)
            + float(depletion["volatile_removal_efficiency"]) / max(float(baseline["cleanup_turnover_s"]) * 6.0, 1.0)
        )
        gas_stripping_efficiency = backend.clip(controls["gas_stripping_efficiency"] * perturbations["gas_stripping_scale"], 0.0, 1.0)

        thermal_load_ratio = power_fraction / backend.maximum(
            effective_flow_fraction * backend.maximum(effective_heat_sink_fraction, 0.15),
            0.05,
        )
        sink_bias = controls["sink_temp_offset_c"] + perturbations["sink_temp_bias_c"]
        fuel_target_c = steady_fuel_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.7 + sink_bias * 0.25
        graphite_target_c = steady_graphite_temp_c + (fuel_temp_c - steady_fuel_temp_c) * 0.7
        coolant_target_c = steady_coolant_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.45 + sink_bias * 0.55
        fuel_temp_c = _first_order_step_backend(backend, fuel_temp_c, fuel_target_c, dt, float(model_parameters["fuel_temperature_response_time_s"]))
        graphite_temp_c = _first_order_step_backend(backend, graphite_temp_c, graphite_target_c, dt, float(model_parameters["graphite_temperature_response_time_s"]))
        coolant_temp_c = _first_order_step_backend(backend, coolant_temp_c, coolant_target_c, dt, float(model_parameters["coolant_temperature_response_time_s"]))

        core_inventory, segment_inventory = _step_precursors_vectorized(
            backend,
            core_inventory=core_inventory,
            segment_inventory=segment_inventory,
            groups=groups,
            loop_segments=loop_segments,
            power_fraction=power_fraction,
            flow_fraction=effective_flow_fraction,
            cleanup_rate_s=cleanup_rate_s,
            dt=dt,
            baseline=baseline,
        )
        core_delayed_neutron_source_fraction = _core_delayed_source(backend, core_inventory, decay_vector) / backend.maximum(
            steady_core_source,
            1.0e-12,
        )

        xenon_fraction = _first_order_step_backend(backend, xenon_fraction, backend.maximum(power_fraction, 0.0), dt, float(model_parameters["xenon_response_time_s"]))
        xenon_fraction = backend.maximum(
            xenon_fraction - cleanup_rate_s * float(depletion["xenon_removal_fraction"]) * xenon_fraction * dt,
            0.0,
        )

        breeding_gain_fraction_per_day = float(depletion["breeding_gain_fraction_per_day"])
        fissile_burn_fraction_per_day_full_power = float(depletion["fissile_burn_fraction_per_day_full_power"])
        minor_actinide_sink_fraction_per_day = float(depletion["minor_actinide_sink_fraction_per_day"])
        protactinium_holdup_days = max(float(depletion["protactinium_holdup_days"]), 0.05)
        protactinium_target_fraction = breeding_gain_fraction_per_day * protactinium_holdup_days * power_fraction
        protactinium_inventory_fraction = _first_order_step_backend(
            backend,
            protactinium_inventory_fraction,
            protactinium_target_fraction,
            dt,
            protactinium_holdup_days * 86400.0,
        )
        fissile_inventory_fraction = backend.clip(
            fissile_inventory_fraction
            + (
                breeding_gain_fraction_per_day * backend.maximum(1.0 - protactinium_inventory_fraction, 0.0)
                - fissile_burn_fraction_per_day_full_power * power_fraction
                - minor_actinide_sink_fraction_per_day
            )
            * dt_days,
            0.2,
            1.5,
        )

        redox_target_ev = target_redox_state_ev + controls["redox_setpoint_shift_ev"] + perturbations["redox_bias_ev"] + impurity_fraction * 0.03
        redox_state_ev = _first_order_step_backend(
            backend,
            redox_state_ev,
            redox_target_ev,
            dt,
            max(float(chemistry["redox_control_time_days"]) * 86400.0, dt),
        )
        impurity_ingress_fraction_per_day = float(chemistry["oxidant_ingress_fraction_per_day"]) * backend.clip(
            controls["impurity_ingress_multiplier"] * perturbations["impurity_ingress_scale"],
            0.0,
            4.0,
        )
        impurity_capture_rate_per_day = (
            float(chemistry["impurity_capture_efficiency"]) + gas_stripping_efficiency
        ) / max(float(baseline["fuel_cycle"].get("cleanup_turnover_days", 14.0)), 0.25)
        impurity_fraction = backend.clip(
            impurity_fraction
            + (impurity_ingress_fraction_per_day - impurity_capture_rate_per_day * impurity_fraction) * dt_days,
            0.0,
            0.05,
        )
        corrosion_index = backend.maximum(
            1.0
            + backend.maximum(redox_state_ev - target_redox_state_ev, 0.0) * float(chemistry["corrosion_acceleration_per_ev"])
            + impurity_fraction * 400.0,
            0.1,
        )

        temperature_feedback_pcm = (
            fuel_temp_feedback_pcm_per_c * (fuel_temp_c - steady_fuel_temp_c)
            + graphite_temp_feedback_pcm_per_c * (graphite_temp_c - steady_graphite_temp_c)
            + coolant_temp_feedback_pcm_per_c * (coolant_temp_c - steady_coolant_temp_c)
        )
        precursor_feedback_pcm = precursor_worth_pcm * (core_delayed_neutron_source_fraction - 1.0)
        xenon_feedback_pcm = xenon_worth_pcm_per_fraction * (xenon_fraction - 1.0)
        depletion_feedback_pcm = (
            float(model_parameters["depletion_reactivity_worth_pcm_per_fraction"]) * (fissile_inventory_fraction - 1.0)
            + float(model_parameters["protactinium_penalty_pcm_per_fraction"]) * protactinium_inventory_fraction
        )
        chemistry_feedback_pcm = (
            float(model_parameters["chemistry_redox_worth_pcm_per_ev"]) * (redox_state_ev - steady_redox_state_ev)
            + float(model_parameters["chemistry_impurity_worth_pcm_per_fraction"]) * impurity_fraction
        )
        final_total_reactivity_pcm = (
            controls["reactivity_pcm"] * perturbations["event_reactivity_scale"]
            + temperature_feedback_pcm
            + precursor_feedback_pcm
            + xenon_feedback_pcm
            + depletion_feedback_pcm
            + chemistry_feedback_pcm
        )
        power_target = backend.clip(
            1.0 + (final_total_reactivity_pcm / max(float(model_parameters["reactivity_to_power_scale_pcm"]), 1.0)),
            0.02,
            float(model_parameters["max_power_fraction"]),
        )
        power_fraction = _first_order_step_backend(backend, power_fraction, power_target, dt, float(model_parameters["power_response_time_s"]))

        power_band = backend.percentiles(power_fraction, (0.05, 0.50, 0.95))
        fuel_band = backend.percentiles(fuel_temp_c, (0.05, 0.50, 0.95))
        reactivity_band = backend.percentiles(final_total_reactivity_pcm, (0.05, 0.50, 0.95))
        corrosion_band = backend.percentiles(corrosion_index, (0.05, 0.50, 0.95))
        core_delayed_source_band = backend.percentiles(core_delayed_neutron_source_fraction, (0.05, 0.50, 0.95))
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
                "core_delayed_neutron_source_fraction_p05": _round_float(core_delayed_source_band[0]),
                "core_delayed_neutron_source_fraction_p50": _round_float(core_delayed_source_band[1]),
                "core_delayed_neutron_source_fraction_p95": _round_float(core_delayed_source_band[2]),
                "corrosion_index_p05": _round_float(corrosion_band[0]),
                "corrosion_index_p50": _round_float(corrosion_band[1]),
                "corrosion_index_p95": _round_float(corrosion_band[2]),
            }
        )
        peak_power_fraction_max = max(peak_power_fraction_max, backend.max_scalar(power_fraction))
        peak_fuel_temperature_c_max = max(peak_fuel_temperature_c_max, backend.max_scalar(fuel_temp_c))
        peak_corrosion_index_max = max(peak_corrosion_index_max, backend.max_scalar(corrosion_index))

    backend.synchronize()
    elapsed_s = time.perf_counter() - integrate_start
    metrics = {
        "duration_s": _round_float(duration_s),
        "time_step_s": _round_float(dt),
        "history_points": len(history),
        "event_count": len(scenario["events"]),
        "samples": int(samples),
        "backend": backend.name,
        "peak_power_fraction_p95": _round_float(max(item["power_fraction_p95"] for item in history)),
        "peak_power_fraction_max": _round_float(peak_power_fraction_max),
        "final_power_fraction_p50": history[-1]["power_fraction_p50"],
        "final_power_fraction_p95": history[-1]["power_fraction_p95"],
        "peak_fuel_temperature_c_p95": _round_float(max(item["fuel_temp_c_p95"] for item in history)),
        "peak_fuel_temperature_c_max": _round_float(peak_fuel_temperature_c_max),
        "final_total_reactivity_pcm_p50": history[-1]["total_reactivity_pcm_p50"],
        "final_total_reactivity_pcm_p95": history[-1]["total_reactivity_pcm_p95"],
        "final_core_delayed_neutron_source_fraction_p50": history[-1]["core_delayed_neutron_source_fraction_p50"],
        "minimum_core_delayed_neutron_source_fraction_p05": _round_float(
            min(item["core_delayed_neutron_source_fraction_p05"] for item in history)
        ),
        "peak_corrosion_index_p95": _round_float(max(item["corrosion_index_p95"] for item in history)),
        "peak_corrosion_index_max": _round_float(peak_corrosion_index_max),
    }
    runtime_performance = {
        "elapsed_s": _round_float(elapsed_s),
        "sample_steps_per_s": _round_float((samples * (step_count + 1)) / max(elapsed_s, 1.0e-12)),
        "backend_memory_allocated_bytes": backend.memory_allocated_bytes(),
    }
    numerical_checks = _vector_numerical_checks(
        backend,
        metrics=metrics,
        power_fraction=power_fraction,
        fuel_temp_c=fuel_temp_c,
        graphite_temp_c=graphite_temp_c,
        coolant_temp_c=coolant_temp_c,
        fissile_inventory_fraction=fissile_inventory_fraction,
        protactinium_inventory_fraction=protactinium_inventory_fraction,
        corrosion_index=corrosion_index,
        core_inventory=core_inventory,
        segment_inventory=segment_inventory,
    )
    metrics["numerical_health"] = numerical_checks["status"]
    metrics["runtime_elapsed_s"] = runtime_performance["elapsed_s"]
    metrics["sample_steps_per_s"] = runtime_performance["sample_steps_per_s"]
    return history, metrics, backend.name, backend_report, runtime_performance, numerical_checks


def _build_backend_perturbations(
    backend: ArrayBackend,
    samples: int,
    seed: int,
    uncertainty_model: dict[str, float],
) -> dict[str, Any]:
    # Use the reference RNG stream so CPU/GPU benchmarks compare the same ensemble.
    raw = _build_perturbations(samples, seed, uncertainty_model)
    return {key: backend.asarray(value) for key, value in raw.items()}


def _first_order_step_backend(backend: ArrayBackend, current: Any, target: Any, dt: float, tau_s: float) -> Any:
    return current + (target - current) * (dt / max(float(tau_s), dt))


def _core_delayed_source(backend: ArrayBackend, core_inventory: Any, decay: Any) -> Any:
    return backend.sum(core_inventory * decay, axis=1)


def _initialize_precursors_vectorized(
    backend: ArrayBackend,
    *,
    samples: int,
    groups: list[dict[str, float | str]],
    loop_segments: list[dict[str, float | str]],
    flow_fraction: Any,
    cleanup_rate_s: Any,
    baseline: dict[str, Any],
) -> tuple[Any, Any, Any]:
    core_transport_rate_s = flow_fraction / max(float(baseline["core_residence_time_s"]), 1.0e-12)
    residence = [float(segment["residence_fraction"]) for segment in loop_segments]
    cleanup_weights = [float(segment["cleanup_weight"]) for segment in loop_segments]
    core_inventory_by_group = []
    segment_inventory_by_group = []
    for group in groups:
        decay = max(float(group["decay_constant_s"]), 1.0e-12)
        source_rate = float(group["relative_yield_fraction"])
        ratios = []
        segment_rates = []
        previous_ratio = core_transport_rate_s
        for index, residence_fraction in enumerate(residence):
            rate = flow_fraction / max(residence_fraction * float(baseline["loop_residence_time_s"]), 1.0e-12)
            diagonal = rate + decay + cleanup_rate_s * cleanup_weights[index]
            ratio = previous_ratio / backend.maximum(diagonal, 1.0e-18)
            ratios.append(ratio)
            segment_rates.append(rate)
            previous_ratio = rate * ratio
        loop_return_term = segment_rates[-1] * ratios[-1]
        core_diagonal = core_transport_rate_s + decay
        core_inventory = backend.maximum(source_rate / backend.maximum(core_diagonal - loop_return_term, 1.0e-18), 0.0)
        segment_inventory = backend.stack([backend.maximum(core_inventory * ratio, 0.0) for ratio in ratios], axis=1)
        core_inventory_by_group.append(core_inventory)
        segment_inventory_by_group.append(segment_inventory)
    core_inventory_matrix = backend.stack(core_inventory_by_group, axis=1)
    segment_inventory_tensor = backend.stack(segment_inventory_by_group, axis=1)
    decay_vector = backend.asarray([float(group["decay_constant_s"]) for group in groups])
    return core_inventory_matrix, segment_inventory_tensor, _core_delayed_source(backend, core_inventory_matrix, decay_vector)


def _step_precursors_vectorized(
    backend: ArrayBackend,
    *,
    core_inventory: Any,
    segment_inventory: Any,
    groups: list[dict[str, float | str]],
    loop_segments: list[dict[str, float | str]],
    power_fraction: Any,
    flow_fraction: Any,
    cleanup_rate_s: Any,
    dt: float,
    baseline: dict[str, Any],
) -> tuple[Any, Any]:
    residence = [float(segment["residence_fraction"]) for segment in loop_segments]
    cleanup_weights = [float(segment["cleanup_weight"]) for segment in loop_segments]
    core_transport_rate_s = flow_fraction / max(float(baseline["core_residence_time_s"]), 1.0e-12)
    next_core_by_group = []
    next_segments_by_group = []
    for group_index, group in enumerate(groups):
        decay = max(float(group["decay_constant_s"]), 1.0e-12)
        source_rate = float(group["relative_yield_fraction"]) * backend.maximum(power_fraction, 0.0)
        affine_constants = []
        affine_slopes = []
        segment_rates = []
        prior_constant = backend.maximum(segment_inventory[:, group_index, 0], 0.0)
        prior_slope = dt * core_transport_rate_s
        for segment_index, residence_fraction in enumerate(residence):
            segment_rate = flow_fraction / max(residence_fraction * float(baseline["loop_residence_time_s"]), 1.0e-12)
            segment_rates.append(segment_rate)
            diagonal = 1.0 + dt * (segment_rate + decay + cleanup_rate_s * cleanup_weights[segment_index])
            if segment_index > 0:
                previous_rate = segment_rates[segment_index - 1]
                prior_constant = backend.maximum(segment_inventory[:, group_index, segment_index], 0.0) + dt * previous_rate * affine_constants[-1]
                prior_slope = dt * previous_rate * affine_slopes[-1]
            affine_constants.append(prior_constant / backend.maximum(diagonal, 1.0e-18))
            affine_slopes.append(prior_slope / backend.maximum(diagonal, 1.0e-18))
        core_diagonal = 1.0 + dt * (core_transport_rate_s + decay)
        rhs_core = backend.maximum(core_inventory[:, group_index], 0.0) + dt * source_rate
        return_rate = dt * segment_rates[-1]
        next_core = (rhs_core + return_rate * affine_constants[-1]) / backend.maximum(
            core_diagonal - return_rate * affine_slopes[-1],
            1.0e-18,
        )
        next_core = backend.maximum(next_core, 0.0)
        next_segments = backend.stack(
            [backend.maximum(affine_constants[index] + affine_slopes[index] * next_core, 0.0) for index in range(len(loop_segments))],
            axis=1,
        )
        next_core_by_group.append(next_core)
        next_segments_by_group.append(next_segments)
    return backend.stack(next_core_by_group, axis=1), backend.stack(next_segments_by_group, axis=1)


def _annotate_vectorized_precursor_baseline(
    baseline: dict[str, Any],
    *,
    backend: ArrayBackend,
    groups: list[dict[str, float | str]],
    loop_segments: list[dict[str, float | str]],
    core_inventory: Any,
    segment_inventory: Any,
    decay_vector: Any,
) -> None:
    total_core = backend.sum(core_inventory, axis=1)
    total_segment = backend.sum(backend.sum(segment_inventory, axis=2), axis=1)
    total_inventory = total_core + total_segment
    core_source = _core_delayed_source(backend, core_inventory, decay_vector)
    segment_sources_by_group = segment_inventory * decay_vector[None, :, None]
    loop_source = backend.sum(backend.sum(segment_sources_by_group, axis=2), axis=1)
    total_source = core_source + loop_source
    sample_count = max(int(getattr(total_core, "shape", [1])[0]), 1)
    baseline["initial_core_precursor_fraction"] = _round_float(backend.scalar(backend.sum(total_core / backend.maximum(total_inventory, 1.0e-12))) / sample_count)
    baseline["initial_core_delayed_neutron_source_absolute_fraction"] = _round_float(
        backend.scalar(backend.sum(core_source / backend.maximum(total_source, 1.0e-12))) / sample_count
    )
    baseline["initial_precursor_transport_loss_fraction"] = _round_float(
        backend.scalar(backend.sum(loop_source / backend.maximum(total_source, 1.0e-12))) / sample_count
    )
    group_summaries = []
    for group_index, group in enumerate(groups):
        core_mean = backend.scalar(backend.sum(core_inventory[:, group_index])) / sample_count
        loop_group = backend.sum(segment_inventory[:, group_index, :], axis=1)
        loop_mean = backend.scalar(backend.sum(loop_group)) / sample_count
        group_total = core_mean + loop_mean
        group_summaries.append(
            {
                "name": str(group["name"]),
                "decay_constant_s": _round_float(float(group["decay_constant_s"])),
                "yield_fraction": _round_float(float(group["yield_fraction"])),
                "relative_yield_fraction": _round_float(float(group["relative_yield_fraction"])),
                "core_inventory": _round_float(core_mean),
                "loop_inventory": _round_float(loop_mean),
                "core_inventory_fraction": _round_float(core_mean / max(group_total, 1.0e-12)),
            }
        )
    baseline["delayed_neutron_precursor_groups"] = group_summaries
    segment_summaries = []
    total_segment_inventory = backend.scalar(backend.sum(segment_inventory))
    total_loop_delayed_source = backend.scalar(backend.sum(segment_sources_by_group))
    for segment_index, segment in enumerate(loop_segments):
        inventory = backend.scalar(backend.sum(segment_inventory[:, :, segment_index]))
        source = backend.scalar(backend.sum(segment_sources_by_group[:, :, segment_index]))
        segment_summaries.append(
            {
                "id": str(segment["id"]),
                "residence_fraction": _round_float(float(segment["residence_fraction"])),
                "cleanup_weight": _round_float(float(segment["cleanup_weight"])),
                "inventory": _round_float(inventory / sample_count),
                "inventory_fraction": _round_float(inventory / max(total_segment_inventory, 1.0e-12)),
                "delayed_neutron_source": _round_float(source / sample_count),
                "delayed_neutron_source_fraction": _round_float(source / max(total_loop_delayed_source, 1.0e-12)),
            }
        )
    baseline["precursor_loop_segment_summary"] = segment_summaries


def _reference_numerical_checks(
    *,
    metrics: dict[str, Any],
    power_fraction: list[float],
    fuel_temp_c: list[float],
    graphite_temp_c: list[float],
    coolant_temp_c: list[float],
    fissile_inventory_fraction: list[float],
    protactinium_inventory_fraction: list[float],
    corrosion_index: list[float],
    precursor_states: list[dict[str, Any]],
) -> dict[str, Any]:
    precursor_min = min(
        [
            *(float(value) for state in precursor_states for value in state["core_inventories"]),
            *(float(value) for state in precursor_states for group in state["loop_segment_inventories"] for value in group),
        ]
    )
    checks = {
        "finite_metrics": all(math.isfinite(float(value)) for value in metrics.values() if isinstance(value, (int, float))),
        "power_fraction_bounded": min(power_fraction) >= 0.0 and max(power_fraction) <= 10.0,
        "temperatures_bounded": min(fuel_temp_c + graphite_temp_c + coolant_temp_c) > 0.0 and max(fuel_temp_c + graphite_temp_c + coolant_temp_c) < 2500.0,
        "precursor_inventory_non_negative": precursor_min >= -1.0e-9,
        "fissile_inventory_non_negative": min(fissile_inventory_fraction) >= 0.0,
        "protactinium_inventory_non_negative": min(protactinium_inventory_fraction) >= 0.0,
        "corrosion_index_positive": min(corrosion_index) > 0.0,
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "failures": [key for key, value in checks.items() if not value],
    }


def _vector_numerical_checks(
    backend: ArrayBackend,
    *,
    metrics: dict[str, Any],
    power_fraction: Any,
    fuel_temp_c: Any,
    graphite_temp_c: Any,
    coolant_temp_c: Any,
    fissile_inventory_fraction: Any,
    protactinium_inventory_fraction: Any,
    corrosion_index: Any,
    core_inventory: Any,
    segment_inventory: Any,
) -> dict[str, Any]:
    min_temp = min(backend.min_scalar(fuel_temp_c), backend.min_scalar(graphite_temp_c), backend.min_scalar(coolant_temp_c))
    max_temp = max(backend.max_scalar(fuel_temp_c), backend.max_scalar(graphite_temp_c), backend.max_scalar(coolant_temp_c))
    checks = {
        "finite_metrics": all(math.isfinite(float(value)) for value in metrics.values() if isinstance(value, (int, float))),
        "power_fraction_bounded": backend.min_scalar(power_fraction) >= 0.0 and backend.max_scalar(power_fraction) <= 10.0,
        "temperatures_bounded": min_temp > 0.0 and max_temp < 2500.0,
        "precursor_inventory_non_negative": min(backend.min_scalar(core_inventory), backend.min_scalar(segment_inventory)) >= -1.0e-7,
        "fissile_inventory_non_negative": backend.min_scalar(fissile_inventory_fraction) >= 0.0,
        "protactinium_inventory_non_negative": backend.min_scalar(protactinium_inventory_fraction) >= 0.0,
        "corrosion_index_positive": backend.min_scalar(corrosion_index) > 0.0,
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "failures": [key for key, value in checks.items() if not value],
    }


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
