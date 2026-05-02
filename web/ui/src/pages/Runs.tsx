import { useEffect, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CheckCircle2, Clock, XCircle } from "lucide-react";
import { api } from "../api";
import { MetricChart } from "../components/MetricChart";
import { RunArtifacts } from "../components/RunArtifacts";

export function Runs() {
  const navigate = useNavigate();
  const params = useParams();
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 5000 });
  const selected = useMemo(() => {
    if (params.caseName && params.runId) return { caseName: params.caseName, runId: params.runId };
    const first = runs.data?.[0];
    return first ? { caseName: first.case_name, runId: first.run_id } : null;
  }, [params.caseName, params.runId, runs.data]);
  const detail = useQuery({
    queryKey: ["run", selected?.caseName, selected?.runId],
    queryFn: () => api.run(selected!.caseName, selected!.runId),
    enabled: Boolean(selected)
  });

  useEffect(() => {
    if (!detail.data || !["queued", "running"].includes(detail.data.status)) return;
    const source = new EventSource(`/api/runs/${detail.data.case_name}/${detail.data.run_id}/events`);
    source.addEventListener("run", () => {
      queryClient.invalidateQueries({ queryKey: ["run", detail.data?.case_name, detail.data?.run_id] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    });
    return () => source.close();
  }, [detail.data, queryClient]);

  return (
    <div className="page split-page">
      <section className="list-panel">
        <div className="section-title">
          <Activity aria-hidden="true" />
          <h1>Runs</h1>
        </div>
        <div className="run-list">
          {runs.data?.map((run) => (
            <button
              key={`${run.case_name}-${run.run_id}`}
              type="button"
              className={run.case_name === selected?.caseName && run.run_id === selected.runId ? "selected" : ""}
              onClick={() => navigate(`/runs/${run.case_name}/${run.run_id}`)}
            >
              <strong>{run.case_name}</strong>
              <span>{run.run_id}</span>
              <mark>{run.status}</mark>
            </button>
          ))}
        </div>
      </section>
      <section className="detail-panel">
        {detail.data ? (
          <>
            <header className="page-header compact">
              <div>
                <p className="eyebrow">{detail.data.case_name}</p>
                <h1>{detail.data.run_id}</h1>
              </div>
              <Link className="secondary-action" to={`/viewer/${detail.data.case_name}/${detail.data.run_id}`}>
                Open 3D
              </Link>
            </header>
            <div className="timeline">
              {(detail.data.command_plan.length ? detail.data.command_plan : ["build", "run", "validate", "report"]).map((phase) => (
                <div key={phase} className={phase === detail.data?.phase ? "current" : ""}>
                  {iconForPhase(detail.data!.status, phase === detail.data!.phase)}
                  <span>{phase}</span>
                </div>
              ))}
            </div>
            {detail.data.latest_event && (
              <div className="event-banner">
                <Clock aria-hidden="true" />
                <span>{detail.data.latest_event.message}</span>
              </div>
            )}
            <section className="two-column">
              <div className="panel">
                <MetricChart metrics={detail.data.metrics} title="Run metrics" />
              </div>
              <div className="panel">
                <h2>Run state</h2>
                <dl className="fact-list">
                  <div>
                    <dt>Status</dt>
                    <dd>{detail.data.status}</dd>
                  </div>
                  <div>
                    <dt>Phase</dt>
                    <dd>{detail.data.phase ?? "n/a"}</dd>
                  </div>
                  <div>
                    <dt>Artifacts</dt>
                    <dd>{detail.data.artifacts.length}</dd>
                  </div>
                  <div>
                    <dt>Capabilities</dt>
                    <dd>{detail.data.capabilities.join(", ") || "n/a"}</dd>
                  </div>
                </dl>
              </div>
            </section>
            <RunArtifacts artifacts={detail.data.artifacts} />
          </>
        ) : (
          <div className="empty-panel tall">No run selected.</div>
        )}
      </section>
    </div>
  );
}

function iconForPhase(status: string, current: boolean) {
  if (status === "failed" && current) return <XCircle aria-hidden="true" />;
  if (status === "completed") return <CheckCircle2 aria-hidden="true" />;
  return <Clock aria-hidden="true" />;
}
