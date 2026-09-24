"""RPM governor and 429 backoff.

On the Gemini free tier the requests-per-minute cap, not latency or cost, is
what decides whether a twenty-case run finishes. A run that dies at case 14 on a
quota error is worse than a slow run that completes, so the client paces itself
before it sends and retries with exponential backoff when the provider pushes
back.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Any, Callable, TypeVar

T = TypeVar("T")

RETRYABLE_MARKERS = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "rate limit",
    "quota",
    "503",
    "unavailable",
    "overloaded",
    "500",
    "internal error",
)


class RpmGovernor:
    """Sliding-window limiter. Blocks until sending now stays under the cap."""

    def __init__(self, max_rpm: int) -> None:
        self.max_rpm = max(1, int(max_rpm))
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()
        self.total_waited_s = 0.0

    def acquire(self) -> float:
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                while self._sent and now - self._sent[0] >= 60.0:
                    self._sent.popleft()
                if len(self._sent) < self.max_rpm:
                    self._sent.append(now)
                    self.total_waited_s += waited
                    return waited
                sleep_for = 60.0 - (now - self._sent[0]) + 0.05
            time.sleep(sleep_for)
            waited += sleep_for


def is_retryable(exc: BaseException) -> bool:
    # A spent daily quota does not come back within any backoff we would wait.
    if type(exc).__name__ == "AllModelsExhausted" or "PerDay" in f"{exc}":
        return False
    text = f"{type(exc).__name__} {exc}".lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (429, 500, 503):
        return True
    return any(marker in text for marker in RETRYABLE_MARKERS)


def with_retry(
    fn: Callable[[], T],
    max_retries: int,
    backoff_base_s: float,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - the caller decides what is fatal
            attempt += 1
            if attempt > max_retries or not is_retryable(exc):
                raise
            # Full jitter, so twenty cases retrying do not resynchronise.
            delay = min(backoff_base_s * (2 ** (attempt - 1)), 60.0)
            delay = random.uniform(delay * 0.5, delay)
            if on_retry:
                on_retry(attempt, exc, delay)
            time.sleep(delay)


class CallLedger:
    """Per-case visibility into what a full run would cost in quota."""

    def __init__(self) -> None:
        self.calls = 0
        self.retries = 0
        self.waited_s = 0.0
        self.by_step: dict[str, int] = {}

    def record(self, step: str, waited_s: float = 0.0, retries: int = 0) -> None:
        self.calls += 1
        self.retries += retries
        self.waited_s += waited_s
        self.by_step[step] = self.by_step.get(step, 0) + 1

    def summary(self) -> dict[str, Any]:
        return {
            "llm_calls": self.calls,
            "retries": self.retries,
            "rate_limit_wait_s": round(self.waited_s, 2),
            "by_step": dict(self.by_step),
        }
