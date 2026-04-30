from __future__ import annotations

import json
from typing import Any

from thorium_reactor.chemistry import (
    build_chemistry_assumptions,
    build_steady_state_chemistry_summary,
    corrosion_index_from_state,
)
from thorium_reactor.capabilities import BALANCE_OF_PLANT, THERMAL_NETWORK, validate_case_capability
from thorium_reactor.precursors import (
    build_initial_precursor_state,
    precursor_group_summary,
    resolve_precursor_transport,
    step_precursor_state,
    summarize_precursor_state,
)


DEFAULT_TRANSIENT_MODEL = "reduced_order_transient_proxy"


def build_depletion_assumptions(config: Any) -> dict[str, Any]:
    depletion = config.data.get("depletion", {})
    if not isinstance(depletion, dict):
        depletion = {}
    reactor = config.reactor
    return {
        "chain": str(depletion.get("chain", "simple_thorium_cleanup_proxy")),
        "cleanup_scenario": str(depletion.get("cleanup_scenario", "baseline")),
        "volatile_removal_efficiency": _round_float(
            float(depletion.get("volatile_removal_efficiency", reactor.get("cleanup_removal_efficiency", 0.75)))
        ),
        "xenon_removal_fraction": _round_float(
            float(depletion.get("xenon_removal_fraction", reactor.get("xenon_removal_fraction", 0.9)))
        ),
        "protactinium_holdup_days": _round_float(
            float(depletion.get("protactinium_holdup_days", reactor.get("protactinium_holdup_days", 2.0)))
        ),
        "initial_fissile_inventory_fraction": _round_float(
            float(depletion.get("initial_fissile_inventory_fraction", 1.0))
        ),
        "fissile_burn_fraction_per_day_full_power": _round_float(
            float(depletion.get("fissile_burn_fraction_per_day_full_power", 8.0e-4))
        ),
        "breeding_gain_fraction_per_day": _round_float(
            float(depletion.get("breeding_gain_fraction_per_day", 5.5e-4))
        ),
        "minor_actinide_sink_fraction_per_day": _round_float(
            float(depletion.get("minor_actinide_sink_fraction_per_day", 1.2e-4))
        ),
    }


