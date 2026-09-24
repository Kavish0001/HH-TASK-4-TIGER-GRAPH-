"""The LLM client the agent talks to.

Lazy: nothing is constructed until the first call, so the whole pipeline
imports and runs with no key set. If the configured provider has no key, the
client falls back to the mock backend and says so in the case record rather
than raising halfway through case 14.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.config import Settings, get_settings
from backend.llm.backends import GoogleBackend, MockBackend, OpenAIBackend
from backend.llm.base import LLMBackend, LLMResult, Message, Usage
from backend.llm.rate_limit import CallLedger, RpmGovernor, with_retry

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._backend: LLMBackend | None = None
        self._governor = RpmGovernor(self._settings.llm_max_rpm)
        self.ledger = CallLedger()
        self.usage = Usage()
        self.models_used: dict[str, int] = {}
        self.fallback_reason = ""

    # ---- construction ----------------------------------------------------

    @property
    def backend(self) -> LLMBackend:
        if self._backend is None:
            self._backend = self._build()
        return self._backend

    @property
    def provider(self) -> str:
        return self.backend.provider

    def _build(self) -> LLMBackend:
        s = self._settings
        if s.dry_run:
            self.fallback_reason = "DRY_RUN is set, so no provider call is made"
            return MockBackend(model="dry-run")
        try:
            if s.llm_provider == "google":
                if not s.google_api_key:
                    raise RuntimeError("GOOGLE_API_KEY is empty")
                return GoogleBackend(
                    api_key=s.google_api_key,
                    model=s.llm_model,
                    fallback_models=s.fallback_models,
                    max_output_tokens=s.llm_max_tokens,
                )
            if s.llm_provider == "openai":
                if not s.openai_api_key:
                    raise RuntimeError("OPENAI_API_KEY is empty")
                return OpenAIBackend(
                    api_key=s.openai_api_key,
                    model=s.llm_model,
                    max_output_tokens=s.llm_max_tokens,
                )
            if s.llm_provider == "mock":
                self.fallback_reason = "LLM_PROVIDER is mock"
                return MockBackend()
            raise RuntimeError(f"unknown LLM_PROVIDER {s.llm_provider!r}")
        except Exception as exc:  # falling back beats failing a 20-case run
            self.fallback_reason = f"{s.llm_provider} unavailable ({exc}); using the deterministic mock backend"
            log.warning(self.fallback_reason)
            return MockBackend()

    # ---- the one entry point ---------------------------------------------

    def complete_structured(
        self,
        system: str,
        messages: list[Message],
        schema: dict[str, Any],
        cache_hint: str = "",
        step: str = "unspecified",
    ) -> LLMResult:
        backend = self.backend
        retries = 0

        def attempt() -> LLMResult:
            return backend.complete_structured(system, messages, schema, cache_hint)

        def note_retry(n: int, exc: BaseException, delay: float) -> None:
            nonlocal retries
            retries = n
            log.warning("llm retry %s for step %s after %s, sleeping %.1fs", n, step, exc, delay)

        waited = self._governor.acquire() if backend.provider != "mock" else 0.0
        try:
            result = with_retry(
                attempt,
                max_retries=self._settings.llm_max_retries,
                backoff_base_s=self._settings.llm_backoff_base_s,
                on_retry=note_retry,
            )
        except Exception as exc:  # the run continues on the deterministic path
            log.error("llm call failed for step %s: %s", step, exc)
            result = MockBackend().complete_structured(system, messages, schema, cache_hint)
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"

        self.ledger.record(step, waited_s=waited, retries=retries)
        self.usage = self.usage + result.usage
        # Which model actually answered matters for reproducibility when the
        # primary was unavailable and a fallback served the call.
        if result.model:
            self.models_used[result.model] = self.models_used.get(result.model, 0) + 1
        return result

    def reset(self) -> None:
        self.usage = Usage()
        self.ledger = CallLedger()
        self.models_used = {}

    def report(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": getattr(self.backend, "model", ""),
            "fallback_reason": self.fallback_reason,
            "tokens": self.usage.total_tokens,
            "tokens_are_estimated": self.usage.estimated,
            "token_note": self.usage.note,
            "models_used": dict(self.models_used),
            **self.ledger.summary(),
        }


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client() -> None:
    global _client
    _client = None


def user_message(payload: dict[str, Any], instruction: str) -> Message:
    """Wrap the case payload in the tag the mock backend reads back."""
    return Message(
        role="user",
        content=f"{instruction}\n\n<case_json>\n{json.dumps(payload, indent=2, default=str)}\n</case_json>",
    )
