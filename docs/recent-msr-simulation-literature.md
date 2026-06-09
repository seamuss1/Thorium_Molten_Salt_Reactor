# Recent MSR Simulation Literature Review

This note records the 2024-2026 literature review used to guide the next realism
upgrades in this repository. The implemented changes from this review are the
segmented delayed-neutron precursor transport model, molten-salt property
uncertainty screen, tritium distribution screen, graphite irradiation lifetime
screen, summary-derived deterministic physics-core loop residence handoff, and
static adjoint-shape weighting for flowing-fuel precursor worth. The May 17,
2026 follow-up also made the existing deterministic decay-heat precursor
transport explicit in reports and metrics with core and external-loop segment
source fractions. A June 2026 follow-up added a volatile-species transport
screen that treats gas removal as contact-limited bubble transfer plus slower
cleanup polishing. These implemented equations are documented in
`docs/current-model-equations.md`.

The May 2026 refresh prioritized primary or official-lab sources published since
2024-05-17. It did not identify a reason to replace the repository's
reduced-order architecture, but it did support focused implementation upgrades:
the deterministic finite-volume physics-core precursor handoff now uses
summary-derived external-loop residence time instead of a fixed nominal loop
time, and its reported delayed-neutron worth now weights the flowing precursor
source by the deterministic adjoint/shape importance profile. A May 2026
follow-up added an MSRE pump-transient benchmark screen so reports carry the
published one-dimensional model error envelope and flag bypass-like salt
inventory that can limit early transient reactivity predictions.

## May 17, 2026 Follow-Up

The latest 24-month screen found one focused implementation opportunity rather
than a need to replace the repository's architecture. Deng et al. published a
2026 RELAP5/RKDG kinetics module that solves delayed-neutron precursor transport
and a simplified decay-heat model consistently over primary-loop control
volumes, validating against MSRE startup, coastdown, and reactivity-insertion
tests before applying the model to MSBR transients. Acierno et al. separately
reported MSFR reduced-order thermal-fluid work that carries both six DNP groups
and three decay-heat precursor groups for future multiphysics coupling. The
repository already had a finite-volume decay-heat precursor screen inside
`physics_core`, but it was not visible enough to users.

Repository action: expose the decay-heat precursor model as a first-class
transport report, add literature source metadata, report core versus
external-loop source fractions, identify the dominant loop segment, publish
summary metrics, validate configured decay-heat groups, and document the
equations. This remains a reduced-order finite-volume screen, not the high-order
RKDG method from the 2026 paper.

Sources: https://doi.org/10.1016/j.applthermaleng.2026.129983 and
https://doi.org/10.1080/00295450.2025.2530813

Other checked items did not justify narrow code changes. The 2026 thermal-MSR
code-to-code benchmark by Pfahl et al. reinforces the existing DNP drift,
temperature-feedback, and simplified heat-exchanger direction. The 2026
multi-point depletion model by Elhareef and Wu is important for future
depletion work but would require a connected-cell isotope inventory matrix, not
a small patch. INL Virtual Test Bed MSRE lower-plenum CFD documentation
supports future channel inlet/profile validation, while the current repository
does not yet have a radial lower-plenum model to calibrate.

Sources: https://doi.org/10.1016/j.nucengdes.2026.114790,
https://doi.org/10.3390/jne7010017, and
https://virtualtestbed.inl.gov/msr/msre/lp_nekrs_model.html

## May 2026 Model Impact Assessment

The review screened each model area named in the May 2026 task and separated
science that changed the repository from science that should remain roadmap
context until higher-fidelity data or architecture exists.

