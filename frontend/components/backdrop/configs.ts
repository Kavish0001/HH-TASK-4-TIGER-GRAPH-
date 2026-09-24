export type ModelKey = "chip" | "rings" | "geo" | "prism";

export const MODEL_URLS: Record<ModelKey, string> = {
  chip: "/models/chip-badge.glb",
  rings: "/models/stacked-rings.glb",
  geo: "/models/abstract-geometric.glb",
  prism: "/models/twisted-prism.glb",
};

/** One model in its own quadrant. x and y are fractions of the viewport from centre. */
export type Slot = { model: ModelKey; x: number; y: number; tilt: number; spin: number };

/** Start positions, one per quadrant. Also the static layout under reduced motion. */
export const SLOTS: Slot[] = [
  { model: "chip", x: 0.3, y: 0.24, tilt: 1.0, spin: 0.2 },
  { model: "rings", x: -0.3, y: -0.24, tilt: 0.3, spin: -0.14 },
  { model: "geo", x: -0.3, y: 0.24, tilt: 0.35, spin: 0.16 },
  { model: "prism", x: 0.3, y: -0.24, tilt: 0.25, spin: -0.18 },
];

export type BackdropConfig = {
  /** CSS opacity of the whole layer. App routes stay low so tables keep contrast. */
  opacity: number;
  /** Light multiplier. */
  bright: number;
  /** Model size as a fraction of the shorter viewport side. */
  size: number;
};

export function configFor(path: string): BackdropConfig {
  if (path === "/") return { opacity: 0.28, bright: 1.1, size: 0.12 };
  return { opacity: 0.09, bright: 0.55, size: 0.1 };
}
