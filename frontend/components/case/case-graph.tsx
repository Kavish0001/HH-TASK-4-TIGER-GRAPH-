"use client";

// Cytoscape, per decision.md 5.2.1: the case subgraph is tens of nodes, so a style engine
// and deterministic layout matter more than WebGL scale. I import it inside the effect
// so the queue and launcher never load it.

import { useEffect, useMemo, useRef } from "react";
import type { Core, ElementDefinition } from "cytoscape";
import type { InternalCase } from "@/lib/types";

const C = {
  text: "#F5EDE8",
  muted: "#C9B7AC",
  faint: "#A38F83",
  surface: "#290C06",
  raised: "#481E14",
  raisedHi: "#673124",
  line: "#601604",
  lineStrong: "#9B3922",
  control: "#BB5841",
  flame: "#F2613F",
  flame400: "#FF8568",
  sand: "#D9C7BC",
};

export function buildElements(c: InternalCase): ElementDefinition[] {
  const els: ElementDefinition[] = [];
  const seen = new Set<string>();
  const node = (id: string, type: string, label: string, extra: Record<string, unknown> = {}, classes = "") => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    els.push({ data: { id, type, label, ...extra }, classes: `${type} ${classes}`.trim() });
  };
  const edge = (a: string, b: string, rel: string, classes = "") => {
    if (!seen.has(a) || !seen.has(b)) return;
    els.push({ data: { id: `${a}->${b}`, source: a, target: b, rel }, classes: `${rel} ${classes}`.trim() });
  };

  const t = c.trigger;
  const ring = c.connected_device_profiles.length > 0 && c.connected_card_ids.length > 0;
  if (ring) node("ring", "ring", `shared origin: ${c.connected_card_ids.length + 1} cards`);
  const parent = ring ? { parent: "ring" } : {};

  node(t.customer_id, "customer", t.customer_id);
  node(t.card_id, "card", t.card_id, parent, "flagged");
  edge(t.customer_id, t.card_id, "holds");

  for (const card of c.connected_card_ids) {
    const cust = card.split("-")[0];
    node(cust, "customer", cust);
    node(card, "card", card, parent);
    edge(cust, card, "holds");
  }
  c.connected_device_profiles.forEach((d) => {
    node(d, "device", d.split("|")[0].trim(), parent);
    edge(t.card_id, d, "used_device", ring ? "ring-edge" : "");
    for (const card of c.connected_card_ids) edge(card, d, "used_device", "ring-edge");
  });

  const txns = [...new Set([t.flagged_txn_id, ...c.affected_txn_ids])].filter(Boolean);
  for (const x of txns) {
    node(x, "txn", x, {}, x === t.flagged_txn_id ? "flagged-txn" : x === c.first_suspicious_txn_id ? "first" : "");
    edge(t.card_id, x, "made");
  }
  for (const id of c.similar_prior_cases) {
    node(id, "prior", id);
    edge(id, t.card_id, "similar");
  }
  return els;
}

