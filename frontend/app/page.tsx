import Link from "next/link";

// Figures from outputs/eval_report.md and the backend query and tool registries.
const STATS = [
  { value: "20", label: "benchmark cases" },
  { value: "590k", label: "transactions in the graph" },
  { value: "26", label: "GSQL queries" },
  { value: "16", label: "MCP tools" },
  { value: "20/20", label: "cases written to the graph" },
];

const STEPS = [
  { n: "01", title: "Trigger", body: "A risk score, a customer report or an analyst request opens a case on a card." },
  { n: "02", title: "Graph evidence via MCP", body: "The agent calls TigerGraph queries over MCP: shared devices, merchants, rings." },
  { n: "03", title: "Assess risk and confidence", body: "Fraud probability and confidence are scored separately, with unknowns listed." },
  { n: "04", title: "Request evidence", body: "When confidence is low it asks for the evidence that would change the answer." },
  { n: "05", title: "Act under policy", body: "Actions are routed by policy. L1 and L2 steps wait for analyst approval." },
  { n: "06", title: "SAR and memory", body: "A SAR draft when required, and the outcome is written back as case memory." },
];

export default function LandingPage() {
  return (
    <>
      <section className="relative min-h-[calc(100svh-46px)] flex items-center">
        {/* Keeps the headline legible where the model sits behind it on narrow screens. */}
        <div aria-hidden className="absolute inset-0 bg-[linear-gradient(90deg,rgba(12,12,12,0.92)_0%,rgba(12,12,12,0.72)_45%,rgba(12,12,12,0)_75%)] max-lg:bg-[linear-gradient(180deg,rgba(12,12,12,0.35)_0%,rgba(12,12,12,0.85)_55%)]" />
        <div className="relative w-full max-w-[1200px] mx-auto px-5 sm:px-8 py-16">
          <div className="max-w-[560px]">
            <p className="eyebrow">{"// agentic fraud investigation on TigerGraph"}</p>
            <h1 className="font-display font-semibold tracking-tight text-[40px] sm:text-[56px] leading-[1.02] mt-4">
              HHGOA fraud desk
            </h1>
            <span aria-hidden className="block mt-4 h-[3px] w-12 bg-flame-500" />
            <p className="mt-5 text-[16px] sm:text-[18px] leading-relaxed text-text-muted">
              An investigation agent that reads the transaction graph, states its uncertainty, and only acts inside policy. Graph evidence, risk and confidence, analyst-gated actions and a SAR draft in one case view.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/cases" className="inline-flex items-center h-10 px-5 font-mono text-[12px] uppercase tracking-wider rounded-xs border border-flame-500 bg-flame-500 text-ink-950 hover:bg-flame-400 transition-colors">
                Open case queue
              </Link>
              <Link href="/launch" className="inline-flex items-center h-10 px-5 font-mono text-[12px] uppercase tracking-wider rounded-xs border border-rust-500 bg-ink-950/60 text-text hover:bg-ember-900 transition-colors">
                Launch investigation
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="relative bg-ink-950 border-t border-rust-800">
        <div className="max-w-[1200px] mx-auto px-5 sm:px-8 py-14">
          <p className="eyebrow">{"// how it works"}</p>
          <h2 className="font-display font-semibold text-[22px] tracking-tight mt-2">From alert to decision in six steps</h2>
          <ol className="mt-8 grid gap-px bg-rust-800 border border-rust-800 rounded-xs overflow-hidden sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            {STEPS.map((s) => (
              <li key={s.n} className="bg-ember-950 p-4">
                <p className="font-mono text-[11px] text-flame-500 tabular">{s.n}</p>
                <p className="mt-2 font-medium text-text">{s.title}</p>
                <p className="mt-1.5 text-text-muted text-[12.5px] leading-relaxed">{s.body}</p>
              </li>
            ))}
          </ol>

          <dl className="mt-10 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-px bg-rust-800 border border-rust-800 rounded-xs overflow-hidden">
            {STATS.map((s, i) => (
              <div key={s.label} className={i === STATS.length - 1 ? "bg-ink-950 px-4 py-5 max-sm:col-span-2" : "bg-ink-950 px-4 py-5"}>
                <dt className="eyebrow">{s.label}</dt>
                <dd className="mt-2 font-mono text-[26px] font-medium tabular text-text">{s.value}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-6 font-mono text-[11px] text-text-faint">{"// figures from outputs/eval_report.md"}</p>
        </div>
      </section>
    </>
  );
}
