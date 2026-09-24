"""Confirm every benchmark case exists in TigerGraph as the answer file says.

    python -m backend.eval.graph_check     exit 1 if any case is missing or disagrees

For each cases/HHG-0NN.json, in `opened_at` order, I read the
InvestigationCase vertex named by `graph_case_id` over REST++ and its outgoing
edges, then compare the vertex against the answer: verdict, pattern,
exposure, affected transactions, similar cases, evidence count. The answer
file claiming `written_to_graph: true` is not proof; the vertex is.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from backend.config import get_settings

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "cache" / "graph_check.json"


def main() -> int:
    s = get_settings()
    base = f"{s.tg_host}:{s.tg_restpp_port}/restpp/graph/{s.tg_graph_name}"
    auth = (s.tg_username, s.tg_password)
    pack = pd.read_csv(REPO / "data" / "case_pack.csv")
    pack["_ts"] = pd.to_datetime(pack["opened_at"])
    pack = pack.sort_values("_ts")

    problems: list[str] = []
    rows = []
    for _, p in pack.iterrows():
        cid = p["case_id"]
        a = json.loads((REPO / "cases" / f"{cid}.json").read_text(encoding="utf-8"))
        c = a["case"]
        gid = c["graph_case_id"]
        row = {"case_id": cid, "opened_at": p["opened_at"], "graph_case_id": gid, "found": False, "edges": {}}
        if not c["written_to_graph"] or not gid:
            problems.append(f"{cid} case.written_to_graph: answer says not written")
            rows.append(row)
            continue
        r = requests.get(f"{base}/vertices/InvestigationCase/{quote(gid, safe='')}", auth=auth, timeout=30)
        body = r.json()
        if r.status_code != 200 or body.get("error") or not body.get("results"):
            problems.append(f"{cid} graph: vertex InvestigationCase {gid} not found ({body.get('message', r.status_code)})")
            rows.append(row)
            continue
        v = body["results"][0]["attributes"]
        row["found"] = True
        e = requests.get(f"{base}/edges/InvestigationCase/{quote(gid, safe='')}", auth=auth, timeout=30).json()
        edges = Counter(x["e_type"] for x in e.get("results", []))
        row["edges"] = dict(edges)

        def diff(field: str, got, want) -> None:
            if got != want:
                problems.append(f"{cid} graph.{field}: vertex {got!r} vs answer {want!r}")

        diff("case_id", v.get("case_id"), cid)
        diff("verdict", v.get("verdict"), c["verdict"])
        diff("pattern", v.get("pattern"), c["pattern"])
        if abs(float(v.get("exposure_usd", -1)) - c["exposure_usd"]) > 0.01:
            diff("exposure_usd", v.get("exposure_usd"), c["exposure_usd"])
        diff("affected_txn_ids", sorted(v.get("affected_txn_ids", [])), sorted(c["affected_txn_ids"]))
        diff("similar_prior_cases", sorted(v.get("similar_prior_cases", [])), sorted(c["similar_prior_cases"]))
        try:
            n_ev = len(json.loads(v.get("evidence_json") or "[]"))
        except json.JSONDecodeError:
            n_ev = -1
        row["evidence_in_vertex"] = n_ev
        if n_ev < 1:
            problems.append(f"{cid} graph.evidence_json: no evidence stored on the vertex")
        if edges.get("HAS_DECISION", 0) < 1:
            problems.append(f"{cid} graph.HAS_DECISION: no decision edges")
        if edges.get("ON_CARD", 0) < 1:
            problems.append(f"{cid} graph.ON_CARD: no card edge")
        if edges.get("CASE_INVOLVES", 0) != len(c["affected_txn_ids"]):
            problems.append(
                f"{cid} graph.CASE_INVOLVES: {edges.get('CASE_INVOLVES', 0)} edges vs {len(c['affected_txn_ids'])} affected txns"
            )
        if edges.get("SIMILAR_TO", 0) < len(c["similar_prior_cases"]):
            problems.append(
                f"{cid} graph.SIMILAR_TO: {edges.get('SIMILAR_TO', 0)} edges vs {len(c['similar_prior_cases'])} similar cases"
            )
        rows.append(row)
        print(f"{cid} {p['opened_at']} {gid} found evidence={n_ev} edges={dict(edges)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"rows": rows, "problems": problems}, indent=2, default=str), encoding="utf-8")
    for pr in problems:
        print(f"ERROR {pr}", file=sys.stderr)
    print(f"{sum(r['found'] for r in rows)}/{len(rows)} cases found in graph, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
