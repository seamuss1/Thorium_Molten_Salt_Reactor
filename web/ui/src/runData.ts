import type { ArtifactRef, OutputSection, RunRecord } from "./types";

export interface NumericRow {
  group?: string;
  kind?: string | null;
  label: string;
  path?: string;
  unit?: string | null;
  value: number;
}

export interface FlatDataRow {
  displayValue: string;
  path: string;
  type: string;
  value: unknown;
}

export interface CsvPreview {
  columns: string[];
  rows: string[][];
  truncated: boolean;
}

export interface ParsedArtifact {
  columns?: string[];
  error?: string;
  kind: "csv" | "error" | "json" | "ndjson" | "text";
  numericRows: NumericRow[];
  rows: FlatDataRow[];
  summary: string;
  tableRows?: string[][];
  text?: string;
  truncated?: boolean;
}

const STRUCTURED_EXTENSIONS = [".json", ".csv", ".yaml", ".yml", ".ndjson"];
const VISUAL_EXTENSIONS = [".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"];

export function visualArtifacts(artifacts: ArtifactRef[]): ArtifactRef[] {
  return artifacts
    .filter((artifact) => {
      const lower = artifact.path.toLowerCase();
      return (
        artifact.kind === "plot" ||
        artifact.kind === "media" ||
        artifact.mime_type.startsWith("image/") ||
        VISUAL_EXTENSIONS.some((extension) => lower.endsWith(extension))
      );
    })
    .sort(compareArtifacts);
}

export function structuredDataArtifacts(artifacts: ArtifactRef[]): ArtifactRef[] {
  return artifacts
    .filter((artifact) => {
      const lower = artifact.path.toLowerCase();
      return artifact.kind === "data" || STRUCTURED_EXTENSIONS.some((extension) => lower.endsWith(extension));
    })
    .sort(compareArtifacts);
}

