import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { BarChart3, ClipboardList, Database, FileJson, Gauge, Sigma, SlidersHorizontal, Table2, type LucideIcon } from "lucide-react";
import { fetchText } from "../api";
import { prefersReducedMotion, useChartTokens } from "../theme";
import {
  artifactKindRows,
  displayValue,
  extractRunNumericRows,
  formatSize,
  metricCoverageRows,
  parseArtifactText,
  runContextRows,
  structuredDataArtifacts
} from "../runData";
import type { FlatDataRow, NumericRow, ParsedArtifact } from "../runData";
import type { ArtifactRef, RunRecord } from "../types";
import { MetricChart } from "./MetricChart";
import { PanelError } from "./StateBlock";

interface RunDataExplorerProps {
  run: RunRecord;
}

export function RunDataExplorer({ run }: RunDataExplorerProps) {
  const dataArtifacts = useMemo(() => structuredDataArtifacts(run.artifacts), [run.artifacts]);
  const firstArtifactUrl = dataArtifacts[0]?.url ?? "";
  const [selectedUrl, setSelectedUrl] = useState(firstArtifactUrl);

  useEffect(() => {
    setSelectedUrl(firstArtifactUrl);
  }, [firstArtifactUrl]);

  const selectedArtifact = dataArtifacts.find((artifact) => artifact.url === selectedUrl) ?? dataArtifacts[0];
  const artifactText = useQuery({
    queryKey: ["artifact-preview", selectedArtifact?.url],
    queryFn: () => fetchText(selectedArtifact!.url),
    enabled: Boolean(selectedArtifact)
  });
  const parsedArtifact = useMemo(
    () => (selectedArtifact && artifactText.data ? parseArtifactText(selectedArtifact, artifactText.data) : null),
    [artifactText.data, selectedArtifact]
  );
  const numericRows = useMemo(() => extractRunNumericRows(run), [run]);
  const contextRows = useMemo(() => runContextRows(run), [run]);
  const coverageRows = useMemo(() => metricCoverageRows(run.output_sections ?? []), [run.output_sections]);
  const artifactRows = useMemo(() => artifactKindRows(run.artifacts), [run.artifacts]);

  return (
    <div className="run-data-stack">
      <section className="run-kpi-grid" aria-label="Run data summary">
        <RunKpi icon={Gauge} label="Numeric outputs" value={numericRows.length} />
        <RunKpi icon={BarChart3} label="Output domains" value={run.output_sections?.length ?? 0} />
        <RunKpi icon={Database} label="Data artifacts" value={dataArtifacts.length} />
        <RunKpi icon={ClipboardList} label="Total artifacts" value={run.artifacts.length} />
      </section>

      <section className="run-chart-grid">
        <div className="panel">
          <div className="pane-title">
            <Sigma aria-hidden="true" />
            <span>Calculations and metrics</span>
          </div>
          <MetricChart rows={numericRows} title="Numeric outputs across the run" limit={32} />
        </div>
        <div className="panel">
          <div className="pane-title">
            <BarChart3 aria-hidden="true" />
            <span>Coverage</span>
          </div>
          <ArtifactDonut rows={artifactRows} />
          <MetricChart className="compact-chart" rows={coverageRows} title="Metrics by output section" limit={14} />
        </div>
      </section>

      <section className="panel">
        <div className="pane-title">
          <SlidersHorizontal aria-hidden="true" />
          <span>Run parameters, validation, and provenance</span>
        </div>
        <DataRows rows={contextRows} />
      </section>

      <section className="panel data-explorer-panel">
        <div className="pane-title">
          <Table2 aria-hidden="true" />
          <span>Structured artifact preview</span>
        </div>
        {dataArtifacts.length ? (
          <div className="data-artifact-layout">
            <div className="data-artifact-list" aria-label="Data artifacts">
              {dataArtifacts.map((artifact) => (
                <button
                  key={artifact.path}
                  type="button"
                  className={artifact.url === selectedArtifact?.url ? "selected" : ""}
                  onClick={() => setSelectedUrl(artifact.url)}
                >
                  <FileJson aria-hidden="true" />
                  <span>{artifact.label}</span>
                  <small>
                    {artifact.kind} / {formatSize(artifact.size)}
                  </small>
                </button>
              ))}
            </div>
            <ArtifactPreview
              artifact={selectedArtifact}
              isLoading={artifactText.isLoading}
              isError={artifactText.isError}
              error={artifactText.error}
              onRetry={() => artifactText.refetch()}
              parsed={parsedArtifact}
            />
          </div>
        ) : (
          <div className="empty-panel">No structured artifacts were found for this run.</div>
        )}
      </section>
    </div>
  );
}

