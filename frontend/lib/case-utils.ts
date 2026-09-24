import type { AnswerFile, Decision, InternalCase, RecommendedAction, Route, Trigger } from "./types";

export function blankRiskAssessment() {
  return {
    fraud_probability: 0,
    confidence: 0,
    components: [],
    unknowns: [],
    evidence_coverage: 0,
    signal_agreement: 0,
    prior: 0,
    method: "",
    checked_signals: [],
    missing_signals: [],
  };
}

export function blankCase(case_id: string, trigger: Trigger): InternalCase {
  return {
    case_id,
    trigger,
    status: "open",
    status_history: [{ status: "open", at: trigger.opened_at, note: "Case opened from trigger" }],
    verdict: "uncertain",
    pattern: "none",
    pattern_description: "",
    risk_assessment: blankRiskAssessment(),
    hypotheses: [],
    affected_txn_ids: [],
    first_suspicious_txn_id: "",
    connected_card_ids: [],
    connected_device_profiles: [],
    exposure_usd: 0,
    evidence: [],
    similar_prior_cases: [],
    retrieved_chunks: [],
    summary: "",
    written_to_graph: false,
    graph_case_id: "",
    evidence_requests: [],
    customer_response: null,
    initial_actions: [],
    final_actions: [],
    what_changed: "nothing",
    sar: null,
    steps: [],
    decisions: [],
    stop_reason: "",
    tool_calls: 0,
    tokens: 0,
    latency_s: 0,
  };
}

/**
 * The live backend may send a list endpoint with trimmed rows or partial SSE snapshots.
 * I fill every missing field from a blank case so components never branch on undefined.
 */
export function normalizeCase(raw: Partial<InternalCase> & { case_id: string }): InternalCase {
  const trigger: Trigger = {
    type: "risk_score",
    source: "",
    trigger_text: "",
    opened_at: new Date(0).toISOString(),
    flagged_txn_id: "",
    card_id: "",
    customer_id: "",
    risk_score: null,
    ...(raw.trigger ?? {}),
  };
  const base = blankCase(raw.case_id, trigger);
  return {
    ...base,
    ...raw,
    trigger,
    risk_assessment: { ...base.risk_assessment, ...(raw.risk_assessment ?? {}) },
  } as InternalCase;
}

export function mergeCase(prev: InternalCase, patch: Partial<InternalCase>): InternalCase {
  const next = { ...prev, ...patch } as InternalCase;
  if (patch.risk_assessment) next.risk_assessment = { ...prev.risk_assessment, ...patch.risk_assessment };
  if (patch.trigger) next.trigger = { ...prev.trigger, ...patch.trigger };
  return next;
}

/** L1 and L2 actions in the final list that still wait for a human. */
export function pendingApprovals(c: InternalCase): { action: RecommendedAction; decision?: Decision }[] {
  return approvalItems(c).filter((x) => !x.decision || x.decision.approval_status === "pending");
}

export function approvalItems(c: InternalCase): { action: RecommendedAction; decision?: Decision }[] {
  return c.final_actions
    .filter((a) => a.route !== "auto")
    .map((a) => ({
      action: a,
      decision: [...c.decisions].reverse().find((d) => d.action === a.action && d.phase !== "initial"),
    }));
}

export function highestPendingRoute(c: InternalCase): Route | null {
  const p = pendingApprovals(c);
  if (p.some((x) => x.action.route === "L2")) return "L2";
  if (p.some((x) => x.action.route === "L1")) return "L1";
  return null;
}

export function hasRun(c: InternalCase): boolean {
  return c.steps.length > 0;
}

/** Mirrors backend/answers/write.py: the graded file is a pure projection of the internal case. */
export function toAnswerFile(c: InternalCase): AnswerFile {
  return {
    case_id: c.case_id,
    case: {
      status: c.status,
      verdict: c.verdict,
      fraud_probability: c.risk_assessment.fraud_probability,
      pattern: c.pattern,
      pattern_description: c.pattern_description,
      affected_txn_ids: c.affected_txn_ids,
      first_suspicious_txn_id: c.first_suspicious_txn_id,
      connected_card_ids: c.connected_card_ids,
      connected_device_profiles: c.connected_device_profiles,
      exposure_usd: c.exposure_usd,
      evidence: c.evidence,
      similar_prior_cases: c.similar_prior_cases,
      summary: c.summary,
      written_to_graph: c.written_to_graph,
      graph_case_id: c.graph_case_id,
    },
    evidence_requests: c.evidence_requests.map((r) => ({
      type: r.type,
      asked_after_step: r.asked_after_step,
      assumed_response: r.assumed_response,
    })),
    next_best_actions: { initial: c.initial_actions, final: c.final_actions, what_changed: c.what_changed },
    sar: c.sar ?? { file: false, reason: "", narrative: "", subjects: [], total_amount_usd: 0, activity_dates: [] },
    stop_reason: c.stop_reason,
    tool_calls: c.tool_calls,
    tokens: c.tokens,
    latency_s: c.latency_s,
  };
}

// ---- formatting --------------------------------------------------------------

export function fmtUsd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

export function fmtPct(n: number): string {
  return n.toFixed(2);
}

/**
 * Case pack timestamps are in 2016, so "age" against the wall clock would read as
 * "8 years" for every row. I measure age against the newest trigger in the set, which
 * is what an analyst would see if the queue were live on that day.
 */
export function fmtAge(iso: string, now: number): string {
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export function riskBand(p: number): "high" | "med" | "low" {
  if (p >= 0.7) return "high";
  if (p >= 0.3) return "med";
  return "low";
}

export const TRIGGER_LABEL: Record<string, string> = {
  risk_score: "risk score",
  customer_report: "customer report",
  analyst_request: "analyst request",
};
