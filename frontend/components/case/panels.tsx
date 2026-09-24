"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { IS_LIVE } from "@/lib/api";
import { approvalItems, fmtPct, fmtUsd, riskBand } from "@/lib/case-utils";
import type { Action, InternalCase, RecommendedAction, Route, RunState } from "@/lib/types";
import { Bar, Button, Chip, OutcomeChip, Panel, type PanelState, RouteBadge, SourceTag, Waiting, cx } from "@/components/ui";
import { Modal } from "./modal";

const live = (r: RunState) => r === "streaming" || r === "polling" || r === "connecting";

// ---- Agent steps -----------------------------------------------------------------

export function StepTimeline({ data, run, className }: { data: InternalCase; run: RunState; className?: string }) {
  const end = useRef<HTMLLIElement>(null);
  const streaming = live(run);
  useEffect(() => {
    if (streaming) end.current?.scrollIntoView({ block: "nearest" });
  }, [data.steps.length, streaming]);
  const state: PanelState = streaming ? "running" : data.steps.length ? "ok" : "waiting";
  return (
    <Panel title="Agent steps" state={state} className={className} meta={<span className="font-mono text-[10px] text-text-faint tabular">{data.steps.length}</span>}>
      {!data.steps.length && !streaming && <Waiting>Not run yet. Start the investigation to stream steps.</Waiting>}
      <ol className="font-mono text-[11px]" aria-live="polite">
        {data.steps.map((s) => (
          <li key={s.index} className="row-dotted grid grid-cols-[22px_minmax(0,1fr)_38px] gap-x-2 py-1.5">
            <span className="text-text-faint tabular">{String(s.index).padStart(2, "0")}</span>
            <span className="min-w-0">
              <span className={cx(s.ok ? "text-text" : "text-flame-400")}>{s.tool || s.node}</span>
              {s.tool && <span className="text-text-faint"> {s.node}</span>}
              {s.summary && <span className="block text-text-muted font-sans text-[11.5px] leading-snug">{s.summary}</span>}
            </span>
            <span className="text-right text-text-faint tabular">{s.duration_s ? `${s.duration_s.toFixed(1)}s` : ""}</span>
          </li>
        ))}
        {streaming && (
          <li ref={end} className="row-dotted grid grid-cols-[22px_1fr] gap-x-2 py-1.5">
            <span className="text-flame-500 tabular pulse">{String(data.steps.length + 1).padStart(2, "0")}</span>
            <span className="eyebrow pulse text-flame-500!">{run === "polling" ? "polling for next step" : run === "connecting" ? "connecting" : "running"}</span>
          </li>
        )}
      </ol>
    </Panel>
  );
}

// ---- Risk, confidence, facts, unknowns ---------------------------------------------

