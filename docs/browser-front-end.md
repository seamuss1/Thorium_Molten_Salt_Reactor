# Browser Front End

## Purpose

The browser front end exposes the repository as a shared lab workspace. It discovers case YAML files, reads result bundles, shows generated reports and visualization artifacts, links the science documentation, and starts safe browser-launched simulation runs.

## Runtime

Start the app with:

```powershell
.\scripts\Run-Web.cmd
```

The FastAPI backend runs on port `8000` in the Docker app runtime. A production React build under `web/ui/dist` is served by the same FastAPI process. During UI development, run Vite from `web/ui` and let it proxy `/api` to `http://localhost:8000`.

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
