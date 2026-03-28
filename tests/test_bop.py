from thorium_reactor.bop.steady_state import BOPInputs, run_steady_state_bop


def test_steady_state_bop_closure_is_near_zero() -> None:
    results = run_steady_state_bop(
        BOPInputs(
            thermal_power_mw=250.0,
            hot_leg_temp_c=700.0,
            cold_leg_temp_c=560.0,
            primary_cp_kj_kgk=1.6,
            steam_generator_effectiveness=0.92,
            turbine_efficiency=0.42,
            generator_efficiency=0.98,
        )
    )

    assert abs(results.closure_error_mw) < 1e-9
    assert results.primary_mass_flow_kg_s > 0.0
