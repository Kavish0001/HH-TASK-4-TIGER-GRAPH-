"""TigerGraph backend: the real graph behind the same ToolBackend interface.

Each tool calls one installed GSQL query over RESTPP and reshapes the result to
the shape backend/tools/mock/read_tools.py returns, so swapping the mock for the
graph changes no agent code. The MCP server in graph/mcp/server.py wraps this
same class, so the MCP route and the direct route cannot drift apart.

Why reshape in Python at all: GSQL prints maps and vertex sets in its own JSON
layout (v_id / attributes wrappers, -999999.0 for missing readings), which the
model reads worse than a flat dict. I keep the aggregation in GSQL and only the
cosmetics here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache
from typing import Any

import requests

from backend.tools.base import ToolBackend, ToolResult

MISSING = -999999.0
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
EMBED_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# JSON bodies need VERTEX<T> parameters as {"id": ...}. These are the queries
# that declare one, and which parameters are typed that way.
VERTEX_PARAMS = {
    "customer_profile": ("customer_id",),
    "tx_context": ("txn_id",),
    "card_window": ("card_id",),
    "velocity": ("card_id",),
    "behavior_shift": ("customer_id", "txn_id"),
    "region_history": ("customer_id",),
    "shared_device_profile": ("device_profile",),
    "match_card_testing": ("card_id", "txn_id"),
    "match_card_not_present": ("card_id", "txn_id"),
    "match_out_of_region": ("card_id", "txn_id"),
    "match_account_takeover": ("card_id", "txn_id"),
    "match_structuring": ("card_id", "txn_id"),
}

PATTERN_QUERIES = {
    "card_testing": ("match_card_testing", {}),
    "card_not_present_fraud": ("match_card_not_present", {"require_new_device": False}),
    "card_not_present_new_device": ("match_card_not_present", {"require_new_device": True}),
    "out_of_region_use": ("match_out_of_region", {}),
    "account_takeover": ("match_account_takeover", {}),
    "undocumented": ("match_structuring", {}),
    "undocumented_structuring": ("match_structuring", {}),
}


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL)


def embed(text: str, query: bool) -> list[float]:
    # bge wants the instruction prefix on the query side only; the stored case
    # and chunk vectors were embedded as plain passages.
    vec = _model().encode([(BGE_QUERY_PREFIX + text) if query else text], normalize_embeddings=True)[0]
    return [float(x) for x in vec]


def _clean(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if v <= MISSING + 1:
            return None
        return round(v, 4) if isinstance(v, float) else v
    if isinstance(v, str) and v in ("", "unknown", "1970-01-01 00:00:00"):
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _flatten(vset: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vertex-set prints come as {v_id, v_type, attributes}; the model wants the attributes."""
    return [_clean(dict(v.get("attributes", {}))) for v in vset or []]


def _top(counts: dict[str, int], n: int, key: str) -> list[dict[str, Any]]:
    items = sorted(counts.items(), key=lambda kv: -kv[1])[:n]
    return [{key: k, "n": v} for k, v in items]


def _norm_addr(addr1: Any) -> str:
    """addr1 is stored as the source text, "264.0". Callers pass 264, "264" or 264.0."""
    if addr1 in (None, "", "nan"):
        return ""
    try:
        return f"{float(addr1):.1f}"
    except (TypeError, ValueError):
        return str(addr1)


def _as_of(v: Any) -> str:
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    s = str(v).replace("T", " ")
    return s[:19]


