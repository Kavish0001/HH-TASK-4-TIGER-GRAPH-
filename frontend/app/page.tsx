"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { listCases } from "@/lib/api";
import { fmtAge, fmtPct, hasRun, highestPendingRoute, pendingApprovals, riskBand } from "@/lib/case-utils";
import type { CaseStatus, InternalCase, Pattern, TriggerType } from "@/lib/types";
import { CrumbBand } from "@/components/shell";
import { Bar, Button, buttonClass, Chip, RouteBadge, StatusChip, TriggerChip, cx } from "@/components/ui";

const STATUSES: CaseStatus[] = ["open", "escalated", "closed_fraud", "closed_legitimate"];
const TRIGGERS: TriggerType[] = ["risk_score", "customer_report", "analyst_request"];
const PATTERNS: Pattern[] = ["card_testing", "card_not_present_fraud", "card_not_present_new_device", "out_of_region_use", "account_takeover", "undocumented", "none"];

type SortKey = "default" | "case_id" | "risk" | "confidence" | "age" | "status";

export default function QueuePage() {
  const router = useRouter();
  const [cases, setCases] = useState<InternalCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Set<CaseStatus>>(new Set(STATUSES));
  const [trig, setTrig] = useState<Set<TriggerType>>(new Set(TRIGGERS));
  const [pat, setPat] = useState<Set<Pattern>>(new Set(PATTERNS));
  const [minRisk, setMinRisk] = useState(0);
  const [pendingOnly, setPendingOnly] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "default", dir: 1 });

  useEffect(() => {
    listCases()
      .then(setCases)
      .catch((e: Error) => setError(e.message));
  }, []);

  // Case pack timestamps are from 2016. Age is measured against the newest trigger.
  const asOf = useMemo(() => (cases?.length ? Math.max(...cases.map((c) => new Date(c.trigger.opened_at).getTime())) + 3600_000 : Date.now()), [cases]);

  const rows = useMemo(() => {
    if (!cases) return [];
    const f = cases.filter(
      (c) =>
        status.has(c.status) &&
        trig.has(c.trigger.type) &&
        (!hasRun(c) || pat.has(c.pattern)) &&
        c.risk_assessment.fraud_probability >= minRisk &&
        (!pendingOnly || pendingApprovals(c).length > 0),
    );
    const rank = (c: InternalCase) => { const r = highestPendingRoute(c); return r === "L2" ? 2 : r === "L1" ? 1 : 0; };
    const cmp: Record<SortKey, (a: InternalCase, b: InternalCase) => number> = {
      default: (a, b) =>
        rank(b) - rank(a) ||
        b.risk_assessment.fraud_probability - a.risk_assessment.fraud_probability ||
        a.trigger.opened_at.localeCompare(b.trigger.opened_at),
      case_id: (a, b) => a.case_id.localeCompare(b.case_id),
      risk: (a, b) => a.risk_assessment.fraud_probability - b.risk_assessment.fraud_probability,
      confidence: (a, b) => a.risk_assessment.confidence - b.risk_assessment.confidence,
      age: (a, b) => b.trigger.opened_at.localeCompare(a.trigger.opened_at),
      status: (a, b) => a.status.localeCompare(b.status),
    };
    return [...f].sort((a, b) => cmp[sort.key](a, b) * sort.dir);
  }, [cases, status, trig, pat, minRisk, pendingOnly, sort]);

  const awaiting = cases?.filter((c) => pendingApprovals(c).length > 0).length ?? 0;

  const toggle = <T,>(set: Set<T>, v: T, fn: (s: Set<T>) => void) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v);
    else n.add(v);
    fn(n);
  };
  const clearFilters = () => {
    setStatus(new Set(STATUSES));
    setTrig(new Set(TRIGGERS));
    setPat(new Set(PATTERNS));
    setMinRisk(0);
    setPendingOnly(false);
  };
  const th = (key: SortKey, label: string, align = "text-left") => (
    <th scope="col" className={cx("eyebrow px-2 py-2 font-medium", align)} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className="uppercase hover:text-text-muted" onClick={() => setSort((s) => ({ key, dir: s.key === key ? ((-s.dir) as 1 | -1) : key === "risk" || key === "confidence" ? -1 : 1 }))}>
        {label}
        {sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );

  return (
    <>
      <CrumbBand crumbs={["fraud", "case queue"]} right={cases ? <span className={awaiting ? "text-flame-500" : ""}>{`// ${awaiting} awaiting approval`}</span> : null} />
      <div className="grid grid-cols-[240px_minmax(0,1fr)] h-[calc(100vh-var(--chrome-h))]">
        <aside className="border-r border-rust-800 bg-ember-950 overflow-auto p-4 space-y-5" aria-label="Filters">
          <FilterGroup label="Status">
            {STATUSES.map((s) => (
              <Check key={s} label={s} checked={status.has(s)} onChange={() => toggle(status, s, setStatus)} />
            ))}
          </FilterGroup>
          <FilterGroup label="Trigger">
            {TRIGGERS.map((t) => (
              <Check key={t} label={t} checked={trig.has(t)} onChange={() => toggle(trig, t, setTrig)} />
            ))}
          </FilterGroup>
          <FilterGroup label={`Min risk ${minRisk.toFixed(2)}`}>
            <input type="range" min={0} max={1} step={0.05} value={minRisk} onChange={(e) => setMinRisk(+e.target.value)} className="w-full accent-flame-500" aria-label="Minimum fraud probability" />
          </FilterGroup>
          <FilterGroup label="Approval">
            <Check label="pending approval only" checked={pendingOnly} onChange={() => setPendingOnly((v) => !v)} />
          </FilterGroup>
          <FilterGroup label="Pattern">
            {PATTERNS.map((p) => (
              <Check key={p} label={p} checked={pat.has(p)} onChange={() => toggle(pat, p, setPat)} />
            ))}
          </FilterGroup>
        </aside>

        <section className="min-h-0 overflow-auto p-4">
          <div className="flex items-center gap-3 mb-3">
            <h1 className="font-display text-[18px] font-medium">Case queue</h1>
            {cases && <Chip className="border-rust-800 text-text-faint">{`${rows.length} of ${cases.length} cases`}</Chip>}
            <Link href="/launch" className={cx("ml-auto", buttonClass("primary", "sm"))}>
              New investigation
            </Link>
          </div>

          {error ? (
            <div className="panel p-4" role="alert">
              <p className="text-flame-500 font-mono text-[12px]">Could not load cases</p>
              <p className="text-text-muted mt-1">{error}</p>
            </div>
          ) : (
            <table className="w-full border-collapse text-[12.5px]">
              <thead className="bg-ember-900 sticky top-0 z-10">
                <tr>
                  {th("case_id", "Case")}
                  <th scope="col" className="eyebrow px-2 py-2 text-left font-medium">Trigger</th>
                  <th scope="col" className="eyebrow px-2 py-2 text-left font-medium">Pattern</th>
                  {th("risk", "Risk")}
                  {th("confidence", "Conf")}
                  {th("status", "Status")}
                  {th("age", "Age", "text-right")}
                  <th scope="col" className="eyebrow px-2 py-2 text-right font-medium">Approval</th>
                </tr>
              </thead>
              <tbody>
                {!cases &&
                  Array.from({ length: 8 }).map((_, i) => (
                    <tr key={i} className="border-b border-rust-800">
                      {Array.from({ length: 8 }).map((__, j) => (
                        <td key={j} className="px-2 py-2.5">
                          <span className="block h-2.5 bg-ember-900 rounded-xs animate-pulse" style={{ width: `${40 + ((i * 7 + j * 13) % 50)}%` }} />
                        </td>
                      ))}
                    </tr>
                  ))}
                {cases && rows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="text-center py-12">
                      <p className="text-text">No cases match these filters</p>
                      <p className="text-text-muted mt-1">Widen the status, trigger or risk filters.</p>
                      <Button className="mt-3" size="sm" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    </td>
                  </tr>
                )}
                {rows.map((c) => {
                  const p = c.risk_assessment.fraud_probability;
                  const run = hasRun(c);
                  const band = riskBand(p);
                  const route = highestPendingRoute(c);
                  const n = pendingApprovals(c).length;
                  return (
                    <tr
                      key={c.case_id}
                      onClick={() => router.push(`/cases/${c.case_id}`)}
                      className={cx(
                        "border-b border-rust-800 hover:bg-ember-900 cursor-pointer border-l-2",
                        run && band === "high" ? "border-l-flame-500" : run && band === "med" ? "border-l-rust-600" : "border-l-transparent",
                      )}
                    >
                      <td className="px-2 py-2 font-mono">
                        <Link href={`/cases/${c.case_id}`} className="text-text hover:underline" onClick={(e) => e.stopPropagation()}>
                          {c.case_id}
                        </Link>
                        <div className="text-text-faint text-[10.5px]">{c.trigger.card_id}</div>
                      </td>
                      <td className="px-2 py-2">
                        <TriggerChip type={c.trigger.type} />
                        {c.trigger.risk_score != null && <span className="ml-1.5 font-mono text-[10.5px] text-text-faint tabular">{c.trigger.risk_score.toFixed(2)}</span>}
                      </td>
                      <td className="px-2 py-2 font-mono text-[11.5px] text-text-muted max-w-[200px] truncate">{run ? c.pattern : <span className="text-text-faint">not run</span>}</td>
                      <td className="px-2 py-2 whitespace-nowrap">
                        {run ? (
                          <>
                            <Bar value={p} tone={band === "high" ? "flame" : band === "med" ? "rust" : "sand"} />
                            <span className={cx("ml-2 font-mono tabular", band === "high" ? "text-flame-500" : band === "med" ? "text-rust-300" : "text-risk-low")}>{fmtPct(p)}</span>
                          </>
                        ) : (
                          <span className="text-text-faint font-mono">--</span>
                        )}
                      </td>
                      <td className="px-2 py-2 whitespace-nowrap">
                        {run ? (
                          <>
                            <Bar value={c.risk_assessment.confidence} tone="rust" />
                            <span className="ml-2 font-mono tabular text-text-muted">{fmtPct(c.risk_assessment.confidence)}</span>
                          </>
                        ) : (
                          <span className="text-text-faint font-mono">--</span>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <StatusChip status={c.status} />
                      </td>
                      <td className="px-2 py-2 text-right font-mono tabular text-text-muted">{fmtAge(c.trigger.opened_at, asOf)}</td>
                      <td className="px-2 py-2 text-right">
                        {route ? (
                          <span className="inline-flex items-center gap-1.5" title={`${n} action(s) awaiting approval`}>
                            <span className="font-mono text-[10.5px] text-text-muted">{n} pending</span>
                            <RouteBadge route={route} />
                          </span>
                        ) : (
                          <span className="text-text-faint font-mono">-</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {cases && (
            <p className="mt-3 font-mono text-[10.5px] text-text-faint">
              {"// age measured against "}
              {new Date(asOf).toISOString().slice(0, 16).replace("T", " ")}
              {" UTC, the case pack clock"}
            </p>
          )}
        </section>
      </div>
    </>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <fieldset>
      <legend className="eyebrow mb-2">{label}</legend>
      <div className="space-y-1">{children}</div>
    </fieldset>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: () => void }) {
  return (
    <label className="flex items-center gap-2 font-mono text-[11px] text-text-muted cursor-pointer hover:text-text">
      <input type="checkbox" checked={checked} onChange={onChange} className="accent-flame-500" />
      {label}
    </label>
  );
}
