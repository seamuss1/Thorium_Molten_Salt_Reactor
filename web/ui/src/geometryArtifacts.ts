import type { ArtifactRef, RunRecord } from "./types";

const VIEWABLE_GEOMETRY_EXTENSIONS = [".gltf", ".glb"];

export function viewableGeometryArtifact(artifacts: ArtifactRef[]): ArtifactRef | undefined {
  return artifacts.find((artifact) => {
    const path = artifact.path.toLowerCase();
    return artifact.kind === "geometry" && artifact.size > 0 && VIEWABLE_GEOMETRY_EXTENSIONS.some((extension) => path.endsWith(extension));
  });
}

export function hasViewableGeometry(run: RunRecord): boolean {
  return Boolean(viewableGeometryArtifact(run.artifacts));
}
