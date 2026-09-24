#!/usr/bin/env python3
"""FraudInvestigation MCP server: one MCP tool per installed GSQL query.

Why a second server next to tigergraph-mcp: the official server exposes
generic tools (run_installed_query, get_vertices, run_gsql ...). A model given
those has to know our query names, parameter types and output layout, and it
can run arbitrary GSQL. Here every tool is named for the investigation step it
serves and says what it returns, which is what the model actually picks from.
Both servers can run at once; see graph/mcp/README.md.

Transport is SSE on port 8765 at /sse, because that is what
backend/tools/factory.py McpBackend connects to by default
(MCP_SERVER_URL=http://localhost:8765/sse).

Run from the repo root:  python graph/mcp/server.py [--transport sse|stdio]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO, ".env"))
except ImportError:  # the server still runs on defaults without python-dotenv
    pass

# mcp 2.x renamed FastMCP to MCPServer. The agent's langchain-mcp-adapters
# 0.1.7 pin needs mcp 1.x, so I support both rather than force one.
try:
    from mcp.server.mcpserver import MCPServer as _Server  # mcp 2.x
    _MCP2 = True
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
    _MCP2 = False

from backend.tools.tg.backend import TigerGraphBackend  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from descriptions import TOOL_DESCRIPTIONS  # noqa: E402

server = _Server(
    name="fraud-investigation-graph",
    instructions=(
        "Read and write tools over the FraudInvestigation TigerGraph graph (IEEE-CIS card-not-present "
        "transactions, Jul to Dec 2016). Every read tool takes as_of ('YYYY-MM-DD HH:MM:SS'): pass the "
        "case's opened_at and never a later time. Every result carries `ref`; copy it into evidence[].ref. "
        "IDs: customer C01234, card C01234-K1, txn 3478782, device profile 'DeviceInfo | OS | browser | screen'."
    ),
)
_backend = TigerGraphBackend()


def _call(name: str, **args: Any) -> str:
    # I return compact JSON text rather than a dict: the SDK pretty-prints dicts,
    # which roughly doubles the tokens a card_window result costs the model.
    res = _backend.invoke(name, args)
    if not res.ok:
        out = {"ok": False, "ref": res.ref, "error": res.error}
    else:
        out = {"ok": True, "ref": res.ref, **res.data}
    return json.dumps(out, separators=(",", ":"), default=str)


# ---- read tools -------------------------------------------------------------


@server.tool(description=TOOL_DESCRIPTIONS["customer_profile"])
def customer_profile(customer_id: str, as_of: str) -> str:
    return _call("customer_profile", customer_id=customer_id, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["tx_context"])
def tx_context(txn_id: str, as_of: str) -> str:
    return _call("tx_context", txn_id=txn_id, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["card_window"])
def card_window(card_id: str, as_of: str, hours: int = 0, days: int = 0) -> str:
    return _call("card_window", card_id=card_id, hours=hours or None, days=days or None, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["velocity"])
def velocity(card_id: str, windows: list[int], as_of: str) -> str:
    return _call("velocity", card_id=card_id, windows=windows, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["behavior_shift"])
def behavior_shift(customer_id: str, txn_id: str, as_of: str) -> str:
    return _call("behavior_shift", customer_id=customer_id, txn_id=txn_id, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["shared_device_profile"])
def shared_device_profile(device_profile: str, as_of: str) -> str:
    return _call("shared_device_profile", device_profile=device_profile, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["region_history"])
def region_history(customer_id: str, addr1: str, as_of: str) -> str:
    return _call("region_history", customer_id=customer_id, addr1=addr1, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["ring_detect"])
def ring_detect(as_of: str, card_id: str = "", device_profile: str = "") -> str:
    return _call("ring_detect", card_id=card_id or None, device_profile=device_profile or None, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["prior_cases_for_entities"])
def prior_cases_for_entities(entity_ids: list[str], as_of: str) -> str:
    return _call("prior_cases_for_entities", entity_ids=entity_ids, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["similar_cases"])
def similar_cases(query_text: str, entity_ids: list[str], as_of: str, k: int = 5) -> str:
    return _call("similar_cases", query_text=query_text, entity_ids=entity_ids, k=k, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["pattern_match"])
def pattern_match(pattern: str, card_id: str, txn_id: str, as_of: str) -> str:
    return _call("pattern_match", pattern=pattern, card_id=card_id, txn_id=txn_id, as_of=as_of)


@server.tool(description=TOOL_DESCRIPTIONS["policy_lookup"])
def policy_lookup(query: str, k: int = 4) -> str:
    return _call("policy_lookup", query=query, k=k)


# ---- write tools ------------------------------------------------------------


@server.tool(description=TOOL_DESCRIPTIONS["write_case"])
def write_case(case: dict) -> str:
    return _call("write_case", case=case)


@server.tool(description=TOOL_DESCRIPTIONS["append_evidence"])
def append_evidence(graph_case_id: str, evidence: dict) -> str:
    return _call("append_evidence", graph_case_id=graph_case_id, evidence=evidence)


@server.tool(description=TOOL_DESCRIPTIONS["record_decision"])
def record_decision(graph_case_id: str, action: str, route: str, authorized: bool, reason: str, actor: str) -> str:
    return _call("record_decision", graph_case_id=graph_case_id, action=action, route=route,
                 authorized=authorized, reason=reason, actor=actor)


@server.tool(description=TOOL_DESCRIPTIONS["link_similar"])
def link_similar(graph_case_id: str, case_ids: list[str], score: float) -> str:
    return _call("link_similar", graph_case_id=graph_case_id, case_ids=case_ids, score=score)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default=os.environ.get("MCP_TRANSPORT", "sse"), choices=["sse", "stdio", "streamable-http"])
    ap.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8765")))
    a = ap.parse_args()
    if a.transport == "stdio":
        server.run("stdio")
    elif _MCP2:
        server.run(a.transport, host=a.host, port=a.port)
    else:
        server.settings.host = a.host
        server.settings.port = a.port
        server.run(a.transport)


if __name__ == "__main__":
    main()
