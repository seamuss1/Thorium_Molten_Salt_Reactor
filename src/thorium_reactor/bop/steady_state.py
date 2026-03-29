from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class BOPInputs:
    thermal_power_mw: float
    hot_leg_temp_c: float
    cold_leg_temp_c: float
    primary_cp_kj_kgk: float
    steam_generator_effectiveness: float
    turbine_efficiency: float
    generator_efficiency: float


@dataclass(slots=True)
class BOPResults:
    thermal_power_mw: float
    primary_delta_t_c: float
    primary_mass_flow_kg_s: float
    steam_generator_duty_mw: float
    steam_cycle_usable_duty_mw: float
    electric_power_mw: float
    condenser_duty_mw: float
    closure_error_mw: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def run_steady_state_bop(inputs: BOPInputs) -> BOPResults:
    delta_t = inputs.hot_leg_temp_c - inputs.cold_leg_temp_c
    if delta_t <= 0:
        raise ValueError("Hot leg temperature must exceed cold leg temperature.")
    if inputs.primary_cp_kj_kgk <= 0:
        raise ValueError("Primary salt heat capacity must be positive.")

    primary_mass_flow = inputs.thermal_power_mw * 1_000.0 / (inputs.primary_cp_kj_kgk * delta_t)
    # In a steady closed-loop balance, the steam generator must remove the full reactor heat load.
    # Effectiveness is treated here as a penalty on useful steam-cycle thermal quality, not on
    # whether heat is removed from the primary loop at all.
    steam_generator_duty = inputs.thermal_power_mw
    steam_cycle_usable_duty = steam_generator_duty * inputs.steam_generator_effectiveness
    electric_power = steam_cycle_usable_duty * inputs.turbine_efficiency * inputs.generator_efficiency
    condenser_duty = steam_generator_duty - electric_power
    closure_error = inputs.thermal_power_mw - electric_power - condenser_duty

    return BOPResults(
        thermal_power_mw=inputs.thermal_power_mw,
        primary_delta_t_c=delta_t,
        primary_mass_flow_kg_s=primary_mass_flow,
        steam_generator_duty_mw=steam_generator_duty,
        steam_cycle_usable_duty_mw=steam_cycle_usable_duty,
        electric_power_mw=electric_power,
        condenser_duty_mw=condenser_duty,
        closure_error_mw=closure_error,
    )
