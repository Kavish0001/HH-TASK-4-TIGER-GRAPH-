"""Case memory: closed cases plus the cases this agent closes.

The 20 benchmark cases run in `opened_at` order so an earlier one is available
as memory to a later one. Two rules hold and are enforced here rather than
trusted:

- A case may only be retrieved by an investigation whose trigger time is at or
  after that case was closed. Reading anything later than the trigger is
  leakage.
- Memory is keyed by entity as well as by text, because the useful retrieval is
  usually "what happened on this card or this device before", not "what reads
  similar".

When the graph lane is live this store is a mirror of the Case vertices, and
`write_case` writes to both. Until then it is the authoritative copy.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import get_settings

_LOCK = threading.Lock()


@dataclass
class MemoryRecord:
    graph_case_id: str
    case_id: str
    opened_at: str
    closed_at: str
    customer_id: str
    card_id: str
    outcome: str
    pattern: str
    exposure_usd: float
    txn_ids: list[str] = field(default_factory=list)
    connected_card_ids: list[str] = field(default_factory=list)
    device_profiles: list[str] = field(default_factory=list)
    summary: str = ""
    unknowns: list[str] = field(default_factory=list)
    fraud_probability: float = 0.0
    confidence: float = 0.0
    source: str = "agent"

    @property
    def entities(self) -> list[str]:
        return [self.customer_id, self.card_id, *self.connected_card_ids, *self.device_profiles]

    def as_text(self) -> str:
        return (
            f"Agent case {self.case_id} ({self.graph_case_id}), outcome {self.outcome}, "
            f"pattern {self.pattern}, exposure ${self.exposure_usd:,.2f}, "
            f"probability {self.fraud_probability:.2f} at confidence {self.confidence:.2f}, "
            f"closed {self.closed_at}.\n{self.summary}\n"
            f"Open questions at close: {'; '.join(self.unknowns[:3])}"
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class CaseMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_settings().cache_dir / "case_memory.json")
        self._records: dict[str, MemoryRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for item in raw:
            record = MemoryRecord(**item)
            self._records[record.graph_case_id] = record

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([r.to_dict() for r in self._records.values()], indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        with _LOCK:
            self._records.clear()
            self._flush()

    def write(self, record: MemoryRecord) -> str:
        with _LOCK:
            self._records[record.graph_case_id] = record
            self._flush()
        return record.graph_case_id

    def next_graph_case_id(self) -> str:
        return f"CASE-2016-{1000 + len(self._records) + 1}"

    def available_at(self, as_of: str | datetime) -> list[MemoryRecord]:
        cut = str(as_of)
        return [r for r in self._records.values() if r.closed_at and r.closed_at <= cut]

    def for_entities(self, entity_ids: list[str], as_of: str | datetime) -> list[MemoryRecord]:
        wanted = set(entity_ids)
        return [r for r in self.available_at(as_of) if wanted & set(r.entities)]

    def all(self) -> list[MemoryRecord]:
        return list(self._records.values())


_memory: CaseMemory | None = None


def get_case_memory() -> CaseMemory:
    global _memory
    if _memory is None:
        _memory = CaseMemory()
    return _memory


def reset_case_memory() -> None:
    global _memory
    _memory = None