export function CaseGraph({ data, highlight }: { data: InternalCase; highlight: string[] }) {
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const els = useMemo(() => buildElements(data), [data]);
  const sig = useMemo(() => els.map((e) => e.data.id).join(","), [els]);

  useEffect(() => {
    let dead = false;
    (async () => {
      const cytoscape = (await import("cytoscape")).default;
      if (dead || !host.current) return;
      cy.current?.destroy();
      const inst = cytoscape({
        container: host.current,
        elements: els,
        minZoom: 0.4,
        maxZoom: 2.5,
        style: [
          { selector: "node", style: { "background-color": C.raised, "border-width": 1, "border-color": C.control, label: "data(label)", color: C.muted, "font-family": "JetBrains Mono, monospace", "font-size": 9, "text-valign": "bottom", "text-margin-y": 3, width: 16, height: 16 } },
          { selector: "node.customer", style: { shape: "round-rectangle", width: 14, height: 14, "background-color": C.surface } },
          { selector: "node.card", style: { shape: "round-rectangle", width: 22, height: 14, "background-color": C.raisedHi } },
          { selector: "node.flagged", style: { "border-color": C.flame, "border-width": 2, color: C.text } },
          { selector: "node.device", style: { shape: "diamond", width: 20, height: 20, "background-color": C.lineStrong, "border-color": C.flame400, color: C.text } },
          { selector: "node.txn", style: { shape: "ellipse", width: 10, height: 10, "background-color": C.surface, "border-color": C.faint, "font-size": 7 } },
          { selector: "node.flagged-txn", style: { "border-color": C.flame, "border-width": 2 } },
          { selector: "node.prior", style: { shape: "tag", width: 12, height: 12, "background-color": C.surface, "border-color": C.line, "border-style": "dashed", color: C.faint, "font-size": 7 } },
          { selector: "node.ring", style: { shape: "round-rectangle", "background-color": C.flame, "background-opacity": 0.06, "border-color": C.flame, "border-width": 1, "border-style": "dashed", label: "data(label)", "text-valign": "top", "text-halign": "center", "text-margin-y": -4, color: C.flame400, "font-size": 8, padding: "14px" } },
          { selector: "edge", style: { width: 1, "line-color": C.line, "curve-style": "bezier", "target-arrow-shape": "none" } },
          { selector: "edge.ring-edge", style: { "line-color": C.flame, width: 1.3, opacity: 0.8 } },
          { selector: "edge.similar", style: { "line-style": "dashed", "line-color": C.line } },
          { selector: ".cited", style: { "border-color": C.text, "border-width": 3, color: C.text, "z-index": 10 } },
          { selector: "edge.cited", style: { "line-color": C.text, width: 2 } },
          { selector: ".dim", style: { opacity: 0.25 } },
        ],
        layout: { name: "preset" },
      });
      // A layered left-to-right layout (customer, card, device, txn, prior case), computed
      // by hand so it is identical on every reload and during the demo run. It reads the
      // same way the evidence does: who holds what, what it touched, what it resembles.
      // The ring boundary is a compound parent and takes its bounds from its members.
      const cols: Record<string, number> = { customer: 0, card: 1, device: 2, txn: 3, prior: 4 };
      const byCol = new Map<number, string[]>();
      inst.nodes().not(":parent").forEach((n) => {
        const col = cols[n.data("type") as string] ?? 4;
        byCol.set(col, [...(byCol.get(col) ?? []), n.id()]);
      });
      const DX = 150;
      const DY = 30;
      byCol.forEach((ids, col) => {
        ids.forEach((id, i) => {
          inst.getElementById(id).position({ x: col * DX, y: (i - (ids.length - 1) / 2) * DY });
        });
      });
      inst.fit(undefined, 20);
      cy.current = inst;
    })();
    return () => {
      dead = true;
    };
    // Rebuild only when the node set changes, not on every streamed patch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);

  useEffect(() => () => cy.current?.destroy(), []);

  useEffect(() => {
    const g = cy.current;
    if (!g) return;
    g.elements().removeClass("cited dim");
    if (!highlight.length) return;
    const hit = g.collection();
    for (const id of highlight) {
      const n = g.getElementById(id);
      if (n.nonempty()) hit.merge(n);
    }
    if (hit.empty()) return;
    const edges = hit.edgesWith(hit);
    hit.addClass("cited");
    edges.addClass("cited");
    g.elements().not(hit).not(edges).not("node.ring").addClass("dim");
  }, [highlight, sig]);

  return (
    <div className="absolute inset-0">
      <div ref={host} style={{ position: "absolute", inset: 0 }} role="img" aria-label={`Case subgraph with ${els.filter((e) => !e.data.source).length} nodes`} />
      <ul className="absolute left-1 bottom-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[9.5px] text-text-faint pointer-events-none">
        <li>{"▭ card"}</li>
        <li>{"◇ device"}</li>
        <li>{"○ txn"}</li>
        <li>{"□ customer"}</li>
        <li className="text-flame-400">{"-- shared origin"}</li>
      </ul>
    </div>
  );
}
