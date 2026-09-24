"""Run the benchmark cases.

    python -m backend.run_cases                 all 20, in opened_at order
    python -m backend.run_cases --case HHG-014  one case
    python -m backend.run_cases --keep-memory   do not reset agent memory first

The 20 run in `opened_at` order, not case-id order, so a case the agent closed
earlier in calendar time is memory for a later one. Memory never leaks the
other way: every record is filtered by `closed_at <= as_of` at retrieval time,
and I reset agent-written memory before a full run so a previous run's later
cases cannot sit in the store at all.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any

import pandas as pd

from backend.agent.graph import run_case
from backend.answers.write import write_answer, write_internal
from backend.config import get_settings
from backend.llm.client import get_llm_client
from backend.memory.case_memory import get_case_memory


def load_pack() -> list[dict[str, Any]]:
    df = pd.read_csv(get_settings().data_dir / "case_pack.csv")
    df["_ts"] = pd.to_datetime(df["opened_at"])
    df = df.sort_values("_ts")
    rows = df.drop(columns=["_ts"]).to_dict(orient="records")
    return rows


def _clear_agent_memory() -> None:
    mem = get_case_memory()
    mem.clear()


def run(case_ids: list[str] | None, keep_memory: bool, verbose: bool) -> int:
    rows = load_pack()
    if case_ids:
        wanted = set(case_ids)
        rows = [r for r in rows if r["case_id"] in wanted]
        if not rows:
            print(f"no case in case_pack.csv matches {case_ids}", file=sys.stderr)
            return 2
    elif not keep_memory:
        _clear_agent_memory()

    llm = get_llm_client()
    failures = 0
    summary = []
    for row in rows:
        started = time.perf_counter()

        def emit(kind: str, payload: dict[str, Any]) -> None:
            if verbose and kind == "step":
                print(f"  [{payload['index']:>2}] {payload['node']:<18} {payload['summary'][:150]}")

        try:
            case = run_case(row, emit=emit, llm=llm)
            paths = write_answer(case)
            write_internal(case)
            summary.append(
                {
                    "case_id": case.case_id,
                    "opened_at": row["opened_at"],
                    "verdict": case.verdict,
                    "status": case.status,
                    "pattern": case.pattern,
                    "p": case.risk_assessment.fraud_probability,
                    "conf": case.risk_assessment.confidence,
                    "exposure": case.exposure_usd,
                    "initial": [a.action for a in case.initial_actions],
                    "final": [a.action for a in case.final_actions],
                    "sar": bool(case.sar and case.sar.file),
                    "tool_calls": case.tool_calls,
                    "tokens": case.tokens,
                }
            )
            print(
                f"{case.case_id} {case.verdict:<10} {case.pattern:<28} p={case.risk_assessment.fraud_probability:.2f} "
                f"conf={case.risk_assessment.confidence:.2f} final={[a.action for a in case.final_actions]} "
                f"sar={bool(case.sar and case.sar.file)} tools={case.tool_calls} tokens={case.tokens} "
                f"({time.perf_counter() - started:.1f}s) -> {paths[0]}"
            )
        except Exception as exc:  # one broken case must not stop the other nineteen
            failures += 1
            logging.exception("case %s failed", row["case_id"])
            print(f"{row['case_id']} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)

    report = llm.report()
    print(json.dumps({"llm": report, "failures": failures}, indent=2, default=str))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run HHGOA benchmark cases through the agent")
    ap.add_argument("--case", action="append", help="case id, repeatable; default runs all 20")
    ap.add_argument("--keep-memory", action="store_true", help="keep agent memory from a previous run")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every agent step")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return run(args.case, args.keep_memory, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
