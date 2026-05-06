# MSRE First Criticality Assumptions

This dossier is intentionally conservative. It records public-source facts that
can be used immediately and labels the remaining benchmark-grade geometry and
material inputs as unresolved until checked against the IRPhEP MSRE evaluation.

## Current Repository Model

The current `msre_first_criticality` case is an illustrative OpenMC handoff
harness. It is useful for exercising result bundles, validation residuals,
reporting, and source traceability, but it is not yet a benchmark-grade
reconstruction of the MSRE first criticality experiment.

## Source-Backed Facts Now Encoded

- The validation anchor is the MSRE U-235 first criticality experiment.
- The experiment state is zero power, stationary salt, and uniform temperature.
- The evaluated benchmark-model keff target is 0.99978 with 420 pcm uncertainty.
- The public Serpent reference calculation reported keff of 1.02132 with 3 pcm uncertainty.
- The U-235 concentration reactivity coefficient is a future differential validation target.

## Blocking Assumptions

- Geometry remains a simplified channel surrogate until the full graphite lattice,
  axial regions, vessel/reflector structures, and central control/sample regions
  are reconstructed from the evaluated benchmark.
- Material composition remains incomplete until fuel salt isotopics, graphite
  impurity content, graphite thermal scattering treatment, and INOR-8 or
  Hastelloy-N structural composition are source-indexed.
- A solver-backed OpenMC result bundle is not publishable until statepoint
  statistics, convergence behavior, and cross-section library metadata are
  captured in the report.

## Promotion Rule

Do not mark this case benchmark-ready until the quality gates in the generated
benchmark report all pass. A broad keff band alone is not evidence of scientific
validity.
