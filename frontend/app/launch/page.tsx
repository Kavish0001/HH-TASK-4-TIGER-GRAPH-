"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createCase, IS_LIVE, listCases } from "@/lib/api";
import { hasRun, TRIGGER_LABEL } from "@/lib/case-utils";
import type { InternalCase, TriggerType } from "@/lib/types";
import { CrumbBand } from "@/components/shell";
import { Button, cx } from "@/components/ui";

const MODES: { type: TriggerType; title: string; blurb: string }[] = [
  { type: "risk_score", title: "Risk score", blurb: "A scored transaction crossed the review threshold." },
  { type: "customer_report", title: "Customer report", blurb: "A cardholder disputes or reports a transaction." },
  { type: "analyst_request", title: "Analyst request", blurb: "You have a lead and an entity to start from." },
];

const CARD_RE = /^C\d{5}-K\d+$/;

export default function LaunchPage() {
  const router = useRouter();
  const [mode, setMode] = useState<TriggerType>("risk_score");
  const [cases, setCases] = useState<InternalCase[]>([]);
  const [bench, setBench] = useState("");
  const [txn, setTxn] = useState("");
  const [card, setCard] = useState("");
  const [score, setScore] = useState("0.70");
  const [text, setText] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const radios = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    listCases().then(setCases).catch(() => setCases([]));
  }, []);

  const benchForMode = useMemo(() => cases.filter((c) => c.trigger.type === mode), [cases, mode]);
  const last = useMemo(() => [...cases].filter(hasRun).sort((a, b) => b.case_id.localeCompare(a.case_id))[0], [cases]);

  const loadBench = (id: string) => {
    setBench(id);
    const c = cases.find((x) => x.case_id === id);
    if (!c) return;
    setTxn(c.trigger.flagged_txn_id);
    setCard(c.trigger.card_id);
    setScore(c.trigger.risk_score != null ? c.trigger.risk_score.toFixed(2) : "0.70");
    setText(c.trigger.trigger_text);
    setErrors({});
  };

  const pickMode = (t: TriggerType) => {
    setMode(t);
    setBench("");
    setErrors({});
  };

  const onRadioKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = (i + d + MODES.length) % MODES.length;
    pickMode(MODES[n].type);
    radios.current[n]?.focus();
  };

  const validate = () => {
    const e: Record<string, string> = {};
    if (!/^\d{7}$/.test(txn.trim())) e.txn = "A 7 digit TransactionID from transactions.csv";
    if (!CARD_RE.test(card.trim())) e.card = "Format C#####-K#, e.g. C13487-K1";
    if (mode === "risk_score") {
      const s = Number(score);
      if (Number.isNaN(s) || s < 0 || s > 1) e.score = "Between 0 and 1";
    }
    if (mode !== "risk_score" && text.trim().length < 10) e.text = mode === "customer_report" ? "Paste the customer's message" : "Say what you want looked at";
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const submit = async () => {
    setSubmitErr(null);
    if (bench) {
      router.push(`/cases/${bench}?run=1`);
      return;
    }
    if (!validate()) return;
    setBusy(true);
    try {
      const cardId = card.trim();
      const s = Number(score);
      const id = await createCase({
        trigger_type: mode,
        flagged_txn_id: txn.trim(),
        card_id: cardId,
        customer_id: cardId.split("-")[0],
        risk_score: mode === "risk_score" ? s : null,
        trigger_text: mode === "risk_score" ? `Real-time model scored transaction ${txn.trim()} at ${s.toFixed(2)}. Review and decide.` : text.trim(),
      });
      router.push(`/cases/${id}?run=1`);
    } catch (e) {
      setSubmitErr(`${(e as Error).message}. Load a benchmark case instead, or check that the backend exposes POST /cases.`);
      setBusy(false);
    }
  };

  return (
    <>
      <CrumbBand crumbs={["fraud", "launcher"]} right={busy ? <span className="text-flame-500">{"// starting"}</span> : "// idle"} />
      <div className="max-w-[880px] mx-auto px-6 py-8">
        <p className="eyebrow">{"// start an investigation"}</p>
        <h1 className="font-display text-[22px] font-medium mt-1">Open a case</h1>
        <p className="text-text-muted mt-1">From a risk score, a customer report, or your own request. The case view opens with the agent already streaming.</p>

        <div role="radiogroup" aria-label="Trigger type" className="grid grid-cols-3 gap-3 mt-6">
          {MODES.map((m, i) => {
            const on = m.type === mode;
            return (
              <button
                key={m.type}
                ref={(el) => {
                  radios.current[i] = el;
                }}
                role="radio"
                aria-checked={on}
                tabIndex={on ? 0 : -1}
                onClick={() => pickMode(m.type)}
                onKeyDown={(e) => onRadioKey(e, i)}
                className={cx("text-left panel px-3 py-3 border-l-2", on ? "border-l-flame-500 bg-ember-900" : "border-l-rust-800 hover:bg-ember-900")}
              >
                <span className={cx("eyebrow", on && "text-flame-400!")}>{String(i + 1).padStart(2, "0")}</span>
                <span className="block font-mono uppercase tracking-wider text-[12px] text-text mt-1">{m.title}</span>
                <span className="block text-[12px] text-text-muted mt-1">{m.blurb}</span>
              </button>
            );
          })}
        </div>

        <form
          className="panel mt-5"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          noValidate
        >
          <header className="panel-h">
            <h2 className="eyebrow">{"// parameters"}</h2>
          </header>
          <div className="p-4 grid grid-cols-2 gap-x-4 gap-y-3">
            <Field label="Load a benchmark case" hint={`${benchForMode.length} ${TRIGGER_LABEL[mode]} cases in the pack`} className="col-span-2">
              <select value={bench} onChange={(e) => loadBench(e.target.value)} className={inputCls}>
                <option value="">Custom trigger</option>
                {benchForMode.map((c) => (
                  <option key={c.case_id} value={c.case_id}>
                    {c.case_id} / {c.trigger.card_id} / txn {c.trigger.flagged_txn_id}
                    {hasRun(c) ? ` / ${c.status}` : " / not run"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Transaction id" error={errors.txn}>
              <input value={txn} onChange={(e) => { setTxn(e.target.value); setBench(""); }} placeholder="3478561" className={inputCls} inputMode="numeric" />
            </Field>
            <Field label="Card id" error={errors.card} hint={card && CARD_RE.test(card) ? `customer ${card.split("-")[0]}` : undefined}>
              <input value={card} onChange={(e) => { setCard(e.target.value); setBench(""); }} placeholder="C13487-K1" className={inputCls} />
            </Field>
            {mode === "risk_score" ? (
              <Field label="Model risk score" error={errors.score}>
                <input value={score} onChange={(e) => { setScore(e.target.value); setBench(""); }} className={inputCls} inputMode="decimal" />
              </Field>
            ) : (
              <Field label={mode === "customer_report" ? "Customer message" : "Request"} error={errors.text} className="col-span-2">
                <textarea value={text} onChange={(e) => { setText(e.target.value); setBench(""); }} rows={3} className={inputCls} placeholder={mode === "customer_report" ? "I never made this purchase. Please check my card." : "Several cards show purchases from the same device profile. Look for related activity."} />
              </Field>
            )}
            <div className="col-span-2 font-mono text-[10.5px] text-text-faint border-t border-rust-800 pt-3">
              Step budget 24 and evidence requests on (customer_validation, step_up_auth, analyst_info), per backend/agent and policy.yaml.
            </div>
          </div>
          <footer className="flex items-center gap-3 px-4 py-3 border-t border-rust-800">
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Starting" : "Run investigation"}
            </Button>
            {bench && <span className="font-mono text-[11px] text-text-muted">runs {bench}</span>}
            {!IS_LIVE && !bench && <span className="font-mono text-[10.5px] text-text-faint">mock mode: a custom trigger replays a benchmark run of the same type</span>}
          </footer>
          {submitErr && (
            <p className="px-4 pb-3 text-flame-400 font-mono text-[11px]" role="alert">
              {submitErr}
            </p>
          )}
        </form>

        <p className="mt-4 font-mono text-[10.5px] text-text-faint">
          {"// "}
          {cases.length} benchmark cases
          {last ? ` . last run ${last.case_id} . ${last.latency_s.toFixed(1)}s . ${last.tool_calls} tool calls` : ""}
        </p>
      </div>
    </>
  );
}

const inputCls = "w-full bg-ember-900 border border-rust-500 rounded-xs px-2 py-1.5 font-mono text-[12px] text-text placeholder:text-text-faint";

function Field({ label, error, hint, className, children }: { label: string; error?: string; hint?: string; className?: string; children: React.ReactNode }) {
  return (
    <label className={cx("block", className)}>
      <span className="eyebrow block mb-1">{label}</span>
      {children}
      {error ? <span className="block mt-1 text-[11px] text-flame-500">{error}</span> : hint ? <span className="block mt-1 text-[11px] text-text-muted">{hint}</span> : null}
    </label>
  );
}
