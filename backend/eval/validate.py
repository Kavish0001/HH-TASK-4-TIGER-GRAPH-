"""Validate the graded answer files.

    python -m backend.eval.validate             cases/*.json, exit 1 on any error
    python -m backend.eval.validate --dir X     another folder of answers

Checks, in order:
1. JSON schema (backend/contracts/answer.schema.json).
2. The rules the schema cannot express, from data/README.md Answer Format and
   Fraud Policy: every ID exists in the dataset, SAR agrees with FILE_REPORT,
   legitimate means no exposure and no SAR, exposure is the summed absolute
   amounts, pattern_description only for undocumented, every route matches
   policy.yaml (BLOCK_CARD split at $2,500), final equals initial when nothing
   was requested, SAR filed when policy 3a requires it.
3. cases/ and outputs/answers/ hold identical files.

Every problem prints as `<case_id> <field>: <message>`. I fail on errors and
print warnings separately, because a warning is a judgement call (for
example, SAR sentence count) while an error is something the grader will
mark zero.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
CACHE = Path(__file__).resolve().parent / "cache"
SCHEMA = REPO / "backend" / "contracts" / "answer.schema.json"
POLICY = REPO / "backend" / "contracts" / "policy.yaml"
EM_DASH = chr(0x2014)
TOL = 0.011


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, cid: str, field: str, msg: str) -> None:
        self.errors.append(f"{cid} {field}: {msg}")

    def warn(self, cid: str, field: str, msg: str) -> None:
        self.warnings.append(f"{cid} {field}: {msg}")


# ---------------------------------------------------------------- dataset ids


def _txn_table() -> pd.DataFrame:
    """TransactionID, amount, ts for all 590k rows, cached after the first scan
    because reading the 708 MB file on every validation run is the slow part."""
    path = CACHE / "txn_ids.parquet"
    if path.exists():
        return pd.read_parquet(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(DATA / "transactions.csv", usecols=["TransactionID", "TransactionAmt", "ts"])
    df["TransactionID"] = df["TransactionID"].astype("int64")
    df.to_parquet(path, index=False)
    return df


def _device_profiles() -> set[str]:
    path = CACHE / "device_profiles.json"
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")))
    from backend.tools.mock.store import compose_device_profile

    ident = pd.read_csv(
        DATA / "identity.csv", usecols=["DeviceInfo", "id_30", "id_31", "id_33"], low_memory=False
    )
    profiles = sorted(set(compose_device_profile(ident)))
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profiles), encoding="utf-8")
    return set(profiles)


class Dataset:
    def __init__(self) -> None:
        txns = _txn_table()
        self.amount = dict(zip(txns["TransactionID"].astype(str), txns["TransactionAmt"].astype(float)))
        self.ts = dict(zip(txns["TransactionID"].astype(str), txns["ts"].astype(str)))
        cards = pd.read_parquet(REPO / "backend" / "data_cache" / "card_index.parquet")
        self.cards = set(cards["card_id"].astype(str))
        self.customers = set(cards["customer_id"].astype(str))
        cc = pd.read_csv(DATA / "closed_cases_history.csv", usecols=["case_id"])
        self.closed = set(cc["case_id"].astype(str))
        self.pack = pd.read_csv(DATA / "case_pack.csv")
        self.pack_ids = set(self.pack["case_id"].astype(str))
        self.devices = _device_profiles()

    def kind(self, ident: str) -> str | None:
        if ident in self.amount:
            return "txn"
        if ident in self.cards:
            return "card"
        if ident in self.customers:
            return "customer"
        if ident in self.closed:
            return "closed_case"
        if ident in self.pack_ids:
            return "case_pack"
        if ident in self.devices:
            return "device_profile"
        return None


# ---------------------------------------------------------------- policy


def load_routes() -> tuple[dict[str, str], float]:
    pol = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    routing = pol["routing"]
    static: dict[str, str] = {}
    for a in routing["auto"]:
        static[a] = "auto"
    for a in routing["l1"]:
        static[a] = "L1"
    for a in routing["l2"]:
        static[a] = "L2"
    split = None
    for branch in routing["conditional"]["BLOCK_CARD"]:
        m = re.search(r"<=\s*([0-9.]+)", branch["when"])
        if m:
            split = float(m.group(1))
    assert split == 2500.0, f"policy.yaml BLOCK_CARD split is {split}, README says 2500"
    return static, split


def expected_route(action: str, exposure: float, static: dict[str, str], split: float) -> str:
    if action == "BLOCK_CARD":
        return "L1" if exposure <= split else "L2"
    return static[action]


# ---------------------------------------------------------------- checks


def check_file(path: Path, ds: Dataset, validator: jsonschema.Validator, routes, rep: Report) -> dict | None:
    cid = path.stem
    try:
        a = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        rep.err(cid, "<file>", f"not valid JSON: {exc}")
        return None

    for e in sorted(validator.iter_errors(a), key=lambda e: list(e.absolute_path)):
        field = ".".join(str(p) for p in e.absolute_path) or "<root>"
        rep.err(cid, field, f"schema: {e.message}")
    if rep.errors and any(x.startswith(f"{cid} ") and "schema:" in x for x in rep.errors):
        # Semantic checks assume the shape is right; stop here for this file.
        return a

    static, split = routes
    c, sar, nba = a["case"], a["sar"], a["next_best_actions"]

    if a["case_id"] != cid:
        rep.err(cid, "case_id", f"file name says {cid}, field says {a['case_id']}")
    if a["case_id"] not in ds.pack_ids:
        rep.err(cid, "case_id", "not in case_pack.csv")

    # --- ids exist
    for t in c["affected_txn_ids"]:
        if t not in ds.amount:
            rep.err(cid, "case.affected_txn_ids", f"{t} not in transactions.csv")
    if len(set(c["affected_txn_ids"])) != len(c["affected_txn_ids"]):
        rep.err(cid, "case.affected_txn_ids", "duplicate ids")
    fs = c["first_suspicious_txn_id"]
    if fs and fs not in ds.amount:
        rep.err(cid, "case.first_suspicious_txn_id", f"{fs} not in transactions.csv")
    if fs and c["affected_txn_ids"] and fs not in c["affected_txn_ids"]:
        rep.err(cid, "case.first_suspicious_txn_id", f"{fs} not in affected_txn_ids")
    if fs and c["affected_txn_ids"]:
        earliest = min(c["affected_txn_ids"], key=lambda t: ds.ts.get(t, "9999"))
        if ds.ts.get(fs) != ds.ts.get(earliest):
            rep.warn(cid, "case.first_suspicious_txn_id", f"{fs} is not the earliest affected txn ({earliest})")
    for k in c["connected_card_ids"]:
        if k not in ds.cards:
            rep.err(cid, "case.connected_card_ids", f"{k} not a dataset card id")
    for d in c["connected_device_profiles"]:
        if d not in ds.devices:
            rep.err(cid, "case.connected_device_profiles", f"'{d}' not composed from identity.csv")
    for s in c["similar_prior_cases"]:
        if s not in ds.closed:
            rep.err(cid, "case.similar_prior_cases", f"{s} not in closed_cases_history.csv")
    for i, ev in enumerate(c["evidence"]):
        for e in ev["entity_ids"]:
            if ds.kind(e) is None:
                rep.err(cid, f"case.evidence[{i}].entity_ids", f"'{e}' is not an id in the dataset")
    for s in sar["subjects"]:
        if ds.kind(s) is None:
            rep.err(cid, "sar.subjects", f"'{s}' is not an id in the dataset")

    # --- the flagged transaction belongs to this case
    row = ds.pack[ds.pack["case_id"] == cid]
    if len(row):
        flagged = str(int(row.iloc[0]["flagged_txn_id"]))
        if c["verdict"] == "fraud" and flagged not in c["affected_txn_ids"]:
            rep.warn(cid, "case.affected_txn_ids", f"fraud verdict but flagged txn {flagged} not included")

    # --- exposure
    total = sum(abs(ds.amount.get(t, 0.0)) for t in c["affected_txn_ids"])
    if abs(total - c["exposure_usd"]) > TOL * max(1, len(c["affected_txn_ids"])):
        rep.err(cid, "case.exposure_usd", f"{c['exposure_usd']} but affected txns sum to {total:.2f}")

    # --- verdict consistency
    v = c["verdict"]
    if v == "legitimate":
        if c["affected_txn_ids"]:
            rep.err(cid, "case.affected_txn_ids", "must be empty for a legitimate verdict")
        if c["exposure_usd"] != 0:
            rep.err(cid, "case.exposure_usd", "must be 0 for a legitimate verdict")
        if sar["file"]:
            rep.err(cid, "sar.file", "must be false for a legitimate verdict")
        if c["pattern"] != "none":
            rep.err(cid, "case.pattern", f"legitimate verdict with pattern {c['pattern']}")
        if c["status"] == "closed_fraud":
            rep.err(cid, "case.status", "closed_fraud with a legitimate verdict")
    if v == "fraud":
        if c["pattern"] == "none":
            rep.err(cid, "case.pattern", "fraud verdict with pattern none")
        if not c["affected_txn_ids"]:
            rep.err(cid, "case.affected_txn_ids", "fraud verdict with no affected txns")
        if c["status"] == "closed_legitimate":
            rep.err(cid, "case.status", "closed_legitimate with a fraud verdict")
    if v == "uncertain" and c["status"] in ("closed_fraud", "closed_legitimate"):
        rep.warn(cid, "case.status", f"{c['status']} with an uncertain verdict")
    if c["written_to_graph"] and not c["graph_case_id"]:
        rep.err(cid, "case.graph_case_id", "written_to_graph true but no id")

    # --- pattern description
    has_desc = bool(c["pattern_description"].strip())
    if c["pattern"] == "undocumented" and not has_desc:
        rep.err(cid, "case.pattern_description", "required when pattern is undocumented")
    if c["pattern"] != "undocumented" and c["pattern_description"] != "":
        rep.err(cid, "case.pattern_description", "must be \"\" unless pattern is undocumented")

    # --- routes, before and after
    for phase in ("initial", "final"):
        seen = Counter(x["action"] for x in nba[phase])
        for act, n in seen.items():
            if n > 1:
                rep.err(cid, f"next_best_actions.{phase}", f"{act} listed {n} times")
        for i, x in enumerate(nba[phase]):
            want = expected_route(x["action"], c["exposure_usd"], static, split)
            if x["route"] != want:
                rep.err(
                    cid,
                    f"next_best_actions.{phase}[{i}].route",
                    f"{x['action']} routed {x['route']}, policy says {want} (exposure {c['exposure_usd']})",
                )
            if not re.search(r"\bR(10|[1-9])\b|3a|3b|section", x["reason"]):
                rep.warn(cid, f"next_best_actions.{phase}[{i}].reason", "cites no policy rule")
    ini = [(x["action"], x["route"]) for x in nba["initial"]]
    fin = [(x["action"], x["route"]) for x in nba["final"]]
    if not a["evidence_requests"]:
        if nba["initial"] != nba["final"]:
            rep.err(cid, "next_best_actions.final", "must equal initial when evidence_requests is empty")
        if nba["what_changed"].strip().lower() != "nothing":
            rep.warn(cid, "next_best_actions.what_changed", "no evidence requested, expected \"nothing\"")
    elif ini != fin and nba["what_changed"].strip().lower() == "nothing":
        rep.err(cid, "next_best_actions.what_changed", "final differs from initial but says nothing")
    final_actions = {x["action"] for x in nba["final"]}
    if v == "legitimate" and final_actions & {"BLOCK_CARD", "BLOCK_ALL_CARDS", "FILE_REPORT"}:
        rep.err(cid, "next_best_actions.final", "legitimate verdict with a block or report")

    # --- SAR
    files = "FILE_REPORT" in final_actions
    if sar["file"] != files:
        rep.err(cid, "sar.file", f"{sar['file']} but FILE_REPORT in final is {files}")
    if files and "CREATE_CASE" not in final_actions:
        rep.err(cid, "next_best_actions.final", "FILE_REPORT without CREATE_CASE (policy 3a)")
    if sar["file"]:
        if not sar["narrative"].strip():
            rep.err(cid, "sar.narrative", "required when file is true")
        n_sent = len([s for s in re.split(r"(?<=[.!?])\s+", sar["narrative"].strip()) if s])
        if not 6 <= n_sent <= 12:
            rep.warn(cid, "sar.narrative", f"{n_sent} sentences, README asks for 6 to 12")
        if not sar["subjects"]:
            rep.err(cid, "sar.subjects", "empty on a filed SAR")
        if len(sar["activity_dates"]) != 2:
            rep.err(cid, "sar.activity_dates", "needs first and last date")
        elif c["affected_txn_ids"]:
            days = sorted(ds.ts[t][:10] for t in c["affected_txn_ids"] if t in ds.ts)
            if days and sar["activity_dates"] != [days[0], days[-1]]:
                rep.warn(cid, "sar.activity_dates", f"{sar['activity_dates']} vs affected txns {days[0]}..{days[-1]}")
        if abs(sar["total_amount_usd"] - c["exposure_usd"]) > TOL * max(1, len(c["affected_txn_ids"])):
            rep.warn(cid, "sar.total_amount_usd", f"{sar['total_amount_usd']} differs from exposure {c['exposure_usd']}")
    else:
        if sar["narrative"] != "":
            rep.err(cid, "sar.narrative", "must be \"\" when file is false")
        if sar["subjects"] != []:
            rep.err(cid, "sar.subjects", "must be [] when file is false")
        if sar["total_amount_usd"] != 0:
            rep.err(cid, "sar.total_amount_usd", "must be 0 when file is false")
        if sar["activity_dates"] != []:
            rep.err(cid, "sar.activity_dates", "must be [] when file is false")
        # Policy 3a: confirmed fraud plus any trigger means a report is required.
        if v == "fraud":
            why = []
            if c["exposure_usd"] > 1000:
                why.append(f"exposure {c['exposure_usd']} > 1000")
            if c["pattern"] == "undocumented":
                why.append("undocumented pattern (R9)")
            if c["connected_card_ids"]:
                why.append(f"connected cards {c['connected_card_ids']}")
            if why:
                rep.err(cid, "sar.file", "policy 3a requires a SAR: " + "; ".join(why))
            if c["connected_device_profiles"]:
                rep.warn(cid, "sar.file", "fraud linked to a device profile without a SAR; check it is shared")

    # --- house rules that bite in graded text
    blob = json.dumps(a, ensure_ascii=False)
    if EM_DASH in blob:
        rep.warn(cid, "<text>", "contains an em-dash")
    if re.search(r"second\s*(==|=|of)\s*0|:00 seconds|zero seconds", blob, re.I):
        rep.err(cid, "<text>", "cites the second==0 generation artifact (identifiers.md)")
    return a


def compare_mirror(src: Path, mirror: Path, rep: Report) -> None:
    a = {p.name for p in src.glob("*.json")}
    b = {p.name for p in mirror.glob("*.json")}
    for name in sorted(a ^ b):
        rep.err(name[:-5], "<mirror>", f"present in only one of {src.name}/ and {mirror.name}/")
    for name in sorted(a & b):
        if json.loads((src / name).read_text(encoding="utf-8")) != json.loads((mirror / name).read_text(encoding="utf-8")):
            rep.err(name[:-5], "<mirror>", f"{src.name}/{name} differs from {mirror.relative_to(REPO)}/{name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(REPO / "cases"))
    ap.add_argument("--mirror", default=str(REPO / "outputs" / "answers"))
    ap.add_argument("--json", help="write a machine-readable result here")
    args = ap.parse_args(argv)

    src = Path(args.dir)
    files = sorted(src.glob("*.json"))
    rep = Report()
    ds = Dataset()
    validator = jsonschema.Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    routes = load_routes()

    missing = ds.pack_ids - {p.stem for p in files}
    for m in sorted(missing):
        rep.err(m, "<file>", f"no answer file in {src}")
    answers = {}
    for p in files:
        a = check_file(p, ds, validator, routes, rep)
        if a is not None:
            answers[p.stem] = a
    if args.mirror and Path(args.mirror).exists():
        compare_mirror(src, Path(args.mirror), rep)

    verdicts = Counter(a["case"]["verdict"] for a in answers.values())
    n = max(1, len(answers))
    print(f"validated {len(files)} files in {src}")
    print(f"verdicts: {dict(verdicts)}")
    legit_rate = verdicts.get("legitimate", 0) / n
    fraud_rate = verdicts.get("fraud", 0) / n
    print(f"legitimate-verdict rate: {legit_rate:.0%}  fraud-verdict rate: {fraud_rate:.0%}")
    if fraud_rate > 0.6:
        print("FLAG: the agent calls fraud on far more than half; the README says half are legitimate")
    if legit_rate < 0.35:
        print("FLAG: legitimate-verdict rate is well below the 50% the README states")
    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}", file=sys.stderr)
    print(f"{len(rep.errors)} errors, {len(rep.warnings)} warnings")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"errors": rep.errors, "warnings": rep.warnings, "verdicts": dict(verdicts)}, indent=2),
            encoding="utf-8",
        )
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
