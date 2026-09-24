"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Float, useGLTF } from "@react-three/drei";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { BackdropConfig } from "./configs";
import { MODEL_URLS } from "./configs";

// Palette: flame #F2613F, rust #9B3922, ember #481E14 (lifted a step so metal reads).
const FLAME = new THREE.Color("#F2613F");
const RUST = new THREE.Color("#9B3922");
const EMBER = new THREE.Color("#4A2A22");
const SAND = new THREE.Color("#D9C7BC");

/** Clone the cached glTF scene, restyle it in palette metal, and fit it to a unit box. */
function useStyledModel(url: string) {
  const { scene } = useGLTF(url);
  return useMemo(() => {
    const root = scene.clone(true);
    const body = new THREE.MeshStandardMaterial({ color: EMBER, metalness: 0.75, roughness: 0.38 });
    const edge = new THREE.MeshStandardMaterial({ color: RUST, metalness: 0.6, roughness: 0.45 });
    const chip = new THREE.MeshStandardMaterial({ color: SAND, metalness: 0.85, roughness: 0.3 });
    const accent = new THREE.MeshStandardMaterial({ color: FLAME, emissive: FLAME, emissiveIntensity: 0.9, roughness: 0.4 });
    root.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      const n = m.name.toLowerCase();
      const src = m.material as THREE.MeshStandardMaterial | undefined;
      const isRed = !!src?.color && src.color.r > 0.9 && src.color.g < 0.1 && src.color.b < 0.1;
      if (n.includes("accent") || n.includes("pin")) m.material = accent;
      else if (n.includes("chip") || n.includes("core") || n.includes("blade") || n.includes("prism")) m.material = chip;
      else if (n.includes("ring") || n.includes("rim") || n.includes("band") || n.includes("seam")) m.material = edge;
      else if (n.includes("disc") || n.includes("pedestal")) m.material = body;
      else m.material = isRed ? accent : body;
    });
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const s = 2 / Math.max(size.x, size.y, size.z, 0.0001);
    root.scale.setScalar(s);
    root.position.copy(center.multiplyScalar(-s));
    const g = new THREE.Group();
    g.add(root);
    return g;
  }, [scene]);
}

function Model({ cfg, still }: { cfg: BackdropConfig; still: boolean }) {
  const obj = useStyledModel(MODEL_URLS[cfg.model]);
  const ref = useRef<THREE.Group>(null);
  const { viewport, size } = useThree();
  const wide = size.width >= 1024;
  const x = wide ? viewport.width * cfg.x : 0;
  const y = wide ? viewport.height * cfg.y : viewport.height * 0.14;
  const scale = cfg.scale * Math.min(viewport.width, viewport.height) * (wide ? 1 : 0.85);

  useFrame((_, dt) => {
    if (still || !ref.current) return;
    ref.current.rotation.y += Math.min(dt, 0.1) * cfg.spin;
  });

  const inner = (
    <group ref={ref} rotation={[cfg.tilt, 0.6, 0]}>
      <primitive object={obj} />
    </group>
  );
  return (
    <group position={[x, y, 0]} scale={scale}>
      {still ? (
        inner
      ) : (
        <Float speed={1} rotationIntensity={0.15} floatIntensity={0.35}>
          {inner}
        </Float>
      )}
    </group>
  );
}

function Rig({ bright }: { bright: number }) {
  return (
    <>
      <ambientLight intensity={0.25 * bright} color="#F5EDE8" />
      <hemisphereLight args={["#FFCDC0", "#290C06", 0.4 * bright]} />
      <directionalLight position={[4, 5, 3]} intensity={2.4 * bright} color="#F2613F" />
      <directionalLight position={[-5, 2, -2]} intensity={1.2 * bright} color="#9B3922" />
      <pointLight position={[0, -3, 3]} intensity={3 * bright} distance={14} color="#FF8568" />
    </>
  );
}

export default function BackdropScene({ cfg, onReady }: { cfg: BackdropConfig; onReady: () => void }) {
  const [still, setStill] = useState(false);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setStill(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    const vis = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", vis);
    return () => {
      mq.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", vis);
    };
  }, []);

  // Reduced motion or a hidden tab: render on demand only, which leaves a static frame.
  const loop = still || hidden ? "demand" : "always";

  return (
    <Canvas
      frameloop={loop}
      dpr={[1, 1.5]}
      camera={{ position: [0, 0, 6], fov: 35 }}
      gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
      onCreated={() => onReady()}
    >
      <Rig bright={cfg.bright} />
      <Suspense fallback={null}>
        <Model key={cfg.model} cfg={cfg} still={still} />
      </Suspense>
    </Canvas>
  );
}

Object.values(MODEL_URLS).forEach((u) => useGLTF.preload(u));
