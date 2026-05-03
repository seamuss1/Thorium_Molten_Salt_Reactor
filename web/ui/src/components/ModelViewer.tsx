import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Bounds, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { Layers, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { viewableGeometryArtifact } from "../geometryArtifacts";
import type { ArtifactRef } from "../types";

interface ModelViewerProps {
  artifacts: ArtifactRef[];
}

export function ModelViewer({ artifacts }: ModelViewerProps) {
  const gltf = viewableGeometryArtifact(artifacts);
  const [showGrid, setShowGrid] = useState(true);
  const [zoomCommand, setZoomCommand] = useState(0);

  if (!gltf) {
    return <div className="empty-panel tall">This run has no viewable glTF geometry export.</div>;
  }

  return (
    <div className="viewer-shell">
      <div className="viewer-toolbar" aria-label="3D viewer controls">
        <button type="button" onClick={() => setZoomCommand((value) => value + 1)} title="Zoom in" aria-label="Zoom in">
          <ZoomIn aria-hidden="true" />
        </button>
        <button type="button" onClick={() => setZoomCommand((value) => value - 1)} title="Zoom out" aria-label="Zoom out">
          <ZoomOut aria-hidden="true" />
        </button>
        <button type="button" onClick={() => setShowGrid((value) => !value)} title="Toggle reference grid">
          <Layers aria-hidden="true" />
        </button>
        <a href={gltf.url} target="_blank" rel="noreferrer" title="Open glTF artifact">
          <Maximize2 aria-hidden="true" />
        </a>
      </div>
      <Canvas camera={{ position: [4, 3, 5], fov: 42 }} dpr={[1, 1.75]} gl={{ antialias: true, preserveDrawingBuffer: true }}>
        <color attach="background" args={["#f6f8fa"]} />
        <ambientLight intensity={0.7} />
        <directionalLight position={[5, 8, 5]} intensity={1.6} />
        {showGrid && <gridHelper args={[8, 16, "#7c8a97", "#d2dbe3"]} position={[0, -1.2, 0]} />}
        <Suspense fallback={<Html center>Loading geometry...</Html>}>
          <Bounds fit clip observe margin={1.2}>
            <ReactorScene url={gltf.url} />
          </Bounds>
        </Suspense>
        <ViewerOrbitControls zoomCommand={zoomCommand} />
      </Canvas>
    </div>
  );
}

function ViewerOrbitControls({ zoomCommand }: { zoomCommand: number }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const lastZoomCommand = useRef(0);
  const { camera } = useThree();

  useEffect(() => {
    const delta = zoomCommand - lastZoomCommand.current;
    if (delta === 0) return;

    const target = controls.current?.target ?? new Vector3(0, 0, 0);
    const offset = camera.position.clone().sub(target);
    if (offset.lengthSq() === 0) offset.set(4, 3, 5);
    const factor = Math.pow(delta > 0 ? 0.82 : 1.22, Math.abs(delta));
    offset.setLength(Math.min(90, Math.max(0.7, offset.length() * factor)));
    camera.position.copy(target.clone().add(offset));
    camera.updateProjectionMatrix();
    controls.current?.update();
    lastZoomCommand.current = zoomCommand;
  }, [camera, zoomCommand]);

  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.08} />;
}

function ReactorScene({ url }: { url: string }) {
  const model = useGLTF(url);
  const scene = useMemo(() => model.scene.clone(true), [model.scene]);
  return (
    <group rotation={[-Math.PI / 2, 0, 0]}>
      <primitive object={scene} />
    </group>
  );
}