class TigerGraphBackend(ToolBackend):
    name = "tigergraph"

    def __init__(
        self,
        host: str | None = None,
        port: int | str | None = None,
        graph: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int = 180,
    ) -> None:
        self.host = (host or os.environ.get("TG_HOST", "http://localhost")).rstrip("/")
        self.port = str(port or os.environ.get("TG_RESTPP_PORT", "14240"))
        self.graph = graph or os.environ.get("TG_GRAPH_NAME", "FraudInvestigation")
        self.auth = (
            username or os.environ.get("TG_USERNAME", "tigergraph"),
            password or os.environ.get("TG_PASSWORD") or "tigergraph",
        )
        self.timeout = timeout
        self.base = f"{self.host}:{self.port}/restpp"

    # ---- transport ---------------------------------------------------------

    def run(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """POST with a JSON body, which is the only form that carries a 384 float vector."""
        body = {k: v for k, v in params.items() if v is not None}
        for key in VERTEX_PARAMS.get(query, ()):
            if key in body:
                body[key] = {"id": str(body[key])}
        r = requests.post(
            f"{self.base}/query/{self.graph}/{query}",
            data=json.dumps(body),
            auth=self.auth,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        payload = r.json()
        if payload.get("error"):
            raise RuntimeError(f"{query}: {payload.get('message')}")
        return payload.get("results", [])

    def upsert_vector(self, vtype: str, vid: str, attr: str, vec: list[float]) -> None:
        body = {"vertices": {vtype: {vid: {attr: {"value": vec}}}}}
        r = requests.post(f"{self.base}/graph/{self.graph}", data=json.dumps(body), auth=self.auth, timeout=60)
        r.raise_for_status()

    @staticmethod
    def _merge(results: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for block in results:
            out.update(block)
        return out

    # ---- dispatch ----------------------------------------------------------

    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return ToolResult(ok=False, ref=f"query:{name}", error=f"unknown tool {name}")
        clean = {k: v for k, v in args.items() if v is not None}
        try:
            return fn(**clean)
        except Exception as exc:  # a failed query is a tool error, not a crash
            return ToolResult(ok=False, ref=f"query:{name}", error=f"{type(exc).__name__}: {exc}")

    # ---- read tools --------------------------------------------------------

    def t_customer_profile(self, customer_id: str, as_of: str, **_: Any) -> ToolResult:
        ref = f"query:customer_profile(customer_id={customer_id}, as_of={_as_of(as_of)})"
        r = self._merge(self.run("customer_profile", {"customer_id": customer_id, "as_of": _as_of(as_of)}))
        if not r.get("found"):
            return ToolResult(ok=True, ref=ref, data={"customer_id": customer_id, "found": False})
        data = {
            "customer_id": customer_id,
            "found": True,
            "cards": sorted(r["cards"]),
            "txn_count": r["txn_count"],
            "amount_stats": {
                "count": r["amount_count"],
                "mean": round(r["amount_mean"], 2),
                "median": round(r["amount_median"], 2),
                "p95": round(r["amount_p95"], 2),
                "max": round(r["amount_max"], 2),
            },
            "billing_regions": _top(r["billing_regions"], 12, "addr1"),
            "product_codes": r["product_codes"],
            "device_profiles": _top(r["device_profiles"], 15, "profile"),
            "channel_split": r["channel_split"],
            "first_txn": r["first_txn"],
            "last_txn": r["last_txn"],
            "mean_risk_score": round(r["mean_risk_score"], 3),
        }
        return ToolResult(ok=True, ref=ref, data=data)

    def t_tx_context(self, txn_id: str, as_of: str, **_: Any) -> ToolResult:
        ref = f"query:tx_context(txn_id={txn_id}, as_of={_as_of(as_of)})"
        res = self.run("tx_context", {"txn_id": str(txn_id), "as_of": _as_of(as_of)})
        r = self._merge(res)
        if r.get("found") is False:
            return ToolResult(ok=False, ref=ref, error=r.get("error", "not visible at as_of"))
        tx = _flatten(r.get("transaction", []))[0]
        identity = {}
        if tx.get("has_identity"):
            identity = {
                "device_type": tx.pop("device_type", None),
                "device_info": _clean(r.get("device_info")),
                "os": _clean(r.get("os")),
                "browser": _clean(r.get("browser")),
                "screen": _clean(r.get("screen")),
                "device_status": tx.pop("device_status", None),
                "proxy": tx.pop("proxy", None),
                "match_status": tx.pop("match_status", None),
                "id_12": tx.pop("id_12", None),
            }
        for k in ("device_type", "device_status", "proxy", "match_status", "id_12"):
            tx.pop(k, None)
        data = {
            "transaction": {k: tx[k] for k in tx if not (k.startswith("D") and k[1:].isdigit()) and k not in ("M4", "M7")},
            "identity": identity,
            "match_flags": {"M4": tx.get("M4"), "M7": tx.get("M7")},
            "day_deltas": {k: tx.get(k) for k in ("D1", "D2", "D3", "D5", "D11")},
            "feature_note": "D and M columns are Vesta features with no published definitions",
            "prev_txn": (_clean(r.get("prev_txn")) or [None])[0],
            "next_txn": (_clean(r.get("next_txn")) or [None])[0],
        }
        return ToolResult(ok=True, ref=ref, data=data)

    def t_card_window(self, card_id: str, as_of: str, hours: int | None = None, days: int | None = None, **_: Any) -> ToolResult:
        span = f"hours={hours}" if hours else f"days={days or 30}"
        ref = f"query:card_window(card_id={card_id}, {span})"
        r = self._merge(
            self.run("card_window", {"card_id": card_id, "hours": hours or 0, "days": days or 0, "as_of": _as_of(as_of)})
        )
        rows = list(reversed(_clean(r["transactions_newest_first"])))
        data = {
            "card_id": card_id,
            "window": span,
            "window_start": r["window_start"],
            "n": r["n"],
            "total_amount": round(r["total_amount"], 2),
            "n_under_5": r["n_under_5"],
            "transactions": rows,
            "truncated": r["truncated"],
        }
        return ToolResult(ok=True, ref=ref, data=data)

    def t_velocity(self, card_id: str, windows: list[int], as_of: str, **_: Any) -> ToolResult:
        ref = f"query:velocity(card_id={card_id}, windows={list(windows)})"
        res = self.run("velocity", {"card_id": card_id, "windows": [int(w) for w in windows], "as_of": _as_of(as_of)})
        out, hist = [], 0
        for block in res:
            if "window_hours" in block:
                b = _clean(block)
                b["sum_amount"] = round(block["sum_amount"], 2)
                b["max_amount"] = round(block["max_amount"], 2)
                out.append(b)
            if "history_n" in block:
                hist = block["history_n"]
        out.sort(key=lambda w: w["window_hours"])
        return ToolResult(ok=True, ref=ref, data={"card_id": card_id, "windows": out, "history_n": hist})

    def t_behavior_shift(self, customer_id: str, txn_id: str, as_of: str, **_: Any) -> ToolResult:
        ref = f"query:behavior_shift(customer_id={customer_id}, txn_id={txn_id})"
        r = self._merge(
            self.run("behavior_shift", {"customer_id": customer_id, "txn_id": str(txn_id), "as_of": _as_of(as_of)})
        )
        for k in ("amount", "baseline_mean", "baseline_std", "amount_z"):
            r[k] = round(r[k], 2)
        r["amount_percentile"] = round(r["amount_percentile"], 3)
        r["hour_share_in_history"] = round(r["hour_share_in_history"], 3)
        r["device_profile"] = r.get("device_profile") or None
        return ToolResult(ok=True, ref=ref, data=r)

    def t_shared_device_profile(self, device_profile: str, as_of: str, **_: Any) -> ToolResult:
        ref = f"query:shared_device_profile(device_profile={device_profile})"
        if not device_profile:
            return ToolResult(ok=True, ref=ref, data={"device_profile": "", "found": False})
        try:
            r = self._merge(self.run("shared_device_profile", {"device_profile": device_profile, "as_of": _as_of(as_of)}))
        except RuntimeError as exc:
            if "not exist" in str(exc).lower() or "invalid" in str(exc).lower():
                return ToolResult(ok=True, ref=ref, data={"device_profile": device_profile, "found": False})
            raise
        rows = max(r["rows_to_as_of"], 1)
        # Notes are dropped here to keep the result small; prior_cases_for_entities
        # returns them in full when the agent wants to read a specific case.
        cases = [{k: v for k, v in c.items() if k != "analyst_notes"} for c in _clean(r["prior_cases"])]
        data = {
            "device_profile": device_profile,
            "found": True,
            "n_cards": r["n_cards"],
            "n_customers": r["n_customers"],
            "parts_present": r["parts_present"],
            # Sharing is the norm (half of all profiles), so rarity alone is not
            # the test; the novelty and proxy shares are what mark a real ring.
            "is_rare": bool(0 < r["n_customers"] <= 8),
            "rarity_threshold_customers": 8,
            "signature": {
                "rows_to_as_of": r["rows_to_as_of"],
                "new_share": round(r["rows_marked_new"] / rows, 3),
                "proxy_share": round(r["rows_with_proxy"] / rows, 3),
                "anonymous_share": round(r["rows_anonymous_proxy"] / rows, 3),
            },
            "cards": _clean(r["cards"]),
            "prior_cases": cases,
            "confirmed_fraud_prior_cases": sorted(r["confirmed_fraud_prior_cases"]),
        }
        return ToolResult(ok=True, ref=ref, data=data)

    def t_region_history(self, customer_id: str, as_of: str, addr1: Any = None, **_: Any) -> ToolResult:
        addr = _norm_addr(addr1)
        ref = f"query:region_history(customer_id={customer_id}, addr1={addr})"
        if not addr:
            return ToolResult(
                ok=True, ref=ref,
                data={"customer_id": customer_id, "addr1": None, "found": False,
                      "reason": "no billing region recorded; this is not the same as no history in a region"},
            )
        r = self._merge(self.run("region_history", {"customer_id": customer_id, "addr1": addr, "as_of": _as_of(as_of)}))
        r = _clean(r)
        r["span_days"] = round(r.get("span_days") or 0.0, 2)
        r["home_share"] = round(r.get("home_share") or 0.0, 3)
        return ToolResult(ok=True, ref=ref, data=r)

    def t_ring_detect(self, as_of: str, card_id: str | None = None, device_profile: str | None = None, **_: Any) -> ToolResult:
        seed = card_id or device_profile or ""
        ref = f"query:ring_detect(seed={seed})"
        r = self._merge(
            self.run("ring_detect", {"card_id": card_id or "", "device_profile": device_profile or "", "as_of": _as_of(as_of)})
        )
        members = sorted(_flatten(r.get("members", [])), key=lambda m: -(m.get("n_shared_txns") or 0))
        shared = _flatten(r.get("shared_elements", []))
        for s in shared:
            s["cards"] = sorted(s.get("cards") or [])[:60]
        data = {
            "seed": seed,
            "ring_size": r["ring_size"],
            "distinct_customers": r["distinct_customers"],
            "members": members,
            "shared_elements": shared,
            "confirmed_fraud_members": r["confirmed_fraud_members"],
            "is_ring": r["is_ring"],
            "method": "connected component from the seed over cards and device profiles up to as_of; "
            "a profile is followed only if rare (8 customers or fewer) or new-device-behind-anonymous-proxy",
        }
        return ToolResult(ok=True, ref=ref, data=data)

    def t_prior_cases_for_entities(self, entity_ids: list[str], as_of: str, **_: Any) -> ToolResult:
        ref = f"query:prior_cases_for_entities(n_entities={len(entity_ids)}, as_of={_as_of(as_of)})"
        r = self._merge(self.run("prior_cases_for_entities", {"entity_ids": list(entity_ids), "as_of": _as_of(as_of)}))
        cases = _flatten(r.get("cases", []))
        for c in cases:
            c["actions_taken"] = [a for a in (c.get("actions_taken") or "").split("|") if a]
            c["source"] = "closed_case"
        agent = _flatten(r.get("agent_cases", []))
        for c in agent:
            c["source"] = "agent_memory"
        cases.sort(key=lambda c: str(c.get("closed_at") or ""), reverse=True)
        allc = agent + cases
        data = {
            "n": r["n"],
            "cases": allc,
            "confirmed_fraud_rate": round(r["n_confirmed_fraud"] / r["n"], 3) if r["n"] else 0.0,
            "patterns": sorted(r["patterns"]),
        }
        return ToolResult(ok=True, ref=ref, data=data)

    def t_similar_cases(
        self, as_of: str, query_text: str = "", entity_ids: list[str] | None = None, k: int = 5,
        case_embedding: list[float] | None = None, **_: Any,
    ) -> ToolResult:
        ref = f"query:similar_cases(k={k}, as_of={_as_of(as_of)})"
        vec = case_embedding or embed(query_text, query=True)
        r = self._merge(
            self.run("similar_cases", {"query_vector": vec, "entity_ids": list(entity_ids or []), "k": int(k), "as_of": _as_of(as_of)})
        )
        hits = []
        for src, key in (("closed_case", "closed_cases"), ("agent_memory", "agent_cases")):
            for h in _flatten(r.get(key, [])):
                shared = h.get("shared_entities") or []
                h["similarity"] = round(h.get("similarity") or 0.0, 4)
                h["blended_score"] = round(h.get("blended_score") or 0.0, 4)
                h["why_matched"] = (
                    f"shares {len(shared)} entity/entities: {', '.join(sorted(shared)[:4])}"
                    if shared else "text similarity to the analyst notes"
                )
                h["source"] = src
                h["ref"] = f"case:{h.get('graph_case_id') or h['case_id']}"
                hits.append(h)
        hits.sort(key=lambda h: -h["blended_score"])
        return ToolResult(ok=True, ref=ref, data={"n": len(hits), "cases": hits[: int(k)]})

    def t_pattern_match(self, pattern: str, card_id: str, txn_id: str, as_of: str, **_: Any) -> ToolResult:
        ref = f"query:pattern_match(pattern={pattern}, card_id={card_id}, txn_id={txn_id})"
        if pattern not in PATTERN_QUERIES:
            return ToolResult(ok=False, ref=ref, error=f"unknown pattern {pattern}; use one of {sorted(PATTERN_QUERIES)}")
        query, extra = PATTERN_QUERIES[pattern]
        r = self._merge(self.run(query, {"card_id": card_id, "txn_id": str(txn_id), "as_of": _as_of(as_of), **extra}))
        present = r.pop("present_signals", [])
        absent = r.pop("absent_signals", [])
        unchk = r.pop("uncheckable_signals", [])
        total = len(present) + len(absent) + len(unchk)
        out = {
            "pattern": r.pop("pattern", pattern),
            "score": round(r.pop("score", 0.0), 3),
            "present_signals": present,
            "absent_signals": absent,
            "uncheckable_signals": unchk,
            "coverage": round((total - len(unchk)) / total, 3) if total else 0.0,
            # TransactionID rises with TransactionDT, so a numeric sort is a time sort.
            "supporting_txn_ids": sorted({str(t) for t in r.pop("supporting_txn_ids", [])}, key=int)[:40],
            "narrative": r.pop("narrative", ""),
        }
        if not out["narrative"]:
            out["narrative"] = (
                f"{out['pattern']} scored {out['score']:.2f}. Present: {', '.join(present) or 'none'}. "
                f"Absent: {', '.join(absent) or 'none'}. Could not check: {', '.join(unchk) or 'none'}."
            )
        out["detail"] = _clean(r)
        return ToolResult(ok=True, ref=ref, data=out)

    def t_policy_lookup(self, query: str, k: int = 4, **_: Any) -> ToolResult:
        ref = f"doc:policy_lookup(query={query[:60]}, k={k})"
        r = self._merge(self.run("policy_search", {"query_vector": embed(query, query=True), "k": int(k)}))
        chunks = [
            {"ref": c["ref"], "text": f"{c['title']}\n{c['text']}", "score": round(c["score"], 4), "kind": "policy"}
            for c in _flatten(r.get("chunks", []))
        ]
        chunks += [
            {"ref": f"pattern:{p['pattern']}", "text": f"{p['title']}. {p['description']} Signals: {p['signals']}",
             "score": round(p["score"], 4), "kind": "pattern"}
            for p in _flatten(r.get("patterns", []))
        ]
        chunks.sort(key=lambda c: -c["score"])
        return ToolResult(ok=True, ref=ref, data={"n": len(chunks[: int(k)]), "chunks": chunks[: int(k)]})

    # ---- write tools -------------------------------------------------------

    def t_write_case(self, case: dict[str, Any], **_: Any) -> ToolResult:
        case_id = str(case.get("case_id", ""))
        gid = str(case.get("graph_case_id") or f"GC-{case_id}")
        risk = case.get("risk_assessment") or {}
        trigger = case.get("trigger") or {}
        params = {
            "graph_case_id": gid,
            "case_id": case_id,
            "customer_id": str(case.get("customer_id", "")),
            "card_id": str(case.get("card_id", "")),
            "status": str(case.get("status", "open")),
            "verdict": str(case.get("verdict", "")),
            "fraud_probability": float(case.get("fraud_probability", risk.get("fraud_probability", 0.0)) or 0.0),
            "confidence": float(case.get("confidence", risk.get("confidence", 0.0)) or 0.0),
            "pattern": str(case.get("pattern", "none")),
            "pattern_description": str(case.get("pattern_description", "")),
            "first_suspicious_txn_id": str(case.get("first_suspicious_txn_id") or ""),
            "exposure_usd": float(case.get("exposure_usd", 0.0) or 0.0),
            "summary": str(case.get("summary", "")),
            "stop_reason": str(case.get("stop_reason", "")),
            "trigger_type": str(case.get("trigger_type") or trigger.get("type", "")),
            "trigger_text": str(case.get("trigger_text") or trigger.get("trigger_text", "")),
            "opened_at": _as_of(case.get("opened_at") or trigger.get("opened_at") or "2016-11-01 00:00:00"),
            "affected_txn_ids": [str(t) for t in case.get("affected_txn_ids", [])],
            "connected_card_ids": [str(c) for c in case.get("connected_card_ids", [])],
            "connected_device_profiles": [str(d) for d in case.get("connected_device_profiles", [])],
            "similar_prior_cases": [str(c) for c in case.get("similar_prior_cases", [])],
            "unknowns": [str(u) for u in (case.get("unknowns") or risk.get("unknowns") or [])],
            "evidence_json": json.dumps(case.get("evidence", []), default=str),
            "internal_json": json.dumps(case, default=str)[:60000],
        }
        r = self._merge(self.run("write_case", params))
        # The vector goes in after the vertex exists; GSQL cannot assign it.
        text = (
            f"Fraud pattern: {params['pattern']}. Outcome: {params['status']}. Card {params['card_id']}, "
            f"customer {params['customer_id']}. {len(params['affected_txn_ids'])} transaction(s), exposure "
            f"${params['exposure_usd']:.2f}. {params['summary']}"
        )
        self.upsert_vector("InvestigationCase", gid, "emb", embed(text, query=False))
        return ToolResult(ok=True, ref=f"write:write_case({gid})", data={**r, "graph_case_id": gid, "written": True})

    def t_append_evidence(self, graph_case_id: str, evidence: dict[str, Any], **_: Any) -> ToolResult:
        r = self._merge(self.run("append_evidence", {"graph_case_id": graph_case_id, "evidence_json": json.dumps(evidence, default=str)}))
        return ToolResult(ok=bool(r.get("ok")), ref=f"write:append_evidence({graph_case_id})",
                          data={"ok": bool(r.get("ok")), "ref": evidence.get("ref", "")})

    def t_record_decision(self, graph_case_id: str, action: str, route: str, authorized: bool, reason: str,
                          actor: str, phase: str = "final", **_: Any) -> ToolResult:
        r = self._merge(self.run("record_decision", {
            "graph_case_id": graph_case_id, "action": action, "route": route, "authorized": bool(authorized),
            "reason": reason, "actor": actor, "phase": phase}))
        r["at"] = datetime.utcnow().isoformat()
        return ToolResult(ok=bool(r.get("ok")), ref=f"write:record_decision({graph_case_id}, {action})", data=r)

    def t_link_similar(self, graph_case_id: str, case_ids: list[str], score: float, **_: Any) -> ToolResult:
        r = self._merge(self.run("link_similar", {"graph_case_id": graph_case_id, "case_ids": list(case_ids), "score": float(score)}))
        return ToolResult(ok=bool(r.get("ok")), ref=f"write:link_similar({graph_case_id})",
                          data={"ok": bool(r.get("ok")), "linked": r.get("linked", []),
                                "not_found": r.get("not_found", []), "score": score})
