"use client";

import { use, useEffect, useState } from "react";
import { getCase } from "@/lib/api";
import type { InternalCase, RecommendedAction } from "@/lib/types";

// I render the findings as a plain printable document and let the browser's
// "Save as PDF" do the export. A PDF library would add weight and a build risk
// for something the print engine already does well, and the output stays text
// (searchable, selectable) rather than a picture of the dashboard.

const pct = (n: number | undefined | null) => (n == null ? "n/a" : `${Math.round(n * 100)}%`);
const usd = (n: number | undefined | null) => (n == null ? "n/a" : `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

function Actions({ items }: { items: RecommendedAction[] }) {
  if (!items?.length) return <p className="r-muted">None.</p>;
  return (
    <table className="r-table">
      <thead><tr><th>Action</th><th>Approval route</th><th>Reason</th></tr></thead>
      <tbody>
        {items.map((a, i) => (
          <tr key={i}><td className="r-mono">{a.action}</td><td className="r-mono">{a.route}</td><td>{a.reason}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export default function CaseReport({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const caseId = decodeURIComponent(id);
  const [c, setC] = useState<InternalCase | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    getCase(caseId).then(setC).catch((e) => setErr(String(e?.message ?? e)));
  }, [caseId]);

  if (err) return <div className="p-8 font-mono text-sm text-flame-400">Could not load {caseId}: {err}</div>;
  if (!c) return <div className="p-8 font-mono text-sm text-text-muted">Loading report for {caseId}...</div>;

  const ra = c.risk_assessment;
  const priors = (c.retrieved_chunks ?? []).filter((r) => r.kind === "prior_case");
  const pending = (c.decisions ?? []).filter((d) => d.approval_status === "pending");
  const request = c.evidence_requests?.[0];

  return (
    <div className="report-wrap">
      <div className="no-print report-bar">
        <span>Findings report for {c.case_id}. Use the button, then choose "Save as PDF".</span>
        <button onClick={() => window.print()}>Download PDF</button>
      </div>

      <article id="report">
        <header className="r-head">
          <div>
            <p className="r-eyebrow">HHGOA fraud desk / investigation findings</p>
            <h1>{c.case_id}</h1>
            <p className="r-muted">Opened {c.trigger.opened_at.replace("T", " ")} / trigger: {c.trigger.type.replace(/_/g, " ")} / graph record {c.graph_case_id || "not written"}</p>
          </div>
          <div className={`r-verdict r-${c.verdict}`}>{c.verdict}</div>
        </header>

        <section>
          <h2>1. Finding</h2>
          <table className="r-kv">
            <tbody>
              <tr><th>Verdict</th><td>{c.verdict}</td></tr>
              <tr><th>Fraud probability</th><td>{pct(ra?.fraud_probability)}</td></tr>
              <tr><th>Confidence</th><td>{pct(ra?.confidence)}</td></tr>
              <tr><th>Pattern</th><td className="r-mono">{c.pattern}</td></tr>
              <tr><th>Exposure</th><td>{usd(c.exposure_usd)}</td></tr>
              <tr><th>Card / customer</th><td className="r-mono">{c.trigger.card_id} / {c.trigger.customer_id}</td></tr>
              <tr><th>Flagged transaction</th><td className="r-mono">{c.trigger.flagged_txn_id}{c.trigger.risk_score != null ? ` (bank score ${c.trigger.risk_score})` : ""}</td></tr>
              <tr><th>Affected transactions</th><td className="r-mono">{c.affected_txn_ids?.length ? c.affected_txn_ids.join(", ") : "none"}</td></tr>
            </tbody>
          </table>
          <p><strong>Trigger:</strong> {c.trigger.trigger_text}</p>
          <p><strong>Summary:</strong> {c.summary}</p>
          {c.pattern_description && <p><strong>Pattern found:</strong> {c.pattern_description}</p>}
          <p><strong>Why the agent stopped:</strong> {c.stop_reason}</p>
        </section>

        <section>
          <h2>2. Evidence from the graph</h2>
          {c.evidence?.length ? (
            <table className="r-table">
              <thead><tr><th>#</th><th>Finding</th><th>Source</th></tr></thead>
              <tbody>
                {c.evidence.map((e, i) => (
                  <tr key={i}><td>{i + 1}</td><td>{e.claim}</td><td className="r-mono">{e.source}{e.ref ? ` / ${e.ref}` : ""}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <p className="r-muted">No evidence recorded.</p>}
        </section>

        <section>
          <h2>3. Risk, confidence and unknowns</h2>
          {ra?.components?.length ? (
            <table className="r-table">
              <thead><tr><th>Signal</th><th>Value</th><th>Weight</th><th>Detail</th></tr></thead>
              <tbody>
                {ra.components.map((s, i) => (
                  <tr key={i}><td className="r-mono">{s.name}</td><td>{s.value?.toFixed?.(2) ?? s.value}</td><td>{s.weight}</td><td>{s.detail}</td></tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <p><strong>Still unknown:</strong></p>
          {ra?.unknowns?.length ? <ul>{ra.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul> : <p className="r-muted">Nothing recorded.</p>}
        </section>

        <section>
          <h2>4. Similar prior cases</h2>
          {priors.length ? (
            <table className="r-table">
              <thead><tr><th>Case</th><th>Outcome</th><th>Pattern</th><th>Similarity</th></tr></thead>
              <tbody>
                {priors.map((p, i) => (
                  <tr key={i}><td className="r-mono">{p.ref.replace(/^case:/, "")}</td><td>{p.outcome ?? "n/a"}</td><td className="r-mono">{p.pattern ?? "n/a"}</td><td>{p.score?.toFixed(2)}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <p className="r-muted">{c.similar_prior_cases?.length ? c.similar_prior_cases.join(", ") : "None found."}</p>}
        </section>

        <section>
          <h2>5. Next best actions</h2>
          <h3>Before additional evidence</h3>
          <Actions items={c.initial_actions} />
          {request && (
            <p><strong>Evidence requested ({request.type.replace(/_/g, " ")}):</strong> {request.assumed_response}</p>
          )}
          <h3>After additional evidence</h3>
          <Actions items={c.final_actions} />
          {c.what_changed && <p><strong>What changed:</strong> {c.what_changed}</p>}
          {pending.length > 0 && (
            <p><strong>Awaiting human approval:</strong> {pending.map((d) => `${d.action} (${d.route})`).join(", ")}</p>
          )}
        </section>

        <section>
          <h2>6. Suspicious activity report</h2>
          {c.sar?.file ? (
            <>
              <p><strong>Filed because:</strong> {c.sar.reason}</p>
              <p><strong>Subjects:</strong> <span className="r-mono">{c.sar.subjects.join(", ")}</span> / <strong>Total:</strong> {usd(c.sar.total_amount_usd)} / <strong>Dates:</strong> {c.sar.activity_dates.join(", ")}</p>
              <p className="r-narrative">{c.sar.narrative}</p>
            </>
          ) : <p className="r-muted">Not required by policy for this case{c.sar?.reason ? `: ${c.sar.reason}` : "."}</p>}
        </section>

        <footer className="r-foot">
          Generated by the HHGOA fraud desk agent. Evidence comes from TigerGraph queries cut at the trigger time; customer replies are simulated because the dataset supplies none. {c.tool_calls} tool calls.
        </footer>
      </article>
    </div>
  );
}
