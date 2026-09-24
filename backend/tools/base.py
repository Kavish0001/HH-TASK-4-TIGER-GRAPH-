"""The one place a tool call happens.

Every backend, mock or MCP, is reached through `Toolset.call(name, args)`. That
is deliberate: `tool_calls` is a graded answer field, so counting at call sites
would eventually miss one. Nothing below this layer touches the counter.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

READ_TOOLS = (
    "customer_profile",
    "tx_context",
    "card_window",
    "velocity",
    "behavior_shift",
    "shared_device_profile",
    "region_history",
    "ring_detect",
    "prior_cases_for_entities",
    "similar_cases",
    "pattern_match",
    "policy_lookup",
)

WRITE_TOOLS = ("write_case", "append_evidence", "record_decision", "link_similar")


@dataclass
class ToolResult:
    """Compact JSON plus the `ref` the agent copies into evidence[].ref."""

    ok: bool
    ref: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class ToolCallRecord:
    index: int
    name: str
    args: dict[str, Any]
    ref: str
    ok: bool
    duration_s: float
    at: datetime


class ToolBackend(ABC):
    """What a backend must provide. The mock and the MCP client both satisfy it."""

    @abstractmethod
    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult: ...

    def available(self) -> tuple[str, ...]:
        return READ_TOOLS + WRITE_TOOLS


class Toolset:
    """Instrumented facade. One counter, one log, one place to swap backends."""

    def __init__(self, backend: ToolBackend, on_call: Callable[[ToolCallRecord], None] | None = None):
        self._backend = backend
        self._on_call = on_call
        self.calls: list[ToolCallRecord] = []

    @property
    def tool_calls(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self.calls.clear()

    def call(self, name: str, **args: Any) -> ToolResult:
        started = time.perf_counter()
        try:
            result = self._backend.invoke(name, args)
        except Exception as exc:  # a failed tool is evidence too, not a crash
            result = ToolResult(ok=False, ref=f"query:{name}", error=f"{type(exc).__name__}: {exc}")
        duration = time.perf_counter() - started

        record = ToolCallRecord(
            index=len(self.calls) + 1,
            name=name,
            args=args,
            ref=result.ref,
            ok=result.ok,
            duration_s=duration,
            at=datetime.utcnow(),
        )
        self.calls.append(record)
        if self._on_call:
            self._on_call(record)
        return result

    # Typed conveniences. Each one goes through call(), so none escapes counting.
    def customer_profile(self, customer_id: str, as_of: str) -> ToolResult:
        return self.call("customer_profile", customer_id=customer_id, as_of=as_of)

    def tx_context(self, txn_id: str, as_of: str) -> ToolResult:
        return self.call("tx_context", txn_id=txn_id, as_of=as_of)

    def card_window(self, card_id: str, as_of: str, hours: int | None = None, days: int | None = None) -> ToolResult:
        return self.call("card_window", card_id=card_id, hours=hours, days=days, as_of=as_of)

    def velocity(self, card_id: str, windows: list[int], as_of: str) -> ToolResult:
        return self.call("velocity", card_id=card_id, windows=windows, as_of=as_of)

    def behavior_shift(self, customer_id: str, txn_id: str, as_of: str) -> ToolResult:
        return self.call("behavior_shift", customer_id=customer_id, txn_id=txn_id, as_of=as_of)

    def shared_device_profile(self, device_profile: str, as_of: str) -> ToolResult:
        return self.call("shared_device_profile", device_profile=device_profile, as_of=as_of)

    def region_history(self, customer_id: str, addr1: str, as_of: str) -> ToolResult:
        return self.call("region_history", customer_id=customer_id, addr1=addr1, as_of=as_of)

    def ring_detect(self, as_of: str, card_id: str | None = None, device_profile: str | None = None) -> ToolResult:
        return self.call("ring_detect", card_id=card_id, device_profile=device_profile, as_of=as_of)

    def prior_cases_for_entities(self, entity_ids: list[str], as_of: str) -> ToolResult:
        return self.call("prior_cases_for_entities", entity_ids=entity_ids, as_of=as_of)

    def similar_cases(self, query_text: str, entity_ids: list[str], k: int, as_of: str) -> ToolResult:
        return self.call("similar_cases", query_text=query_text, entity_ids=entity_ids, k=k, as_of=as_of)

    def pattern_match(self, pattern: str, card_id: str, txn_id: str, as_of: str) -> ToolResult:
        return self.call("pattern_match", pattern=pattern, card_id=card_id, txn_id=txn_id, as_of=as_of)

    def policy_lookup(self, query: str, k: int = 4) -> ToolResult:
        return self.call("policy_lookup", query=query, k=k)

    def write_case(self, case: dict[str, Any]) -> ToolResult:
        return self.call("write_case", case=case)

    def append_evidence(self, graph_case_id: str, evidence: dict[str, Any]) -> ToolResult:
        return self.call("append_evidence", graph_case_id=graph_case_id, evidence=evidence)

    def record_decision(
        self, graph_case_id: str, action: str, route: str, authorized: bool, reason: str, actor: str
    ) -> ToolResult:
        return self.call(
            "record_decision",
            graph_case_id=graph_case_id,
            action=action,
            route=route,
            authorized=authorized,
            reason=reason,
            actor=actor,
        )

    def link_similar(self, graph_case_id: str, case_ids: list[str], score: float) -> ToolResult:
        return self.call("link_similar", graph_case_id=graph_case_id, case_ids=case_ids, score=score)
