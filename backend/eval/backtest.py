"""Backtest the agent on closed cases it has never seen.

    python -m backend.eval.backtest --build-slice   one-off: extract the rows the sample needs
    python -m backend.eval.backtest                 run the sample and print the metrics

Runs with TOOL_BACKEND=mock and DRY_RUN=true only. I do not tune anything
here; this measures the agent as it is.

Leakage control, enforced rather than trusted:
- Each closed case becomes a synthetic trigger whose `opened_at` is the case's
  own `opened_at`. Every mock read tool cuts at that time, and closed-case
  memory is filtered on `closed_at <= as_of`. A case always closes after it
  opens, so its own record is invisible to its own investigation. I also
  assert that below rather than rely on the arithmetic.
- Agent-written memory is redirected to a scratch file, so backtest cases
  never land in the benchmark memory store and never see benchmark cases.

The benchmark mock slice only holds the 20 case-pack customers, so the
backtest builds its own slice (same columns, same builder functions) for the
sampled customers plus every transaction that shares a device profile with
them.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

os.environ["TOOL_BACKEND"] = "mock"
os.environ["DRY_RUN"] = "true"

import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
CACHE = EVAL_DIR / "cache"
SAMPLE_PATH = CACHE / "backtest_sample.csv"
RESULTS_PATH = CACHE / "backtest_results.json"

SEED = 20161231
N_CLEARED = 25
N_PER_KNOWN_PATTERN = 4  # five known patterns, 20 confirmed
N_UNDOCUMENTED = 5
KNOWN = [
    "card_testing",
    "card_not_present_fraud",
    "card_not_present_new_device",
    "out_of_region_use",
    "account_takeover",
]


def draw_sample() -> pd.DataFrame:
    cc = pd.read_csv(REPO / "data" / "closed_cases_history.csv")
    rng = random.Random(SEED)
    picks: list[str] = []
    cleared = sorted(cc.loc[cc["outcome"] == "cleared", "case_id"])
    picks += rng.sample(cleared, N_CLEARED)
    for pat in KNOWN:
        pool = sorted(cc.loc[(cc["outcome"] == "confirmed_fraud") & (cc["pattern"] == pat), "case_id"])
        picks += rng.sample(pool, N_PER_KNOWN_PATTERN)
    und = sorted(cc.loc[cc["pattern"] == "undocumented", "case_id"])
    picks += rng.sample(und, min(N_UNDOCUMENTED, len(und)))
    return cc[cc["case_id"].isin(picks)].copy()


def build_slice() -> None:
    from backend.tools.mock.extract_slice import load_identity, scan_transactions

    CACHE.mkdir(parents=True, exist_ok=True)
    sample = draw_sample()
    sample.to_csv(SAMPLE_PATH, index=False)
    customers = set(sample["customer_id"].astype(str))
    txn_ids: set[int] = set()
    for s in sample["txn_ids"].fillna(""):
        txn_ids |= {int(t) for t in str(s).split("|") if t}
    print(f"pass 1: {len(customers)} customers, {len(txn_ids)} case txns")
    first = scan_transactions(customers, txn_ids)
    ident = load_identity()
    seen = set(first["TransactionID"].astype("int64"))
    by_id = ident.set_index("TransactionID")
    profiles = {p for p in by_id.loc[by_id.index.intersection(seen), "device_profile"] if p}
    linked = set(ident.loc[ident["device_profile"].isin(profiles), "TransactionID"].astype("int64")) - seen
    print(f"pass 2: {len(profiles)} profiles, {len(linked)} linked txns")
    txns = first
    if linked:
        second = scan_transactions(set(), linked)
        txns = pd.concat([first, second], ignore_index=True).drop_duplicates("TransactionID")
    txns = txns.sort_values("ts").reset_index(drop=True)
    ident_slice = ident.loc[ident["TransactionID"].isin(set(txns["TransactionID"].astype("int64")))]
    txns.to_parquet(CACHE / "txn_slice.parquet", index=False)
    ident_slice.to_parquet(CACHE / "identity_slice.parquet", index=False)
    print(f"wrote {len(txns)} txns, {len(ident_slice)} identity rows to {CACHE}")


def _install_backtest_store_and_memory() -> None:
    """Point the mock layer at the backtest slice and a scratch memory file.

    I patch module attributes instead of editing agent code: the factory and
    read tools both resolve `get_store` from the store module at call time.
    """
    from backend.memory import case_memory
    from backend.tools.mock import read_tools, store as store_mod
    from backend.config import get_settings

    settings = get_settings()
    assert settings.tool_backend == "mock", settings.tool_backend
    assert not settings.llm_enabled, "backtest must run with the LLM disabled"

    txns = pd.read_parquet(CACHE / "txn_slice.parquet")
    identity = pd.read_parquet(CACHE / "identity_slice.parquet")
    card_index = pd.read_parquet(settings.cache_dir / "card_index.parquet")
    case_pack = pd.read_csv(settings.data_dir / "case_pack.csv")
    closed = store_mod._load_closed_cases(settings.data_dir / "closed_cases_history.csv")
    txns["TransactionID"] = txns["TransactionID"].astype("int64")
    txns["ts"] = pd.to_datetime(txns["ts"])
    txns["TransactionAmt"] = txns["TransactionAmt"].astype(float)
    txns["risk_score"] = txns["risk_score"].astype(float)
    identity["TransactionID"] = identity["TransactionID"].astype("int64")
    identity["device_profile"] = store_mod.compose_device_profile(identity)
    txns = store_mod._assign_card_ids(txns, card_index).sort_values("ts").reset_index(drop=True)
    st = store_mod.Store(txns=txns, identity=identity, closed_cases=closed, case_pack=case_pack)

    def _get_store() -> Any:
        return st

    store_mod.get_store = _get_store  # type: ignore[assignment]
    read_tools.get_store = _get_store  # type: ignore[attr-defined]

    scratch = CACHE / "backtest_memory.json"
    if scratch.exists():
        scratch.unlink()
    case_memory._memory = case_memory.CaseMemory(path=scratch)


def _trigger_row(r: pd.Series, txns: pd.DataFrame) -> dict[str, Any]:
    """Turn a closed case into a case-pack style row.

    The flagged transaction is the latest case transaction at or before
    `opened_at`, which is where an alert would have fired. The trigger type
    comes from the analyst note, since that is the only record of why the case
    was opened.
    """
    ids = [int(t) for t in str(r["txn_ids"]).split("|") if t]
    sub = txns[txns["TransactionID"].isin(ids)].sort_values("ts")
    opened = pd.Timestamp(r["opened_at"])
    before = sub[sub["ts"] <= opened]
    row = (before if len(before) else sub).iloc[-1]
    amt = float(row["TransactionAmt"])
    tid = int(row["TransactionID"])
    notes = str(r["analyst_notes"]).lower()
    # If the flagged txn is after opened_at, I move the trigger to the txn time
    # so the agent can see the transaction it is asked about.
    as_of = max(opened, pd.Timestamp(row["ts"]))
    if "reported" in notes or "disput" in notes or "complain" in notes:
        ttype, score = "customer_report", None
        text = f"Customer {r['customer_id']} message: 'I never made this ${amt:,.2f} purchase. Please check my card.' Refers to {tid}."
    elif "analyst" in notes and "model scored" not in notes:
        ttype, score = "analyst_request", None
        text = f"Analyst request: review transaction {tid} on card {r['card_id']} and look for related activity."
    else:
        ttype, score = "risk_score", round(float(row["risk_score"]), 2)
        where = "online" if row["channel"] == "online" else f"in billing region {row['addr1']}"
        text = f"Real-time model scored transaction {tid} (${amt:,.2f}, {where}) at {score:.2f}. Review and decide."
    return {
        "case_id": r["case_id"],
        "opened_at": as_of.strftime("%Y-%m-%d %H:%M:%S"),
        "trigger_type": ttype,
        "trigger_text": text,
        "flagged_txn_id": tid,
        "card_id": r["card_id"],
        "customer_id": r["customer_id"],
        "risk_score": score,
    }


def run_backtest(limit: int | None = None) -> dict[str, Any]:
    _install_backtest_store_and_memory()
    from backend.agent.graph import run_case
    from backend.tools.mock.store import get_store  # patched above

    import backend.tools.mock.store as store_mod

    st = store_mod.get_store()
    sample = pd.read_csv(SAMPLE_PATH)
    if limit:
        sample = sample.head(limit)
    rows = []
    for _, r in sample.iterrows():
        trig = _trigger_row(r, st.txns)
        # The case's own record must be invisible at its trigger time.
        own_visible = (st.closed_cases_before(trig["opened_at"])["case_id"] == r["case_id"]).any()
        assert not own_visible, f"{r['case_id']}: own record visible at {trig['opened_at']}"
        t0 = time.perf_counter()
        try:
            case = run_case(trig)
            err = ""
            verdict, pattern = case.verdict, case.pattern
            p = case.risk_assessment.fraud_probability
            final = [a.action for a in case.final_actions]
            sim = list(case.similar_prior_cases)
        except Exception as exc:  # one broken case must not stop the sample
            err = f"{type(exc).__name__}: {exc}"
            verdict, pattern, p, final, sim = "error", "error", None, [], []
        leaked = r["case_id"] in sim
        rows.append(
            {
                "case_id": r["case_id"],
                "truth_outcome": r["outcome"],
                "truth_pattern": r["pattern"],
                "trigger_type": trig["trigger_type"],
                "verdict": verdict,
                "pattern": pattern,
                "p": p,
                "final": final,
                "leaked_self": leaked,
                "error": err,
                "secs": round(time.perf_counter() - t0, 1),
            }
        )
        print(f"{r['case_id']} truth={r['outcome']}/{r['pattern']} got={verdict}/{pattern} p={p} {err}", flush=True)
    metrics = score(rows)
    RESULTS_PATH.write_text(json.dumps({"rows": rows, "metrics": metrics}, indent=2, default=str), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return metrics


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["verdict"] != "error"]
    truth_fraud = [r["truth_outcome"] == "confirmed_fraud" for r in ok]
    called_fraud = [r["verdict"] == "fraud" for r in ok]
    tp = sum(t and c for t, c in zip(truth_fraud, called_fraud))
    fp = sum((not t) and c for t, c in zip(truth_fraud, called_fraud))
    fn = sum(t and not c for t, c in zip(truth_fraud, called_fraud))
    # Treating `uncertain` as "not a fraud call" is the strict reading. I also
    # report the lenient one, where uncertain on a confirmed case is not a miss.
    unc_on_fraud = sum(t and r["verdict"] == "uncertain" for t, r in zip(truth_fraud, ok))
    cleared = [r for r in ok if r["truth_outcome"] == "cleared"]
    confirmed = [r for r in ok if r["truth_outcome"] == "confirmed_fraud"]
    pat_hits = sum(r["pattern"] == r["truth_pattern"] for r in confirmed)
    pat_hits_all = sum(r["pattern"] == r["truth_pattern"] for r in ok)
    per_pattern: dict[str, dict[str, int]] = {}
    for r in confirmed:
        d = per_pattern.setdefault(r["truth_pattern"], {"n": 0, "pattern_correct": 0, "called_fraud": 0})
        d["n"] += 1
        d["pattern_correct"] += int(r["pattern"] == r["truth_pattern"])
        d["called_fraud"] += int(r["verdict"] == "fraud")
    return {
        "n": len(rows),
        "errors": len(rows) - len(ok),
        "n_confirmed": len(confirmed),
        "n_cleared": len(cleared),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "recall_counting_uncertain": round((tp + unc_on_fraud) / len(confirmed), 3) if confirmed else None,
        "fraud_call_rate": round(sum(called_fraud) / len(ok), 3) if ok else None,
        "legit_rate_on_cleared": round(sum(r["verdict"] == "legitimate" for r in cleared) / len(cleared), 3) if cleared else None,
        "verdicts_on_cleared": dict(Counter(r["verdict"] for r in cleared)),
        "verdicts_on_confirmed": dict(Counter(r["verdict"] for r in confirmed)),
        "pattern_accuracy_confirmed": round(pat_hits / len(confirmed), 3) if confirmed else None,
        "pattern_accuracy_all": round(pat_hits_all / len(ok), 3) if ok else None,
        "per_pattern": per_pattern,
        "self_leaks": sum(r["leaked_self"] for r in rows),
        "trigger_types": dict(Counter(r["trigger_type"] for r in rows)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-slice", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    if args.build_slice:
        build_slice()
        return 0
    if not (CACHE / "txn_slice.parquet").exists():
        print("run with --build-slice first", file=sys.stderr)
        return 2
    run_backtest(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
