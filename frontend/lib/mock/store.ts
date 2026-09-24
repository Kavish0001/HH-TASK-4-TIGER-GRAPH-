// In-memory stand-in for the backend. It lives for the browser session only, which is
// enough to demo approve/reject and re-running a case without a server.
import type { ApprovalRequest, InternalCase, NewCaseRequest } from "../types";
import { blankCase } from "../case-utils";

type Json = InternalCase[];

let cases: Map<string, InternalCase> | null = null;
let results: Map<string, InternalCase> | null = null;
let adhocSeq = 0;

async function load() {
  if (cases && results) return;
  // Dynamic import so the 480 KB of mock JSON never ships when the live backend is set.
  const [c, r] = await Promise.all([import("./cases.json"), import("./results.json")]);
  cases = new Map((c.default as unknown as Json).map((x) => [x.case_id, x]));
  results = new Map((r.default as unknown as Json).map((x) => [x.case_id, x]));
}

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

export async function mockList(): Promise<InternalCase[]> {
  await load();
  return [...cases!.values()].map(clone);
}

export async function mockGet(id: string): Promise<InternalCase> {
  await load();
  const c = cases!.get(id);
  if (!c) throw new Error(`Case ${id} not found`);
  return clone(c);
}

export async function mockCreate(req: NewCaseRequest): Promise<string> {
  await load();
  // A trigger naming a benchmark transaction opens that benchmark case rather than a copy.
  const existing = [...cases!.values()].find((c) => c.trigger.flagged_txn_id === req.flagged_txn_id);
  if (existing) return existing.case_id;
  adhocSeq += 1;
  const id = `ADH-${String(adhocSeq).padStart(3, "0")}`;
  const trigger = {
    type: req.trigger_type,
    source: req.trigger_type === "analyst_request" ? "analyst_console" : req.trigger_type === "customer_report" ? "customer_channel" : "realtime_model_v3",
    trigger_text: req.trigger_text,
    opened_at: new Date().toISOString(),
    flagged_txn_id: req.flagged_txn_id,
    card_id: req.card_id,
    customer_id: req.customer_id,
    risk_score: req.risk_score,
  };
  cases!.set(id, blankCase(id, trigger));
  // Ad hoc cases replay a benchmark run of the same trigger type, relabelled. The UI marks
  // the whole app as mock data, so this is a stand-in for the agent, not a claim.
  const template = { risk_score: "HHG-010", customer_report: "HHG-003", analyst_request: "HHG-014" }[req.trigger_type];
  const r = clone(results!.get(template)!);
  results!.set(id, { ...r, case_id: id, trigger });
  return id;
}

export async function mockResult(id: string): Promise<InternalCase> {
  await load();
  const r = results!.get(id);
  if (!r) throw new Error(`No mock run recorded for ${id}`);
  return clone(r);
}

export async function mockSave(c: InternalCase) {
  await load();
  cases!.set(c.case_id, clone(c));
}

export async function mockApprove(id: string, req: ApprovalRequest): Promise<InternalCase> {
  await load();
  const c = clone(cases!.get(id)!);
  const now = new Date().toISOString();
  const status = req.approved ? "approved" : "rejected";
  let hit = false;
  c.decisions = c.decisions.map((d) => {
    if (d.action === req.action && d.phase !== "initial" && d.approval_status === "pending") {
      hit = true;
      return { ...d, approval_status: status, authorized: req.approved, executed: req.approved };
    }
    return d;
  });
  if (!hit) {
    const a = c.final_actions.find((x) => x.action === req.action);
    if (a) c.decisions.push({ actor: req.analyst, action: a.action, route: a.route, authorized: req.approved, executed: req.approved, reason: req.note || a.reason, phase: "final", approval_status: status, at: now });
  }
  c.status_history.push({ status: c.status, at: now, note: `${req.action} ${status} by ${req.analyst}${req.note ? `: ${req.note}` : ""}` });
  // Once no L1/L2 action is waiting, the case can close on its verdict. An uncertain
  // verdict stays where it is, because a human decision on one action does not settle it.
  const waiting = c.decisions.some((d) => d.phase !== "initial" && d.route !== "auto" && d.approval_status === "pending");
  if (!waiting && c.verdict !== "uncertain") {
    const closed = c.verdict === "fraud" ? "closed_fraud" : "closed_legitimate";
    if (c.status !== closed) {
      c.status = closed;
      c.status_history.push({ status: closed, at: now, note: "All human approvals recorded" });
    }
  }
  cases!.set(id, c);
  return clone(c);
}
