"use client";

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { CaseStatus, EvidenceSource, Route, TriggerType } from "@/lib/types";
import { TRIGGER_LABEL } from "@/lib/case-utils";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

// ---- Panel -------------------------------------------------------------------

export type PanelState = "idle" | "waiting" | "running" | "ok" | "alert" | "error";

const STATE_LABEL: Record<PanelState, string> = {
  idle: "",
  waiting: "waiting",
  running: "streaming",
  ok: "ok",
  alert: "action",
  error: "error",
};

export function Panel({
  title,
  state = "idle",
  meta,
  children,
  className,
  bodyClassName,
  id,
}: {
  title: string;
  state?: PanelState;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section className={cx("panel", className)} data-state={state} aria-labelledby={id ? `${id}-h` : undefined}>
      <header className="panel-h">
        <h2 id={id ? `${id}-h` : undefined} className={cx("eyebrow", state === "running" && "pulse text-flame-500")}>
          {title}
        </h2>
        <div className="ml-auto flex items-center gap-2">
          {meta}
          {state !== "idle" && <StateToken state={state} />}
        </div>
      </header>
      <div className={cx("panel-b", bodyClassName)}>{children}</div>
    </section>
  );
}

function StateToken({ state }: { state: PanelState }) {
  const cls =
    state === "running" || state === "alert"
      ? "text-flame-500 border-flame-700"
      : state === "error"
        ? "text-flame-400 border-flame-600"
        : "text-text-faint border-rust-800";
  return <span className={cx("font-mono text-[9.5px] uppercase tracking-wider border rounded-xs px-1.5 py-px", cls)}>{STATE_LABEL[state]}</span>;
}

/** Placeholder for a pane the agent has not reached yet. */
export function Waiting({ children }: { children: ReactNode }) {
  return <p className="eyebrow py-3">{children}</p>;
}

// ---- Chips -------------------------------------------------------------------

export function Chip({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap font-mono text-[10px] leading-none border rounded-xs px-1.5 py-[3px]", className)}>
      {children}
    </span>
  );
}

const STATUS_CLS: Record<CaseStatus, string> = {
  open: "border-rust-500 text-text-muted",
  escalated: "border-flame-600 text-flame-400",
  closed_fraud: "border-rust-600 bg-rust-600 text-text",
  closed_legitimate: "border-sage-700 bg-sage-900 text-sage-300",
};

export function StatusChip({ status }: { status: CaseStatus }) {
  return <Chip className={STATUS_CLS[status] ?? "border-rust-800 text-text-muted"}>{status}</Chip>;
}

export function OutcomeChip({ outcome }: { outcome: string }) {
  if (outcome === "cleared" || outcome === "closed_legitimate") return <Chip className="border-sage-700 bg-sage-900 text-sage-300">{outcome}</Chip>;
  if (outcome === "confirmed_fraud" || outcome === "closed_fraud") return <Chip className="border-rust-600 text-rust-300">{outcome}</Chip>;
  return <Chip className="border-rust-800 text-text-faint">{outcome || "unknown"}</Chip>;
}

/** L1 and L2 are needs-action; auto is informational. */
export function RouteBadge({ route }: { route: Route }) {
  if (route === "L2") return <Chip className="border-flame-500 bg-flame-500 text-ink-950 font-bold">L2</Chip>;
  if (route === "L1") return <Chip className="border-rust-600 bg-rust-600 text-text font-bold">L1</Chip>;
  return <Chip className="border-rust-800 text-text-faint">auto</Chip>;
}

export function TriggerChip({ type }: { type: TriggerType }) {
  return <Chip className="border-rust-800 text-text-muted">{TRIGGER_LABEL[type] ?? type}</Chip>;
}

const SOURCE_LABEL: Record<EvidenceSource, string> = { graph: "GRAPH", document: "DOC", customer: "CUST", external: "EXT" };

/** Four fixed widths so the column aligns. Graph gets the rust edge because graph evidence is the differentiator. */
export function SourceTag({ source }: { source: EvidenceSource }) {
  return (
    <span
      className={cx(
        "inline-block w-[44px] text-center font-mono text-[9.5px] tracking-wider border rounded-xs py-[2px]",
        source === "graph" ? "border-rust-500 text-text-muted" : "border-rust-800 text-text-faint",
      )}
    >
      {SOURCE_LABEL[source] ?? source}
    </span>
  );
}

// ---- Bar ---------------------------------------------------------------------

export function Bar({ value, tone, width = 40 }: { value: number; tone: "flame" | "rust" | "sand" | "sage"; width?: number }) {
  const fill = { flame: "bg-flame-500", rust: "bg-rust-500", sand: "bg-risk-low", sage: "bg-sage-400" }[tone];
  return (
    <span className="inline-block relative h-[6px] border border-rust-800 rounded-xs align-middle" style={{ width }} aria-hidden>
      <span className={cx("absolute inset-y-0 left-0", fill)} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </span>
  );
}

// ---- Button ------------------------------------------------------------------

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "reject"; size?: "sm" | "md" };

export function buttonClass(variant: "primary" | "ghost" | "reject" = "ghost", size: "sm" | "md" = "md") {
  const v =
    variant === "primary"
      ? "bg-flame-500 text-ink-950 border-flame-500 hover:bg-flame-400 disabled:bg-transparent disabled:text-text-faint disabled:border-rust-800"
      : variant === "reject"
        ? "bg-transparent text-rust-300 border-rust-500 hover:bg-ember-900 disabled:text-text-faint disabled:border-rust-800"
        : "bg-transparent text-text-muted border-rust-500 hover:bg-ember-900 hover:text-text disabled:text-text-faint disabled:border-rust-800";
  const s = size === "sm" ? "text-[10.5px] px-2 py-1" : "text-[11.5px] px-3 py-1.5";
  return cx("inline-block font-mono uppercase tracking-wider border rounded-xs transition-colors disabled:cursor-not-allowed", v, s);
}

/** One filled control per screen (primary); everything else is a hairline ghost. */
export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button({ variant = "ghost", size = "md", className, ...rest }, ref) {
  return <button ref={ref} className={cx(buttonClass(variant, size), className)} {...rest} />;
});
