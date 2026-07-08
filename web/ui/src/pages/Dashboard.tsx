import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Atom, CheckCircle2, Clock, FileText, Gauge, PlaySquare } from "lucide-react";
import { api } from "../api";
import { Truncate } from "../components/Truncate";
import { PanelError, PanelLoading, EmptyState } from "../components/StateBlock";
import type { RunRecord } from "../types";

export function Dashboard() {
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 5000 });
  const docs = useQuery({ queryKey: ["docs"], queryFn: api.docs });
  const latestCase = cases.data?.find((item) => item.latest_run) ?? cases.data?.[0];
  const caseCount = cases.data?.length ?? 0;
  const runCount = runs.data?.length ?? 0;
  const completedCount = runs.data?.filter((run) => run.status === "completed").length ?? 0;
  const docCount = docs.data?.length ?? 0;
  const featured =
    runs.data
      ?.flatMap((run) => run.artifacts)
      .find((artifact) => artifact.label.includes("hero_cutaway") || artifact.label.includes("annotated_cutaway")) ??
    runs.data?.flatMap((run) => run.artifacts).find((artifact) => artifact.mime_type.startsWith("image/"));

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Shared lab server</p>
          <h1>
            <Truncate lines={2}>Thorium molten-salt reactor simulation</Truncate>
          </h1>
        </div>
        <Link className="primary-action" to="/builder">
          <PlaySquare aria-hidden="true" />
          <span>New run</span>
        </Link>
      </header>

      <section className="hero-band">
        <div className="hero-copy">
          <h2>
            <Truncate lines={2}>{String(latestCase?.reactor?.name ?? latestCase?.name ?? "Simulation workspace")}</Truncate>
          </h2>
          <div className="stat-row">
            <Stat icon={Atom} label="Simulation types" value={statValue(cases.isLoading, caseCount)} />
            <Stat icon={Activity} label="Result bundles" value={statValue(runs.isLoading, runCount)} />
            <Stat icon={CheckCircle2} label="Completed" value={statValue(runs.isLoading, completedCount)} />
            <Stat icon={FileText} label="Docs" value={statValue(docs.isLoading, docCount)} />
          </div>
        </div>
        <div className="hero-media">
          {featured ? <img src={featured.url} alt={featured.label} /> : <ReactorReadout runs={runs.data} docCount={docCount} caseCount={caseCount} />}
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="panel">
          <div className="section-title">
            <Clock aria-hidden="true" />
            <h2>Simulation portfolio</h2>
          </div>
          {cases.isLoading ? (
            <PanelLoading label="Loading simulations" lines={4} />
          ) : cases.isError ? (
            <PanelError error={cases.error} onRetry={() => cases.refetch()} />
          ) : cases.data?.length ? (
            <div className="doc-links">
              {cases.data.slice(0, 6).map((item) => (
                <Link key={item.name} to={`/cases/${item.name}`}>
                  <Truncate className="list-title">{String(item.reactor.name ?? item.name)}</Truncate>
                  <Truncate className="list-meta">
                    {[item.reactor.family, item.reactor.mode, item.latest_run?.status].filter(Boolean).join(" · ") || item.name}
                  </Truncate>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState icon={Atom}>No simulation types found.</EmptyState>
          )}
          <Link className="text-link" to="/cases">
            Open simulation outputs
          </Link>
        </div>
        <div className="panel">
          <div className="section-title">
            <FileText aria-hidden="true" />
            <h2>Science library</h2>
          </div>
          {docs.isLoading ? (
            <PanelLoading label="Loading documents" lines={4} />
          ) : docs.isError ? (
            <PanelError error={docs.error} onRetry={() => docs.refetch()} />
          ) : docs.data?.length ? (
            <div className="doc-links">
              {docs.data.slice(0, 6).map((doc) => (
                <Link key={doc.slug} to={`/docs/${doc.slug}`}>
                  <Truncate className="list-title">{doc.title}</Truncate>
                  <Truncate className="list-meta">{doc.path}</Truncate>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState icon={FileText}>No documents found.</EmptyState>
          )}
        </div>
      </section>
    </div>
  );
}

function statValue(isLoading: boolean, value: number): ReactNode {
  return isLoading ? "—" : value;
}

function Stat({ icon: Icon, label, value }: { icon: typeof Atom; label: string; value: ReactNode }) {
  return (
    <div className="stat-item">
      <Icon aria-hidden="true" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

const TONE_VAR: Record<string, string> = { ok: "var(--ok)", info: "var(--info)", danger: "var(--danger)", neutral: "var(--line-strong)" };

function statusBuckets(runList: RunRecord[]) {
  const counts = { ok: 0, info: 0, danger: 0, neutral: 0 };
  for (const run of runList) {
    if (run.status === "completed") counts.ok += 1;
    else if (run.status === "running" || run.status === "queued") counts.info += 1;
    else if (run.status === "failed" || run.status === "canceled") counts.danger += 1;
    else counts.neutral += 1;
  }
  return [
    { tone: "ok" as const, label: "Completed", count: counts.ok },
    { tone: "info" as const, label: "Running", count: counts.info },
    { tone: "danger" as const, label: "Failed", count: counts.danger },
    { tone: "neutral" as const, label: "Other", count: counts.neutral }
  ].filter((bucket) => bucket.count > 0);
}

function ReactorReadout({ runs, caseCount, docCount }: { runs?: RunRecord[]; caseCount: number; docCount: number }) {
  const runList = runs ?? [];
  const completed = runList.filter((run) => run.status === "completed").length;
  const buckets = statusBuckets(runList);
  const total = runList.length || 1;

  return (
    <div className="reactor-readout" aria-label="Repository reactor readout">
      <div className="status-strip">
        <div className="readout-caption">
          <Gauge aria-hidden="true" />
          <span>Run status</span>
        </div>
        {runList.length ? (
          <>
            <div className="status-strip-bar" role="img" aria-label={buckets.map((bucket) => `${bucket.count} ${bucket.label}`).join(", ")}>
              {buckets.map((bucket) => (
                <span key={bucket.tone} className={`seg tone-${bucket.tone}`} style={{ width: `${(bucket.count / total) * 100}%` }} />
              ))}
            </div>
            <div className="status-strip-legend">
              {buckets.map((bucket) => (
                <span key={bucket.tone}>
                  <i style={{ background: TONE_VAR[bucket.tone] }} />
                  {bucket.label} · {bucket.count}
                </span>
              ))}
            </div>
          </>
        ) : (
          <div className="status-strip-legend">
            <span>No result bundles yet.</span>
          </div>
        )}
      </div>
      <div className="readout-table">
        <div>
          <span>Simulation types</span>
          <strong>{caseCount}</strong>
        </div>
        <div>
          <span>Run bundles</span>
          <strong>{runList.length}</strong>
        </div>
        <div>
          <span>Completed</span>
          <strong>{completed}</strong>
        </div>
        <div>
          <span>Science notes</span>
          <strong>{docCount}</strong>
        </div>
      </div>
    </div>
  );
}
