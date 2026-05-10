# Web Lab

The browser lab turns the repository into a shared simulation console. FastAPI serves both the JSON API and the production React build on one browser-facing port:

```text
http://localhost:18488
```

Start it with:

```powershell
.\scripts\Run-Web.cmd
```

Use `.\scripts\Run-Web.cmd -SkipUiBuild` only when `web/ui/dist` is already current.

## Architecture

```text
web/ui/src/                 React application
web/ui/dist/                Production build served by FastAPI
src/thorium_reactor/web/    FastAPI app, repository adapter, job runner, permissions
configs/cases/              Case discovery source
results/                    Run and artifact discovery source
docs/                       Science library source
README.md                   Front-page science document source
```

FastAPI owns the public port. Vite is only a development convenience while editing `web/ui`; it proxies `/api` to `http://localhost:18488`.

## Screens

| Screen | Route | Notes |
| --- | --- | --- |
| Dashboard | `/` | Portfolio counts, latest bundles, featured outputs, science links |
| Simulations | `/cases` | Case metadata, capabilities, editable inputs, latest outputs |
| Builder | `/builder` | Draft-per-run parameter editing and safe workflow selection |
| Run log | `/runs` and `/runs/:case/:runId` | Job status, metrics, reports, plots, raw JSON, artifacts |
| Science | `/docs` and `/docs/:slug` | Markdown from `README.md` and `docs/*.md`, with KaTeX math |
| 3D | `/viewer` and `/viewer/:case/:runId` | glTF viewer with image fallback |
| Admin | `/admin` | Local/deployed run-limit reset view for configured admins |

## API Shape

The API is intentionally filesystem-backed. Important endpoints include:

| Endpoint | Purpose |
| --- | --- |
| `/api/health` | Liveness check plus repo root |
| `/api/me` | Local or Access-derived identity, admin status, run-limit state |
| `/api/cases` | Case index and editable parameter metadata |
| `/api/runs` | Result bundle index |
| `/api/runs/{case}/{run_id}` | Bundle details, report, metrics, plots, artifacts |
| `/api/docs` | Markdown document index |
| `/api/docs/{slug}` | Markdown document content |
| `/api/runs` `POST` | Start an allowed workflow phase |

## Run Safety

Browser-launched runs are intentionally narrower than the CLI:

- Allowed: `build`, `run --no-solver`, `transient`, `transient-sweep`, `validate`, `render`, `report`.
- Not browser-launchable by default: solver-backed OpenMC benchmarks and external integrations.
- Each browser job writes an isolated bundle under `results/<case>/<run_id>/`.
- Drafted parameters are written to `case_snapshot.yaml` inside the bundle.
- Canonical YAML under `configs/cases` stays untouched.

This split keeps the browser useful for exploration without making it a general remote execution surface.

## Access And Rate Limits

The deployed Docker service can require Cloudflare Access identity:

| Variable | Meaning |
| --- | --- |
| `THORIUM_REACTOR_ACCESS_REQUIRED` | When `1`, run starts require an authenticated Access email header |
| `THORIUM_REACTOR_ADMIN_EMAILS` | Comma-separated emails with unlimited starts and Admin view access |
| `THORIUM_REACTOR_RATE_LIMIT_PER_DAY` | Non-admin daily run-start limit |
| `THORIUM_REACTOR_RATE_LIMIT_TIMEZONE` | Day boundary for rate limits |
| `THORIUM_REACTOR_RATE_LIMIT_PATH` | Optional path for the rate-limit state JSON |

The local `Run-Web` wrapper disables the Access requirement for development unless `-RequireAccessIdentity` is supplied.

## Frontend Development

From `web/ui`:

```powershell
npm.cmd run build
npm.cmd run test
```

The app uses React, React Query, React Router, ECharts, Three.js via React Three Fiber, lucide icons, and KaTeX-enabled Markdown rendering.

Before handing off visible UI changes, check:

```text
http://localhost:18488/api/health
```

Then click through Dashboard, Simulations, Builder, Run log, Science, and 3D.

## Screenshot Figures

The README dashboard and builder figures were captured from the running web app and stored in [resources/readme](../resources/readme). They should be refreshed after material UI changes.
