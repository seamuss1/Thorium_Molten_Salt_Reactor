# Current Model Equations And Correlations

This note documents the equations, closures, and input-unit assumptions used by the repository as it exists today. It is intentionally scoped to the implemented reduced-order model, not a future higher-fidelity roadmap.

## Scope

The current workflow combines:

- OpenMC eigenvalue-style neutronics setup from case-config simulation inputs,
- a reduced-order core flow allocation model,
- a 1D-style primary-loop hydraulic budget,
- lumped heat-exchanger and thermal-profile calculations,
- and a reduced-order transient proxy for temperature, reactivity, precursor transport, and cleanup scenarios.

The relevant implementation lives in:

- [src/thorium_reactor/neutronics/workflows.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/neutronics/workflows.py)
- [src/thorium_reactor/flow/reduced_order.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/flow/reduced_order.py)
- [src/thorium_reactor/flow/primary_system.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/flow/primary_system.py)
- [src/thorium_reactor/flow/properties.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/flow/properties.py)
- [src/thorium_reactor/transient.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/transient.py)

## Input Units

Case loading now validates the property-unit combinations below:

- Density: `g/cm3`, `kg/m3`
- Specific heat: `j/kg-k`, `kj/kg-k`
- Thermal conductivity: `w/m-k`
- Dynamic viscosity: `pa-s`

Supported property models are:

- `constant`: requires `value`
- `linear`: requires `reference_value`, `reference_temperature_c`, `slope_per_c`
- `arrhenius`: requires `pre_exponential`, `activation_temperature_k`

Geometry inputs remain case-specific, but the current flow model assumes:

- most core and render-layout dimensions in centimeters,
- pipe and channel radii declared in centimeters unless a field name explicitly ends in `_m2` or `_m`,
- internal pressure-drop and heat-transfer calculations converted to SI units before use.

## Material Property Evaluation

The property evaluator supports:

- constant properties: `x(T) = value`
- linear properties: `x(T) = x_ref + slope * (T - T_ref)`
- Arrhenius-style viscosity form: `x(T) = A * exp(T_a / T_K)`

where `T_K = T_C + 273.15`.

## Reduced-Order Core Flow Allocation

The reduced-order core model distributes total primary mass flow across salt-bearing channels.

For active channels, the allocated mass-flow fraction is:

```text
flow_fraction_i = weight_i / sum(weight_j)
```

Two weighting rules are implemented:

- `salt_area_weighted`

```text
weight_i = A_i
```

- `pressure_balanced`

```text
weight_i = A_i * D_h,i^2 / L_i
L_i = V_i / A_i
```

This second option is an equal-pressure-drop conductance proxy, not a calibrated channel friction model.

Derived channel outputs include:

```text
mdot_i = mdot_total * flow_fraction_i
Vdot_i = Vdot_total * flow_fraction_i
u_i = Vdot_i / A_i
t_res,i = V_i / Vdot_i
```

## Primary-Loop Pressure Drop

For each declared pipe run, the model computes:

```text
D = 2r
A = pi r^2
u = Vdot / A
Re = rho u D / mu
```

The current Darcy friction factor closure is:

```text
f = 64 / Re                    for Re < 2300
f = 0.3164 / Re^0.25           for Re >= 2300
```

That is laminar Hagen-Poiseuille plus a Blasius-style turbulent correlation.

Pressure losses per run are:

```text
Delta_p_f = f (L / D) (rho u^2 / 2)
Delta_p_K = K (rho u^2 / 2)
Delta_p_g = rho g Delta_z
```

where:

- `K = K_terminal + N_bends * K_elbow`
- `L` is the polyline run length converted from centimeters to meters
- `Delta_z` is the net elevation change across the run

## Parallel Branch Solver

If the loop graph contains parallel branches, the solver assumes a common branch pressure drop and uses bisection:

1. Estimate a high pressure-drop bound from each branch evaluated at full flow.
2. Bisect the shared branch pressure drop for 40 iterations.
3. For each branch, invert pressure drop to flow with a second bisection solve.

