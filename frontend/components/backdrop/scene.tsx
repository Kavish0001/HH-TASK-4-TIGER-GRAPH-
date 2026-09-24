"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useGLTF } from "@react-three/drei";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { BackdropConfig } from "./configs";
import { MODEL_URLS, SLOTS } from "./configs";

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
    const body = new THREE.MeshStandardMaterial({ color: EMBER, metalness: 0.6, roughness: 0.6 });
    const edge = new THREE.MeshStandardMaterial({ color: RUST, metalness: 0.5, roughness: 0.6 });
    const chip = new THREE.MeshStandardMaterial({ color: SAND, metalness: 0.6, roughness: 0.55 });
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
    // Radius of the bounding sphere of the normalised model (half the box diagonal).
    const r = (size.length() * s) / 2;
    return { g, r };
  }, [scene]);
}

const MIN_SPEED = 0.04;
const MAX_SPEED = 0.08;

/**
 * All four models drift freely in the z = 0 plane. Walls come from the camera
 * frustum at that depth (R3F viewport), so they follow resizes. Each model is a
 * circle with its scaled bounding-sphere radius; overlapping pairs are pushed
 * apart along the line between centres, then speed is clamped to stay ambient.
 * Reduced motion keeps the non-overlapping quadrant layout from SLOTS.
 */
function Swarm({ cfg, still }: { cfg: BackdropConfig; still: boolean }) {
  const chip = useStyledModel(MODEL_URLS.chip);
  const rings = useStyledModel(MODEL_URLS.rings);
  const geo = useStyledModel(MODEL_URLS.geo);
  const prism = useStyledModel(MODEL_URLS.prism);
  const byKey = { chip, rings, geo, prism };
  const models = SLOTS.map((slot) => ({ slot, ...byKey[slot.model] }));

  const { viewport } = useThree();
  const scale = cfg.size * Math.min(viewport.width, viewport.height);
  const outer = useRef<(THREE.Group | null)[]>([]);
  const spinner = useRef<(THREE.Group | null)[]>([]);
  const sim = useRef<{ p: THREE.Vector2[]; v: THREE.Vector2[] } | null>(null);
  if (!sim.current) {
    sim.current = {
      p: SLOTS.map((sl) => new THREE.Vector2(sl.x * viewport.width, sl.y * viewport.height)),
      v: SLOTS.map((_, i) => {
        const a = 0.7 + i * 1.9;
        return new THREE.Vector2(Math.cos(a), Math.sin(a)).multiplyScalar(0.06);
      }),
    };
  }

  // Reduced motion: snap back to the static quadrant layout.
  useEffect(() => {
    if (!still || !sim.current) return;
    SLOTS.forEach((sl, i) => {
      sim.current!.p[i].set(sl.x * viewport.width, sl.y * viewport.height);
      outer.current[i]?.position.set(sim.current!.p[i].x, sim.current!.p[i].y, 0);
    });
  }, [still, viewport.width, viewport.height]);

  useFrame((state, delta) => {
    if (still || !sim.current) return;
    const dt = Math.min(delta, 0.05);
    const t = state.clock.elapsedTime;
    const hw = state.viewport.width / 2;
    const hh = state.viewport.height / 2;
    const { p, v } = sim.current;
    const r = models.map((m) => m.r * scale);

    for (let i = 0; i < p.length; i++) {
      // Slight wander so paths never settle into a loop.
      v[i].x += Math.sin(t * 0.23 + i * 2.1) * 0.02 * dt;
      v[i].y += Math.cos(t * 0.19 + i * 1.3) * 0.02 * dt;
      p[i].addScaledVector(v[i], dt);
      // Soft walls: clamp inside and reflect with a little damping.
      const wx = Math.max(hw - r[i], 0);
      const wy = Math.max(hh - r[i], 0);
      if (p[i].x > wx) { p[i].x = wx; v[i].x = -Math.abs(v[i].x) * 0.9; }
      if (p[i].x < -wx) { p[i].x = -wx; v[i].x = Math.abs(v[i].x) * 0.9; }
      if (p[i].y > wy) { p[i].y = wy; v[i].y = -Math.abs(v[i].y) * 0.9; }
      if (p[i].y < -wy) { p[i].y = -wy; v[i].y = Math.abs(v[i].y) * 0.9; }
    }

    const d = new THREE.Vector2();
    for (let i = 0; i < p.length; i++) {
      for (let j = i + 1; j < p.length; j++) {
        d.subVectors(p[j], p[i]);
        const dist = d.length() || 0.0001;
        const min = r[i] + r[j];
        if (dist >= min) continue;
        d.divideScalar(dist);
        const push = (min - dist) / 2;
        p[i].addScaledVector(d, -push);
        p[j].addScaledVector(d, push);
        v[i].addScaledVector(d, -0.04);
        v[j].addScaledVector(d, 0.04);
      }
    }

    for (let i = 0; i < p.length; i++) {
      const sp = v[i].length();
      if (sp > MAX_SPEED) v[i].multiplyScalar(MAX_SPEED / sp);
      else if (sp < MIN_SPEED) v[i].multiplyScalar(MIN_SPEED / Math.max(sp, 0.0001));
      outer.current[i]?.position.set(p[i].x, p[i].y, 0);
      const sg = spinner.current[i];
      if (sg) sg.rotation.y += dt * models[i].slot.spin * 0.5;
    }
  });

  return (
    <>
      {models.map((m, i) => (
        <group
          key={m.slot.model}
          ref={(el) => { outer.current[i] = el; }}
          position={[sim.current!.p[i].x, sim.current!.p[i].y, 0]}
          scale={scale}
        >
          <group ref={(el) => { spinner.current[i] = el; }} rotation={[m.slot.tilt, 0.6, 0]}>
            <primitive object={m.g} />
          </group>
        </group>
      ))}
    </>
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
        <Swarm cfg={cfg} still={still} />
      </Suspense>
    </Canvas>
  );
}

Object.values(MODEL_URLS).forEach((u) => useGLTF.preload(u));
