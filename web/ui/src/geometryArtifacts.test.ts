import { describe, expect, it } from "vitest";
import { hasViewableGeometry, viewableGeometryArtifact } from "./geometryArtifacts";
import type { ArtifactRef, RunRecord } from "./types";

function artifact(path: string, kind = "geometry", size = 12): ArtifactRef {
  return {
    label: path.split("/").pop() ?? path,
    kind,
    mime_type: "application/octet-stream",
    path,
    size,
    url: `/artifacts/${path}`
  };
}

function run(artifacts: ArtifactRef[]): RunRecord {
  return {
    artifacts,
    capabilities: [],
    case_name: "case",
    command_plan: [],
    metrics: {},
    provenance: {},
    reactor: {},
    run_id: "run",
    status: "completed",
    validation: {}
  };
}

describe("geometry artifact selection", () => {
  it("requires a non-empty glTF or GLB geometry artifact", () => {
    expect(viewableGeometryArtifact([artifact("geometry/exports/core.gltf")])?.path).toBe("geometry/exports/core.gltf");
    expect(hasViewableGeometry(run([artifact("geometry/exports/core.glb")]))).toBe(true);
    expect(hasViewableGeometry(run([artifact("geometry/exports/core.obj")]))).toBe(false);
    expect(hasViewableGeometry(run([artifact("geometry/exports/core.gltf", "media")]))).toBe(false);
    expect(hasViewableGeometry(run([artifact("geometry/exports/core.gltf", "geometry", 0)]))).toBe(false);
  });
});
