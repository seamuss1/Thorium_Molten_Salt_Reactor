# Current Model Equations And Correlations

This note documents the equations, closures, and input-unit assumptions used by the repository as it exists today. It is intentionally scoped to the implemented reduced-order model, not a future higher-fidelity roadmap.

## Scope

The current workflow combines:

- OpenMC eigenvalue-style neutronics setup from case-config simulation inputs,
- a reduced-order core flow allocation model,
- a 1D-style primary-loop hydraulic budget,
- lumped heat-exchanger and thermal-profile calculations,
- a reduced-order transient proxy for temperature, reactivity, segmented precursor transport, and cleanup scenarios,
- a deterministic finite-volume handoff for delayed-neutron and decay-heat precursor source partitioning,
- a steady-state salt chemistry proxy and transient chemistry/depletion coupling terms,
- and literature-backed screening models for molten-salt property uncertainty, tritium distribution, and graphite irradiation lifetime.

The relevant implementation lives in:

- [src/thorium_reactor/neutronics/workflows.py](../src/thorium_reactor/neutronics/workflows.py)
- [src/thorium_reactor/flow/reduced_order.py](../src/thorium_reactor/flow/reduced_order.py)
- [src/thorium_reactor/flow/primary_system.py](../src/thorium_reactor/flow/primary_system.py)
- [src/thorium_reactor/flow/properties.py](../src/thorium_reactor/flow/properties.py)
- [src/thorium_reactor/transient.py](../src/thorium_reactor/transient.py)
- [src/thorium_reactor/integrations.py](../src/thorium_reactor/integrations.py)
- [src/thorium_reactor/chemistry.py](../src/thorium_reactor/chemistry.py)

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

$$
x(T) = \text{value}
$$

$$
x(T) = x_{\mathrm{ref}} + s(T - T_{\mathrm{ref}})
$$

$$
x(T) = A \exp \left(\frac{T_a}{T_K}\right)
$$

where $T_K = T_C + 273.15$.

The run summary also carries a TMSR-SF0-inspired uncertainty screen for molten
salt thermophysical properties. The default 95% confidence bands are:

$$
\sigma_{95}(\rho)=2\%, \qquad
\sigma_{95}(c_p)=10\%, \qquad
\sigma_{95}(k)=10\%, \qquad
\sigma_{95}(\mu)=10\%
$$

The reported core outlet temperature uncertainty defaults to the larger of the
case-propagated heat-capacity/density uncertainty and 10 C.

## Reduced-Order Core Flow Allocation

The reduced-order core model distributes total primary mass flow across salt-bearing channels.

For active channels, the allocated mass-flow fraction is:

$$
f_i = \frac{w_i}{\sum_j w_j}
$$

Two weighting rules are implemented:

- `salt_area_weighted`

$$
w_i = A_i
$$

- `pressure_balanced`

$$
w_i = \frac{A_iD_{h,i}^{2}}{L_i},
\qquad
L_i = \frac{V_i}{A_i}
$$

This second option is an equal-pressure-drop conductance proxy, not a calibrated channel friction model.

Derived channel outputs include:

$$
\dot m_i = \dot m_{\mathrm{total}} f_i,
\qquad
\dot V_i = \dot V_{\mathrm{total}} f_i,
\qquad
u_i = \frac{\dot V_i}{A_i},
\qquad
t_{\mathrm{res},i} = \frac{V_i}{\dot V_i}
$$

## Primary-Loop Pressure Drop

For each declared pipe run, the model computes:

$$
D=2r,
\qquad
A=\pi r^2,
\qquad
u=\frac{\dot V}{A},
\qquad
Re=\frac{\rho uD}{\mu}
$$

The current Darcy friction factor closure is:

$$
f =
\begin{cases}
\frac{64}{Re}, & Re < 2300 \\
\frac{0.3164}{Re^{0.25}}, & Re \ge 2300
\end{cases}
$$

That is laminar Hagen-Poiseuille plus a Blasius-style turbulent correlation.

Pressure losses per run are:

