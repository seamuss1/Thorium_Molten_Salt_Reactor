# Thermal-Hydraulics Modeling Strategy

This note defines a practical analysis ladder for this repository's molten-salt reactor workflow. It is intentionally scoped to early serious study, where the goal is to get the physics hierarchy right before spending effort on expensive geometry-resolved CFD.

## Recommended Baseline

- Build a whole-loop thermal-hydraulics model first.
- Represent the reactor core as a porous or homogenized region unless the decision truly depends on channel-by-channel detail.
- Add local 3D CFD only where geometry drives the answer: upper and lower plena, inlet and outlet manifolds, collector regions, bypass paths, heat-exchanger headers, stratification zones, and strong mixing regions.
- If the salt is fuel-bearing, treat the problem as coupled multiphysics rather than CFD with a prescribed heat source. Flow, delayed neutron precursor transport, temperature feedback, and neutronics belong in the same modeling picture.

## Current Repo Position

The repository already contains two useful first-pass pieces:

- a reduced-order whole-loop hydraulic summary in [src/thorium_reactor/flow/primary_system.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/flow/primary_system.py),
- and a reduced-order channel allocation model in [src/thorium_reactor/flow/reduced_order.py](/C:/Users/Admin/Documents/GitHub/Thorium_Molten_Salt_Reactor/src/thorium_reactor/flow/reduced_order.py).

Those models are appropriate for early budgeting of:

- pressure drop,
- pump head,
- representative residence time,
- heat-exchanger duty,
- and inventory estimates.

They should be treated as the first rung in the stack, not the final thermal-hydraulics architecture.

## Fluid Governing Equations

For the fluid, the baseline equations are:

- Continuity, low-Mach variable-density form:

```text
d(rho)/dt + div(rho*u) = 0
```

- If density variation is only needed for buoyancy, an acceptable first simplification is:

```text
div(u) = 0
rho(T) = rho_ref * [1 - beta * (T - T_ref)]
```

- Momentum:

```text
rho * (du/dt + u.grad(u)) =
  -grad(p) + div[ mu * (grad(u) + grad(u)^T ) ] + rho*g + S_mom
```

- Use `S_mom = 0` in open-fluid regions.
- In porous-core regions, use a Darcy-Forchheimer sink such as:

```text
S_mom = -(mu/K)*u - C_F * rho * |u| * u / sqrt(K)
```

where `K` is permeability and `C_F` is the inertial resistance coefficient.

- Fluid energy:

```text
rho*cp * (dT/dt + u.grad(T)) =
  div(k*grad(T)) + q_vol
```

- Ignore viscous dissipation unless shear heating is shown to matter.
- Use `q_vol` for fission heating, imposed volumetric heating, or zero depending on the region.

## Molten-Salt Property Treatment

For molten salts, temperature-dependent properties are a requirement, not an optional refinement. Use explicit property models for:

- `rho = rho(T, composition)`
- `mu = mu(T, composition)`
- `k = k(T, composition)`
- `cp = cp(T, composition)`

Constant properties are acceptable only for debugging, solver shakeout, or rough sensitivity checks.

## Solids And Conjugate Heat Transfer

For graphite, vessel shells, exchanger walls, and similar structures, solve:

```text
rho_s*cp_s * d(T_s)/dt = div(k_s*grad(T_s)) + q_s,vol
```

At fluid-solid interfaces in conjugate heat transfer:

```text
T_f = T_s
-k_f * grad(T_f).n = -k_s * grad(T_s).n
```

For reduced-order models, it is usually enough to replace the resolved interface with:

```text
q" = h * (T_wall - T_bulk)
```

## Core Modeling Guidance

If the core contains many repeated passages, do not start with explicit 3D channels. Use a porous or homogenized core model:

- Local thermal equilibrium (LTE): one temperature field, suitable when graphite and salt temperatures are expected to stay close.
- Local thermal non-equilibrium (LTNE): separate fluid and solid temperatures, suitable when graphite thermal lag matters.

For LTNE porous modeling, solve:

```text
eps*rho_f*cp_f * (dT_f/dt + u.grad(T_f)) =
  div(k_f,eff*grad(T_f)) + h_as*(T_s - T_f) + q_f,vol
```

```text
(1-eps)*rho_s*cp_s * dT_s/dt =
  div(k_s,eff*grad(T_s)) + h_as*(T_f - T_s) + q_s,vol
```

where `eps` is porosity and `h_as` is interfacial heat transfer per bulk volume.

## Non-Fueled Loop Scope

For a non-fueled molten-salt loop, solve:

- continuity,
- momentum,
- fluid energy,
- solid conduction,
- and temperature-dependent material properties.

The model should explicitly include:

- pump head,
- distributed friction losses,
- minor losses,
- buoyancy,
- and heat-exchanger duty.

Expected outputs include:

- pressure drop by component,
- flow split by branch or channel family,
- bulk and wall temperatures,
- hot spots,
- residence time and mixing trends,
- heat-transfer coefficients,
- and freeze-margin locations when relevant.

## Liquid-Fueled MSR Scope

For a liquid-fueled MSR, add delayed neutron precursor transport for each precursor group `i`:

```text
d(C_i)/dt + div(u*C_i) =
  div(D_i*grad(C_i)) + S_i,fission - lambda_i*C_i
```

A common precursor source term is:

```text
S_i,fission = beta_i * sum_g(nu*Sigma_f,g*phi_g) / k_eff
```

or the equivalent time-dependent source used by the neutronics solver.