export function artifactKindRows(artifacts: ArtifactRef[]): NumericRow[] {
  const counts = new Map<string, number>();
  for (const artifact of artifacts) {
    counts.set(artifact.kind, (counts.get(artifact.kind) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([kind, count]) => ({ group: "Artifacts", label: humanizeKey(kind), value: count }));
}

export function metricCoverageRows(sections: OutputSection[]): NumericRow[] {
  return sections
    .filter((section) => section.metrics.length > 0)
    .map((section) => ({
      group: "Output sections",
      label: section.title,
      path: section.id,
      value: section.metrics.length
    }));
}

export function extractRunNumericRows(run: RunRecord): NumericRow[] {
  const rows: NumericRow[] = [];
  for (const section of run.output_sections ?? []) {
    for (const metric of section.metrics) {
      if (typeof metric.value === "number" && Number.isFinite(metric.value)) {
        rows.push({
          group: section.title,
          kind: metric.kind,
          label: metric.label,
          path: `${section.id}.${metric.label}`,
          unit: metric.unit,
          value: metric.value
        });
      }
    }
  }
  for (const [key, value] of Object.entries(run.metrics)) {
    if (typeof value === "number" && Number.isFinite(value)) {
      rows.push({
        group: "Raw metrics",
        label: humanizeKey(key),
        path: `metrics.${key}`,
        value
      });
    }
  }
  return dedupeNumericRows(rows);
}

export function runContextRows(run: RunRecord): FlatDataRow[] {
  const rows: FlatDataRow[] = [
    primitiveRow("status", run.status),
    primitiveRow("phase", run.phase ?? "n/a"),
    primitiveRow("case_name", run.case_name),
    primitiveRow("run_id", run.run_id),
    primitiveRow("created_at", run.created_at ?? "n/a"),
    primitiveRow("started_at", run.started_at ?? "n/a"),
    primitiveRow("finished_at", run.finished_at ?? "n/a"),
    primitiveRow("artifact_count", run.artifacts.length),
    primitiveRow("capability_count", run.capabilities.length)
  ];
  rows.push(...flattenValue(run.reactor, "reactor", { limit: 50 }));
  rows.push(...flattenValue(run.provenance, "provenance", { limit: 50 }));
  rows.push(...flattenValue(run.validation, "validation", { limit: 50 }));
  return rows.slice(0, 140);
}

export function flattenValue(
  value: unknown,
  prefix = "root",
  options: { arrayLimit?: number; limit?: number; maxDepth?: number } = {}
): FlatDataRow[] {
  const rows: FlatDataRow[] = [];
  const arrayLimit = options.arrayLimit ?? 8;
  const limit = options.limit ?? 160;
  const maxDepth = options.maxDepth ?? 6;

  function walk(current: unknown, path: string, depth: number) {
    if (rows.length >= limit) return;
    if (current === null || current === undefined || typeof current !== "object") {
      rows.push(primitiveRow(path, current));
      return;
    }

    if (Array.isArray(current)) {
      if (!current.length || depth >= maxDepth) {
        rows.push(primitiveRow(path, `${current.length} items`));
        return;
      }
      current.slice(0, arrayLimit).forEach((item, index) => walk(item, `${path}.${index}`, depth + 1));
      if (current.length > arrayLimit && rows.length < limit) {
        rows.push(primitiveRow(`${path}.*`, `${current.length - arrayLimit} more items`));
      }
      return;
    }

    const entries = Object.entries(current as Record<string, unknown>);
    if (!entries.length || depth >= maxDepth) {
      rows.push(primitiveRow(path, `${entries.length} keys`));
      return;
    }
    for (const [key, child] of entries) {
      walk(child, path ? `${path}.${key}` : key, depth + 1);
      if (rows.length >= limit) return;
    }
  }

  walk(value, prefix, 0);
  return rows;
}

export function parseArtifactText(artifact: ArtifactRef, text: string): ParsedArtifact {
  const lower = artifact.path.toLowerCase();
  if (lower.endsWith(".json")) {
    try {
      const value = JSON.parse(text);
      const rows = flattenValue(value, artifact.label.replace(/\.[^.]+$/, ""), { limit: 240 });
      return {
        kind: "json",
        numericRows: numericRowsFromFlatRows(rows, artifact.label),
        rows,
        summary: `${rows.length} flattened values`
      };
    } catch (error) {
      return parseError(error);
    }
  }

  if (lower.endsWith(".ndjson")) {
    try {
      const records = text
        .split(/\r?\n/)
        .filter((line) => line.trim())
        .map((line) => JSON.parse(line)) as Array<Record<string, unknown>>;
      const columns = preferredColumns(records);
      const tableRows = records.slice(0, 120).map((record) => columns.map((column) => displayValue(record[column])));
      const rows = flattenValue(records, artifact.label.replace(/\.[^.]+$/, ""), { limit: 180 });
      return {
        columns,
        kind: "ndjson",
        numericRows: numericRowsFromFlatRows(rows, artifact.label),
        rows,
        summary: `${records.length} records`,
        tableRows,
        truncated: records.length > tableRows.length
      };
    } catch (error) {
      return parseError(error);
    }
  }

  if (lower.endsWith(".csv")) {
    const preview = parseCsv(text, 120);
    return {
      columns: preview.columns,
      kind: "csv",
      numericRows: numericRowsFromCsv(preview, artifact.label),
      rows: [],
      summary: `${preview.rows.length}${preview.truncated ? "+" : ""} rows`,
      tableRows: preview.rows,
      truncated: preview.truncated
    };
  }

  const truncated = text.length > 12000;
  return {
    kind: "text",
    numericRows: [],
    rows: [],
    summary: truncated ? "Text preview truncated" : "Text preview",
    text: truncated ? text.slice(0, 12000) : text,
    truncated
  };
}

export function parseCsv(text: string, maxRows = 80): CsvPreview {
  const parsedRows = text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map(parseCsvLine);
  const columns = parsedRows[0] ?? [];
  const rows = parsedRows.slice(1, maxRows + 1);
  return { columns, rows, truncated: Math.max(0, parsedRows.length - 1) > rows.length };
}

export function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "n/a";
    return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(value);
  }
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function compareArtifacts(a: ArtifactRef, b: ArtifactRef) {
  const kind = a.kind.localeCompare(b.kind);
  return kind || a.label.localeCompare(b.label);
}

function dedupeNumericRows(rows: NumericRow[]) {
  const seen = new Set<string>();
  const unique: NumericRow[] = [];
  for (const row of rows) {
    const key = `${row.group ?? ""}|${row.label}|${row.unit ?? ""}|${row.value}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(row);
  }
  return unique;
}

function humanizeKey(value: string) {
  return value
    .replace(/\.[^.]+$/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function primitiveRow(path: string, value: unknown): FlatDataRow {
  return {
    displayValue: displayValue(value),
    path,
    type: value === null ? "null" : Array.isArray(value) ? "array" : typeof value,
    value
  };
}

function numericRowsFromFlatRows(rows: FlatDataRow[], group: string): NumericRow[] {
  return rows
    .filter((row) => typeof row.value === "number" && Number.isFinite(row.value))
    .slice(0, 80)
    .map((row) => ({ group, label: row.path.split(".").slice(-2).join("."), path: row.path, value: row.value as number }));
}

function numericRowsFromCsv(preview: CsvPreview, group: string): NumericRow[] {
  const rows: NumericRow[] = [];
  for (let rowIndex = 0; rowIndex < preview.rows.length; rowIndex += 1) {
    const row = preview.rows[rowIndex];
    const rowLabel = row[0] || `Row ${rowIndex + 1}`;
    for (let columnIndex = 1; columnIndex < preview.columns.length; columnIndex += 1) {
      const value = Number(row[columnIndex]);
      if (!Number.isFinite(value)) continue;
      rows.push({
        group,
        label: `${rowLabel} / ${preview.columns[columnIndex]}`,
        path: `${rowIndex}.${preview.columns[columnIndex]}`,
        value
      });
      if (rows.length >= 80) return rows;
    }
  }
  return rows;
}

function parseCsvLine(line: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
      continue;
    }
    if (char === "," && !quoted) {
      cells.push(cell);
      cell = "";
      continue;
    }
    cell += char;
  }
  cells.push(cell);
  return cells;
}

function preferredColumns(records: Array<Record<string, unknown>>) {
  const preferred = ["sequence", "timestamp", "level", "phase", "progress", "message"];
  const discovered = new Set<string>();
  for (const record of records.slice(0, 20)) {
    Object.keys(record).forEach((key) => discovered.add(key));
  }
  return [...preferred.filter((key) => discovered.has(key)), ...Array.from(discovered).filter((key) => !preferred.includes(key))].slice(0, 10);
}

function parseError(error: unknown): ParsedArtifact {
  return {
    error: error instanceof Error ? error.message : "Could not parse artifact.",
    kind: "error",
    numericRows: [],
    rows: [],
    summary: "Parse error"
  };
}