$$
\Delta p_f = f\frac{L}{D}\frac{\rho u^2}{2},
\qquad
\Delta p_K = K\frac{\rho u^2}{2},
\qquad
\Delta p_g = \rho g\Delta z
$$

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

$$
Pr=\frac{c_p\mu}{k}
$$

$$
Nu =
\begin{cases}
3.66, & Re < 2300 \\
\text{linear blend}(3.66, Nu_t), & 2300 \le Re < 4000 \\
0.023Re^{0.8}Pr^{0.4}, & Re \ge 4000
\end{cases}
$$

$$
h=\frac{Nu\,k}{D_h}
$$

The turbulent branch is a Dittus-Boelter-style correlation. The transitional branch is a linear interpolation between laminar and turbulent limits.

When the heat exchanger does not rely on an explicit configured `U`, the clean overall coefficient is:

$$
\frac{1}{U_{\mathrm{clean}}}
=
\frac{1}{h_{\mathrm{primary}}}
+
\frac{1}{h_{\mathrm{secondary}}}
$$

Wall resistance and fouling are not yet modeled explicitly.

## Heat Exchanger Sizing

The current heat-exchanger calculation uses:

$$
Q=\dot m_{\mathrm{secondary}}c_{p,\mathrm{secondary}}\Delta T_{\mathrm{secondary}}
$$

$$
LMTD=
\frac{\Delta T_{\mathrm{hot}}-\Delta T_{\mathrm{cold}}}
{\ln(\Delta T_{\mathrm{hot}}/\Delta T_{\mathrm{cold}})}
$$

$$
A_{\mathrm{required}}=\frac{Q}{U_{\mathrm{effective}}LMTD}
$$

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
- delayed-neutron precursor inventories in core and external-loop regions,
- core precursor fraction and core delayed-neutron source fraction,
- and xenon-poison fraction.

The implemented update pattern is first-order relaxation toward control-dependent targets:

$$
\frac{dX}{dt} = \frac{X_{\mathrm{target}} - X}{\tau}
$$

with temperature-reactivity feedback assembled as:

$$
\rho_{\mathrm{total}} =
\rho_{\mathrm{control}}
+ \alpha_{\mathrm{fuel}}(T_{\mathrm{fuel}}-T_{\mathrm{fuel},0})
+ \alpha_{\mathrm{graphite}}(T_{\mathrm{graphite}}-T_{\mathrm{graphite},0})
+ \alpha_{\mathrm{coolant}}(T_{\mathrm{coolant}}-T_{\mathrm{coolant},0})
+ \rho_{\mathrm{precursor}}
+ \rho_{\mathrm{xenon}}
$$

and power fraction driven toward:

$$
P_{\mathrm{target}}
=
\operatorname{clamp}
\left(
1+\frac{\rho_{\mathrm{total}}}{\rho_{\mathrm{scale}}},
P_{\min},
P_{\max}
\right)
$$

Delayed-neutron precursor transport uses a configurable group set. By default,
the repository uses six conventional delayed-neutron groups, each with a declared
decay constant `lambda_i` and yield fraction `y_i`. The older two-region
transport model remains available and splits each group into core and
external-loop inventories:

$$
\frac{dC_{\mathrm{core},i}}{dt}
=
y_iP
+ \frac{C_{\mathrm{loop},i}}{\tau_{\mathrm{loop}}}
- \frac{C_{\mathrm{core},i}}{\tau_{\mathrm{core}}}
- \lambda_i C_{\mathrm{core},i}
$$

$$
\frac{dC_{\mathrm{loop},i}}{dt}
=
\frac{C_{\mathrm{core},i}}{\tau_{\mathrm{core}}}
- \frac{C_{\mathrm{loop},i}}{\tau_{\mathrm{loop}}}
- \lambda_i C_{\mathrm{loop},i}
- k_{\mathrm{cleanup}} C_{\mathrm{loop},i}
$$

The residence times are derived from the steady-state reduced-order flow summary
and scaled as:

$$
\tau_{\mathrm{core}}=\frac{\tau_{\mathrm{core},0}}{f_{\mathrm{flow}}},
\qquad
\tau_{\mathrm{loop}}=\frac{\tau_{\mathrm{loop},0}}{f_{\mathrm{flow}}}
$$