export function AssessmentPanel({ data, run, className }: { data: InternalCase; run: RunState; className?: string }) {
  const ra = data.risk_assessment;
  const assessed = data.steps.some((s) => s.node === "assess") || ra.fraud_probability > 0 || ra.confidence > 0;
  const band = riskBand(ra.fraud_probability);
  const state: PanelState = assessed ? "ok" : "waiting";
  return (
    <Panel title="Risk / confidence" state={state} className={className}>
      {!assessed ? (
        <Waiting>Waiting for the assess step.</Waiting>
      ) : (
        <>
          <div className="grid grid-cols-2 border border-rust-800 rounded-xs">
            <Gauge label="Fraud probability" value={ra.fraud_probability} tone={band === "high" ? "flame" : band === "med" ? "rust" : "sand"} caption={`${band} risk`} lead />
            <Gauge label="Confidence" value={ra.confidence} tone="rust" caption={`coverage ${fmtPct(ra.evidence_coverage)} / agreement ${fmtPct(ra.signal_agreement)}`} />
          </div>
          {ra.confidence < 0.6 && ra.fraud_probability >= 0.3 && (
            <p className="mt-2 text-[11.5px] text-rust-300">Confidence below 0.60: the agent asks for evidence before acting.</p>
          )}

          <dl className="mt-3 grid grid-cols-[92px_minmax(0,1fr)] gap-x-2 gap-y-1 text-[12px]">
            <Kv k="verdict" v={<span className={data.verdict === "fraud" ? "text-flame-500" : data.verdict === "legitimate" ? "text-text" : "text-rust-300"}>{data.verdict}</span>} />
            <Kv k="pattern" v={<span className="font-mono text-[11.5px]">{data.pattern}</span>} />
            <Kv k="exposure" v={<span className="font-mono tabular">{fmtUsd(data.exposure_usd)}</span>} />
            <Kv k="txns" v={<span className="font-mono text-[11.5px]">{data.affected_txn_ids.length}{data.first_suspicious_txn_id ? ` / first ${data.first_suspicious_txn_id}` : ""}</span>} />
            <Kv k="graph case" v={<span className="font-mono text-[11.5px]">{data.written_to_graph ? data.graph_case_id : "not written yet"}</span>} />
          </dl>
          {data.pattern_description && <p className="mt-2 text-[11.5px] text-text-muted">{data.pattern_description}</p>}

          {ra.components.length > 0 && (
            <>
              <h3 className="eyebrow mt-3 mb-1">Blend</h3>
              <table className="w-full font-mono text-[10.5px]">
                <tbody>
                  {ra.components.map((c) => (
                    <tr key={c.name} title={c.detail} className="row-dotted">
                      <td className={cx("py-0.5 pr-2", c.observed ? "text-text-muted" : "text-text-faint line-through")}>{c.name}</td>
                      <td className="py-0.5">
                        <Bar value={c.value} tone="rust" width={36} />
                      </td>
                      <td className="py-0.5 text-right text-text-faint tabular">x{c.weight.toFixed(2)}</td>
                      <td className="py-0.5 text-right text-text tabular">{c.contribution.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h3 className="eyebrow mt-3 mb-1">Unknowns <span className="text-text-faint">[{ra.unknowns.length}]</span></h3>
          {ra.unknowns.length ? (
            <ul className="border-l-2 border-rust-600 pl-2 space-y-1 text-[12px] text-text-muted">
              {ra.unknowns.map((u) => (
                <li key={u}>{u}</li>
              ))}
            </ul>
          ) : (
            <p className="text-text-faint text-[11.5px]">None recorded.</p>
          )}
        </>
      )}
    </Panel>
  );
}

function Gauge({ label, value, tone, caption, lead }: { label: string; value: number; tone: "flame" | "rust" | "sand"; caption: string; lead?: boolean }) {
  const txt = tone === "flame" ? "text-flame-500" : tone === "rust" ? (lead ? "text-rust-300" : "text-text") : "text-risk-low";
  return (
    <div className="px-3 py-2 [&+&]:border-l [&+&]:border-rust-800">
      <p className="eyebrow">{label}</p>
      <p className={cx("font-display text-[26px] leading-tight tabular", txt)}>{fmtPct(value)}</p>
      <Bar value={value} tone={tone} width={120} />
      <p className="mt-1 font-mono text-[9.5px] text-text-faint">{caption}</p>
    </div>
  );
}

function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt className="eyebrow pt-0.5">{k}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </>
  );
}

// ---- Evidence --------------------------------------------------------------------

export function EvidencePanel({ data, run, selected, onSelect, className }: { data: InternalCase; run: RunState; selected: number | null; onSelect: (i: number | null) => void; className?: string }) {
  const state: PanelState = data.evidence.length ? "ok" : "waiting";
  return (
    <Panel title="Evidence" state={state} className={className} meta={<span className="font-mono text-[10px] text-text-faint tabular">{data.evidence.length}</span>}>
      {!data.evidence.length && <Waiting>No evidence gathered yet.</Waiting>}
      <ul>
        {data.evidence.map((e, i) => {
          const on = selected === i;
          return (
            <li key={`${e.ref}-${i}`} className="row-dotted">
              <button
                onClick={() => onSelect(on ? null : i)}
                aria-pressed={on}
                className={cx("w-full text-left grid grid-cols-[44px_minmax(0,1fr)] gap-x-2 py-1.5 px-1 border-l-2", on ? "border-l-flame-500 bg-ember-900" : "border-l-transparent hover:bg-ember-900")}
              >
                <SourceTag source={e.source} />
                <span className="min-w-0">
                  <span className="block text-[12px] text-text leading-snug">{e.claim}</span>
                  <span className="block font-mono text-[10px] text-text-faint truncate" title={e.ref}>
                    ref: {e.ref}
                    {e.entity_ids.length > 0 && ` / ${e.entity_ids.length} entit${e.entity_ids.length === 1 ? "y" : "ies"}`}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {data.evidence_requests.length > 0 && (
        <>
          <h3 className="eyebrow mt-3 mb-1">Evidence requests</h3>
          <ul className="space-y-1.5">
            {data.evidence_requests.map((r, i) => (
              <li key={i} className="border border-rust-800 rounded-xs px-2 py-1.5 text-[11.5px]">
                <div className="flex items-center gap-2 font-mono text-[10.5px]">
                  <span className="text-text">{r.type}</span>
                  <span className="text-text-faint">after step {r.asked_after_step}</span>
                  <span className="ml-auto">
                    {r.resolved_as ? (
                      <Chip className={r.resolved_as === "confirmed" ? "border-rust-800 text-text-muted" : r.resolved_as === "denied" ? "border-rust-600 text-rust-300" : "border-rust-800 text-text-muted"}>{r.resolved_as}</Chip>
                    ) : (
                      <Chip className="border-flame-600 text-flame-400">awaiting</Chip>
                    )}
                  </span>
                </div>
                {r.resolved_as && (
                  <p className="text-text-muted mt-0.5">
                    {r.assumed_response} <span className="text-text-faint">(simulated response: the dataset supplies none)</span>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

// ---- Similar prior cases ----------------------------------------------------------

export function PriorCases({ data, run, className }: { data: InternalCase; run: RunState; className?: string }) {
  const state: PanelState = data.similar_prior_cases.length ? "ok" : "waiting";
  return (
    <Panel title="Similar prior cases" state={state} className={className} meta={<span className="font-mono text-[10px] text-text-faint tabular">{data.similar_prior_cases.length}</span>}>
      {!data.similar_prior_cases.length && <Waiting>Waiting for retrieval.</Waiting>}
      <ul>
        {data.similar_prior_cases.map((id, i) => {
          const chunk = data.retrieved_chunks.find((r) => r.ref === id);
          return (
            <li key={id} className={cx("row-dotted py-1.5 pl-1.5 border-l-2", i === 0 ? "border-l-rust-600" : "border-l-transparent")}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11.5px] text-text">{id}</span>
                {chunk?.outcome && <OutcomeChip outcome={chunk.outcome} />}
                {chunk && <span className="ml-auto font-mono text-[10.5px] text-text-faint tabular">{chunk.score.toFixed(2)}</span>}
              </div>
              {chunk ? (
                <p className="text-[11.5px] text-text-muted leading-snug">
                  {chunk.pattern && <span className="font-mono text-[10.5px] text-text-faint">{chunk.pattern}{chunk.exposure_usd ? ` / ${fmtUsd(chunk.exposure_usd)}` : ""}. </span>}
                  {chunk.text}
                </p>
              ) : (
                <p className="text-[11px] text-text-faint">Outcome not returned by the backend.</p>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

// ---- Next best actions, before vs after -------------------------------------------

type DiffMark = "+" | "-" | "=" | "~";

export function ActionDiff({ data, run, className }: { data: InternalCase; run: RunState; className?: string }) {
  const rows = useMemo(() => {
    const init = new Map(data.initial_actions.map((a) => [a.action, a]));
    const fin = new Map(data.final_actions.map((a) => [a.action, a]));
    const order: Action[] = [...new Set([...data.initial_actions.map((a) => a.action), ...data.final_actions.map((a) => a.action)])];
    return order.map((action) => {
      const b = init.get(action);
      const a = fin.get(action);
      const mark: DiffMark = !b ? "+" : !a ? "-" : a.route !== b.route ? "~" : "=";
      return { action, before: b, after: a, mark };
    });
  }, [data.initial_actions, data.final_actions]);
  const hasFinal = data.final_actions.length > 0;
  const state: PanelState = hasFinal ? "ok" : "waiting";
  const resp = data.customer_response;
  return (
    <Panel
      title="Next best actions: before vs after evidence"
      state={state}
      className={className}
      meta={resp && <Chip className={resp === "confirmed" ? "border-rust-800 text-text-muted" : "border-rust-600 text-rust-300"}>{`customer: ${resp}`}</Chip>}
    >
      {!data.initial_actions.length ? (
        <Waiting>Waiting for the first decision.</Waiting>
      ) : (
        <>
          <table className="w-full text-[11.5px]">
            <thead>
              <tr className="border-b border-rust-800">
                <th scope="col" className="eyebrow text-left py-1 font-medium w-[18px]" aria-label="change" />
                <th scope="col" className="eyebrow text-left py-1 font-medium">Action</th>
                <th scope="col" className="eyebrow text-left py-1 font-medium">Before evidence</th>
                <th scope="col" className="eyebrow text-left py-1 font-medium">After evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.action} className="row-dotted align-top">
                  <td className={cx("py-1 font-mono font-bold", r.mark === "+" ? "text-flame-500" : r.mark === "-" ? "text-text-faint" : r.mark === "~" ? "text-rust-300" : "text-text-faint")} title={{ "+": "added", "-": "dropped", "=": "unchanged", "~": "route changed" }[r.mark]}>
                    {r.mark}
                  </td>
                  <td className={cx("py-1 pr-2 font-mono text-[11px]", r.mark === "-" ? "text-text-faint line-through" : "text-text")}>{r.action}</td>
                  <ActionCell a={r.before} />
                  <ActionCell a={r.after} pending={!hasFinal} dropped={hasFinal && !r.after} />
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 border-l-2 border-rust-600 pl-2">
            <p className="eyebrow">What changed</p>
            <p className="text-[12px] text-text-muted">{hasFinal ? data.what_changed : "Waiting for evidence."}</p>
          </div>
        </>
      )}
    </Panel>
  );
}

function ActionCell({ a, pending, dropped }: { a?: RecommendedAction; pending?: boolean; dropped?: boolean }) {
  if (!a) return <td className="py-1 pr-2 font-mono text-[10.5px] text-text-faint">{pending ? "..." : dropped ? "dropped" : "-"}</td>;
  return (
    <td className="py-1 pr-2">
      <div className="flex items-start gap-1.5">
        <RouteBadge route={a.route} />
        <span className="text-[11px] text-text-muted leading-snug line-clamp-2" title={a.reason}>
          {a.reason}
        </span>
      </div>
    </td>
  );
}

// ---- Approvals -------------------------------------------------------------------

// From backend/contracts/policy.yaml routing, so the card can say why the action needs a human.
function routingRule(action: Action, route: Route, exposure: number): string {
  if (action === "BLOCK_CARD") return exposure > 2500 ? `exposure ${fmtUsd(exposure)} > $2,500, so L2` : `exposure ${fmtUsd(exposure)} <= $2,500, so L1`;
  if (action === "DECLINE_TRANSACTION") return "policy routes DECLINE_TRANSACTION to L1";
  if (action === "FILE_REPORT" || action === "BLOCK_ALL_CARDS") return `policy routes ${action} to L2`;
  return `route ${route}`;
}

export function ApprovalPanel({ data, onDecide, className }: { data: InternalCase; onDecide: (a: Action, approved: boolean, note: string) => Promise<unknown>; className?: string }) {
  const items = approvalItems(data);
  const pending = items.filter((x) => !x.decision || x.decision.approval_status === "pending");
  const [confirm, setConfirm] = useState<{ action: RecommendedAction; approved: boolean } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const state: PanelState = pending.length ? "alert" : items.length ? "ok" : "idle";

  const submit = async () => {
    if (!confirm) return;
    setBusy(true);
    setErr(null);
    try {
      await onDecide(confirm.action.action, confirm.approved, note);
      setConfirm(null);
      setNote("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Approvals" state={state} className={className} meta={pending.length > 0 && <span className="font-mono text-[10px] text-flame-500">{pending.length} pending</span>}>
      {!data.final_actions.length ? (
        <Waiting>Nothing to approve until the agent decides.</Waiting>
      ) : !items.length ? (
        <p className="text-text-muted text-[12px]">Every final action is auto. The agent may execute them without approval.</p>
      ) : (
        <ul className="space-y-2">
          {items.map(({ action: a, decision: d }) => {
            const status = d?.approval_status ?? "pending";
            const open = status === "pending";
            return (
              <li key={a.action} className={cx("border rounded-xs px-2.5 py-2", open ? "border-rust-600 bg-ember-950" : "border-rust-800")}>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11.5px] text-text">{a.action}</span>
                  <RouteBadge route={a.route} />
                  {!open && <Chip className={status === "approved" ? "ml-auto border-rust-600 text-rust-300" : "ml-auto border-rust-800 text-text-faint"}>{status}</Chip>}
                </div>
                <p className="font-mono text-[10px] text-text-faint mt-0.5">{routingRule(a.action, a.route, data.exposure_usd)}</p>
                <p className="text-[11.5px] text-text-muted mt-1 leading-snug">{a.reason}</p>
                {open && (
                  <div className="mt-2 flex gap-2">
                    <Button variant="primary" size="sm" onClick={() => setConfirm({ action: a, approved: true })}>
                      Approve
                    </Button>
                    <Button variant="reject" size="sm" onClick={() => setConfirm({ action: a, approved: false })}>
                      Reject
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {data.status_history.length > 1 && (
        <>
          <h3 className="eyebrow mt-3 mb-1">Case log</h3>
          <ol className="font-mono text-[10.5px] space-y-0.5">
            {data.status_history.slice(-4).map((h, i) => (
              <li key={`${h.at}-${i}`} className="text-text-muted">
                <span className="text-text-faint">{h.at.slice(5, 16).replace("T", " ")}</span> {h.status}
                {h.note ? ` . ${h.note}` : ""}
              </li>
            ))}
          </ol>
        </>
      )}
      <Modal
        open={!!confirm}
        title={confirm ? `${confirm.approved ? "Approve" : "Reject"} ${confirm.action.action}` : ""}
        onClose={() => !busy && setConfirm(null)}
        footer={
          <>
            <Button size="sm" onClick={() => setConfirm(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant={confirm?.approved ? "primary" : "reject"} size="sm" onClick={submit} disabled={busy}>
              {busy ? "Sending" : confirm?.approved ? "Confirm approve" : "Confirm reject"}
            </Button>
          </>
        }
      >
        {confirm && (
          <div className="space-y-3 text-[12.5px]">
            <p className="text-text-muted">
              {confirm.action.action} at <span className="font-mono">{confirm.action.route}</span> on {data.trigger.card_id}. {confirm.action.reason}
            </p>
            <label className="block">
              <span className="eyebrow block mb-1">Note (optional)</span>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} className="w-full bg-ember-900 border border-rust-500 rounded-xs px-2 py-1.5 font-mono text-[12px] text-text" />
            </label>
            <p className="font-mono text-[10.5px] text-text-faint">
              {IS_LIVE ? `Sends POST /cases/${data.case_id}/approve. The agent records the outcome in its decision log.` : "Mock mode: the outcome is recorded in this browser session only."}
            </p>
            {err && <p className="text-flame-400 font-mono text-[11px]" role="alert">{err}</p>}
          </div>
        )}
      </Modal>
    </Panel>
  );
}

// ---- SAR -------------------------------------------------------------------------

export function SarPanel({ data, run, className }: { data: InternalCase; run: RunState; className?: string }) {
  const [open, setOpen] = useState(false);
  const sar = data.sar;
  const sentences = sar?.narrative ? sar.narrative.split(/(?<=[.!?])\s+(?=[A-Z0-9])/).filter(Boolean).length : 0;
  const state: PanelState = sar ? (sar.file ? "alert" : "ok") : "waiting";
  return (
    <Panel title="SAR" state={state} className={className} meta={sar && <Chip className={sar.file ? "border-flame-600 text-flame-400" : "border-rust-800 text-text-faint"}>{sar.file ? "required" : "not filed"}</Chip>}>
      {!sar ? (
        <Waiting>Waiting for the SAR decision.</Waiting>
      ) : !sar.file ? (
        <div className="text-[12px]">
          <p className="eyebrow mb-1">Why no SAR</p>
          <p className="text-text-muted">{sar.reason}</p>
        </div>
      ) : (
        <div className="text-[12px] space-y-2">
          <p className="text-text-muted">{sar.reason}</p>
          <dl className="grid grid-cols-[70px_minmax(0,1fr)] gap-x-2 gap-y-1">
            <Kv k="amount" v={<span className="font-mono tabular">{fmtUsd(sar.total_amount_usd)}</span>} />
            <Kv k="dates" v={<span className="font-mono text-[11.5px]">{sar.activity_dates.join(" to ") || "-"}</span>} />
            <Kv k="subjects" v={<span className="font-mono text-[10.5px] text-text-muted">{sar.subjects.join(", ")}</span>} />
          </dl>
          <p className="text-text-muted line-clamp-3">{sar.narrative}</p>
          <Button size="sm" onClick={() => setOpen(true)}>
            View full SAR
          </Button>
          <Modal open={open} title={`SAR preview / ${data.case_id}`} onClose={() => setOpen(false)}>
            <div className="max-w-[72ch]">
              <p className="font-mono text-[10.5px] mb-2">
                <span className={sentences >= 6 && sentences <= 12 ? "text-text-faint" : "text-flame-400"}>{sentences} sentences</span>
                <span className="text-text-faint"> / policy range 6 to 12</span>
              </p>
              <p className="text-[14px] leading-relaxed text-text">{sar.narrative}</p>
              <dl className="mt-4 grid grid-cols-[90px_minmax(0,1fr)] gap-x-2 gap-y-1 text-[12px]">
                <Kv k="reason" v={sar.reason} />
                <Kv k="amount" v={<span className="font-mono">{fmtUsd(sar.total_amount_usd)}</span>} />
                <Kv k="dates" v={<span className="font-mono">{sar.activity_dates.join(" to ")}</span>} />
                <Kv k="subjects" v={<span className="font-mono text-[11px]">{sar.subjects.join(", ")}</span>} />
              </dl>
            </div>
          </Modal>
        </div>
      )}
    </Panel>
  );
}
