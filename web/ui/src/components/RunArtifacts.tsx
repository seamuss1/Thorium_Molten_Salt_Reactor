import { useQuery } from "@tanstack/react-query";
import { Database, ExternalLink, FileJson, Image, ScrollText } from "lucide-react";
import { fetchText } from "../api";
import { Truncate } from "./Truncate";
import { Markdown } from "./Markdown";
import type { ArtifactRef } from "../types";
import { formatSize, visualArtifacts } from "../runData";

interface RunArtifactsProps {
  artifacts: ArtifactRef[];
}

export function RunArtifacts({ artifacts }: RunArtifactsProps) {
  const report = artifacts.find((artifact) => artifact.label === "report.md");
  const visuals = visualArtifacts(artifacts);
  const visualPaths = new Set(visuals.map((artifact) => artifact.path));
  const files = artifacts.filter((artifact) => artifact.label !== "report.md" && !visualPaths.has(artifact.path));
  const reportQuery = useQuery({
    queryKey: ["artifact-text", report?.url],
    queryFn: () => fetchText(report!.url),
    enabled: Boolean(report)
  });

  return (
    <div className="artifact-grid">
      <section className="artifact-pane report-pane">
        <div className="pane-title">
          <ScrollText aria-hidden="true" />
          <span>Report</span>
        </div>
        {report ? (
          reportQuery.isError ? (
            <div className="empty-panel">Could not load report: {(reportQuery.error as Error)?.message ?? "unknown error"}</div>
          ) : (
            <Markdown content={reportQuery.data ?? "Loading report…"} />
          )
        ) : (
          <div className="empty-panel">No report artifact yet.</div>
        )}
      </section>
      <section className="artifact-pane">
        <div className="pane-title">
          <Image aria-hidden="true" />
          <span>Visual outputs</span>
        </div>
        <div className="media-strip">
          {visuals.map((artifact) => (
            <a key={artifact.path} href={artifact.url} target="_blank" rel="noreferrer" className="media-tile">
              {artifact.mime_type.startsWith("image/") ? <img src={artifact.url} alt={artifact.label} /> : <span>{artifact.label}</span>}
              <small>{artifact.label}</small>
            </a>
          ))}
          {!visuals.length && <div className="empty-panel">No plot or render media yet.</div>}
        </div>
      </section>
      <section className="artifact-pane">
        <div className="pane-title">
          <Database aria-hidden="true" />
          <span>Files and datasets</span>
        </div>
        <div className="file-list">
          {files.map((artifact) => (
            <div className="file-item" key={artifact.path}>
              <FileJson aria-hidden="true" />
              <Truncate className="file-name">{artifact.label}</Truncate>
              <small>
                {artifact.kind} / {formatSize(artifact.size)}
              </small>
              <a className="icon-action" href={artifact.url} target="_blank" rel="noreferrer" aria-label={`Open ${artifact.label}`}>
                <ExternalLink aria-hidden="true" />
              </a>
            </div>
          ))}
          {!files.length && <div className="empty-panel">No additional artifacts yet.</div>}
        </div>
      </section>
    </div>
  );
}
