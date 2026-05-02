import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Atom, BookOpen, ChevronRight, Settings2 } from "lucide-react";
import { api } from "../api";
import { MetricChart } from "../components/MetricChart";
import type { EditableParameter } from "../types";

export function Cases() {
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.cases });
  const [selected, setSelected] = useState<string | null>(null);
  const selectedName = selected ?? cases.data?.[0]?.name;
  const detail = useQuery({
    queryKey: ["case", selectedName],
    queryFn: () => api.caseDetail(selectedName!),
    enabled: Boolean(selectedName)
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
          <h1>Cases</h1>
        </div>
        <div className="case-list">
          {cases.data?.map((item) => (
            <button key={item.name} type="button" className={item.name === selectedName ? "selected" : ""} onClick={() => setSelected(item.name)}>
              <strong>{String(item.reactor.name ?? item.name)}</strong>
              <span>{item.name}</span>
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
                <h1>{String(detail.data.reactor.name ?? detail.data.name)}</h1>
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
            <div className="two-column">
              <div className="panel">
                <h2>Latest output</h2>
                {detail.data.latest_run ? (
                  <>
                    <div className="run-line">
                      <strong>{detail.data.latest_run.run_id}</strong>
                      <mark>{detail.data.latest_run.status}</mark>
                    </div>
                    <MetricChart metrics={detail.data.latest_run.metrics} title="Case metrics" />
                  </>
                ) : (
                  <div className="empty-panel">No result bundle has been created for this case.</div>
                )}
              </div>
              <div className="panel">
                <div className="section-title">
                  <BookOpen aria-hidden="true" />
                  <h2>Relevant docs</h2>
                </div>
                <div className="doc-links">
                  {detail.data.docs.map((doc) => (
                    <Link key={doc.slug} to={`/docs/${doc.slug}`}>
                      <strong>{doc.title}</strong>
                    </Link>
                  ))}
                </div>
              </div>
            </div>
            <div className="parameter-groups">
              {[...groupedParameters.entries()].map(([group, parameters]) => (
                <section key={group} className="parameter-band">
                  <h2>{group}</h2>
                  <div className="parameter-table">
                    {parameters.slice(0, 12).map((parameter) => (
                      <div key={parameter.path}>
                        <span>{parameter.label}</span>
                        <strong>{String(parameter.value)}</strong>
                        <small>{parameter.unit ?? parameter.path}</small>
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
