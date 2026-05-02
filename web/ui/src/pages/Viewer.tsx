import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Box, Cuboid } from "lucide-react";
import { api } from "../api";
import { ModelViewer } from "../components/ModelViewer";

export function Viewer() {
  const params = useParams();
  const navigate = useNavigate();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const selected = useMemo(() => {
    if (params.caseName && params.runId) return { caseName: params.caseName, runId: params.runId };
    const withGeometry = runs.data?.find((run) => run.artifacts.some((artifact) => artifact.kind === "geometry" || artifact.kind === "media"));
    return withGeometry ? { caseName: withGeometry.case_name, runId: withGeometry.run_id } : null;
  }, [params.caseName, params.runId, runs.data]);
  const run = useQuery({
    queryKey: ["run", selected?.caseName, selected?.runId],
    queryFn: () => api.run(selected!.caseName, selected!.runId),
    enabled: Boolean(selected)
  });

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Geometry exports</p>
          <h1>3D viewer</h1>
        </div>
        <label className="field inline">
          <span>Run</span>
          <select value={selected ? `${selected.caseName}/${selected.runId}` : ""} onChange={(event) => {
            const [caseName, runId] = event.target.value.split("/");
            navigate(`/viewer/${caseName}/${runId}`);
          }}>
            {runs.data?.map((item) => (
              <option key={`${item.case_name}/${item.run_id}`} value={`${item.case_name}/${item.run_id}`}>
                {item.case_name} / {item.run_id}
              </option>
            ))}
          </select>
        </label>
      </header>
      {run.data ? (
        <>
          <section className="viewer-meta">
            <div>
              <Cuboid aria-hidden="true" />
              <strong>{run.data.case_name}</strong>
              <span>{run.data.run_id}</span>
            </div>
            <Link className="secondary-action" to={`/runs/${run.data.case_name}/${run.data.run_id}`}>
              Open run
            </Link>
          </section>
          <ModelViewer artifacts={run.data.artifacts} />
        </>
      ) : (
        <div className="empty-panel tall">
          <Box aria-hidden="true" />
          <span>No geometry run selected.</span>
        </div>
      )}
    </div>
  );
}
