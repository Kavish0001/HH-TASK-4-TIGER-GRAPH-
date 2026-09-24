export type ModelKey = "chip" | "rings" | "geo" | "prism";

export const MODEL_URLS: Record<ModelKey, string> = {
  chip: "/models/chip-badge.glb",
  rings: "/models/stacked-rings.glb",
  geo: "/models/abstract-geometric.glb",
  prism: "/models/twisted-prism.glb",
};

export type BackdropConfig = {
  model: ModelKey;
  /** CSS opacity of the whole layer. App routes stay low so tables keep contrast. */
  opacity: number;
  /** Light multiplier. */
  bright: number;
  /** Size as a fraction of the shorter viewport side, in world units. */
  scale: number;
  /** Offset as a fraction of the viewport, applied on wide screens only. */
  x: number;
  y: number;
  tilt: number;
  /** Radians per second around Y. */
  spin: number;
};

export function configFor(path: string): BackdropConfig {
  if (path === "/") return { model: "chip", opacity: 1, bright: 1.2, scale: 0.3, x: 0.22, y: 0.02, tilt: 1.0, spin: 0.22 };
  if (path.startsWith("/cases/")) return { model: "geo", opacity: 0.16, bright: 0.8, scale: 0.26, x: 0.3, y: -0.22, tilt: 0.35, spin: 0.06 };
  if (path.startsWith("/cases")) return { model: "rings", opacity: 0.2, bright: 0.8, scale: 0.3, x: 0.3, y: -0.18, tilt: 0.3, spin: 0.07 };
  if (path.startsWith("/launch")) return { model: "prism", opacity: 0.2, bright: 0.85, scale: 0.22, x: 0.36, y: -0.05, tilt: 0.25, spin: 0.08 };
  return { model: "rings", opacity: 0.18, bright: 0.8, scale: 0.28, x: 0.3, y: -0.18, tilt: 0.3, spin: 0.07 };
}
