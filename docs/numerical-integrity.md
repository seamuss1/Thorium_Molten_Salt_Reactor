# Numerical method contracts and acceptance

The numerical corrections for issues #99–#104 change generated results. Existing
bundles remain historical records; rerun a case to obtain corrected results.
These checks verify numerical behavior of screening models, not reactor validation.

## Transient observations

Scalar, Python-ensemble, NumPy, and Torch integrations use the same time grid.
The first observation is the initialized state at zero. Intervals end at the next
nominal output time, event time, or requested end time. Each state update uses the
actual interval length, including a final partial step. A requested step longer
than the duration produces one shortened step. Events are right-continuous:
controls at a boundary apply to the following interval; an end-time event changes
reported controls/reactivity without advancing state. Histories include event
boundaries as additional observations. The existing nodal response remains a
first-order Euler proxy and converges toward the analytic response as steps shrink.

## Diffusion screening and calibration

The model is `one_way_diffusion_screening_core_v2`. All three options solve a
multigroup diffusion equation. `diffusion_variant_a` and `diffusion_variant_b`
use fixed coefficient multipliers for screening; they are not SP3 or angular
transport solvers. Legacy input names `sp3` and `transport` map to these variants,
with the mapping recorded in `method_alias_migration`.

Default cross sections are synthetic screening coefficients, not evaluated nuclear
data or material-composition predictions. Configured cross-section arrays are
static; declaring a temperature grid does not interpolate a library. The small
dense generalized eigenproblem includes scattering in the loss operator and
checks the relative infinity-norm equation residual against $10^{-8}$. Failed
residuals raise an error. Direct eigensolves have no exposed iteration count.
The reported importance profile is a static response proxy, not an adjoint
eigenfunction.

`k_eff` and `raw_k_eff` report the prediction. Validation target bounds and external
reference metrics never rescale it. Optional calibration accepts a previously
determined positive `factor`, an independent `reference_id`, and a declared `scope`
under `physics_core.neutronics.calibration`. This routine does not fit a factor.
It records the unchanged factor and publishes `calibrated_k_eff` separately.
The caller must establish that the reference and scope are appropriate; a scope
label does not independently validate an out-of-sample use.

Temperature feedback uses the reactivity definition $\rho = 1 - 1/k$.
For the finite temperature perturbation $\Delta T$, the coefficient is
$10^5(1/k_{\mathrm{cold}} - 1/k_{\mathrm{hot}})/\Delta T$ in pcm per degree C.
Relative changes in $k$ are not substituted for reactivity away from criticality.

## One-way dependencies

Thermal hydraulics consumes prescribed total heat with a cosine axial shape.
Precursors consume that prescribed source and residence times. Diffusion consumes
mean thermal temperatures. Precursor source distributions affect only the reported
delayed-source importance diagnostic, not the eigenproblem. Neither the computed
neutronic power shape nor decay-heat source fractions are fed back into the thermal
energy equation. `coupling.mode` is `one_way_screening`, and
`feedback_converged` is false. Component integrity checks do not establish coupled
energy balance. No new coupled mode is claimed or enabled by these corrections.

The active thermal-cell length is active salt volume divided by flow area.
When active volume is unavailable, configured active-core height is the fallback;
vessel plenums do not lengthen a core with valid active geometry. Cell volumes and
residence times preserve those dimensions.

## Conservative ring transport

Ring unknowns are cell inventories $N_i$. An advective face transfers $N_i/\tau_i$
from one cell to the next with equal and opposite entries in the operator.
Diffusive transfer is $DA(N_i/V_i-N_j/V_j)/\Delta x$, using shared face geometry.
The steady linear system is solved directly. Each delayed-neutron and decay-heat
group records production, decay, removal, equation residual, and source-balance
residual. Both relative residuals must be at most $10^{-8}$; negative or nonfinite
inventories and singular systems fail explicitly.

Default ring diffusion is zero because residence times alone do not define a
physical diffusion length. Nonzero diffusion requires positive `loop_length_m`
under `physics_core.precursor_transport`. Core geometry comes from thermal cells;
the loop uses an equivalent constant area consistent with its total residence
time and core volumetric flow. This is a declared equivalent geometry, not a
resolved piping model. Loop cell refinement preserves total residence time without
the former per-cell 0.05-second floor. A residence time of infinity represents
zero advection in the numerical operator.

Absolute delayed-neutron sources and the physics-core `beta_eff` diagnostic are
normalized by precursor production before cleanup. Removal is retained as
`cleanup_loss_fraction`; it cannot disappear by renormalizing surviving decays.
The existing cell `delayed_neutron_source_fraction` remains the conditional spatial
distribution of decays. The separately named absolute cell fraction drives beta.

Transient absolute source fractions use fixed nominal precursor production and
can exceed one during storage or power transients. The bounded transient
`precursor_transport_loss_fraction` instead divides loop decay plus cleanup by
total instantaneous decay plus cleanup. Basis metadata distinguishes these
quantities. Scalar, NumPy, and Torch baseline annotations use the same convention.

## Depletion and solver geometry

A reaction channel removes its parent once at the total channel rate. Daughter
branches split production using their branching ratios; conflicting total rates
for branches of the same channel are rejected. Non-fission branches with ratios
summing to one conserve atom inventory in a closed chain.

OpenMC radial cells honor both declared inner and outer radii. Any gap is an
explicit void cell, including small gaps in the supplied core configurations.
Detailed-core void cells share the material cells' axial bounds. Build manifests
separate declared layer counts from OpenMC void-gap and total cell counts.

## R-Z finite-volume transport

The current model is `native_rz_finite_volume_ssprk3_v2`: one cell-average scalar
per field and cell, first-order upwind advection, finite-volume diffusion, and
SSP-RK3 time integration. Only `polynomial_order: 0` is accepted, with one degree
of freedom per field per cell. Higher polynomial requests fail. NPZ schema 2
retains the schema-1 array layout `[field, axial_cell, radial_cell]`; old arrays
were already cell averages regardless of their reported order. Historical
artifacts are readable but their order claims are not evidence of DG accuracy.
Legacy `transport_rkdg_*` metric names remain compatibility aliases for
`transport_fv_*` metrics.

Source, decay, cleanup, and outlet integrals use the RK stage weights
$1/6,1/6,2/3$. The stability bound includes the combined outgoing advection,
diffusion, and reaction rates of the actual mesh operator. Oversized configured
steps are subdivided; requested, stable, and effective steps are reported.
Each floor correction is accumulated with its propagated stage weight. Both the
physical balance residual and the residual accounting for this correction are
reported. Normalization uses initial inventory plus production and final
inventory, without a unit-inventory floor that could conceal small-field errors.

`status` is `completed` only if finite fields, balance, and relative limiter
correction satisfy `balance_tolerance` (default $10^{-8}$). A numerically rejected
solution remains inspectable as a failed artifact, and the CLI records a failed
stage and exits nonzero. Execution completion alone is not numerical acceptance.

## Verification

`tests/test_numerical_integrity.py` includes analytic response/time refinement,
target-only and geometry perturbations, a manufactured diffusion eigenproblem,
the unequal two-cell ring, source/removal balances, dimensional scaling,
manufactured diffusion mesh refinement, RK-stage balances for small inventories,
stiff reactions, outflow in both directions, and deliberately failed solves.
XPU checks are opt-in with `RUN_XPU_NUMERICAL_TESTS=1` on supported hardware.

`tests/test_audit_numerical_fixes.py` adds branched-reaction analytic solutions,
stock active-core geometry, precursor cleanup/source balances and beta response,
reactivity coefficients, and scalar/vector baseline agreement.