def run_transient_case(
    config: Any,
    bundle,
    summary: dict[str, Any],
    *,
    scenario_name: str | None = None,
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

    history, metrics = _integrate_transient(
        baseline=baseline,
        scenario=scenario,
        model_parameters=model_parameters,
        depletion=depletion,
        chemistry=chemistry,
    )

    payload = {
        "case": config.name,
        "model": DEFAULT_TRANSIENT_MODEL,
        "scenario": scenario,
        "baseline": baseline,
        "depletion": depletion,
        "chemistry": chemistry,
        "model_parameters": model_parameters,
        "metrics": metrics,
        "history": history,
    }
    if provenance:
        payload["provenance"] = json.loads(json.dumps(provenance))

    transient_path = bundle.write_json("transient.json", payload)
    summary.setdefault("transient", {})
    summary["transient"] = {
        "status": "completed",
        "model": DEFAULT_TRANSIENT_MODEL,
        "scenario_name": scenario["name"],
        "duration_s": metrics["duration_s"],
        "time_step_s": metrics["time_step_s"],
        "event_count": metrics["event_count"],
        "history_path": str(transient_path),
        "peak_power_fraction": metrics["peak_power_fraction"],
        "final_power_fraction": metrics["final_power_fraction"],
        "peak_fuel_temperature_c": metrics["peak_fuel_temperature_c"],
        "peak_graphite_temperature_c": metrics["peak_graphite_temperature_c"],
        "peak_coolant_temperature_c": metrics["peak_coolant_temperature_c"],
        "minimum_precursor_core_fraction": metrics["minimum_precursor_core_fraction"],
        "minimum_core_delayed_neutron_source_fraction": metrics["minimum_core_delayed_neutron_source_fraction"],
        "final_core_delayed_neutron_source_fraction": metrics["final_core_delayed_neutron_source_fraction"],
        "final_precursor_transport_loss_fraction": metrics["final_precursor_transport_loss_fraction"],
        "final_total_reactivity_pcm": metrics["final_total_reactivity_pcm"],
        "depletion_chain": depletion["chain"],
        "cleanup_scenario": depletion["cleanup_scenario"],
        "final_fissile_inventory_fraction": metrics["final_fissile_inventory_fraction"],
        "peak_protactinium_inventory_fraction": metrics["peak_protactinium_inventory_fraction"],
        "final_redox_state_ev": metrics["final_redox_state_ev"],
        "peak_corrosion_index": metrics["peak_corrosion_index"],
    }
    summary.setdefault("metrics", {})
    summary["metrics"]["transient_peak_power_fraction"] = metrics["peak_power_fraction"]
    summary["metrics"]["transient_peak_fuel_temperature_c"] = metrics["peak_fuel_temperature_c"]
    summary["metrics"]["transient_final_reactivity_pcm"] = metrics["final_total_reactivity_pcm"]
    summary["metrics"]["transient_final_core_delayed_neutron_source_fraction"] = metrics[
        "final_core_delayed_neutron_source_fraction"
    ]
    summary["metrics"]["transient_final_redox_state_ev"] = metrics["final_redox_state_ev"]
    summary["metrics"]["transient_final_fissile_inventory_fraction"] = metrics["final_fissile_inventory_fraction"]
    return payload


def _resolve_scenario(transient_config: dict[str, Any], scenario_name: str | None) -> dict[str, Any]:
    scenarios = transient_config.get("scenarios", [])
    if not isinstance(scenarios, list):
        scenarios = []
    selected = None
    if scenarios:
        if scenario_name:
            for scenario in scenarios:
                if str(scenario.get("name")) == scenario_name:
                    selected = scenario
                    break
        else:
            selected = scenarios[0]
    if selected is None:
        selected = {
            "name": scenario_name or str(transient_config.get("default_name", "steady_state_hold")),
            "duration_s": transient_config.get("duration_s", 120.0),
            "time_step_s": transient_config.get("time_step_s", 1.0),
            "events": transient_config.get("events", []),
        }
    events = selected.get("events", [])
    if not isinstance(events, list):
        events = []
    normalized_events = sorted(
        [
            {
                "time_s": float(event.get("time_s", 0.0)),
                **{
                    key: value
                    for key, value in event.items()
                    if key != "time_s"
                },
            }
            for event in events
            if isinstance(event, dict)
        ],
        key=lambda item: item["time_s"],
    )
    return {
        "name": str(selected.get("name", scenario_name or "steady_state_hold")),
        "duration_s": float(selected.get("duration_s", transient_config.get("duration_s", 120.0))),
        "time_step_s": float(selected.get("time_step_s", transient_config.get("time_step_s", 1.0))),
        "events": normalized_events,
    }


def _build_transient_baseline(config: Any, summary: dict[str, Any]) -> dict[str, Any]:
    bop = summary.get("bop", {})
    primary_system = summary.get("primary_system", {})
    reduced_order = summary.get("flow", {}).get("reduced_order", {})
    fuel_cycle = summary.get("fuel_cycle", primary_system.get("fuel_cycle", {}))
    thermal_profile = primary_system.get("thermal_profile", {})
    inventory = primary_system.get("inventory", {})
    fuel_inventory = inventory.get("fuel_salt", {})

    thermal_power_mw = float(bop.get("thermal_power_mw", config.reactor.get("design_power_mwth", 0.0)))
    hot_leg_temp_c = float(
        thermal_profile.get("estimated_hot_leg_temp_c", config.reactor.get("hot_leg_temp_c", 700.0))
    )
    cold_leg_temp_c = float(
        thermal_profile.get("estimated_cold_leg_temp_c", config.reactor.get("cold_leg_temp_c", 560.0))
    )
    average_temp_c = 0.5 * (hot_leg_temp_c + cold_leg_temp_c)
    delta_t_c = max(hot_leg_temp_c - cold_leg_temp_c, 1.0)
    core_residence_time_s = float(
        reduced_order.get("active_flow", {}).get("representative_residence_time_s", 1.0)
    )
    total_fuel_volume_m3 = float(fuel_inventory.get("total_m3", 0.0))
    total_volumetric_flow_m3_s = float(
        reduced_order.get("active_flow", {}).get("total_volumetric_flow_m3_s", 0.0)
    )
    loop_residence_time_s = (
        total_fuel_volume_m3 / total_volumetric_flow_m3_s
        if total_fuel_volume_m3 > 0.0 and total_volumetric_flow_m3_s > 0.0
        else max(core_residence_time_s * 8.0, 5.0)
    )
    cleanup_turnover_hours = float(fuel_cycle.get("cleanup_turnover_hours", 24.0 * float(config.reactor.get("cleanup_turnover_days", 14.0))))
    cleanup_turnover_s = max(cleanup_turnover_hours * 3600.0, 1.0)
    cleanup_removal_efficiency = float(
        fuel_cycle.get("cleanup_removal_efficiency", config.reactor.get("cleanup_removal_efficiency", 0.75))
    )
    initial_core_precursor_fraction = _clamp(
        loop_residence_time_s / max(core_residence_time_s + loop_residence_time_s, 1.0e-9),
        0.05,
        0.95,
    )
    chemistry_summary = summary.get("chemistry", {})
    if not isinstance(chemistry_summary, dict) or not chemistry_summary:
        chemistry_summary = build_steady_state_chemistry_summary(
            config,
            fuel_salt_volume_m3=total_fuel_volume_m3,
            bulk_temperature_c=average_temp_c,
            cleanup_turnover_days=float(fuel_cycle.get("cleanup_turnover_days", config.reactor.get("cleanup_turnover_days", 14.0))),
        )
    return {
        "thermal_power_mw": _round_float(thermal_power_mw),
        "hot_leg_temp_c": _round_float(hot_leg_temp_c),
        "cold_leg_temp_c": _round_float(cold_leg_temp_c),
        "average_primary_temp_c": _round_float(average_temp_c),
        "steady_state_delta_t_c": _round_float(delta_t_c),
        "core_residence_time_s": _round_float(max(core_residence_time_s, 0.05)),
        "loop_residence_time_s": _round_float(max(loop_residence_time_s, 0.5)),
        "cleanup_turnover_s": _round_float(cleanup_turnover_s),
        "cleanup_removal_efficiency": _round_float(cleanup_removal_efficiency),
        "initial_core_precursor_fraction": _round_float(initial_core_precursor_fraction),
        "chemistry": chemistry_summary,
        "fuel_cycle": fuel_cycle,
    }


def _resolve_model_parameters(transient_config: dict[str, Any]) -> dict[str, Any]:
    model_parameters: dict[str, Any] = {
        "power_response_time_s": float(transient_config.get("power_response_time_s", 2.5)),
        "fuel_temperature_response_time_s": float(transient_config.get("fuel_temperature_response_time_s", 8.0)),
        "graphite_temperature_response_time_s": float(transient_config.get("graphite_temperature_response_time_s", 28.0)),
        "coolant_temperature_response_time_s": float(transient_config.get("coolant_temperature_response_time_s", 12.0)),
        "precursor_inventory_response_time_s": float(transient_config.get("precursor_inventory_response_time_s", 5.0)),
        "precursor_transport_response_time_s": float(transient_config.get("precursor_transport_response_time_s", 3.0)),
        "xenon_response_time_s": float(transient_config.get("xenon_response_time_s", 240.0)),
        "reactivity_to_power_scale_pcm": float(transient_config.get("reactivity_to_power_scale_pcm", 650.0)),
        "fuel_temperature_feedback_pcm_per_c": float(
            transient_config.get("fuel_temperature_feedback_pcm_per_c", -3.0)
        ),
        "graphite_temperature_feedback_pcm_per_c": float(
            transient_config.get("graphite_temperature_feedback_pcm_per_c", -0.8)
        ),
        "coolant_temperature_feedback_pcm_per_c": float(
            transient_config.get("coolant_temperature_feedback_pcm_per_c", -0.5)
        ),
        "precursor_worth_pcm": float(transient_config.get("precursor_worth_pcm", 180.0)),
        "xenon_worth_pcm_per_fraction": float(
            transient_config.get("xenon_worth_pcm_per_fraction", -120.0)
        ),
        "depletion_reactivity_worth_pcm_per_fraction": float(
            transient_config.get("depletion_reactivity_worth_pcm_per_fraction", 320.0)
        ),
        "protactinium_penalty_pcm_per_fraction": float(
            transient_config.get("protactinium_penalty_pcm_per_fraction", -140.0)
        ),
        "chemistry_redox_worth_pcm_per_ev": float(
            transient_config.get("chemistry_redox_worth_pcm_per_ev", -90.0)
        ),
        "chemistry_impurity_worth_pcm_per_fraction": float(
            transient_config.get("chemistry_impurity_worth_pcm_per_fraction", -240.0)
        ),
        "max_power_fraction": float(transient_config.get("max_power_fraction", 3.0)),
    }
    model_parameters.update(resolve_precursor_transport(transient_config))
    return model_parameters


def _integrate_transient(
    *,
    baseline: dict[str, Any],
    scenario: dict[str, Any],
    model_parameters: dict[str, float],
    depletion: dict[str, Any],
    chemistry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dt = max(float(scenario["time_step_s"]), 0.05)
    duration_s = max(float(scenario["duration_s"]), dt)
    event_index = 0
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
    steady_fuel_temp_c = float(baseline["hot_leg_temp_c"])
    steady_graphite_temp_c = float(baseline["average_primary_temp_c"])
    steady_coolant_temp_c = float(baseline["average_primary_temp_c"])
    precursor_groups = model_parameters["delayed_neutron_precursor_groups"]
    nominal_cleanup_rate_s = _precursor_cleanup_rate_s(
        baseline=baseline,
        depletion=depletion,
        cleanup_multiplier=1.0,
    )
    precursor_state = build_initial_precursor_state(
        groups=precursor_groups,
        core_residence_time_s=float(baseline["core_residence_time_s"]),
        loop_residence_time_s=float(baseline["loop_residence_time_s"]),
        cleanup_rate_s=nominal_cleanup_rate_s,
    )
    precursor_summary = summarize_precursor_state(
        precursor_state,
        precursor_groups,
        steady_state=precursor_state["steady_state"],
    )
    steady_precursor_fraction = float(precursor_summary["core_precursor_fraction"])
    baseline["initial_core_precursor_fraction"] = _round_float(steady_precursor_fraction)
    baseline["initial_core_delayed_neutron_source_absolute_fraction"] = precursor_summary[
        "core_delayed_neutron_source_absolute_fraction"
    ]
    baseline["initial_precursor_transport_loss_fraction"] = precursor_summary[
        "precursor_transport_loss_fraction"
    ]
    baseline["delayed_neutron_precursor_groups"] = precursor_group_summary(
        precursor_state,
        precursor_groups,
    )

    power_fraction = 1.0
    fuel_temp_c = steady_fuel_temp_c
    graphite_temp_c = steady_graphite_temp_c
    coolant_temp_c = steady_coolant_temp_c
    precursor_total = float(precursor_summary["precursor_total_fraction"])
    core_precursor_fraction = steady_precursor_fraction
    core_delayed_neutron_source_fraction = float(precursor_summary["core_delayed_neutron_source_fraction"])
    precursor_transport_loss_fraction = float(precursor_summary["precursor_transport_loss_fraction"])
    xenon_fraction = 1.0
    fissile_inventory_fraction = float(depletion["initial_fissile_inventory_fraction"])
    protactinium_inventory_fraction = 0.0
    chemistry_baseline = baseline.get("chemistry", {})
    steady_redox_state_ev = float(chemistry_baseline.get("redox_state_ev", chemistry["initial_redox_state_ev"]))
    target_redox_state_ev = float(chemistry_baseline.get("target_redox_state_ev", chemistry["target_redox_state_ev"]))
    redox_state_ev = steady_redox_state_ev
    impurity_fraction = float(chemistry_baseline.get("impurity_fraction", 0.0))
    corrosion_index = float(chemistry_baseline.get("corrosion_index", 1.0))

    history: list[dict[str, Any]] = []
    peak_power_fraction = power_fraction
    peak_fuel_temp_c = fuel_temp_c
    peak_graphite_temp_c = graphite_temp_c
    peak_coolant_temp_c = coolant_temp_c
    minimum_core_precursor_fraction = core_precursor_fraction
    minimum_core_delayed_neutron_source_fraction = core_delayed_neutron_source_fraction
    total_reactivity_pcm = 0.0
    peak_protactinium_inventory_fraction = protactinium_inventory_fraction
    peak_corrosion_index = corrosion_index

    step_count = int(round(duration_s / dt))
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

        effective_flow_fraction = _clamp(controls["flow_fraction"], 0.05, 1.5)
        effective_heat_sink_fraction = _clamp(controls["heat_sink_fraction"], 0.0, 1.5)
        cleanup_rate_s = _precursor_cleanup_rate_s(
            baseline=baseline,
            depletion=depletion,
            cleanup_multiplier=_clamp(controls["cleanup_multiplier"], 0.0, 2.0),
        )
        gas_stripping_efficiency = _clamp(controls["gas_stripping_efficiency"], 0.0, 1.0)

        thermal_load_ratio = power_fraction / max(
            effective_flow_fraction * max(effective_heat_sink_fraction, 0.15),
            0.05,
        )
        fuel_target_c = steady_fuel_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.7
        fuel_target_c += controls["sink_temp_offset_c"] * 0.25
        graphite_target_c = steady_graphite_temp_c + (fuel_temp_c - steady_fuel_temp_c) * 0.7
        coolant_target_c = steady_coolant_temp_c + (thermal_load_ratio - 1.0) * float(baseline["steady_state_delta_t_c"]) * 0.45
        coolant_target_c += controls["sink_temp_offset_c"] * 0.55

        fuel_temp_c = _first_order_step(
            fuel_temp_c,
            fuel_target_c,
            dt,
            model_parameters["fuel_temperature_response_time_s"],
        )
        graphite_temp_c = _first_order_step(
            graphite_temp_c,
            graphite_target_c,
            dt,
            model_parameters["graphite_temperature_response_time_s"],
        )
        coolant_temp_c = _first_order_step(
            coolant_temp_c,
            coolant_target_c,
            dt,
            model_parameters["coolant_temperature_response_time_s"],
        )

        precursor_state = step_precursor_state(
            state=precursor_state,
            groups=precursor_groups,
            power_fraction=power_fraction,
            flow_fraction=effective_flow_fraction,
            dt_s=dt,
            core_residence_time_s=float(baseline["core_residence_time_s"]),
            loop_residence_time_s=float(baseline["loop_residence_time_s"]),
            cleanup_rate_s=cleanup_rate_s,
        )
        precursor_summary = summarize_precursor_state(
            precursor_state,
            precursor_groups,
            steady_state=precursor_state["steady_state"],
        )
        core_precursor_fraction = float(precursor_summary["core_precursor_fraction"])
        precursor_total = float(precursor_summary["precursor_total_fraction"])
        core_delayed_neutron_source_fraction = float(
            precursor_summary["core_delayed_neutron_source_fraction"]
        )
        precursor_transport_loss_fraction = float(precursor_summary["precursor_transport_loss_fraction"])

        xenon_target = max(power_fraction, 0.0)
        xenon_fraction = _first_order_step(
            xenon_fraction,
            xenon_target,
            dt,
            model_parameters["xenon_response_time_s"],
        )
        xenon_fraction = max(
            xenon_fraction - cleanup_rate_s * float(depletion["xenon_removal_fraction"]) * xenon_fraction * dt,
            0.0,
        )

        breeding_gain_fraction_per_day = float(depletion["breeding_gain_fraction_per_day"])
        fissile_burn_fraction_per_day_full_power = float(depletion["fissile_burn_fraction_per_day_full_power"])
        minor_actinide_sink_fraction_per_day = float(depletion["minor_actinide_sink_fraction_per_day"])
        protactinium_holdup_days = max(float(depletion["protactinium_holdup_days"]), 0.05)
        protactinium_target_fraction = breeding_gain_fraction_per_day * protactinium_holdup_days * power_fraction
        protactinium_inventory_fraction = _first_order_step(
            protactinium_inventory_fraction,
            protactinium_target_fraction,
            dt,
            protactinium_holdup_days * 86400.0,
        )
        fissile_inventory_fraction += (
            breeding_gain_fraction_per_day * max(1.0 - protactinium_inventory_fraction, 0.0)
            - fissile_burn_fraction_per_day_full_power * power_fraction
            - minor_actinide_sink_fraction_per_day
        ) * dt_days
        fissile_inventory_fraction = _clamp(fissile_inventory_fraction, 0.2, 1.5)

        redox_target_ev = target_redox_state_ev + controls["redox_setpoint_shift_ev"]
        redox_target_ev += impurity_fraction * 0.03
        redox_state_ev = _first_order_step(
            redox_state_ev,
            redox_target_ev,
            dt,
            max(float(chemistry["redox_control_time_days"]) * 86400.0, dt),
        )
        impurity_ingress_fraction_per_day = float(chemistry["oxidant_ingress_fraction_per_day"]) * _clamp(
            controls["impurity_ingress_multiplier"],
            0.0,
            4.0,
        )
        impurity_capture_rate_per_day = (
            float(chemistry["impurity_capture_efficiency"]) + gas_stripping_efficiency
        ) / max(float(baseline["fuel_cycle"].get("cleanup_turnover_days", 14.0)), 0.25)
        impurity_fraction += (impurity_ingress_fraction_per_day - impurity_capture_rate_per_day * impurity_fraction) * dt_days
        impurity_fraction = _clamp(impurity_fraction, 0.0, 0.05)
        corrosion_index = corrosion_index_from_state(
            redox_state_ev=redox_state_ev,
            target_redox_state_ev=target_redox_state_ev,
            impurity_fraction=impurity_fraction,
            corrosion_acceleration_per_ev=float(chemistry["corrosion_acceleration_per_ev"]),
        )

        temperature_feedback_pcm = (
            model_parameters["fuel_temperature_feedback_pcm_per_c"] * (fuel_temp_c - steady_fuel_temp_c)
            + model_parameters["graphite_temperature_feedback_pcm_per_c"] * (graphite_temp_c - steady_graphite_temp_c)
            + model_parameters["coolant_temperature_feedback_pcm_per_c"] * (coolant_temp_c - steady_coolant_temp_c)
        )
        precursor_feedback_pcm = model_parameters["precursor_worth_pcm"] * (
            core_delayed_neutron_source_fraction - 1.0
        )
        xenon_feedback_pcm = model_parameters["xenon_worth_pcm_per_fraction"] * (xenon_fraction - 1.0)
        depletion_feedback_pcm = (
            model_parameters["depletion_reactivity_worth_pcm_per_fraction"] * (fissile_inventory_fraction - 1.0)
            + model_parameters["protactinium_penalty_pcm_per_fraction"] * protactinium_inventory_fraction
        )
        chemistry_feedback_pcm = (
            model_parameters["chemistry_redox_worth_pcm_per_ev"] * (redox_state_ev - steady_redox_state_ev)
            + model_parameters["chemistry_impurity_worth_pcm_per_fraction"] * impurity_fraction
        )
        total_reactivity_pcm = (
            controls["reactivity_pcm"]
            + temperature_feedback_pcm
            + precursor_feedback_pcm
            + xenon_feedback_pcm
            + depletion_feedback_pcm
            + chemistry_feedback_pcm
        )
        power_target = _clamp(
            1.0 + (total_reactivity_pcm / max(model_parameters["reactivity_to_power_scale_pcm"], 1.0)),
            0.02,
            model_parameters["max_power_fraction"],
        )
        power_fraction = _first_order_step(
            power_fraction,
            power_target,
            dt,
            model_parameters["power_response_time_s"],
        )

        peak_power_fraction = max(peak_power_fraction, power_fraction)
        peak_fuel_temp_c = max(peak_fuel_temp_c, fuel_temp_c)
        peak_graphite_temp_c = max(peak_graphite_temp_c, graphite_temp_c)
        peak_coolant_temp_c = max(peak_coolant_temp_c, coolant_temp_c)
        minimum_core_precursor_fraction = min(minimum_core_precursor_fraction, core_precursor_fraction)
        minimum_core_delayed_neutron_source_fraction = min(
            minimum_core_delayed_neutron_source_fraction,
            core_delayed_neutron_source_fraction,
        )
        peak_protactinium_inventory_fraction = max(peak_protactinium_inventory_fraction, protactinium_inventory_fraction)
        peak_corrosion_index = max(peak_corrosion_index, corrosion_index)

        history.append(
            {
                "time_s": _round_float(time_s),
                "power_fraction": _round_float(power_fraction),
                "thermal_power_mw": _round_float(float(baseline["thermal_power_mw"]) * power_fraction),
                "fuel_temp_c": _round_float(fuel_temp_c),
                "graphite_temp_c": _round_float(graphite_temp_c),
                "coolant_temp_c": _round_float(coolant_temp_c),
                "core_precursor_fraction": _round_float(core_precursor_fraction),
                "precursor_total_fraction": _round_float(precursor_total),
                "core_delayed_neutron_source_fraction": _round_float(core_delayed_neutron_source_fraction),
                "precursor_transport_loss_fraction": _round_float(precursor_transport_loss_fraction),
                "xenon_fraction": _round_float(xenon_fraction),
                "fissile_inventory_fraction": _round_float(fissile_inventory_fraction),
                "protactinium_inventory_fraction": _round_float(protactinium_inventory_fraction),
                "redox_state_ev": _round_float(redox_state_ev),
                "impurity_fraction": _round_float(impurity_fraction),
                "corrosion_index": _round_float(corrosion_index),
                "total_reactivity_pcm": _round_float(total_reactivity_pcm),
                "control_reactivity_pcm": _round_float(controls["reactivity_pcm"]),
                "depletion_reactivity_pcm": _round_float(depletion_feedback_pcm),
                "chemistry_reactivity_pcm": _round_float(chemistry_feedback_pcm),
                "flow_fraction": _round_float(effective_flow_fraction),
                "heat_sink_fraction": _round_float(effective_heat_sink_fraction),
            }
        )

    metrics = {
        "duration_s": _round_float(duration_s),
        "time_step_s": _round_float(dt),
        "history_points": len(history),
        "event_count": len(scenario["events"]),
        "peak_power_fraction": _round_float(peak_power_fraction),
        "final_power_fraction": _round_float(power_fraction),
        "peak_fuel_temperature_c": _round_float(peak_fuel_temp_c),
        "peak_graphite_temperature_c": _round_float(peak_graphite_temp_c),
        "peak_coolant_temperature_c": _round_float(peak_coolant_temp_c),
        "minimum_precursor_core_fraction": _round_float(minimum_core_precursor_fraction),
        "minimum_core_delayed_neutron_source_fraction": _round_float(minimum_core_delayed_neutron_source_fraction),
        "final_core_delayed_neutron_source_fraction": _round_float(core_delayed_neutron_source_fraction),
        "final_precursor_transport_loss_fraction": _round_float(precursor_transport_loss_fraction),
        "final_total_reactivity_pcm": _round_float(total_reactivity_pcm),
        "final_xenon_fraction": _round_float(xenon_fraction),
        "final_fissile_inventory_fraction": _round_float(fissile_inventory_fraction),
        "peak_protactinium_inventory_fraction": _round_float(peak_protactinium_inventory_fraction),
        "final_redox_state_ev": _round_float(redox_state_ev),
        "peak_corrosion_index": _round_float(peak_corrosion_index),
    }
    return history, metrics


def _precursor_cleanup_rate_s(
    *,
    baseline: dict[str, Any],
    depletion: dict[str, Any],
    cleanup_multiplier: float,
) -> float:
    cleanup_rate_s = (
        float(baseline["cleanup_removal_efficiency"])
        * _clamp(float(cleanup_multiplier), 0.0, 2.5)
        / max(float(baseline["cleanup_turnover_s"]), 1.0)
    )
    cleanup_rate_s += float(depletion["volatile_removal_efficiency"]) / max(
        float(baseline["cleanup_turnover_s"]) * 6.0,
        1.0,
    )
    return cleanup_rate_s


def _first_order_step(current_value: float, target_value: float, dt: float, time_constant_s: float) -> float:
    tau = max(time_constant_s, dt)
    return current_value + (target_value - current_value) * (dt / tau)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_float(value: float) -> float:
    return round(float(value), 6)
