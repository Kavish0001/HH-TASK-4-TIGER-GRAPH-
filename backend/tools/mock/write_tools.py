"""Mock write tools: the case memory stand-in for the graph.

`write_case` returns a `graph_case_id`, and the answer file's
`written_to_graph` and `graph_case_id` come from here. When the graph lane is
live the same four calls go to TigerGraph and the ids become real Case vertex
ids; the agent code does not change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.memory.case_memory import MemoryRecord, get_case_memory
from backend.tools.base import ToolResult


def write_case(case: dict[str, Any], **_: Any) -> ToolResult:
    memory = get_case_memory()
    graph_case_id = case.get("graph_case_id") or memory.next_graph_case_id()
    record = MemoryRecord(
        graph_case_id=graph_case_id,
        case_id=str(case.get("case_id", "")),
        opened_at=str(case.get("opened_at", "")),
        closed_at=str(case.get("closed_at") or case.get("opened_at") or ""),
        customer_id=str(case.get("customer_id", "")),
        card_id=str(case.get("card_id", "")),
        outcome=str(case.get("status", "open")),
        pattern=str(case.get("pattern", "none")),
        exposure_usd=float(case.get("exposure_usd", 0.0)),
        txn_ids=[str(t) for t in case.get("affected_txn_ids", [])],
        connected_card_ids=[str(c) for c in case.get("connected_card_ids", [])],
        device_profiles=[str(d) for d in case.get("connected_device_profiles", [])],
        summary=str(case.get("summary", "")),
        unknowns=[str(u) for u in case.get("unknowns", [])],
        fraud_probability=float(case.get("fraud_probability", 0.0)),
        confidence=float(case.get("confidence", 0.0)),
        source="agent",
    )
    memory.write(record)
    return ToolResult(
        ok=True,
        ref=f"write:write_case({graph_case_id})",
        data={"graph_case_id": graph_case_id, "written": True},
    )


def append_evidence(graph_case_id: str, evidence: dict[str, Any], **_: Any) -> ToolResult:
    return ToolResult(
        ok=True,
        ref=f"write:append_evidence({graph_case_id})",
        data={"ok": True, "ref": evidence.get("ref", "")},
    )


def record_decision(
    graph_case_id: str, action: str, route: str, authorized: bool, reason: str, actor: str, **_: Any
) -> ToolResult:
    return ToolResult(
        ok=True,
        ref=f"write:record_decision({graph_case_id}, {action})",
        data={
            "ok": True,
            "action": action,
            "route": route,
            "authorized": authorized,
            "actor": actor,
            "at": datetime.utcnow().isoformat(),
        },
    )


def link_similar(graph_case_id: str, case_ids: list[str], score: float, **_: Any) -> ToolResult:
    return ToolResult(
        ok=True,
        ref=f"write:link_similar({graph_case_id})",
        data={"ok": True, "linked": case_ids, "score": score},
    )


WRITE_DISPATCH = {
    "write_case": write_case,
    "append_evidence": append_evidence,
    "record_decision": record_decision,
    "link_similar": link_similar,
}
