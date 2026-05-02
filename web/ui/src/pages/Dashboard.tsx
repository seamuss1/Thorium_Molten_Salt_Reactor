import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Atom, CheckCircle2, Clock, FileText, PlaySquare } from "lucide-react";
import { api } from "../api";
import { MetricChart } from "../components/MetricChart";

export function Dashboard() {
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 5000 });
  const docs = useQuery({ queryKey: ["docs"], queryFn: api.docs });
  const latest = runs.data?.[0];
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
          <h1>Thorium molten-salt reactor simulation</h1>
        </div>
        <Link className="primary-action" to="/builder">
          <PlaySquare aria-hidden="true" />
          <span>New run</span>
        </Link>
      </header>

      <section className="hero-band">
        <div className="hero-copy">
          <h2>{String(latest?.reactor?.name ?? latest?.case_name ?? "Simulation workspace")}</h2>
          <div className="stat-row">
            <Stat icon={Atom} label="Cases" value={caseCount} />
            <Stat icon={Activity} label="Runs" value={runCount} />
            <Stat icon={CheckCircle2} label="Completed" value={completedCount} />
            <Stat icon={FileText} label="Docs" value={docCount} />
          </div>
        </div>
        <div className="hero-media">
          {featured ? (
            <img src={featured.url} alt={featured.label} />
          ) : (
            <ReactorReadout cases={caseCount} docs={docCount} runs={runCount} completed={completedCount} />
          )}
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="panel">
          <div className="section-title">
            <Clock aria-hidden="true" />
            <h2>Latest run</h2>
          </div>
          {latest ? (
            <>
              <div className="run-line">
                <strong>{latest.case_name}</strong>
                <span>{latest.run_id}</span>
                <mark>{latest.status}</mark>
              </div>
              <MetricChart metrics={latest.metrics} title="Latest run metrics" />
              <Link className="text-link" to={`/runs/${latest.case_name}/${latest.run_id}`}>
                Open run workspace
              </Link>
            </>
          ) : (
            <div className="empty-panel">No result bundles found.</div>
          )}
        </div>
        <div className="panel">
          <div className="section-title">
            <FileText aria-hidden="true" />
            <h2>Science library</h2>
          </div>
          <div className="doc-links">
            {docs.data?.slice(0, 6).map((doc) => (
              <Link key={doc.slug} to={`/docs/${doc.slug}`}>
                <strong>{doc.title}</strong>
                <small>{doc.path}</small>
              </Link>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function Stat({ icon: Icon, label, value }: { icon: typeof Atom; label: string; value: number }) {
  return (
    <div className="stat-item">
      <Icon aria-hidden="true" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReactorReadout({ cases, completed, docs, runs }: { cases: number; completed: number; docs: number; runs: number }) {
  return (
    <div className="reactor-readout" aria-label="Repository reactor readout">
      <div className="readout-grid" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>
      <div className="readout-table">
        <div>
          <span>Case index</span>
          <strong>{cases}</strong>
        </div>
        <div>
          <span>Run bundles</span>
          <strong>{runs}</strong>
        </div>
        <div>
          <span>Validated</span>
          <strong>{completed}</strong>
        </div>
        <div>
          <span>Science notes</span>
          <strong>{docs}</strong>
        </div>
      </div>
    </div>
  );
}
