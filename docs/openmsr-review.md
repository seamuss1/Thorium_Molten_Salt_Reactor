# OpenMSR Review And Novelty Roadmap

This note captures how the `openmsr` ecosystem can sharpen the direction of this repository without turning it into a clone of the MSRE work.

## What OpenMSR Does Well

- `openmsr/msre` treats the reactor as a benchmark stack, not just a geometry file. The repository ties together CAD, OpenMC, control-rod studies, temperature-coefficient work, depletion analysis, and heat-exchanger thermohydraulics.
- `openmsr/CAD_to_OpenMC` shows a practical path from STEP CAD to OpenMC-ready h5m geometry.
- `openmsr/msrDynamics` focuses on flowing-fuel transient behavior, where transport delays and thermal-hydraulic coupling matter.
- `openmsr/ca_depletion_chains` handles MSR-specific depletion concerns such as volatile and fission-product removal assumptions.
- `openmsr/msr-archive` makes the literature searchable enough to support benchmark-grade traceability.

## Where This Repo Is Already Strong

- The case format is compact and readable, which makes it a better experimentation surface than many benchmark repositories.
- The current workflow already connects geometry generation, OpenMC export, validation, reporting, and a first-pass balance-of-plant model.
- The detailed `tmsr_lf1_core` case is a good platform for trying new ideas because its geometry is procedural, testable, and already exposes channel families and vessel structure.

## Biggest Gaps To Close

- The benchmark assumptions are still surrogate-heavy and only lightly tied to source material.
- The workflow is mostly steady-state. It does not yet express transients, delayed transport, or cleanup scenarios.
- The geometry pipeline is CSG-only, so there is no cross-check between procedural geometry and a CAD-derived representation.
- The plant model is not yet coupled back to neutronic state changes or control actions.

## The Most Novel Direction

Build an evidence-linked reactor twin for a TMSR-inspired system.

That means one case definition drives:

- procedural CSG geometry for fast iteration,
- optional CAD-to-h5m geometry for benchmark comparison,
- steady-state and transient operating scenarios,
- depletion and salt-cleanup variants,
- and source-linked validation claims with explicit confidence tags.

The novelty is not any single physics feature. It is the combination of open traceability, dual geometry representations, and flowing-fuel dynamics in one reproducible workflow.

## Recommended Roadmap

1. Add source traceability as a first-class artifact.
2. Add a transient subsystem for nodal temperature and reactivity response.
3. Add depletion-chain selection and cleanup scenarios.
4. Add an optional CAD-backed benchmark path for one canonical case.
5. Compare CSG, CAD, steady-state, and transient outputs in the same report bundle.

## Suggested First Implementations

- Introduce confidence-labeled benchmark evidence for each validation target.
- Add a `transient` command that runs a simplified nodal or point-kinetics scenario from config.
- Add a `depletion_chain` selector to case configs so cleanup assumptions are explicit.
- Add one benchmark notebook or report that compares CSG and CAD-derived keff trends for the same core.

## Success Criteria

- A generated report can answer not only "what did the model compute?" but also "why should I trust this assumption?" and "how sensitive is it to fuel motion, cleanup, and geometry representation?"