Neutronics must then be coupled back to flow and temperature. A representative multigroup diffusion form is:

```text
(1/v_g)*d(phi_g)/dt
  - div(D_g*grad(phi_g))
  + Sigma_r,g*phi_g
  = sum_g'(Sigma_s,g'->g * phi_g')
  + chi_p,g * (1-beta) * sum_g'(nu*Sigma_f,g' * phi_g')
  + sum_i(chi_d,i,g * lambda_i * C_i)
```

Whether diffusion or transport is used, the coupling logic is the same:

- flow changes precursor distribution,
- precursor distribution changes delayed neutron source,
- temperature changes density and cross sections,
- and power changes the thermal field.

This is the core reason a fueled MSR should be modeled as a coupled multiphysics problem rather than CFD-only with a volumetric heat source.

## Buoyancy, Natural Circulation, And Transients

If startup, pump coastdown, passive heat removal, or decay-heat transients matter, include gravity and thermal expansion explicitly.

Two common first-pass treatments are:

- low-Mach variable-density flow with `rho(T)`,
- or the Boussinesq approximation when density variation is modest and mainly enters through buoyancy.

Start from steady state, then add transients such as:

- loss of flow,
- loss of heat sink,
- pump coastdown,
- startup and shutdown ramps,
- and reactivity insertion for fueled salt.

## Turbulence Guidance

Choose the turbulence model from Reynolds number and geometry, not habit.

- Use laminar flow when passage Reynolds numbers are actually low and the geometry is orderly.
- Use RANS, commonly `k-omega SST`, in plena, manifolds, and separated-flow regions where turbulence is expected.
- Reserve LES or DNS for research-grade local studies rather than first-pass design work.

## Modeling Ladder

### 1D Whole-Loop Model First

This is the correct starting point for early design work.

For each branch, pipe, or component, solve:

- mass conservation through the network,
- momentum balance with friction, minor losses, pump head, and hydrostatic terms,
- and an energy balance along the loop or across lumped components.

A representative momentum form is:

```text
Delta_p = f*(L/D)*(rho*u^2/2) + sum(K_j * rho*u^2/2) - pump_head_term + hydrostatic_terms
```

Use this level for:

- pressure-drop budgeting,
- pump sizing,
- transient system studies,
- system response,
- and first temperature estimates.

### 2D Porous Or Homogenized Core Next

This is usually the best next step after the 1D model behaves sensibly.

Use it to capture:

- axial and radial temperature gradients,
- radial flow redistribution,
- power-shape effects,
- and graphite-salt heat exchange.

In most MSR design work, this step gives much more insight per unit cost than full-core 3D resolved channels.

### Local 3D CFD Last

Use 3D CFD only in regions where the geometry itself controls the answer:

- inlet manifolds,
- outlet collectors,
- upper and lower plena,
- recirculation pockets,
- stratification regions,
- freeze-risk regions,
- and heat-exchanger headers and turns.

In those local studies, solve full Navier-Stokes with conjugate heat transfer and variable properties.

## Numerical Approach

- Finite volume is the default practical choice for loop models and CFD.
- Finite element or spectral element methods are strong choices for tightly coupled multiphysics and high-order work.
- Validate steady-state balances first, then move into transient studies.

## Validation Priorities

Validation should proceed in this order:

1. Energy balance: generated heat, removed heat, and component losses.
2. Pressure balance: pump head against friction, form losses, and hydrostatic contributions.
3. Spatial and transport behavior: bulk temperatures, wall temperatures, flow split, residence time, plena mixing, and precursor inventory split between core and loop for fueled salt.

## Open-Source Tool Fit

- MOOSE Thermal Hydraulics Module: strongest fit for full-loop reduced-order thermal-hydraulics and heat structures.
- OpenFOAM: strongest free general-purpose option for local 3D CFD, conjugate heat transfer, and variable-property molten-salt flow.
- Cardinal: strong option when tight CFD-neutronics coupling is needed through MOOSE-based multiphysics workflows.
- NekRS: strong high-order CFD option for serious HPC work, but beyond the needs of an initial design pass.
- OpenMC: best-aligned open neutronics engine in this stack for power distribution and temperature-dependent neutronic feedback workflows.
- FiPy: useful for fast Python finite-volume prototypes of porous-core, scalar transport, and reduced-order loop equations.
- FEniCSx: useful for custom finite-element development of porous, thermal, and precursor-transport equations.

## Recommended Repo Roadmap

Near-term, the repository should evolve in this order:

1. Strengthen the existing 1D whole-loop model with temperature-dependent salt properties, buoyancy, branch loss accounting, and clearer component pressure-drop budgeting.
2. Add a porous-core thermal model, ideally with an LTNE option so graphite and salt temperatures can separate when needed.
3. Add delayed neutron precursor advection-decay transport for fueled-salt cases.
4. Couple neutronic power feedback to flow and temperature rather than treating power as a fixed external input.
5. Add local 3D CFD only in the few regions where the reduced-order model identifies geometry-driven uncertainty.

## Practical Recommendation

- For a non-fueled loop, start with 1D whole-loop thermal-hydraulics plus a porous heated core region, conjugate heat transfer into solids, and variable salt properties.
- For a liquid-fueled MSR, start with 1D whole-loop thermal-hydraulics, a 2D porous core, delayed neutron precursor transport, and multigroup neutronics coupled to temperature and flow.
- In both cases, delay full-core 3D CFD until the reduced-order and porous-core models are behaving sensibly and identifying a real geometry-driven uncertainty.
