import { Suspense, useMemo, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { Bounds, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { Box, Layers, Maximize2 } from "lucide-react";
import type { ArtifactRef } from "../types";

interface ModelViewerProps {
  artifacts: ArtifactRef[];
}

export function ModelViewer({ artifacts }: ModelViewerProps) {
  const gltf = artifacts.find((artifact) => artifact.path.endsWith(".gltf"));
  const fallbackImage =
    artifacts.find((artifact) => artifact.label.includes("physics_overlay")) ??
    artifacts.find((artifact) => artifact.label.includes("annotated_cutaway")) ??
    artifacts.find((artifact) => artifact.mime_type.startsWith("image/"));
  const [showGrid, setShowGrid] = useState(true);

  if (!gltf && fallbackImage) {
    return (
      <div className="viewer-fallback">
        <img src={fallbackImage.url} alt={fallbackImage.label} />
      </div>
    );
  }

  if (!gltf) {
    return <div className="empty-panel tall">No 3D geometry export is available for this run.</div>;
  }

  return (
    <div className="viewer-shell">
      <div className="viewer-toolbar" aria-label="3D viewer controls">
        <button type="button" onClick={() => setShowGrid((value) => !value)} title="Toggle reference grid">
          <Layers aria-hidden="true" />
        </button>
        <a href={gltf.url} target="_blank" rel="noreferrer" title="Open glTF artifact">
          <Maximize2 aria-hidden="true" />
        </a>
      </div>
      <Canvas camera={{ position: [4, 3, 5], fov: 42 }} dpr={[1, 1.75]}>
        <color attach="background" args={["#f6f8fa"]} />
        <ambientLight intensity={0.7} />
        <directionalLight position={[5, 8, 5]} intensity={1.6} />
        {showGrid && <gridHelper args={[8, 16, "#7c8a97", "#d2dbe3"]} position={[0, -1.2, 0]} />}
        <Suspense fallback={<Html center>Loading geometry...</Html>}>
          <Bounds fit clip observe margin={1.2}>
            <ReactorScene url={gltf.url} />
          </Bounds>
        </Suspense>
        <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
      </Canvas>
    </div>
  );
}

function ReactorScene({ url }: { url: string }) {
  const model = useGLTF(url);
  const scene = useMemo(() => model.scene.clone(true), [model.scene]);
  return (
    <group rotation={[-Math.PI / 2, 0, 0]}>
      <primitive object={scene} />
      <Html position={[0, 1.4, 0]} center className="viewer-label">
        <Box size={14} /> geometry export
      </Html>
    </group>
  );
}
