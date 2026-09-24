"""Provider-agnostic LLM interface.

One entry point, `complete_structured(system, messages, schema, cache_hint)`,
returning a parsed dict plus real usage numbers. Backends are selected by
LLM_PROVIDER and are the only files that know a vendor SDK exists.

The system block is passed separately and kept byte-stable across all 20 cases,
so a provider with implicit or explicit caching can reuse it. Nothing here
depends on that working.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False
    note: str = ""

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            estimated=self.estimated or other.estimated,
            note="; ".join(n for n in (self.note, other.note) if n),
        )


@dataclass
class LLMResult:
    data: dict[str, Any]
    usage: Usage
    provider: str
    model: str
    raw_text: str = ""
    ok: bool = True
    error: str = ""


@dataclass
class Message:
    role: str  # "user" or "assistant"
    content: str


class LLMBackend(ABC):
    provider: str = "base"

    @abstractmethod
    def complete_structured(
        self,
        system: str,
        messages: list[Message],
        schema: dict[str, Any],
        cache_hint: str = "",
    ) -> LLMResult: ...


def estimate_tokens(text: str) -> int:
    """Rough fallback when a provider returns no usage.

    Anything using this must set Usage.estimated so the case record says the
    number is an estimate rather than reporting a guess as a measurement.
    """
    return max(1, len(text) // 4)
