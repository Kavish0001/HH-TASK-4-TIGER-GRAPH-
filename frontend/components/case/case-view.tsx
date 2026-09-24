"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useState } from "react";
import { hasRun, toAnswerFile } from "@/lib/case-utils";
import type { RunState } from "@/lib/types";
import { useCaseRun } from "@/lib/use-case-run";
import { CrumbBand } from "@/components/shell";
import { Button, Panel, StatusChip, TriggerChip, Waiting, cx } from "@/components/ui";
import { Modal } from "./modal";
import { ActionDiff, ApprovalPanel, AssessmentPanel, EvidencePanel, PriorCases, SarPanel, StepTimeline } from "./panels";

// Cytoscape is about 400 KB. Only this route pays for it, and never on the server.
const CaseGraph = dynamic(() => import("./case-graph").then((m) => m.CaseGraph), {
  ssr: false,
  loading: () => <Waiting>Loading graph.</Waiting>,
});

const RUN_LABEL: Record<RunState, string> = {
  idle: "idle",
  connecting: "connecting",
  streaming: "streaming",
  polling: "polling",
  done: "complete",
  error: "error",
};

export function CaseView({ id, autoRun }: { id: string; autoRun: boolean }) {
  const { data, loadError, run, runDetail, startedAt, start, decide } = useCaseRun(id, autoRun);
  const [selected, setSelected] = useState<number | null>(null);
  const [answerOpen, setAnswerOpen] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const running = run === "streaming" || run === "polling" || run === "connecting";

  useEffect(() => {
    if (!running || !startedAt) return;
    const t = setInterval(() => setElapsed((Date.now() - startedAt) / 1000), 200);
    return () => clearInterval(t);
  }, [running, startedAt]);

  useEffect(() => setSelected(null), [run === "connecting"]);

  if (loadError)
    return (
      <>
        <CrumbBand crumbs={["fraud", "case", id]} />
        <div className="p-6">
          <div className="panel p-4 max-w-xl" role="alert">
            <p className="text-flame-500 font-mono text-[12px]">Could not load {id}</p>
            <p className="text-text-muted mt-1">{loadError}</p>
            <Link href="/" className="mt-3 inline-block font-mono text-[11px] text-text-muted underline">
              back to queue
            </Link>
          </div>
        </div>
      </>
    );

  const highlight = data && selected != null ? data.evidence[selected]?.entity_ids ?? [] : [];
  const lastStep = data?.steps.at(-1);

  return (
    <>
      <CrumbBand
        crumbs={["fraud", "case", id]}
        right={
          data && (
            <span className={cx(running && "text-flame-500", run === "error" && "text-flame-400")}>
              {"// "}
              {RUN_LABEL[run]}
              {lastStep ? ` . step ${lastStep.index}` : ""}
              {running ? ` . ${elapsed.toFixed(1)}s` : data.latency_s ? ` . ${data.latency_s.toFixed(1)}s` : ""}
              {` . ${data.tool_calls} tool calls`}
              {data.tokens ? ` . ${data.tokens.toLocaleString()} tokens` : ""}
            </span>
          )
        }
      />
      {!data ? (
        <div className="p-6">
          <Waiting>Loading case.</Waiting>
        </div>
      ) : (
        <div className="xl:h-[calc(100vh-var(--chrome-h))] grid gap-2.5 p-2.5 grid-cols-1 xl:grid-cols-[330px_minmax(0,1fr)_340px] xl:grid-rows-[auto_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)]">
          {/* Case header strip, spans all columns */}
          <div className="xl:col-span-3 flex items-center gap-3 min-w-0 border border-rust-800 rounded-xs bg-ember-950 px-3 py-1.5">
            <h1 className="font-display text-[17px] font-medium whitespace-nowrap">{data.case_id}</h1>
            <TriggerChip type={data.trigger.type} />
            <StatusChip status={data.status} />
            <p className="text-[12px] text-text-muted truncate min-w-0" title={data.trigger.trigger_text}>
              {data.trigger.trigger_text}
            </p>
            <div className="ml-auto flex items-center gap-2 flex-none">
              {run === "error" && (
                <span className="font-mono text-[10.5px] text-flame-400 max-w-[280px] truncate" role="alert" title={runDetail}>
                  {runDetail || "event stream disconnected"}
                </span>
              )}
              {run === "polling" && <span className="font-mono text-[10.5px] text-text-faint max-w-[240px] truncate" title={runDetail}>{runDetail}</span>}
              <Button size="sm" onClick={() => setAnswerOpen(true)} disabled={!hasRun(data) || running}>
                Answer file
              </Button>
              <Button size="sm" variant={hasRun(data) ? "ghost" : "primary"} onClick={start} disabled={running}>
                {running ? "Running" : hasRun(data) ? "Re-run" : "Run investigation"}
              </Button>
            </div>
          </div>

          <StepTimeline data={data} run={run} className="xl:row-span-2 min-h-[260px] xl:min-h-0" />
          <Panel title="Case subgraph" state={hasRun(data) ? "ok" : "waiting"} bodyClassName="p-0! relative" className="min-h-[280px] xl:min-h-0"
            meta={selected != null && <button className="font-mono text-[10px] text-text-faint hover:text-text" onClick={() => setSelected(null)}>clear highlight</button>}>
            <CaseGraph data={data} highlight={highlight} />
          </Panel>
          <AssessmentPanel data={data} run={run} className="min-h-[260px] xl:min-h-0" />

          <EvidencePanel data={data} run={run} selected={selected} onSelect={setSelected} className="min-h-[200px] xl:min-h-0" />
          <ApprovalPanel data={data} onDecide={decide} className="min-h-[180px] xl:min-h-0" />

          <PriorCases data={data} run={run} className="min-h-[160px] xl:min-h-0" />
          <ActionDiff data={data} run={run} className="min-h-[200px] xl:min-h-0" />
          <SarPanel data={data} run={run} className="min-h-[140px] xl:min-h-0" />
        </div>
      )}
      {data && (
        <Modal open={answerOpen} title={`Answer file preview / cases/${data.case_id}.json`} onClose={() => setAnswerOpen(false)}>
          <p className="font-mono text-[10.5px] text-text-faint mb-2">The graded projection of this case, same fields as answer.schema.json. Confidence and unknowns live in stop_reason and action reasons.</p>
          <pre className="font-mono text-[11px] text-text-muted whitespace-pre-wrap break-words">{JSON.stringify(toAnswerFile(data), null, 2)}</pre>
        </Modal>
      )}
    </>
  );
}