| Area | Usefulness assessment | Repository action |
| --- | --- | --- |
| Reduced-order thermal hydraulics | Recent RELAP5-TMSR, SyTH, and RESTA3D work reinforces explicit loop residence, flow-fraction scaling, and finite-volume handoff patterns. The repo already had the core reduced-order closures, so the material gap was not a new TH correlation but better residence propagation into coupled precursor outputs. | Kept the reduced-order TH architecture. Used summary-derived loop residence in the deterministic precursor handoff. |
| Neutronics handoffs | Squirrel and Griffin/Pronghorn/Squirrel MSRE validation work shows that point-kinetics reductions for liquid fuel should account for spatial source importance when precursor material moves out of high-worth regions. | Changed deterministic `physics_core.beta_eff` to use static adjoint-shape-weighted flowing precursor source, while preserving `unweighted_beta_eff` for comparison. |
| Delayed-neutron precursor transport | New and recent DNP studies consistently support multi-group moving precursor treatment with core/external-loop residence effects. The existing segmented six-group model is directionally appropriate, but its deterministic handoff needed a worth-weighted metric. | Added cell-level delayed-neutron source fractions and the adjoint-weighted flowing-fuel worth report. |
| Species transport | Recent ORNL and Argonne work makes a narrower point that is implementable here: volatile-species removal is contact-limited at the gas-liquid interface, while cleanup systems act more like slower polishing stages. Full species transport still requires a finite-volume inventory solve, but that does not prevent a better reduced-order screen. | Added a volatile-species transport summary that combines loop residence, segment-weighted contact factor, gas stripping, and cleanup polishing into an effective removal fraction and Xe-135 equilibrium inventory multiplier. |
| Chemistry | Recent multiphysics and fission-product transport work supports coupling chemistry, cleanup, and species state. The available public findings did not provide a small defensible replacement for the current redox/impurity/corrosion proxy. | No chemistry model change. Existing chemistry outputs remain explicitly labeled as proxy/screening values. |
| Depletion | Current literature points toward online processing and inventory-control coupling, but implementing that well would require depletion-chain transport or SaltProc-style integration beyond this reduced-order patch. | No depletion code change. Existing depletion fields remain assumption metadata and reduced-order transient terms. |
| Validation | MSRE remains the strongest public validation anchor, and recent CAD/CSG OpenMC plus Squirrel/Griffin/Pronghorn work increases confidence that MSRE pump and natural-circulation cases are the right next validation targets. | No new benchmark dataset was added in this pass. The literature note now records the validation relevance and the implemented metric can be compared against MSRE flowing-fuel delayed-neutron worth studies. |
| Reporting | The original repository outputs reported precursor loss but not an importance-weighted delayed-neutron worth. It also lacked a concise validation note for the expected error of one-dimensional MSRE pump-transient reductions and did not surface decay-heat precursor source partitioning. | Reports now expose weighted `beta_eff`, `beta_eff_basis`, `unweighted_beta_eff`, `delayed_neutron_flow_loss_pcm`, precursor-coupling fractions, decay-heat precursor core/loop/segment source fractions, and an MSRE pump-transient benchmark screen with startup/coastdown error bands and non-active salt inventory fractions. |

## Main Findings

- Lee et al. coupled Mole species transport with Griffin neutronics for the MSRE
  and explicitly tracked six delayed-neutron precursor groups with position- and
  flow-dependent primary-loop velocity. Their results reinforce that MSR
  neutronics should not treat delayed precursors as stationary-core inventory.
  Source: https://doi.org/10.1016/j.nucengdes.2023.112824

- Lee et al. separately reported 2024 ORNL PHYSOR work on xenon-135 and
  tritium transport in the MSRE and emphasized that interfacial area and
  mass-transfer coefficients are central to predicting gas removal. This is a
  direct argument against treating volatile removal as only a fixed scalar
  cleanup fraction.
  Sources: https://doi.org/10.13182/PHYSOR24-43702 and
  https://www.ornl.gov/publication/transport-highly-volatile-gases-related-noble-gases-and-tritium-msre

- Marone et al. used a multiphysics tool to study helium-bubble fission-gas
  removal in a liquid-fueled MSR and concluded that removal efficiency depends
  strongly on bubble/interface behavior; accurate prediction ultimately needs
  three-dimensional two-phase simulation. That supports adding a screening model
  while keeping it explicitly labeled as reduced-order.
  Source: https://doi.org/10.1080/00295639.2025.2455884

- Argonne's 2024 SAM species-transport report added gas/liquid phase transport,
  two-film interphase transfer, and parent-to-daughter decay transfer for
  Xe-135-class species in MSRE-like conditions. The report is more detailed
  than this repository should emulate directly, but it supports reporting a
  loop-residence and phase-contact-aware proxy instead of a pure fixed-removal
  term.
  Source: https://publications.anl.gov/anlpubs/2024/10/191780.pdf

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

- Elhareef et al. developed a peer-reviewed MSRE pump-transient benchmark using
  simplified one-dimensional and R-Z porous-medium models. The reported mean
  reactivity-response errors for the one-dimensional model were 11-21 pcm for
  pump startup and 5-13 pcm for coastdown, while the higher-order model improved
  the early transient rate by resolving radial salt distribution and bypass
  flow. This directly motivated the new `msre_pump_transient_benchmark` report
  section and the non-active salt inventory metrics.
  Source: https://doi.org/10.1080/00295639.2025.2475650

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
- three-group decay-heat precursor source partitioning over the same
  finite-volume core and external-loop cells,
- implicit advection-decay stepping for numerical stability,
- residence-time scaling with transient flow fraction,
- cleanup removal weighted by loop segment,
- core delayed-neutron source fraction reported into transient history and
  summaries.
- property uncertainty bands in run summaries and transient sweeps,
- contact-limited volatile-species removal and Xe-135 equilibrium inventory
  screening,
- normalized tritium production/distribution accounting,
- and graphite fast-flux/lifetime screening metrics.
- and the MSRE pump-transient validation screen for one-dimensional reactivity
  response limits and bypass-like inventory awareness.

It is still not a spatial neutron kinetics solve, a Mole/Griffin coupling, a
RELAP5-class system model, or a finite-volume species-transport solver. The next
scientifically natural step is to calibrate these screens against open
cross-code outputs and, where data are available, measured operating histories.
