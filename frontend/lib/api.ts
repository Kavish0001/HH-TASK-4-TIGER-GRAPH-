// One data layer for every screen. With NEXT_PUBLIC_API_URL unset it serves lib/mock/;
// with it set it talks to backend/api/main.py:
//   GET  /cases               list
//   GET  /cases/{id}          one InternalCase
//   POST /cases/{id}/run      SSE of agent steps
//   POST /cases/{id}/approve  approve or reject an L1/L2 action
//   POST /cases               open a case from a trigger (launcher; optional on the backend)
// Components never touch fetch or EventSource directly, so switching sources, or falling
// back from SSE to polling, does not change any screen.

import type { AgentStep, ApprovalRequest, InternalCase, NewCaseRequest, RunState } from "./types";
import { blankCase, mergeCase, normalizeCase } from "./case-utils";
import { mockApprove, mockCreate, mockGet, mockList, mockResult, mockSave } from "./mock/store";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
export const IS_LIVE = API_URL.length > 0;
const POLL_MS = Number(process.env.NEXT_PUBLIC_POLL_MS ?? 1500) || 1500;

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${res.status} ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

type CaseLike = Partial<InternalCase> & { case_id: string };

function unwrapList(x: unknown): CaseLike[] {
  if (Array.isArray(x)) return x as CaseLike[];
  if (x && typeof x === "object" && Array.isArray((x as { cases?: unknown }).cases)) return (x as { cases: CaseLike[] }).cases;
  return [];
}

// backend/agent/nodes.py _emit ships every milestone as {...payload, case: snapshot},
// and "done" also has case_id at the top level, so the nested snapshot wins when present.
function unwrapCase(x: unknown): CaseLike | null {
  if (!x || typeof x !== "object") return null;
  const inner = (x as { case?: unknown }).case;
  if (inner && typeof inner === "object" && "case_id" in inner) return inner as CaseLike;
  if ("case_id" in x) return x as CaseLike;
  return null;
}

export async function listCases(): Promise<InternalCase[]> {
  if (!IS_LIVE) return mockList();
  return unwrapList(await http<unknown>("/cases")).map(normalizeCase);
}

export async function getCase(id: string): Promise<InternalCase> {
  if (!IS_LIVE) return mockGet(id);
  const c = unwrapCase(await http<unknown>(`/cases/${encodeURIComponent(id)}`));
  if (!c) throw new Error(`GET /cases/${id} returned no case`);
  return normalizeCase(c);
}

export async function approveAction(id: string, req: ApprovalRequest): Promise<InternalCase> {
  if (!IS_LIVE) return mockApprove(id, req);
  const res = await http<unknown>(`/cases/${encodeURIComponent(id)}/approve`, { method: "POST", body: JSON.stringify(req) });
  const c = unwrapCase(res);
  return c ? normalizeCase(c) : getCase(id);
}

export async function createCase(req: NewCaseRequest): Promise<string> {
  if (!IS_LIVE) return mockCreate(req);
  const res = await http<{ case_id?: string; id?: string }>("/cases", { method: "POST", body: JSON.stringify(req) });
  const id = res.case_id ?? res.id;
  if (!id) throw new Error("POST /cases returned no case_id");
  return id;
}

// ---- running a case ------------------------------------------------------------

export interface RunHandlers {
  /** Called with the whole case each time it changes, so the view is a pure function of it. */
  onCase: (c: InternalCase) => void;
  onState: (s: RunState, detail?: string) => void;
}

/** Starts an investigation and streams it. Returns a cancel function. */
export function runCase(id: string, start: InternalCase, h: RunHandlers): () => void {
  return IS_LIVE ? runLive(id, start, h) : runMock(id, start, h);
}

function isStep(x: unknown): x is AgentStep {
  return !!x && typeof x === "object" && "index" in x && "node" in x;
}

