import { useQuery } from "@tanstack/react-query";
import { Database, FileJson, Image, ScrollText } from "lucide-react";
import { fetchText } from "../api";
import { Markdown } from "./Markdown";
import type { ArtifactRef } from "../types";

interface RunArtifactsProps {
  artifacts: ArtifactRef[];
}

export function RunArtifacts({ artifacts }: RunArtifactsProps) {
  const report = artifacts.find((artifact) => artifact.label === "report.md");
  const media = artifacts.filter((artifact) => artifact.kind === "media").slice(0, 6);
  const data = artifacts.filter((artifact) => artifact.kind === "data").slice(0, 10);
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
          <Markdown content={reportQuery.data ?? "Loading report..."} />
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
          {media.map((artifact) => (
            <a key={artifact.path} href={artifact.url} target="_blank" rel="noreferrer" className="media-tile">
              {artifact.mime_type.startsWith("image/") ? <img src={artifact.url} alt={artifact.label} /> : <span>{artifact.label}</span>}
            </a>
          ))}
          {!media.length && <div className="empty-panel">No plot or render media yet.</div>}
        </div>
      </section>
      <section className="artifact-pane">
        <div className="pane-title">
          <Database aria-hidden="true" />
          <span>Data files</span>
        </div>
        <div className="file-list">
          {data.map((artifact) => (
            <a key={artifact.path} href={artifact.url} target="_blank" rel="noreferrer">
              <FileJson aria-hidden="true" />
              <span>{artifact.label}</span>
              <small>{formatSize(artifact.size)}</small>
            </a>
          ))}
          {!data.length && <div className="empty-panel">No structured data artifacts yet.</div>}
        </div>
      </section>
    </div>
  );
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
