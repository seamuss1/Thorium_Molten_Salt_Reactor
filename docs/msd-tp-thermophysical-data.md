# MSD-TP Thermophysical Data

This repository carries a public-safe MSD-TP provider for source-backed molten-salt thermophysical-property evaluation. The provider can read locally supplied ORNL MSD-TP CSV data, but the public repository does not bundle the extracted MSD-TP CSV files until redistribution rights are confirmed.

## Runtime Scope

The runtime implementation is in `src/thorium_reactor/msd_tp.py`. It reads CSV files from a property-spec `data_dir` or from the `THORIUM_REACTOR_MSD_TP_DATA_DIR` environment variable. It does not require the Windows GUI, the HDF5 database, Saline, network access, or a database service during simulation.

Supported providers:

| Provider | Use | Guardrails |
| --- | --- | --- |
| `msd_tp` | Direct MSD-TP records for density, dynamic viscosity, heat capacity, and thermal conductivity | Requires explicit formula and exact mole-fraction composition for mixtures; fails outside reported temperature range unless `allow_extrapolation: true` is set |
| `msd_tp_redlich_kister` | Binary Redlich-Kister mixture estimates for density and dynamic viscosity | Binary mixtures only; requires explicit composition; fails outside the Redlich-Kister model temperature range |

The flow evaluator carries source metadata into `property_sources`, `source_backing`, property audits, reduced-order flow summaries, and reports. This is intentionally conservative: a case with MSD-TP density and configured heat capacity is reported as `partial`, not fully source-backed.

## Data Provenance

The first local snapshot inspected during development was extracted from `MSTDBTP_GUI_windows_x86.exe` version `0.0.3` from ORNL Code Package Registry package `16582`, SHA-256 `b1f30615b7fba8a0d5b162b359177371bcbbb2c7d0ab300c9739e4bcc7d817a6`.

The source project pages used for acquisition and parity review are:

- ORNL MSD-TP GUI: https://msd.ornl.gov/gui-tp/
- NEAMS Saline API: https://code.ornl.gov/neams/saline

The GUI and Saline are useful acquisition and cross-check tools. They are not runtime dependencies.

Before publishing MSD-TP data files publicly, confirm that the CSV data may be redistributed under terms compatible with this repository. The ORNL MSD-TP and Saline pages identify the project and API, but the public Saline GitLab project is marked with an `Other` license rather than a standard open-data license. The public-safe branch therefore keeps the provider behind a user-supplied data path.

## Acquisition Policy

Updates to local or privately distributed MSD-TP datasets must be reproducible:

1. Record the upstream package version, URL, asset name, file size, and SHA-256.
2. Extract data for local/private use into an ignored workspace directory, or into a separate private data package with a documented license.
3. Record SHA-256 checksums for each local/private CSV.
4. Keep generated extraction artifacts under `.tmp/`; do not commit GUI executables, HDF5 databases, caches, or dependency build trees.
5. Add or update parity tests against a known Saline calculation or independently evaluated formula.
6. Update this document when source scope, formulas, licensing, or limitations change.

## Formula Conventions

MSD-TP temperatures are evaluated in kelvin. Runtime values are converted to repository units before they enter flow calculations.

Direct density records use:

$$
\rho = A - B T
$$

Direct dynamic-viscosity records use the MSD-TP Arrhenius form:

$$
\mu = A \exp \left(\frac{B/R}{T}\right)
$$

or the logarithmic form when the third coefficient is present:

$$
\mu = 10^{A + B/T + C/T^2}
$$

Thermal conductivity uses:

$$
k = A + BT
$$

Heat-capacity records are evaluated as molar heat capacity and converted to mass-specific heat capacity with the record molecular weight:

$$
C_p = A + BT + C/T^2 + DT^2
$$

Binary Redlich-Kister models are evaluated for density and dynamic viscosity only. Density uses an ideal molar-volume blend plus excess volume; viscosity uses logarithmic ideal mixing plus Redlich-Kister excess terms.

## Case Usage

To enable MSD-TP data in a local case, add `provider: msd_tp`, an exact `formula`, a matching `composition`, and either `data_dir` or `THORIUM_REACTOR_MSD_TP_DATA_DIR`. Public example cases keep configured properties so the repository remains runnable without redistributed third-party data.

Other properties should remain configured constants or local correlations unless the exact MSD-TP composition/property pair exists. This avoids claiming source backing for heat capacity, conductivity, or viscosity when local data only supports density for the selected composition.

## Limitations

- The provider does not search for nearby compositions or interpolate across direct records.
- Ternary and higher Redlich-Kister mixtures are not implemented.
- Sparse MSD-TP properties remain sparse in the simulation; missing properties should stay configured and visibly marked as such.
- Source uncertainty is used only when MSD-TP reports a 95% fraction for that property. Otherwise the literature-model defaults or case overrides remain in force.
- A source-backed thermophysical property does not validate the reactor model by itself. Whole-loop validation still depends on geometry, heat-exchanger assumptions, pressure-loss closures, neutronics coupling, and benchmark evidence.