This is appropriate while each branch pressure drop increases monotonically with flow.

## Internal Convection And Heat Transfer

For pipe-side and heat-exchanger-side film coefficients, the current model computes:

```text
Pr = cp mu / k
Nu = 3.66                                           for Re < 2300
Nu = linear blend between 3.66 and turbulent Nu     for 2300 <= Re < 4000
Nu = 0.023 Re^0.8 Pr^0.4                            for Re >= 4000
h = Nu k / D_h
```

The turbulent branch is a Dittus-Boelter-style correlation. The transitional branch is a linear interpolation between laminar and turbulent limits.

When the heat exchanger does not rely on an explicit configured `U`, the clean overall coefficient is:

```text
1 / U_clean = 1 / h_primary + 1 / h_secondary
```

Wall resistance and fouling are not yet modeled explicitly.

## Heat Exchanger Sizing

The current heat-exchanger calculation uses:

```text
Q = mdot_secondary cp_secondary Delta_T_secondary
LMTD = (Delta_T_hot - Delta_T_cold) / ln(Delta_T_hot / Delta_T_cold)
A_required = Q / (U_effective * LMTD)
```

with:

- `U_effective = configured U` when `steam_generator_overall_u_w_m2k > 0`
- otherwise `U_effective = U_clean`

The report surfaces both the configured and estimated clean `U`.

## OpenMC Simulation Inputs

The repo currently serializes the configured neutronics assumptions into the build manifest and run summary:

- run mode
- particles
- batches
- inactive batches
- active batches
- source definition
- tally list
- radial boundary
- axial boundary when the geometry uses the detailed ring-lattice core

The implemented source helper currently supports a point source:

```text
source.type = point
source.parameters = [x, y, z]
```

## Reduced-Order Transient Proxy

The transient command uses a scenario-driven, first-order nodal proxy rather than a high-fidelity transient multiphysics solve.

Controls can change at event times:

- external reactivity insertion in pcm,
- flow fraction,
- heat-sink fraction,
- cleanup multiplier,
- and sink-temperature offset.

The transient state tracks:

- power fraction,
- representative fuel, graphite, and coolant temperatures,
- total precursor inventory,
- core precursor fraction,
- and xenon-poison fraction.

The implemented update pattern is first-order relaxation toward control-dependent targets:

```text
dX/dt = (X_target - X) / tau
```

with temperature-reactivity feedback assembled as:

```text
rho_total =
  rho_control
  + a_fuel * (T_fuel - T_fuel,0)
  + a_graphite * (T_graphite - T_graphite,0)
  + a_coolant * (T_coolant - T_coolant,0)
  + rho_precursor
  + rho_xenon
```

and power fraction driven toward:

```text
P_target = clamp(1 + rho_total / rho_scale, P_min, P_max)
```

The precursor and xenon pieces are explicit proxies:

- precursor inventory follows power with a configurable response time,
- precursor core fraction shifts with flow,
- cleanup removes loop-associated precursor inventory,
- xenon follows power with a configurable lag and cleanup removal term.

This model is intended for scenario comparison and qualitative trend studies, not licensing-grade transient prediction.

## Depletion And Cleanup Assumptions

Case configs may now declare an optional `depletion` section to make cleanup assumptions explicit in both steady-state fuel-cycle summaries and transient runs.

The current implementation records:

- `chain`
- `cleanup_scenario`
- `volatile_removal_efficiency`
- `xenon_removal_fraction`
- `protactinium_holdup_days`

These assumptions currently parameterize reduced-order poison and cleanup proxies; they are not full depletion-chain transport calculations.

## Current Boundaries And Limitations

This document describes the implemented reduced-order model only. It does not imply:

- transient thermal hydraulics,
- precursor drift or coupled kinetics,
- benchmark-grade depletion or online reprocessing,
- corrosion or redox chemistry,
- or validated molten-salt-specific closure laws beyond the current first-pass correlations.
