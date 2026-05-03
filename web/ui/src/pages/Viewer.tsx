import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Box, Cuboid } from "lucide-react";
import { api } from "../api";
import { ExpandableText } from "../components/ExpandableText";
import { ModelViewer } from "../components/ModelViewer";
import { hasViewableGeometry } from "../geometryArtifacts";

export function Viewer() {
  const params = useParams();
  const navigate = useNavigate();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const geometryRuns = useMemo(() => runs.data?.filter(hasViewableGeometry) ?? [], [runs.data]);
  const defaultGeometryRun = useMemo(() => {
    const flagshipRuns = geometryRuns.filter((runItem) => runItem.case_name.toLowerCase().includes("flagship"));
    return latestRun(flagshipRuns) ?? latestRun(geometryRuns);
  }, [geometryRuns]);
  const requestedCaseName = params.caseName;
  const requestedRunId = params.runId;
  const hasRequestedRun = Boolean(requestedCaseName && requestedRunId);
  const selected = useMemo(() => {
    if (requestedCaseName && requestedRunId) {
      const requestedRun = geometryRuns.find((runItem) => runItem.case_name === requestedCaseName && runItem.run_id === requestedRunId);
      return requestedRun ? { caseName: requestedRun.case_name, runId: requestedRun.run_id } : null;
    }
    return defaultGeometryRun ? { caseName: defaultGeometryRun.case_name, runId: defaultGeometryRun.run_id } : null;
  }, [defaultGeometryRun, geometryRuns, requestedCaseName, requestedRunId]);
  const run = useQuery({
    queryKey: ["run", selected?.caseName, selected?.runId],
    queryFn: () => api.run(selected!.caseName, selected!.runId),
    enabled: Boolean(selected)
  });
  const selectedRun =
    selected && run.data?.case_name === selected.caseName && run.data.run_id === selected.runId
      ? run.data
      : null;
  const emptyMessage = runs.isLoading || (Boolean(selected) && run.isLoading)
    ? "Loading geometry exports..."
    : hasRequestedRun
      ? "The selected run does not have a viewable glTF geometry export."
      : "No runs with viewable glTF geometry exports are available. Run the render phase to create one.";

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Geometry exports</p>
          <h1>3D viewer</h1>
        </div>
        <label className="field inline">
          <span>Run</span>
          <select
            value={selected ? `${selected.caseName}/${selected.runId}` : ""}
            onChange={(event) => {
              if (!event.target.value) return;
              const [caseName, runId] = event.target.value.split("/");
              navigate(`/viewer/${caseName}/${runId}`);
            }}
            disabled={!geometryRuns.length}
          >
            {!geometryRuns.length && <option value="">No viewable 3D exports</option>}
            {!selected && geometryRuns.length > 0 && (
              <option value="">{hasRequestedRun ? "Selected run has no 3D export" : "Choose a 3D run"}</option>
            )}
            {geometryRuns.map((item) => (
              <option key={`${item.case_name}/${item.run_id}`} value={`${item.case_name}/${item.run_id}`}>
                {item.case_name} / {item.run_id}
              </option>
            ))}
          </select>
        </label>
      </header>
      {selectedRun ? (
        <>
          <section className="viewer-meta">
            <div>
              <Cuboid aria-hidden="true" />
              <ExpandableText className="viewer-case" lines={1}>
                {selectedRun.case_name}
              </ExpandableText>
              <ExpandableText className="viewer-run" lines={1}>
                {selectedRun.run_id}
              </ExpandableText>
            </div>
            <Link className="secondary-action" to={`/runs/${selectedRun.case_name}/${selectedRun.run_id}`}>
              Open run
            </Link>
          </section>
          <ModelViewer artifacts={selectedRun.artifacts} />
        </>
      ) : (
        <div className="empty-panel tall">
          <Box aria-hidden="true" />
          <span>{emptyMessage}</span>
        </div>
      )}
    </div>
  );
}

function latestRun<T extends { created_at?: string | null; finished_at?: string | null; started_at?: string | null }>(runs: T[]): T | null {
  return runs.reduce<T | null>((latest, current) => {
    if (!latest) return current;
    return runTimestamp(current) > runTimestamp(latest) ? current : latest;
  }, null);
}

function runTimestamp(run: { created_at?: string | null; finished_at?: string | null; started_at?: string | null }) {
  const value = run.finished_at ?? run.started_at ?? run.created_at;
  const timestamp = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(timestamp) ? timestamp : 0;
}
