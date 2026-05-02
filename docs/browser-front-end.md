# Browser Front End

## Purpose

The browser front end exposes the repository as a shared lab workspace. It discovers case YAML files, reads result bundles, shows generated reports and visualization artifacts, links the science documentation, and starts safe browser-launched simulation runs.

## Runtime

Start the app with:

```powershell
.\scripts\Run-Web.cmd
```

The FastAPI backend runs on port `18488` in the Docker web runtime. A production React build under `web/ui/dist` is served by the same FastAPI process, so normal browser use only needs:

```text
http://localhost:18488
```

The wrapper builds `web/ui/dist` when it is missing. If the UI build is already current, use:

```powershell
.\scripts\Run-Web.cmd -SkipUiBuild
```

During focused UI development, Vite can still be run from `web/ui` for hot reload and proxies `/api` to `http://localhost:18488`. That is a development convenience only; the documented app runtime is the one-port FastAPI service.

## Run Safety

Browser-launched runs use bundle-local snapshots. The backend writes `case_snapshot.yaml`, optional `benchmark_snapshot.yaml`, `provenance.json`, `job_status.json`, and `job_events.ndjson` into `results/<case>/<run_id>/` before invoking the CLI. Canonical files under `configs/cases` are not modified.

The browser command allowlist is intentionally limited to:

- `build`
- `run --no-solver`
- `transient`
- `transient-sweep`
- `validate`
- `render`
- `report`

OpenMC benchmark runs and external integration execution remain visible through existing result artifacts but are not browser-launchable in the first version.

## Main Screens

- Dashboard: current lab status, latest run, featured visuals, and quick links.
- Cases: reactor metadata, capabilities, editable inputs, docs, and latest outputs.
- Builder: draft-per-run parameter edits and safe workflow selection.
- Runs: live job state, streamed events, metrics, reports, plots, and raw data.
- Science: Markdown documentation from `README.md` and `docs/*.md`.
- 3D: glTF geometry viewer with image fallback.

## Science Documents

The Science view indexes `README.md` and every Markdown file under `docs`. New `.md` files appear automatically; use the first H1 as the document title so the library remains readable.

Equations are rendered with KaTeX. Use inline math for compact terms such as `$k_{eff}$`, and display math for model equations:

```markdown
$$
\rho = \frac{k_{eff} - 1}{k_{eff}}
$$
```

Generated reports use the same Markdown renderer, so report equations and documentation equations should share the same syntax.

## Development Checks

For frontend changes:

```powershell
cd web\ui
npm.cmd run build
npm.cmd run test
```

For backend or full-stack checks, keep using the repo wrappers:

```powershell
.\scripts\Run-Tests.cmd
.\scripts\Run-Web.cmd
```

Before handing off visible web changes, verify `http://localhost:18488/api/health` and click through Dashboard, Cases, Builder, Runs, Science, and 3D. The 3D page should load glTF exports from result bundles when present and fall back to generated images when a 3D asset is not available.