The precursor reactivity term follows the core delayed-neutron source rather
than a scalar inventory relaxation:

$$
S_{\mathrm{core}}=\sum_i \lambda_i C_{\mathrm{core},i},
\qquad
\rho_{\mathrm{precursor}}
=
W_{\mathrm{precursor}}
\left(\frac{S_{\mathrm{core}}}{S_{\mathrm{core},0}}-1\right)
$$

The default transport model now subdivides the external loop into configured
segments. For segment `j`:

$$
\frac{dC_{1,i}}{dt}
=
\frac{C_{\mathrm{core},i}}{\tau_{\mathrm{core}}}
- \frac{C_{1,i}}{\tau_1}
- \lambda_i C_{1,i}
- k_{\mathrm{cleanup},1} C_{1,i}
$$

$$
\frac{dC_{j,i}}{dt}
=
\frac{C_{j-1,i}}{\tau_{j-1}}
- \frac{C_{j,i}}{\tau_j}
- \lambda_i C_{j,i}
- k_{\mathrm{cleanup},j} C_{j,i}
$$

$$
\frac{dC_{\mathrm{core},i}}{dt}
=
y_iP
+ \frac{C_{\mathrm{last},i}}{\tau_{\mathrm{last}}}
- \frac{C_{\mathrm{core},i}}{\tau_{\mathrm{core}}}
- \lambda_i C_{\mathrm{core},i}
$$

Segment residence fractions are normalized from the case `loop_segments` block,
or inferred from primary-loop pipe geometry when available. The update is solved
implicitly so severe flow-reduction scenarios remain numerically stable.

The deterministic `physics_core` finite-volume precursor handoff uses the same
idea, but now assigns its external-loop cells an absolute residence time from
the run summary instead of a fixed nominal loop time. When primary-system
inventory and flow are available, the loop residence estimate is:

$$
\tau_{\mathrm{loop,FV}}
=
\frac{V_{\mathrm{fuel,total}}-V_{\mathrm{active\ core}}}
{\dot V_{\mathrm{primary}}}
$$

If that inventory balance is unavailable, the handoff falls back to pipe segment
volume divided by local segment flow, then to a core-residence scaled fallback.
The reported `loop_residence_basis` records which path was used.

The deterministic neutronics handoff now reports `beta_eff` with a static
adjoint-shape precursor worth correction, following recent Squirrel and
Griffin/Pronghorn/Squirrel MSRE validation work. For core finite-volume cell
`j`, the precursor solve reports the delayed-neutron source fraction
`s_{d,j}`. The deterministic neutron solve reports a normalized axial importance
profile `w_j`, and the thermal-hydraulic source profile gives the stationary
source fraction `s_{0,j}`. The flowing-fuel worth fraction is:

$$
F_{\beta,\mathrm{flow}}
=
\frac{\sum_{j \in \mathrm{core}} s_{d,j}w_j}
{\sum_{j \in \mathrm{core}} s_{0,j}w_j}
$$

The deterministic effective delayed-neutron fraction is then:

$$
\beta_{\mathrm{eff,flow}}
=
\beta_{\mathrm{total}}F_{\beta,\mathrm{flow}}
$$

The older unweighted core delayed-neutron source fraction remains in the JSON as
`unweighted_beta_eff` and `core_delayed_neutron_source_absolute_fraction` for
comparison.

The same finite-volume ring is also used to screen decay-heat precursor
transport. Recent 2025-2026 liquid-fueled MSR work emphasizes that decay heat
is not released only in the core when fuel circulates through the primary loop.
For decay-heat group `g`, the implemented screening equation is:

$$
\frac{dH_g}{dt}
+
\nabla \cdot (uH_g)
=
\nabla \cdot (D_g\nabla H_g)
+
q_gS_f
-
\lambda_gH_g
-
k_{\mathrm{cleanup}}H_g
$$

where `q_g` is the configured group weight. The default reduced-order set uses
three decay-heat groups, following recent MSFR reduced-order-model practice for
decay-heat precursor transport. Cases may override the group set through
`physics_core.precursor_transport.decay_heat_groups`.