function runLive(id: string, start: InternalCase, h: RunHandlers): () => void {
  const ctrl = new AbortController();
  let current: InternalCase = blankCase(start.case_id, start.trigger);
  let finished = false;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;

  const push = (c: InternalCase) => {
    current = c;
    h.onCase(c);
  };
  const finish = () => {
    if (finished) return;
    finished = true;
    h.onState("done");
  };

  const handle = (event: string, raw: string) => {
    let data: unknown = raw;
    try {
      data = JSON.parse(raw);
    } catch {
      /* plain text payloads are allowed, e.g. a keepalive */
    }
    if (event === "error") {
      const msg = (data as { message?: string })?.message ?? String(raw);
      h.onState("error", msg);
      return;
    }
    if (event === "step" || isStep(data)) {
      if (isStep(data)) {
        const steps = [...current.steps.filter((s) => s.index !== data.index), data].sort((a, b) => a.index - b.index);
        push({ ...current, steps, tool_calls: Math.max(current.tool_calls, steps.filter((s) => s.tool).length) });
      }
    } else {
      const c = unwrapCase(data);
      if (c) {
        // Milestone snapshots are taken mid-node, so they can lag the step events that
        // arrived just before them. Keep whichever step list and counter is further along.
        const merged = mergeCase(current, c);
        if ((c.steps?.length ?? 0) < current.steps.length) merged.steps = current.steps;
        merged.tool_calls = Math.max(merged.tool_calls, merged.steps.filter((s) => s.tool).length);
        push(merged);
      }
    }
    if (event === "done" || event === "end" || event === "complete") finish();
  };

  // Polling is the fallback named in PLAN.md section 11. It stops once the agent has
  // written a stop_reason, which the backend sets as the last thing a run does.
  const poll = async (started: number) => {
    if (ctrl.signal.aborted || finished) return;
    try {
      const c = await getCase(id);
      push(c);
      if (c.stop_reason) return finish();
    } catch (e) {
      h.onState("error", (e as Error).message);
      return;
    }
    if (Date.now() - started > 10 * 60 * 1000) return h.onState("error", "Polling timed out after 10 minutes");
    pollTimer = setTimeout(() => poll(started), POLL_MS);
  };
  const fallBackToPolling = (why: string) => {
    if (ctrl.signal.aborted || finished) return;
    h.onState("polling", why);
    poll(Date.now());
  };

  (async () => {
    h.onState("connecting");
    h.onCase(current);
    let res: Response;
    try {
      res = await fetch(`${API_URL}/cases/${encodeURIComponent(id)}/run`, {
        method: "POST",
        headers: { Accept: "text/event-stream" },
        signal: ctrl.signal,
      });
    } catch (e) {
      if (ctrl.signal.aborted) return;
      return fallBackToPolling(`Stream request failed (${(e as Error).message}); polling instead`);
    }
    if (!res.ok) return fallBackToPolling(`POST /run returned ${res.status}; polling instead`);
    const type = res.headers.get("content-type") ?? "";
    if (!type.includes("text/event-stream") || !res.body) {
      // The backend accepted the run but did not stream. Poll for progress.
      return fallBackToPolling("Backend did not stream; polling");
    }
    h.onState("streaming");
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let cut: number;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          let event = "message";
          const lines: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith(":")) continue;
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) lines.push(line.slice(5).replace(/^ /, ""));
          }
          if (lines.length) handle(event, lines.join("\n"));
        }
      }
    } catch (e) {
      if (ctrl.signal.aborted) return;
      return fallBackToPolling(`Event stream disconnected (${(e as Error).message}); polling`);
    }
    // The stream closed. Take the stored case as the final truth, since SSE may have
    // carried only steps and not every field.
    if (!ctrl.signal.aborted) {
      try {
        const c = await getCase(id);
        push(c);
        if (c.stop_reason || finished) finish();
        else fallBackToPolling("Stream ended before the run finished; polling");
      } catch {
        finish();
      }
    }
  })();

  return () => {
    ctrl.abort();
    if (pollTimer) clearTimeout(pollTimer);
  };
}

// ---- mock replay ---------------------------------------------------------------

/**
 * Rebuilds what the case would look like after step k of a recorded run, so the mock
 * stream reveals fields in the same order the agent produces them. Keyed on node names
 * from backend/agent/graph.py (trigger, open_case, plan, gather_evidence, assess,
 * nba_initial, policy_check, request_evidence, reassess, nba_final, execute_or_route,
 * sar_check, explain, write_case, update_memory, answer_file). Older recordings used
 * gather, retrieve, decide_initial, resolve_evidence, decide_final and sar, so both work.
 */
