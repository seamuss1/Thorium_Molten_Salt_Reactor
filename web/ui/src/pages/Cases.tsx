import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Atom, BookOpen, Box, ChevronRight, Settings2 } from "lucide-react";
import { api } from "../api";
import { ExpandableText } from "../components/ExpandableText";
import { RunOutputSections } from "../components/RunOutputSections";
import { hasViewableGeometry } from "../geometryArtifacts";
import type { EditableParameter, RunRecord } from "../types";

export function Cases() {
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const [selected, setSelected] = useState<string | null>(null);
  const selectedName = selected ?? cases.data?.[0]?.name;
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
          <h1>Simulation types</h1>
        </div>
        <div className="case-list">
          {cases.data?.map((item) => (
            <button key={item.name} type="button" className={item.name === selectedName ? "selected" : ""} onClick={() => setSelected(item.name)}>
              <ExpandableText className="list-title" insideInteractive lines={1}>
                {String(item.reactor.name ?? item.name)}
              </ExpandableText>
              <ExpandableText className="list-meta" insideInteractive lines={1}>
                {simulationLabel(item.reactor)}
              </ExpandableText>
              <ChevronRight aria-hidden="true" />
            </button>
          ))}
        </div>
      </section>

      <section className="detail-panel">
        {detail.data && (
          <>
            <header className="page-header compact">
              <div>
                <p className="eyebrow">{String(detail.data.reactor.family ?? "MSR case")}</p>
                <h1>
                  <ExpandableText lines={2}>{String(detail.data.reactor.name ?? detail.data.name)}</ExpandableText>
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
                    <ExpandableText className="run-meta" lines={1}>
                      {latestRunRef.run_id}
                    </ExpandableText>
                  )}
                </div>
                <div>
                  {latestRunRef && <mark>{latestRun.data?.status ?? latestRunRef.status}</mark>}
                  {latestRun.data && hasViewableGeometry(latestRun.data) && (
                    <Link className="secondary-action" to={`/viewer/${latestRun.data.case_name}/${latestRun.data.run_id}`}>
                      <Box aria-hidden="true" />
                      <span>Open 3D</span>
                    </Link>
                  )}
                </div>
              </div>
              {latestRunRef ? (
                latestRun.data ? (
                  <RunOutputSections sections={latestRun.data.output_sections ?? []} />
                ) : (
                  <div className="empty-panel">Loading latest simulation output...</div>
                )
              ) : (
                <div className="empty-panel">No result bundle has been created for this simulation type.</div>
              )}
            </section>
            <div className="two-column supporting-grid">
              <div className="panel">
                <div className="section-title">
                  <BookOpen aria-hidden="true" />
                  <h2>Relevant docs</h2>
                </div>
                <div className="doc-links">
                  {detail.data.docs.map((doc) => (
                    <Link key={doc.slug} to={`/docs/${doc.slug}`}>
                      <span className="list-title">{doc.title}</span>
                    </Link>
                  ))}
                </div>
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
                        <ExpandableText className="parameter-label" lines={2}>
                          {parameter.label}
                        </ExpandableText>
                        <ExpandableText className="parameter-value" lines={1}>
                          {String(parameter.value)}
                        </ExpandableText>
                        <ExpandableText className="parameter-meta" lines={1}>
                          {parameter.unit ?? parameter.path}
                        </ExpandableText>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </>
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
        <ExpandableText lines={1}>{formatFact(value, unit)}</ExpandableText>
      </dd>
    </div>
  );
}

function simulationLabel(reactor: Record<string, unknown>) {
  return [reactor.family, reactor.mode].filter(Boolean).join(" / ") || "reactor simulation";
}

function formatFact(value: unknown, unit?: string) {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value === "number") {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value)}${unit ? ` ${unit}` : ""}`;
  }
  return `${String(value)}${unit ? ` ${unit}` : ""}`;
}