function RunKpi({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: number | string }) {
  return (
    <div className="run-kpi">
      <Icon aria-hidden="true" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ArtifactDonut({ rows }: { rows: NumericRow[] }) {
  const tokens = useChartTokens();
  if (!rows.length) return <div className="empty-panel">No artifacts counted.</div>;
  const option = {
    animation: !prefersReducedMotion(),
    color: tokens.series,
    legend: { bottom: 0, type: "scroll", textStyle: { color: tokens.label } },
    series: [
      {
        avoidLabelOverlap: true,
        data: rows.map((row) => ({ name: row.label, value: row.value })),
        label: { formatter: "{b}: {c}", color: tokens.label },
        labelLine: { lineStyle: { color: tokens.axis } },
        radius: ["46%", "70%"],
        type: "pie"
      }
    ],
    tooltip: { confine: true, trigger: "item" }
  };
  return <ReactECharts className="chart donut-chart" option={option} notMerge lazyUpdate />;
}

function ArtifactPreview({
  artifact,
  isLoading,
  isError,
  error,
  onRetry,
  parsed
}: {
  artifact?: ArtifactRef;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  parsed: ParsedArtifact | null;
}) {
  if (!artifact) return <div className="empty-panel">Select an artifact to preview.</div>;
  if (isError) {
    return <PanelError error={error} label={`Could not load ${artifact.label}.`} onRetry={onRetry} />;
  }
  if (isLoading || !parsed) return <div className="empty-panel">Loading {artifact.label}…</div>;
  if (parsed.kind === "error") {
    return <div className="empty-panel">Could not parse {artifact.label}: {parsed.error}</div>;
  }

  return (
    <div className="data-preview">
      <div className="data-preview-header">
        <div>
          <strong>{artifact.label}</strong>
          <span>
            {parsed.summary} / {formatSize(artifact.size)}
          </span>
        </div>
        <a className="secondary-action" href={artifact.url} target="_blank" rel="noreferrer">
          Open
        </a>
      </div>

      {parsed.numericRows.length > 1 && (
        <MetricChart className="compact-chart" rows={parsed.numericRows} title="Numeric fields in this artifact" limit={20} />
      )}

      {parsed.columns && parsed.tableRows ? (
        <DataTable columns={parsed.columns} rows={parsed.tableRows} truncated={parsed.truncated} />
      ) : parsed.rows.length ? (
        <DataRows rows={parsed.rows} />
      ) : (
        <pre className="text-preview">{parsed.text}</pre>
      )}
    </div>
  );
}

function DataRows({ rows }: { rows: FlatDataRow[] }) {
  if (!rows.length) return <div className="empty-panel">No values available.</div>;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Path</th>
            <th>Value</th>
            <th>Type</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.path}>
              <td>{row.path}</td>
              <td>{row.displayValue}</td>
              <td>{row.type}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DataTable({ columns, rows, truncated }: { columns: string[]; rows: string[][]; truncated?: boolean }) {
  if (!columns.length) return <div className="empty-panel">No table columns available.</div>;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}-${row.join("|")}`}>
              {columns.map((column, columnIndex) => (
                <td key={column}>{displayValue(row[columnIndex] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {truncated && <div className="table-note">Preview truncated to keep the browser responsive.</div>}
    </div>
  );
}
