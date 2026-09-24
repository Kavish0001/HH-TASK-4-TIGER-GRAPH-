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
        tools = asyncio.get_event_loop().run_until_complete(client.get_tools())
        self._tools = {t.name: t for t in tools}
        log.info("loaded %s MCP tools: %s", len(self._tools), sorted(self._tools))
        return self._tools

    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult:
        tools = self._load()
        tool = tools.get(name)
        if tool is None:
            return ToolResult(ok=False, ref=f"query:{name}", error=f"MCP server exposes no tool {name}")
        payload = tool.invoke({k: v for k, v in args.items() if v is not None})
        if isinstance(payload, dict):
            data = payload
        else:
            import json

            try:
                data = json.loads(payload)
            except (TypeError, ValueError):
                data = {"raw": str(payload)}
        ref = data.get("ref") or f"query:{name}({_arg_string(args)})"
        return ToolResult(ok=True, ref=ref, data=data)


def _arg_string(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items() if v is not None and k != "as_of")


def get_backend() -> ToolBackend:
    settings = get_settings()
    if settings.tool_backend == "mcp":
        return McpBackend(settings.mcp_server_url)
    return MockBackend()


def build_toolset(on_call: Any = None) -> Toolset:
    return Toolset(get_backend(), on_call=on_call)


__all__ = ["build_toolset", "get_backend", "READ_TOOLS", "WRITE_TOOLS"]
