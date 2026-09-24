"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { Component, Suspense, useEffect, useState, type ReactNode } from "react";
import { configFor } from "./configs";

const Scene = dynamic(() => import("./scene"), { ssr: false, loading: () => null });

/** WebGL or model failures must never take down the page; the static glow stays. */
class Guard extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {}
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function canWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

/**
 * Fixed, full-viewport decorative layer behind every route. Mounted once in the root
 * layout so the canvas persists across navigation; the model and dimming change per
 * route. App routes stay dim so dense tables keep their contrast.
 */
export function Backdrop() {
  const path = usePathname();
  const cfg = configFor(path);
  const [ok, setOk] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => setOk(canWebGL()), []);
  const landing = path === "/";

  return (
    <div aria-hidden className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
      {/* Static fallback: a warm glow that also shows while the scene loads. */}
      <div
        className="absolute inset-0 transition-opacity duration-700"
        style={{
          background: landing
            ? "radial-gradient(ellipse 42% 52% at 72% 46%, rgba(155,57,34,0.30), transparent 70%), radial-gradient(ellipse 26% 28% at 72% 46%, rgba(242,97,63,0.12), transparent 70%)"
            : "radial-gradient(ellipse 40% 45% at 85% 85%, rgba(72,30,20,0.35), transparent 70%)",
        }}
      />
      {ok && (
        <div
          className="absolute inset-0 transition-opacity duration-1000"
          style={{ opacity: ready ? cfg.opacity : 0, filter: landing ? undefined : "saturate(0.55)" }}
        >
          <Guard>
            <Suspense fallback={null}>
              <Scene cfg={cfg} onReady={() => setReady(true)} />
            </Suspense>
          </Guard>
        </div>
      )}
    </div>
  );
}
