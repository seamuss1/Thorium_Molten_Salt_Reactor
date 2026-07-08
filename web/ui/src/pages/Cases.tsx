import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Atom, BookOpen, Box, ChevronRight, Settings2 } from "lucide-react";
import { api } from "../api";
import { Truncate } from "../components/Truncate";
import { StatusBadge } from "../components/StatusBadge";
import { PanelError, PanelLoading, EmptyState } from "../components/StateBlock";
import { RunOutputSections } from "../components/RunOutputSections";
import { hasViewableGeometry } from "../geometryArtifacts";
import type { EditableParameter, RunRecord } from "../types";

export function Cases() {
  const params = useParams();
  const navigate = useNavigate();
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const selectedName = params.caseName ?? cases.data?.[0]?.name;
  const detail = useQuery({
    queryKey: ["case", selectedName],
    queryFn: () => api.caseDetail(selectedName!),
    enabled: Boolean(selectedName)
  });
  const latestRunRef = detail.data?.latest_run;
  const latestRun = useQuery({
    queryKey: ["run", latestRunRef?.case_name, latestRunRef?.run_id, "outputs"],
    queryFn: () => api.run(latestRunRef!.case_name, latestRunRef!.run_id),
    enabled: Boolean(latestRunRef)
  });
  const groupedParameters = useMemo(() => {
    const groups = new Map<string, EditableParameter[]>();
    detail.data?.editable_parameters.forEach((parameter) => {
      groups.set(parameter.group, [...(groups.get(parameter.group) ?? []), parameter]);
    });
    return groups;
  }, [detail.data]);

  return (
    <div className="page split-page">
      <section className="list-panel">
        <div className="section-title">
          <Atom aria-hidden="true" />
          <h1>Simulations</h1>
        </div>
        {cases.isLoading ? (
          <PanelLoading label="Loading simulations" lines={6} />
        ) : cases.isError ? (
          <PanelError error={cases.error} onRetry={() => cases.refetch()} />
        ) : cases.data?.length ? (
          <div className="case-list">
            {cases.data.map((item) => {
              const isSelected = item.name === selectedName;
              return (
                <button
                  key={item.name}
                  type="button"
                  className={isSelected ? "selected" : ""}
                  aria-pressed={isSelected}
                  onClick={() => navigate(`/cases/${item.name}`)}
                >
                  <Truncate className="list-title">{String(item.reactor.name ?? item.name)}</Truncate>
                  <Truncate className="list-meta">{simulationLabel(item.reactor)}</Truncate>
                  <ChevronRight aria-hidden="true" />
                </button>
              );
            })}
          </div>
        ) : (
          <EmptyState icon={Atom}>No simulation types found.</EmptyState>
        )}
      </section>

      <section className="detail-panel">
        {detail.isLoading ? (
          <PanelLoading label="Loading simulation" lines={8} tall />
        ) : detail.isError ? (
          <PanelError error={detail.error} onRetry={() => detail.refetch()} tall />
        ) : detail.data ? (
          <>
            <header className="page-header compact">
              <div>
                <p className="eyebrow">{String(detail.data.reactor.family ?? "MSR case")}</p>
                <h1>
                  <Truncate lines={2}>{String(detail.data.reactor.name ?? detail.data.name)}</Truncate>
                </h1>
              </div>
              <Link className="primary-action" to={`/builder?case=${detail.data.name}`}>
                <Settings2 aria-hidden="true" />
                <span>Configure</span>
              </Link>
            </header>
            <div className="tag-row">
              {detail.data.capabilities.map((capability) => (
                <span key={capability}>{capability.replaceAll("_", " ")}</span>
              ))}
            </div>
            <SimulationOverview reactor={detail.data.reactor} latestRun={latestRun.data ?? latestRunRef ?? null} />
            <section className="output-focus">
              <div className="output-focus-title">
                <div>
                  <h2>Latest detailed output</h2>
                  {latestRunRef && (
                    <Truncate className="run-meta">{latestRunRef.run_id}</Truncate>
                  )}
                </div>
                <div>
                  {latestRunRef && <StatusBadge status={latestRun.data?.status ?? latestRunRef.status} />}
                  {latestRun.data && hasViewableGeometry(latestRun.data) && (
                    <Link className="secondary-action" to={`/viewer/${latestRun.data.case_name}/${latestRun.data.run_id}`}>
                      <Box aria-hidden="true" />
                      <span>Open 3D</span>
                    </Link>
                  )}
                </div>
              </div>
              {latestRunRef ? (
                latestRun.isLoading ? (
                  <PanelLoading label="Loading latest output" lines={4} />
                ) : latestRun.isError ? (
                  <PanelError error={latestRun.error} onRetry={() => latestRun.refetch()} />
                ) : latestRun.data ? (
                  <RunOutputSections sections={latestRun.data.output_sections ?? []} />
                ) : (
                  <EmptyState>No detailed output was recorded for this run.</EmptyState>
                )
              ) : (
                <EmptyState>No result bundle has been created for this simulation type.</EmptyState>
              )}
            </section>
            <div className="two-column supporting-grid">
              <div className="panel">
                <div className="section-title">
                  <BookOpen aria-hidden="true" />
                  <h2>Relevant docs</h2>
                </div>
                {detail.data.docs.length ? (
                  <div className="doc-links">
                    {detail.data.docs.map((doc) => (
                      <Link key={doc.slug} to={`/docs/${doc.slug}`}>
                        <Truncate className="list-title">{doc.title}</Truncate>
                      </Link>
                    ))}
                  </div>
                ) : (
                  <EmptyState icon={BookOpen}>No linked documents.</EmptyState>
                )}
              </div>
              <div className="panel">
                <h2>Simulation model</h2>
                <dl className="fact-list">
                  <Fact label="Family" value={detail.data.reactor.family} />
                  <Fact label="Mode" value={detail.data.reactor.mode} />
                  <Fact label="Benchmark" value={detail.data.benchmark_path ?? "n/a"} />
                  <Fact label="Editable controls" value={detail.data.editable_parameters.length} />
                </dl>
              </div>
            </div>
            <div className="parameter-groups">
              {[...groupedParameters.entries()].map(([group, parameters]) => (
                <section key={group} className="parameter-band">
                  <h2>{group}</h2>
                  <div className="parameter-table">
                    {parameters.slice(0, 12).map((parameter) => (
                      <div key={parameter.path}>
                        <Truncate className="parameter-label" lines={2}>
                          {parameter.label}
                        </Truncate>
                        <Truncate className="parameter-value">{String(parameter.value)}</Truncate>
                        <Truncate className="parameter-meta">{parameter.unit ?? parameter.path}</Truncate>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </>
        ) : (
          <EmptyState tall icon={Atom}>Select a simulation to view its outputs.</EmptyState>
        )}
      </section>
    </div>
  );
}

function SimulationOverview({ reactor, latestRun }: { reactor: Record<string, unknown>; latestRun: RunRecord | null }) {
  return (
    <dl className="simulation-overview">
      <Fact label="Design thermal power" value={reactor.design_power_mwth} unit="MWth" />
      <Fact label="Hot leg" value={reactor.hot_leg_temp_c} unit="C" />
      <Fact label="Cold leg" value={reactor.cold_leg_temp_c} unit="C" />
      <Fact label="Latest status" value={latestRun?.status ?? "No run"} />
    </dl>
  );
}

function Fact({ label, value, unit }: { label: string; value: unknown; unit?: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <Truncate>{formatFact(value, unit)}</Truncate>
      </dd>
    </div>
  );
}

function simulationLabel(reactor: Record<string, unknown>) {
  return [reactor.family, reactor.mode].filter(Boolean).join(" · ") || "reactor simulation";
}

function formatFact(value: unknown, unit?: string) {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value === "number") {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value)}${unit ? ` ${unit}` : ""}`;
  }
  return `${String(value)}${unit ? ` ${unit}` : ""}`;
}