The report aggregates the solved decay-heat source into:

$$
F_{\mathrm{DH,core}}
=
\frac{\sum_{j\in \mathrm{core}}\sum_g \lambda_gH_{g,j}}
{\sum_j\sum_g \lambda_gH_{g,j}},
\qquad
F_{\mathrm{DH,loop}}
=
1-F_{\mathrm{DH,core}}
$$

and also reports source fractions by configured loop segment. This remains the
reduced-order screening handoff used by `physics_core`.

## Native R-Z RKDG Scalar Transport

The additive `reactor transport <case>` path writes native high-fidelity
precursor artifacts without replacing `physics_core`. Version 1 uses a
structured axisymmetric R-Z mesh, polynomial-order DG field metadata, and an
SSP-RK3 finite-volume/DG-compatible update for scalar advection,
diffusion, decay, source, cleanup, and outlet terms:

$$
\frac{\partial C_g}{\partial t}
+
u_z\frac{\partial C_g}{\partial z}
=
D_g\nabla^2_{r,z}C_g
-
(\lambda_g+k_{\mathrm{cleanup}})C_g
+
S_g(r,z).
$$

The axis boundary uses a finite no-flux condition, radial walls use zero
normal flux, and axial boundaries use upwind inlet/outlet fluxes. The native
artifacts are `transport_mesh.json`, `transport_summary.json`, and
`transport_solution.npz`. Reports and the browser show mesh/order/time-step,
CFL, minimum field value, source fractions, and conservation residuals.

## Native Sparse Depletion Matrix

The additive `reactor deplete <case>` path solves constant-rate isotope
evolution with a sparse Bateman matrix:

$$
\frac{d\mathbf{n}}{dt}
=
A(t)\mathbf{n}
+
\mathbf{b}(t),
$$

where `A` contains decay, neutron transmutation, fission-yield production,
online removal, and optional inter-zone transfer terms, while `b` contains
feed/source terms. The default stepper uses SciPy
`scipy.sparse.linalg.expm_multiply`; a dense NumPy fallback is available for
small verification chains in runtimes that have not yet been bootstrapped with
SciPy. The repo-local test chain lives at
`resources/depletion/tiny_thorium_chain.yaml`, and OpenMC-style XML chain
imports are supported for realistic depletion-chain handoffs.

The native depletion artifacts are `depletion_chain.json`,
`depletion_summary.json`, `depletion_history.json`, and
`depletion_matrix.npz`. Reports and the browser surface the chain source,
isotope count, zone count, matrix nonzeros, inventory deltas, feed/removal
totals, and atom-balance residuals.

The xenon piece remains an explicit proxy:

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

These assumptions parameterize reduced-order poison, breeding, and cleanup
proxies. Full native depletion-chain calculations are emitted separately by
`reactor deplete` and are not yet predictor-corrector coupled back into the
transient proxy.

## Salt Chemistry Proxy

The repository now includes a steady-state salt chemistry summary and transient chemistry state variables.

The steady-state chemistry model tracks:

- redox state relative to a target setpoint,
- oxidant/impurity ingress,
- impurity capture and gas stripping,
- a derived corrosion index,
- noble metal plateout fraction,
- and tritium release fraction.

The chemistry proxy computes a corrosion indicator of the form:

$$
I_{\mathrm{corrosion}}
=
1
+ \max(R_{\mathrm{state}}-R_{\mathrm{target}},0)a_{\mathrm{redox}}
+ C_{\mathrm{imp}}f_{\mathrm{impurity}}
$$

with the current implementation using a simple linear impurity penalty.

## Tritium Transport Screen

The tritium model is a reduced-order distribution screen, not an isotope
transport solver. It tracks normalized tritium production and partitions the
inventory into:

- environmental release,
- active removal,
- graphite retention,
- and circulating inventory.

The default closure follows recent 10 MWe TMSR literature: unmitigated systems
can release about one-third of produced tritium, while MSRE-like spray-gas
removal can remove about two-thirds and reduce permeation toward roughly 10%.
Graphite saturation adds a configurable release penalty over operating time.

