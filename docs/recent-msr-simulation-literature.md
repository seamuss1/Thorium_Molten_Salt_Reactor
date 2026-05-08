# Recent MSR Simulation Literature Review

This note records the 2024-2026 literature review used to guide the next realism
upgrades in this repository. The implemented changes from this review are the
segmented delayed-neutron precursor transport model, molten-salt property
uncertainty screen, tritium distribution screen, graphite irradiation lifetime
screen, summary-derived deterministic physics-core loop residence handoff, and
static adjoint-shape weighting for flowing-fuel precursor worth now documented
in `docs/current-model-equations.md`.

The May 2026 refresh prioritized primary or official-lab sources published since
2024-05-08. It did not identify a reason to replace the repository's
reduced-order architecture, but it did support focused implementation upgrades:
the deterministic finite-volume physics-core precursor handoff now uses
summary-derived external-loop residence time instead of a fixed nominal loop
time, and its reported delayed-neutron worth now weights the flowing precursor
source by the deterministic adjoint/shape importance profile.

## May 2026 Model Impact Assessment

The review screened each model area named in the May 2026 task and separated
science that changed the repository from science that should remain roadmap
context until higher-fidelity data or architecture exists.

| Area | Usefulness assessment | Repository action |
| --- | --- | --- |
| Reduced-order thermal hydraulics | Recent RELAP5-TMSR, SyTH, and RESTA3D work reinforces explicit loop residence, flow-fraction scaling, and finite-volume handoff patterns. The repo already had the core reduced-order closures, so the material gap was not a new TH correlation but better residence propagation into coupled precursor outputs. | Kept the reduced-order TH architecture. Used summary-derived loop residence in the deterministic precursor handoff. |
| Neutronics handoffs | Squirrel and Griffin/Pronghorn/Squirrel MSRE validation work shows that point-kinetics reductions for liquid fuel should account for spatial source importance when precursor material moves out of high-worth regions. | Changed deterministic `physics_core.beta_eff` to use static adjoint-shape-weighted flowing precursor source, while preserving `unweighted_beta_eff` for comparison. |
| Delayed-neutron precursor transport | New and recent DNP studies consistently support multi-group moving precursor treatment with core/external-loop residence effects. The existing segmented six-group model is directionally appropriate, but its deterministic handoff needed a worth-weighted metric. | Added cell-level delayed-neutron source fractions and the adjoint-weighted flowing-fuel worth report. |
| Species transport | Mole/Griffin and ThorFPMC results show that fission-product migration, decay heat, xenon poison, source term, and online removal are coupled. That is scientifically relevant, but a real implementation would require a finite-volume species inventory model rather than a narrow patch. | No code change in this pass. Kept as roadmap context; current xenon, cleanup, and tritium pieces remain screening proxies. |
| Chemistry | Recent multiphysics and fission-product transport work supports coupling chemistry, cleanup, and species state. The available public findings did not provide a small defensible replacement for the current redox/impurity/corrosion proxy. | No chemistry model change. Existing chemistry outputs remain explicitly labeled as proxy/screening values. |
| Depletion | Current literature points toward online processing and inventory-control coupling, but implementing that well would require depletion-chain transport or SaltProc-style integration beyond this reduced-order patch. | No depletion code change. Existing depletion fields remain assumption metadata and reduced-order transient terms. |
| Validation | MSRE remains the strongest public validation anchor, and recent CAD/CSG OpenMC plus Squirrel/Griffin/Pronghorn work increases confidence that MSRE pump and natural-circulation cases are the right next validation targets. | No new benchmark dataset was added in this pass. The literature note now records the validation relevance and the implemented metric can be compared against MSRE flowing-fuel delayed-neutron worth studies. |
| Reporting | The original repository outputs reported precursor loss but not an importance-weighted delayed-neutron worth. That made the deterministic output less decision-useful for liquid-fuel kinetics comparisons. | Reports now expose weighted `beta_eff`, `beta_eff_basis`, `unweighted_beta_eff`, `delayed_neutron_flow_loss_pcm`, and precursor-coupling fractions through the physics-core JSON. |

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
  finite-volume loop segments with physically meaningful residence times.
  Source: https://doi.org/10.13182/MC25-47271

- Elhareef and Wu benchmarked a consistent point-reactor kinetics method against
  MSRE reactivity insertion and natural-circulation tests while solving delayed
  precursor concentration as part of the thermal-hydraulics/species-transport
  model. This reinforces treating loop residence and flow as first-class inputs
  to the precursor handoff, not as a hard-coded constant.
  Source: https://doi.org/10.1016/j.anucene.2025.111366

