import { describe, expect, it } from "vitest";
import {
  extractRunNumericRows,
  flattenValue,
  parseArtifactText,
  parseCsv,
  structuredDataArtifacts,
  visualArtifacts
} from "./runData";
import type { ArtifactRef, RunRecord } from "./types";

function artifact(path: string, kind = "data", mimeType = "application/json"): ArtifactRef {
  return {
    kind,
    label: path.split("/").pop() ?? path,
    mime_type: mimeType,
    path,
    size: 120,
    url: `/api/runs/example/run/artifacts/${path}`
  };
}

function run(): RunRecord {
  return {
    artifacts: [],
    capabilities: [],
    case_name: "example",
    command_plan: [],
    metrics: { raw_keff: 1.01 },
    output_sections: [
      {
        id: "neutronics",
        metrics: [{ label: "k-effective", unit: "delta-k/k", value: 1.008 }],
        notes: [],
        title: "Neutronics"
      }
    ],
    provenance: {},
    reactor: {},
    run_id: "run",
    status: "completed",
    validation: {}
  };
}

describe("run data helpers", () => {
  it("treats plots and images as visual artifacts", () => {
    const artifacts = [
      artifact("summary.json"),
      artifact("plots/transient_power.svg", "plot", "image/svg+xml"),
      artifact("geometry/exports/core.png", "media", "image/png")
    ];

    expect(visualArtifacts(artifacts).map((item) => item.label)).toEqual(["core.png", "transient_power.svg"]);
  });

  it("discovers structured generated artifacts beyond the original short list", () => {
    const artifacts = [
      artifact("cash_flow.csv", "data", "text/csv"),
      artifact("job_events.ndjson", "artifact", "application/octet-stream"),
      artifact("plots/transient_power.svg", "plot", "image/svg+xml")
    ];

    expect(structuredDataArtifacts(artifacts).map((item) => item.label)).toEqual(["job_events.ndjson", "cash_flow.csv"]);
  });

  it("flattens nested payloads for table previews", () => {
    expect(flattenValue({ physics: { k_eff: 1.02 }, checks: [true] }, "summary").map((row) => row.path)).toEqual([
      "summary.physics.k_eff",
      "summary.checks.0"
    ]);
  });

  it("parses csv previews and numeric fields", () => {
    const preview = parseCsv("metric,value\nkeff,1.01\npower,250\n");
    const parsed = parseArtifactText(artifact("metrics.csv", "data", "text/csv"), "metric,value\nkeff,1.01\n");

    expect(preview.columns).toEqual(["metric", "value"]);
    expect(parsed.numericRows[0]).toMatchObject({ label: "keff / value", value: 1.01 });
  });

  it("combines curated section metrics with raw metrics", () => {
    expect(extractRunNumericRows(run()).map((row) => row.label)).toEqual(["k-effective", "Raw Keff"]);
  });
});