## Graphite Irradiation Lifetime Screen

The graphite screen uses recent SINAP graphite optimization literature as a
first-pass design check. It reports:

- fuel volume fraction,
- control-channel fraction,
- fast-flux peaking proxy,
- nominal maximum fast flux,
- estimated lifetime against a default `3e22 n/cm2` fast-fluence limit,
- and a pass/watch status against the configured target lifetime.

The screen is intentionally conservative: it highlights when a case should be
sent to a neutronics/thermal-mechanics workflow, rather than replacing that
workflow.

## MSRE Pump-Transient Benchmark Screen

Recent MSRE pump-transient benchmark work compared a simplified
one-dimensional system model with an R-Z porous-medium model and experimental
MSRE pump startup/coastdown data. The repository now reports this as a
validation screen rather than changing the reduced-order solver closure.

The report carries the literature mean-error envelopes:

$$
\epsilon_{\mathrm{startup}} = 11\text{ to }21\ \mathrm{pcm}
$$

$$
\epsilon_{\mathrm{coastdown}} = 5\text{ to }13\ \mathrm{pcm}
$$

It also reports the fraction of tracked salt inventory outside the configured
active-flow channels:

$$
f_{\mathrm{nonactive}}
=
\frac{V_{\mathrm{nonactive}}}
{V_{\mathrm{active}} + V_{\mathrm{nonactive}}}
$$

and the stagnant subset:

$$
f_{\mathrm{stagnant}}
=
\frac{V_{\mathrm{stagnant}}}
{V_{\mathrm{active}} + V_{\mathrm{nonactive}}}
$$

The screen is marked `watch` when non-active or stagnant salt inventory is
present, because one-dimensional early transient reactivity rates may then be
limited by radial distribution or bypass-like flow effects. It is a reporting
and validation-context metric, not a replacement for a spatial
thermal-hydraulic solve.

## Coupled Depletion And Chemistry Terms In The Transient Proxy

The transient model now also tracks:

- fissile inventory fraction,
- protactinium inventory fraction,
- redox state,
- impurity fraction,
- and corrosion index.

The depletion proxy evolves fissile inventory with breeding, burnup, and a sink term:

$$
\frac{df_{\mathrm{fissile}}}{dt}
=
G_{\mathrm{breeding}}
- r_{\mathrm{burn}}P_{\mathrm{fraction}}
- S_{\mathrm{minor\ actinide}}
$$

Protactinium inventory relaxes toward a holdup-time target proportional to breeding rate and power.

The transient reactivity balance now includes additional proxy terms:

$$
\rho_{\mathrm{total}}
=
\rho_{\mathrm{control}}
+ \rho_{\mathrm{temperature}}
+ \rho_{\mathrm{precursor}}
+ \rho_{\mathrm{xenon}}
+ \rho_{\mathrm{depletion}}
+ \rho_{\mathrm{chemistry}}
$$

where `rho_depletion` depends on fissile and protactinium inventory fractions, and `rho_chemistry` depends on redox and impurity state.

## MOOSE And SCALE Integration Surface

The repository now exposes two external solver handoff paths:

- `reactor moose <case>`
- `reactor scale <case>`

The current implementation does three things:

1. ensures a steady-state summary exists for the case,
2. exports a proxy input deck plus a structured handoff JSON into the result bundle,
3. optionally tries to execute the external solver if the configured executable is available on `PATH`.

The exported decks are intentionally lightweight:

- the MOOSE deck carries steady-state thermal-hydraulic summary values into a simple input-file scaffold,
- the SCALE deck carries case identity, sequence choice, particle/batch settings, and material inventory into a simple SCALE-style scaffold.

This is an integration adapter layer, not a validated mesh/material translation or a benchmark-quality external deck generator.

## Current Boundaries And Limitations

This document describes the implemented reduced-order model only. It does not imply:

- transient thermal hydraulics,
- spatially resolved precursor drift or coupled kinetics,
- benchmark-grade depletion or online reprocessing,
- or validated molten-salt-specific closure laws beyond the current first-pass correlations.