- Pfahl et al. developed the MOOSE-based Squirrel point-kinetics solver and
  emphasized that liquid-fuel transient reactivity should weight moving delayed
  neutron precursors by a static adjoint or neutron-shape importance function
  when reducing spatial dynamics to a point-kinetics handoff. A related
  DTU/INL/Idaho National Laboratory validation study compared Squirrel, Griffin,
  and Pronghorn against MSRE zero-power pump transients and natural-circulation
  tests; it reported good agreement for point and spatial dynamics models and
  quantified roughly 35% flowing-fuel delayed-neutron worth loss in MSRE cases.
  This directly motivated the new deterministic `physics_core` adjoint-weighted
  delayed-neutron worth report.
  Sources: https://doi.org/10.1080/00295639.2025.2494182 and
  https://doi.org/10.3389/fnuen.2025.1617048

- Yilmaz et al. compared CAD and CSG OpenMC models for the MSRE and reiterated
  that MSRE remains one of the few high-value validation anchors for liquid-fuel
  MSRs. This supports the repository's MSRE benchmark-harness direction but did
  not justify a reduced-order model change by itself.
  Source: https://doi.org/10.3389/fnuen.2024.1385478

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

## China Thorium And TMSR Leads

- Wang et al. extended RESTA3D for MSR transient safety analysis with
  multi-channel thermal hydraulics, delayed-neutron precursor transport, and
  few-group cross sections generated by TMSR-LINK/OpenMC. Their public 2 MWth
  TMSR operating point reports 696 active fuel channels, 873 K inlet, 893 K
  outlet, 55 kg/s fuel-salt mass flow, and 55.25 s residence time outside the
  core. This directly motivated the new loop-segment precursor model and the
  source-linked benchmark operating-point dataset.
  Source: https://doi.org/10.3390/en19040964

- Poo summarized China's Wuwei TMSR milestones: first criticality in October
  2023, 100% operating capacity in June 2024, and reported thorium-to-uranium
  conversion in November 2025. This turned the TMSR-LF1 benchmark note from a
  purely surrogate context into a partially literature-backed operating-point
  record.
  Source: https://doi.org/10.1093/nsr/nwaf509

- Zeng et al. simulated tritium distribution in a 10 MWe TMSR and found that
  unmitigated tritium can substantially permeate out of the system, while
  MSRE-like spray-gas removal can remove roughly two-thirds and lower permeation
  toward about 10%. This motivated the normalized tritium distribution screen.
  Source: https://doi.org/10.1016/j.anucene.2023.110272

- Kang et al. optimized small modular TMSR core parameters to flatten fast
  neutron flux and extend graphite lifetime, using a graphite fast-fluence
  limit around `3e22 n/cm2` above 0.05 MeV. This motivated the fuel-volume,
  control-channel, and fast-flux peaking screen.
  Source: https://doi.org/10.3390/jne5020012

- Zhong et al. compared TMSR graphite component deformation and reported that a
  hexagonal prism assembly deformed more slowly than a round-channel assembly
  under comparable conditions. This informed the optional graphite assembly
  credit used by the screening model.
  Source: https://doi.org/10.3390/en17112469

- Wang et al. propagated FLiNaK thermophysical-property uncertainties through a
  TMSR-SF0 RELAP5 model and reported default 95% bands of 2% density and 10%
  heat capacity, conductivity, and viscosity, with about 10 C uncertainty in
  core outlet temperature. This is now the default property-uncertainty screen.
  Source: https://doi.org/10.1016/j.net.2023.11.016

## Implemented Scope

The implemented models are intentionally reduced-order bridges:

- six configurable DNP groups,
- core plus configurable external-loop segment inventories,
- summary-derived external-loop residence time for deterministic
  finite-volume precursor handoffs,
- adjoint-shape-weighted deterministic beta-effective and flowing-fuel delayed
  neutron loss metrics,
- implicit advection-decay stepping for numerical stability,
- residence-time scaling with transient flow fraction,
- cleanup removal weighted by loop segment,
- core delayed-neutron source fraction reported into transient history and
  summaries.
- property uncertainty bands in run summaries and transient sweeps,
- normalized tritium production/distribution accounting,
- and graphite fast-flux/lifetime screening metrics.

It is still not a spatial neutron kinetics solve, a Mole/Griffin coupling, a
RELAP5-class system model, or a finite-volume species-transport solver. The next
scientifically natural step is to calibrate these screens against open
cross-code outputs and, where data are available, measured operating histories.