export function partialAt(full: InternalCase, k: number): InternalCase {
  const done = full.steps.slice(0, k);
  const has = (...nodes: string[]) => done.some((s) => nodes.includes(s.node));
  const isGather = (s: AgentStep) => s.node === "gather_evidence" || s.node === "gather" || s.node === "retrieve";
  const hasTool = (tool: string) => done.some((s) => s.tool === tool);
  const c = blankCase(full.case_id, full.trigger);
  c.steps = done;
  c.tool_calls = done.filter((s) => s.tool).length;
  c.tokens = Math.round((full.tokens * k) / Math.max(1, full.steps.length));
  c.latency_s = +done.reduce((a, s) => a + s.duration_s + 0.3, 0).toFixed(1);

  const gatherSteps = full.steps.filter(isGather).length;
  const gatherDone = done.filter(isGather).length;
  const graphEv = full.evidence.filter((e) => e.source === "graph" || e.source === "external");
  c.evidence = graphEv.slice(0, Math.ceil((graphEv.length * gatherDone) / Math.max(1, gatherSteps)));
  if (hasTool("policy_lookup")) c.evidence.push(...full.evidence.filter((e) => e.source === "document"));
  if (hasTool("shared_device_profile") || hasTool("ring_detect")) c.connected_device_profiles = full.connected_device_profiles;
  if (hasTool("ring_detect")) c.connected_card_ids = full.connected_card_ids;
  if (hasTool("similar_cases")) {
    c.similar_prior_cases = full.similar_prior_cases;
    c.retrieved_chunks = full.retrieved_chunks.filter((r) => r.kind !== "policy");
  }
  if (hasTool("policy_lookup")) c.retrieved_chunks = full.retrieved_chunks;
  if (has("assess")) {
    c.risk_assessment = full.risk_assessment;
    c.hypotheses = full.hypotheses;
    c.affected_txn_ids = full.affected_txn_ids;
    c.first_suspicious_txn_id = full.first_suspicious_txn_id;
    c.exposure_usd = full.exposure_usd;
    c.pattern = full.pattern;
    c.pattern_description = full.pattern_description;
    c.connected_card_ids = full.connected_card_ids;
    c.connected_device_profiles = full.connected_device_profiles;
  }
  if (has("nba_initial", "decide_initial")) {
    c.initial_actions = full.initial_actions;
    c.decisions = full.decisions.filter((d) => d.phase === "initial").map((d) => ({ ...d, approval_status: d.route === "auto" ? d.approval_status : "pending" }));
  }
  if (has("request_evidence")) c.evidence_requests = full.evidence_requests.map((r) => ({ ...r, resolved_as: null }));
  if (has("reassess", "resolve_evidence")) {
    c.evidence_requests = full.evidence_requests;
    c.customer_response = full.customer_response;
    c.evidence.push(...full.evidence.filter((e) => e.source === "customer"));
  }
  if (has("nba_final", "decide_final")) {
    c.final_actions = full.final_actions;
    c.what_changed = full.what_changed;
    c.decisions = awaitingHuman(full).decisions;
    c.verdict = full.verdict;
  }
  if (has("sar_check", "sar")) c.sar = full.sar;
  if (k >= full.steps.length) return awaitingHuman(full);
  return c;
}

/**
 * A replay ends where a real run ends: the agent has recommended L1/L2 actions but no
 * human has acted, so they are pending and the case cannot be closed yet.
 */
function awaitingHuman(full: InternalCase): InternalCase {
  const needsHuman = full.final_actions.some((a) => a.route !== "auto");
  if (!needsHuman) return { ...full };
  const decisions = full.decisions.map((d) => (d.phase !== "initial" && d.route !== "auto" ? { ...d, approval_status: "pending", authorized: false, executed: false } : d));
  const status = full.status === "escalated" ? "escalated" : "open";
  const status_history = full.status_history.filter((h) => !h.status.startsWith("closed"));
  return { ...full, decisions, status, status_history };
}

function runMock(id: string, _start: InternalCase, h: RunHandlers): () => void {
  let cancelled = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  (async () => {
    h.onState("connecting");
    let full: InternalCase;
    try {
      full = await mockResult(id);
    } catch (e) {
      h.onState("error", (e as Error).message);
      return;
    }
    if (cancelled) return;
    h.onState("streaming");
    h.onCase(partialAt(full, 0));
    // Pace each step by its recorded duration, clamped, so a replay feels like a run
    // without making the demo wait out real tool latency.
    let t = 450;
    full.steps.forEach((s, i) => {
      t += Math.min(1100, Math.max(350, s.duration_s * 700));
      timers.push(
        setTimeout(async () => {
          if (cancelled) return;
          const c = partialAt(full, i + 1);
          h.onCase(c);
          if (i + 1 === full.steps.length) {
            await mockSave(c);
            h.onState("done");
          }
        }, t),
      );
    });
  })();
  return () => {
    cancelled = true;
    timers.forEach(clearTimeout);
  };
}
