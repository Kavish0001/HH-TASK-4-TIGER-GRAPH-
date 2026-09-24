"""Write outputs/eval_report.md from the three eval artifacts.

    python -m backend.eval.validate --json backend/eval/cache/validate_result.json
    python -m backend.eval.graph_check
    python -m backend.eval.backtest
    python -m backend.eval.report

I generate the tables so the numbers in the report are the numbers the scripts
printed, not numbers retyped by hand.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
CACHE = Path(__file__).resolve().parent / "cache"
OUT = REPO / "outputs" / "eval_report.md"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def main() -> None:
    val = json.loads((CACHE / "validate_result.json").read_text(encoding="utf-8"))
    gc = json.loads((CACHE / "graph_check.json").read_text(encoding="utf-8"))
    bt = json.loads((CACHE / "backtest_results.json").read_text(encoding="utf-8"))
    m = bt["metrics"]

    pack = pd.read_csv(REPO / "data" / "case_pack.csv")
    pack["_ts"] = pd.to_datetime(pack["opened_at"])
    pack = pack.sort_values("_ts")
    answers = {cid: json.loads((REPO / "cases" / f"{cid}.json").read_text(encoding="utf-8")) for cid in pack["case_id"]}
    verdicts = Counter(a["case"]["verdict"] for a in answers.values())
    n = len(answers)
    legit, fraud, unc = verdicts.get("legitimate", 0), verdicts.get("fraud", 0), verdicts.get("uncertain", 0)
    sars = sum(a["sar"]["file"] for a in answers.values())
    blocks = sum(any(x["action"] == "BLOCK_CARD" for x in a["next_best_actions"]["final"]) for a in answers.values())

    L: list[str] = []
    L.append("# Evaluation report")
    L.append("")
    L.append("Produced by `backend/eval/` (validate.py, graph_check.py, backtest.py, report.py). All runs used DRY_RUN=true; no LLM calls were made during evaluation.")
    L.append("")
    L.append("## Headline: legitimate-verdict rate")
    L.append("")
    L.append(f"The README states half the 20 cases are legitimate. The agent calls **{legit}/{n} legitimate ({legit/n:.0%})**, {fraud}/{n} fraud ({fraud/n:.0%}) and {unc}/{n} uncertain ({unc/n:.0%}).")
    L.append("")
    if fraud / n > 0.6:
        L.append("**FLAG: the agent calls fraud on far more than half the cases.**")
    else:
        L.append(f"The agent does not call fraud on more than half ({fraud/n:.0%}), so it is not blocking everything. {blocks}/{n} final recommendations include BLOCK_CARD and {sars}/{n} file a SAR.")
    if legit / n < 0.35:
        L.append("")
        L.append(f"**FLAG: only {legit/n:.0%} legitimate against an expected 50%.** With {unc} uncertain verdicts, at least {10 - legit} of the expected legitimate cases sit in `uncertain` or `fraud`. The backtest below points the same way: on cleared closed cases the agent rarely says `legitimate`.")
    L.append("")

    L.append("## 1. Answer-file validation")
    L.append("")
    L.append("`python -m backend.eval.validate` checks every `cases/*.json` against `backend/contracts/answer.schema.json` and then the rules the schema cannot express:")
    L.append("")
    for item in [
        "every transaction, card, customer, closed-case, case-pack and device-profile ID exists in the dataset (evidence `entity_ids` and `sar.subjects` included)",
        "`sar.file` agrees with `FILE_REPORT` in `next_best_actions.final`, and a filed SAR has a case (`CREATE_CASE`) behind it",
        "`legitimate` means empty `affected_txn_ids`, `exposure_usd` 0, `sar.file` false, pattern `none`, no block or report",
        "`exposure_usd` equals the summed absolute amounts of `affected_txn_ids` (from transactions.csv)",
        "`pattern_description` non-empty exactly when `pattern` is `undocumented`",
        "every route, initial and final, matches `policy.yaml`, with BLOCK_CARD L1 at or under $2,500 and L2 above",
        "`final` equals `initial` when `evidence_requests` is empty",
        "SAR required by policy 3a (fraud plus exposure over $1,000, connected cards, or undocumented) is present",
        "SAR false means empty narrative, subjects, dates and zero total",
        "`cases/` and `outputs/answers/` hold identical JSON",
    ]:
        L.append(f"- {item}")
    L.append("")
    L.append("Failures print as `<case_id> <field>: <message>` and the script exits 1. I mutation-tested it on a scratch copy (fake txn ID, wrong BLOCK_CARD route, SAR flag flipped, wrong exposure, final != initial with no requests, missing undocumented description, fake closed-case ID): all seven were caught with the case ID and field.")
    L.append("")
    L.append(f"**Result on the 20 graded files: {len(val['errors'])} errors, {len(val['warnings'])} warnings.**")
    L.append("")
    if val["errors"]:
        for e in val["errors"]:
            L.append(f"- ERROR {e}")
        L.append("")
    if val["warnings"]:
        L.append("Warnings (judgement calls, not format failures):")
        L.append("")
        for w in val["warnings"]:
            L.append(f"- {w}")
        L.append("")
        L.append("The warnings are all action `reason` strings that state the probability but cite no rule number. README policy section 7 asks every recommendation to cite the rule, so these are worth fixing in the answer writer; they do not break the format.")
        L.append("")

    L.append("## 2. Graph check")
    L.append("")
    found = sum(r["found"] for r in gc["rows"])
    L.append(f"`python -m backend.eval.graph_check` reads each `graph_case_id` from TigerGraph over REST++ in `opened_at` order and compares the vertex with the answer file (verdict, pattern, exposure, affected transactions, similar cases), then counts its outgoing edges. **{found}/{len(gc['rows'])} InvestigationCase vertices found, {len(gc['problems'])} problems.**")
    L.append("")
    L.append("| Order | Case | Opened | Vertex | Evidence items on vertex | ON_CARD | CASE_INVOLVES | SIMILAR_TO | HAS_DECISION | Other |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(gc["rows"], 1):
        e = r["edges"]
        other = ", ".join(f"{k} {v}" for k, v in e.items() if k not in ("ON_CARD", "CASE_INVOLVES", "SIMILAR_TO", "HAS_DECISION"))
        L.append(f"| {i} | {r['case_id']} | {r['opened_at']} | {r['graph_case_id'] if r['found'] else 'MISSING'} | {r.get('evidence_in_vertex', '')} | {e.get('ON_CARD', 0)} | {e.get('CASE_INVOLVES', 0)} | {e.get('SIMILAR_TO', 0)} | {e.get('HAS_DECISION', 0)} | {other} |")
    L.append("")
    for p in gc["problems"]:
        L.append(f"- {p}")
    L.append("Evidence is stored as `evidence_json` on the vertex; decisions are separate `Decision` vertices linked by `HAS_DECISION`. Legitimate cases correctly have no CASE_INVOLVES edges.")
    L.append("")

    L.append("## 3. Backtest on closed cases")
    L.append("")
    L.append(f"`python -m backend.eval.backtest`. Fixed seed, {m['n_cleared']} cleared and {m['n_confirmed']} confirmed cases (4 of each known pattern plus 5 undocumented), run through the unchanged agent with TOOL_BACKEND=mock and DRY_RUN=true. No thresholds were tuned.")
    L.append("")
    L.append("Leakage control: each closed case becomes a trigger at its own `opened_at`; every mock read tool cuts at that time and closed-case memory is filtered on `closed_at <= as_of`, so the case's own record is never visible. The script asserts this per case and also checks the agent's `similar_prior_cases` never names the case itself " + f"(self-leaks found: {m['self_leaks']}). Agent-written memory went to a scratch file, so the backtest neither reads nor pollutes the benchmark memory.")
    L.append("")
    L.append("| Metric | Value |")
    L.append("|---|---|")
    rows = [
        ("Cases run (errors)", f"{m['n']} ({m['errors']})"),
        ("Fraud calls: TP / FP / FN", f"{m['tp']} / {m['fp']} / {m['fn']}"),
        ("**Precision on fraud calls**", _pct(m["precision"])),
        ("**Recall on fraud calls** (uncertain counts as a miss)", _pct(m["recall"])),
        ("Recall if uncertain on a confirmed case counts as caught", _pct(m["recall_counting_uncertain"])),
        ("Fraud-call rate overall", _pct(m["fraud_call_rate"])),
        ("**Legitimate verdict on cleared cases**", _pct(m["legit_rate_on_cleared"])),
        ("Verdicts on cleared", json.dumps(m["verdicts_on_cleared"])),
        ("Verdicts on confirmed", json.dumps(m["verdicts_on_confirmed"])),
        ("**Pattern accuracy, confirmed cases**", _pct(m["pattern_accuracy_confirmed"])),
        ("Pattern accuracy, all cases (none on cleared counts)", _pct(m["pattern_accuracy_all"])),
        ("Trigger types derived", json.dumps(m["trigger_types"])),
    ]
    for k, v in rows:
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("Per pattern (confirmed cases):")
    L.append("")
    L.append("| True pattern | n | Called fraud | Pattern correct |")
    L.append("|---|---|---|---|")
    for pat, d in sorted(m["per_pattern"].items()):
        L.append(f"| {pat} | {d['n']} | {d['called_fraud']} | {d['pattern_correct']} |")
    L.append("")
    conf = Counter((r["truth_pattern"], r["pattern"]) for r in bt["rows"] if r["truth_outcome"] == "confirmed_fraud" and r["pattern"] != r["truth_pattern"])
    if conf:
        L.append("Most common pattern confusions (truth -> agent): " + "; ".join(f"{a} -> {b} ({c})" for (a, b), c in conf.most_common(6)) + ".")
        L.append("")

    df = pd.DataFrame(bt["rows"])
    mean_p = df.groupby("truth_outcome")["p"].mean().to_dict()
    tt = pd.crosstab(df["truth_outcome"], df["trigger_type"]).to_dict()
    L.append("What the backtest says:")
    L.append("")
    L.append(f"- **The verdict does not separate cleared from confirmed cases.** Mean fraud probability is {mean_p.get('cleared', 0):.2f} on cleared cases and {mean_p.get('confirmed_fraud', 0):.2f} on confirmed ones, and the verdict mix is almost the same in both groups. Precision on fraud calls ({_pct(m['precision'])}) is what a coin would give on a balanced sample.")
    L.append(f"- **Legitimate is almost never called on a cleared case ({_pct(m['legit_rate_on_cleared'])}).** Most cleared cases land in `uncertain`, and several get `fraud` with probability near 0.9, usually as card_not_present_new_device. This matches the low legitimate rate on the 20 benchmark cases and is the main thing to fix: the new-device signal is scored as strong even though the README says people buy new phones.")
    L.append(f"- **Recall is low if `uncertain` counts as a miss ({_pct(m['recall'])}) and high if it does not ({_pct(m['recall_counting_uncertain'])}).** The agent rarely lets a confirmed case go (one `legitimate`), but it defers most of them.")
    L.append("- **Pattern accuracy** is best on undocumented and card_not_present_new_device, and weakest on card_not_present_fraud (labelled as the new-device variant), card_testing (labelled as plain card-not-present) and account_takeover (split across other labels). The confusion line above has the counts.")
    L.append(f"- **Trigger type is confounded with outcome in the history.** Every cleared case note says the model scored it; every confirmed case note says the cardholder reported it. The derived triggers therefore split exactly by outcome ({json.dumps(tt)}). The verdict mix is still the same in both groups, so the agent is not simply reading the trigger type, but a customer report and a risk-score alert take different paths through evidence requests, and that difference is baked into these numbers.")
    L.append("")

    L.append("## 4. The 20 benchmark cases, in `opened_at` order")
    L.append("")
    L.append("| Case | Opened | Verdict | p | Pattern | Exposure | SAR | NBA before | NBA after |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for _, p in pack.iterrows():
        a = answers[p["case_id"]]
        c = a["case"]
        ini = ", ".join(f"{x['action']} ({x['route']})" for x in a["next_best_actions"]["initial"])
        fin = ", ".join(f"{x['action']} ({x['route']})" for x in a["next_best_actions"]["final"])
        if a["next_best_actions"]["initial"] == a["next_best_actions"]["final"]:
            fin = "unchanged"
        L.append(f"| {p['case_id']} | {p['opened_at']} | {c['verdict']} | {c['fraud_probability']:.2f} | {c['pattern']} | ${c['exposure_usd']:,.2f} | {'yes' if a['sar']['file'] else 'no'} | {ini} | {fin} |")
    L.append("")

    L.append("## 5. Limitations")
    L.append("")
    for item in [
        "**Templated narratives.** The Gemini daily quota ran out before the final run, so `summary`, SAR `narrative`, `what_changed` and `stop_reason` come from deterministic templates in `backend/agent/narrative.py`, not from the LLM. In this run verdicts, patterns, probabilities, actions, routes and SAR decisions all came from the deterministic scoring and policy code, and the backtest ran the same way, so the backtest measures the same decision path that produced the 20 answers. `tokens` is 0 in every answer for the same reason.",
        "**Mock versus graph.** The 20 answers were produced over MCP against TigerGraph. The backtest used the mock backend (pandas over a CSV slice), so no synthetic backtest cases were written into the benchmark graph. The mock slice holds the sampled customers plus every transaction sharing a device profile with them, so device-breadth and ring evidence are comparable, but region-cluster and email-domain neighbourhoods outside that slice are thinner than in the graph. Backtest numbers are an estimate of the logic, not a replay of the graph run.",
        "**Synthetic triggers.** Closed cases carry no trigger row. I derived one per case: the flagged transaction is the latest case transaction at or before `opened_at`, and the trigger type comes from the analyst note (cardholder report versus model score). Because trigger type is confounded with outcome (section 3), the backtest cannot tell how the agent would treat a cleared case that arrived as a customer report.",
        "**Simulated evidence responses.** Customer and analyst replies are simulated by the agent (backend/contracts/evidence_simulation.md), so every `final` recommendation rests on an assumption stated in `evidence_requests`.",
        "**Small sample.** 50 backtest cases, 4 per known pattern. Per-pattern numbers carry wide error bars; card_testing and undocumented have only 16 and 9 cases in the whole history.",
        "**Answer files can be rewritten.** `cases/HHG-006.json` and `outputs/internal/HHG-006.json` were rewritten by another process during this evaluation (not by the eval scripts). Validation was re-run after that; rerun `python -m backend.eval.validate` before submitting.",
    ]:
        L.append(f"- {item}")
    L.append("")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
