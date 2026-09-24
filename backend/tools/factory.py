"""Backend selection. One config switch, TOOL_BACKEND.

`mock` runs against the CSV-derived slice. `mcp` loads graph-engineer's
TigerGraph MCP tools through langchain-mcp-adapters. Both satisfy ToolBackend,
so the agent is unaware of which it has.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.config import get_settings
from backend.tools.base import READ_TOOLS, WRITE_TOOLS, ToolBackend, ToolResult, Toolset

log = logging.getLogger(__name__)


class MockBackend(ToolBackend):
    """Real dataset rows, in-process, no graph required."""

    name = "mock"

    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult:
        from backend.tools.mock.read_tools import READ_DISPATCH, policy_lookup
        from backend.tools.mock.store import get_store
        from backend.tools.mock.write_tools import WRITE_DISPATCH

        clean = {k: v for k, v in args.items() if v is not None}
        if name == "policy_lookup":
            return policy_lookup(**clean)
        if name in READ_DISPATCH:
            return READ_DISPATCH[name](get_store(), **clean)
        if name in WRITE_DISPATCH:
            return WRITE_DISPATCH[name](**clean)
        return ToolResult(ok=False, ref=f"query:{name}", error=f"unknown tool {name}")


class McpBackend(ToolBackend):
    """TigerGraph MCP through langchain-mcp-adapters.

    Constructed lazily, because the MCP server is a separate process that may
    not be up. If a tool is missing from the server the call fails as a tool
    error rather than an exception, which keeps a partial graph lane usable.
    """

    name = "mcp"

    def __init__(self, server_url: str) -> None:
        self.server_url = server_url
        self._tools: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        import asyncio

        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {"tigergraph": {"url": self.server_url, "transport": "sse"}}
        )
        tools = _run_async(client.get_tools())
        self._tools = {t.name: t for t in tools}
        log.info("loaded %s MCP tools: %s", len(self._tools), sorted(self._tools))
        return self._tools

    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult:
        tools = self._load()
        tool = tools.get(name)
        if tool is None:
            return ToolResult(ok=False, ref=f"query:{name}", error=f"MCP server exposes no tool {name}")
        # MCP tools are async-only StructuredTools; sync invoke raises.
        payload = _run_async(tool.ainvoke({k: v for k, v in args.items() if v is not None}))
        data = _parse_payload(payload)
        ref = data.get("ref") or f"query:{name}({_arg_string(args)})"
        # A failed GSQL query comes back as {"ok": false, "error": ...} rather
        # than an exception, and must count as a failed tool, not as evidence.
        ok = bool(data.get("ok", True))
        # graph/mcp/server.py flattens the result to {ok, ref, **data}, so the
        # payload already has the mock and tg shapes.
        return ToolResult(ok=ok, ref=ref, data=data, error=str(data.get("error", "")) if not ok else "")


def _run_async(coro: Any) -> Any:
    """Run a coroutine from sync code, including from inside a running loop
    (the FastAPI worker threads have none, but a notebook or test might)."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _parse_payload(payload: Any) -> dict[str, Any]:
    import json

    if isinstance(payload, dict):
        return payload
    # langchain-mcp-adapters returns text content, sometimes as a list of parts.
    if isinstance(payload, (list, tuple)):
        texts = [p.get("text", "") if isinstance(p, dict) else getattr(p, "text", str(p)) for p in payload]
        payload = "".join(texts)
    if isinstance(payload, tuple):
        payload = payload[0]
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {"result": data}
    except (TypeError, ValueError):
        return {"raw": str(payload)}


def _arg_string(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items() if v is not None and k != "as_of")


def get_backend() -> ToolBackend:
    settings = get_settings()
    if settings.tool_backend == "mcp":
        return McpBackend(settings.mcp_server_url)
    if settings.tool_backend == "tg":
        # Imported lazily so a machine without the graph lane's dependencies
        # still runs the mock path.
        from backend.tools.tg.backend import TigerGraphBackend

        return TigerGraphBackend(
            host=settings.tg_host,
            port=settings.tg_restpp_port,
            graph=settings.tg_graph_name,
            username=settings.tg_username,
            password=settings.tg_password or None,
        )
    return MockBackend()


def build_toolset(on_call: Any = None) -> Toolset:
    return Toolset(get_backend(), on_call=on_call)


__all__ = ["build_toolset", "get_backend", "READ_TOOLS", "WRITE_TOOLS"]
