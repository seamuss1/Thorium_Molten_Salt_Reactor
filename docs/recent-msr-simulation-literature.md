# Recent MSR Simulation Literature Review

This note records the 2024-2025 literature review used to guide the next realism
upgrade in this repository. The implementation change chosen from this review is
the reduced-order delayed-neutron precursor transport model now documented in
`docs/current-model-equations.md` and implemented in
`src/thorium_reactor/precursors.py`.

## Main Findings

- Lee et al. coupled Mole species transport with Griffin neutronics for the MSRE
  and explicitly tracked six delayed-neutron precursor groups with position- and
  flow-dependent primary-loop velocity. Their results reinforce that MSR
  neutronics should not treat delayed precursors as stationary-core inventory.
  Source: https://doi.org/10.1016/j.nucengdes.2023.112824

- Chen et al. used RELAP5-TMSR with one-dimensional DNP transport for MSBR
  transients and reported strong coupling among DNP redistribution, temperature
  feedback, and reactor power, especially in primary-flow transients. This
  supports making flow fraction act directly on precursor residence times.
  Source: https://doi.org/10.3390/en18030670

- Abuqudaira et al. compared reduced, conventional six-group, and expanded DNP
  representations for thermal-spectrum MSRs. Their conclusion that reduced DNP
  models can distort reactivity loss and transient power response argues for a
  configurable multi-group model rather than a single precursor lag.
  Source: https://doi.org/10.1016/j.anucene.2025.111461

- Pecora et al. derived one-dimensional delayed-neutron and decay-heat precursor
  transport equations in the SyTH system thermal-hydraulics model. The paper is
  a useful next step for extending this repository from a two-region model to
  finite-volume loop segments.
  Source: https://doi.org/10.13182/MC25-47271

- Chen et al. developed and verified ThorFPMC for coupled fission-product
  transport, highlighting that source term, decay heat, shielding, xenon poison,
  and online removal are all affected by species migration. This points to a
  future replacement for the current xenon and cleanup proxies.
  Source: https://doi.org/10.3390/en17215448

- Holler et al. presented a multiphysics and uncertainty framework for
  liquid-fueled MSRs using coupled thermal hydraulics, neutronics, inventory
  control, species distribution, optimization, and UQ. This supports the repo's
  existing transient-sweep direction and suggests that future improvements should
  keep uncertainty metadata near each closure.
  Source: https://doi.org/10.3390/app14177615

- Davidson et al. showed that accounting for moving delayed-neutron precursors
  can materially change activation source terms for primary heat-exchanger
  components in MSBR shielding calculations. This broadens the relevance of DNP
  transport beyond reactor kinetics into maintenance dose and component
  activation handoffs.
  Source: https://doi.org/10.1016/j.anucene.2023.110276

## Implemented Scope

The implemented model is intentionally a reduced-order bridge:

- six configurable DNP groups,
- separate core and external-loop inventories,
- implicit advection-decay stepping for numerical stability,
- residence-time scaling with transient flow fraction,
- cleanup removal from external-loop inventory,
- core delayed-neutron source fraction reported into transient history and
  summaries.

It is still not a spatial neutron kinetics solve, a Mole/Griffin coupling, a
RELAP5-class system model, or a finite-volume species-transport solver. The next
scientifically natural step is to replace the two-region split with the existing
`loop_segments` topology and solve the same equations per segment.
