import { useEffect, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowLeft, Atom, CheckCircle2, Clock, XCircle } from "lucide-react";
import { api } from "../api";
import { Truncate } from "../components/Truncate";
import { StatusBadge } from "../components/StatusBadge";
import { ProgressBar } from "../components/ProgressBar";
import { PanelError, PanelLoading, EmptyState } from "../components/StateBlock";
import { RunDataExplorer } from "../components/RunDataExplorer";
import { RunOutputSections } from "../components/RunOutputSections";
import { RunArtifacts } from "../components/RunArtifacts";
import { hasViewableGeometry } from "../geometryArtifacts";

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

  // Depend on the run's identity and whether it is live -- not on `detail.data`,
  // whose object identity changes on every refetch. The listener below triggers
  // exactly those refetches, so depending on the data made the effect re-run on
  // every event: each one tore down the EventSource, opened a new one, and
  // replayed the whole event log from offset zero.
  const caseName = detail.data?.case_name;
  const runId = detail.data?.run_id;
  const isLive = Boolean(detail.data && ["queued", "running"].includes(detail.data.status));

  useEffect(() => {
    if (!isLive || !caseName || !runId) return;
    const source = new EventSource(`/api/runs/${caseName}/${runId}/events`);
    source.addEventListener("run", () => {
      queryClient.invalidateQueries({ queryKey: ["run", caseName, runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    });
    return () => source.close();
  }, [isLive, caseName, runId, queryClient]);

  const active = isLive;
  // Progress comes from the run's own status, not from whichever event happened
  // to arrive last: log lines carry no progress, so reading the latest event
  // made the bar flip to indeterminate every time a phase printed a line.
  const progress = detail.data?.progress ?? detail.data?.latest_event?.progress;

  return (
    <div className="page split-page">
      <section className="list-panel">
        <div className="section-title">
          <Activity aria-hidden="true" />
          <h1>Runs</h1>
        </div>
        {runs.isLoading ? (
          <PanelLoading label="Loading runs" lines={6} />
        ) : runs.isError ? (
          <PanelError error={runs.error} onRetry={() => runs.refetch()} />
        ) : runs.data?.length ? (
          <div className="run-list">
            {runs.data.map((run) => {
              const isSelected = run.case_name === selected?.caseName && run.run_id === selected.runId;
              return (
                <button
                  key={`${run.case_name}-${run.run_id}`}
                  type="button"
                  className={isSelected ? "selected" : ""}
                  aria-pressed={isSelected}
                  onClick={() => navigate(`/runs/${run.case_name}/${run.run_id}`)}
                >
                  <Truncate className="list-title">{run.case_name}</Truncate>
                  <Truncate className="list-meta">{run.run_id}</Truncate>
                  <StatusBadge status={run.status} />
                </button>
              );
            })}
          </div>
        ) : (
          <EmptyState icon={Activity}>No runs yet. Start one from the Builder.</EmptyState>
        )}
      </section>
      <section className="detail-panel">
        {detail.isLoading ? (
          <PanelLoading label="Loading run" lines={8} tall />
        ) : detail.isError ? (
          <PanelError error={detail.error} onRetry={() => detail.refetch()} tall />
        ) : detail.data ? (
          <>
            <header className="page-header compact">
              <div>
                {params.runId && (
                  <Link className="back-link" to="/runs">
                    <ArrowLeft aria-hidden="true" />
                    All runs
                  </Link>
                )}
                <p className="eyebrow">{detail.data.case_name}</p>
                <h1>
                  <Truncate lines={2}>{detail.data.run_id}</Truncate>
                </h1>
              </div>
              <div className="output-focus-title" style={{ flex: "0 0 auto" }}>
                <StatusBadge status={detail.data.status} />
                {hasViewableGeometry(detail.data) && (
                  <Link className="secondary-action" to={`/viewer/${detail.data.case_name}/${detail.data.run_id}`}>
                    Open 3D
                  </Link>
                )}
              </div>
            </header>
            {detail.data.command_plan.length ? (
              <div className="timeline">
                {detail.data.command_plan.map((phase) => (
                  <div key={phase} className={phaseClass(detail.data!.status, phase === detail.data!.phase)}>
                    {iconForPhase(detail.data!.status, phase === detail.data!.phase)}
                    <span>{phase}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon={Clock}>No phase plan recorded for this run.</EmptyState>
            )}
            {(active || detail.data.latest_event) && (
              <div className="event-banner">
                <Clock aria-hidden="true" />
                <div className="event-body">
                  {detail.data.latest_event && <Truncate lines={2}>{detail.data.latest_event.message}</Truncate>}
                  {active && (
                    <div className="progress-line">
                      <ProgressBar value={progress} label="Run progress" />
                      {progress != null && Number.isFinite(progress) && (
                        <span className="progress-percent">{Math.round(progress * 100)}%</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
            <RunDataExplorer run={detail.data} />
            <RunOutputSections sections={detail.data.output_sections ?? []} />
            <RunArtifacts artifacts={detail.data.artifacts} />
          </>
        ) : (
          <EmptyState tall icon={Atom}>Select a run to view its details.</EmptyState>
        )}
      </section>
    </div>
  );
}

function phaseClass(status: string, current: boolean): string {
  if (current) return "current";
  if (status === "completed") return "done";
  return "";
}

function iconForPhase(status: string, current: boolean) {
  if (status === "failed" && current) return <XCircle aria-hidden="true" />;
  if (status === "completed") return <CheckCircle2 aria-hidden="true" />;
  return <Clock aria-hidden="true" />;
}
