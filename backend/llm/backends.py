"""The three backends. Each one is the only file that imports its vendor SDK.

Every SDK import is inside the constructor, so the pipeline imports and runs
with no key and no provider package installed.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from backend.llm.base import LLMBackend, LLMResult, Message, Usage, estimate_tokens


class MockBackend(LLMBackend):
    """Deterministic, free, and exercises every node.

    This is how the pipeline is tested without spending quota, and it is the
    fallback if the free tier runs out mid-run. It fills the schema from the
    evidence the agent already computed, so a mock run produces a valid answer
    file rather than placeholder text.
    """

    provider = "mock"

    def __init__(self, model: str = "mock") -> None:
        self.model = model

    def complete_structured(
        self, system: str, messages: list[Message], schema: dict[str, Any], cache_hint: str = ""
    ) -> LLMResult:
        payload = self._extract_payload(messages)
        data = self._fill(schema, payload)
        text = json.dumps(data)
        prompt_text = system + "".join(m.content for m in messages)
        return LLMResult(
            data=data,
            usage=Usage(
                prompt_tokens=estimate_tokens(prompt_text),
                completion_tokens=estimate_tokens(text),
                total_tokens=estimate_tokens(prompt_text) + estimate_tokens(text),
                estimated=True,
                note="mock backend: token counts are estimated from character length, not measured",
            ),
            provider=self.provider,
            model=self.model,
            raw_text=text,
        )

    @staticmethod
    def _extract_payload(messages: list[Message]) -> dict[str, Any]:
        for message in reversed(messages):
            match = re.search(r"<case_json>(.*?)</case_json>", message.content, re.S)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
        return {}

    def _fill(self, schema: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        props = schema.get("properties", {})
        out: dict[str, Any] = {}
        if "summary" in props:
            out["summary"] = payload.get("draft_summary", "No summary available in mock mode.")
        if "evidence_claims" in props:
            out["evidence_claims"] = [
                {"finding_id": f.get("id", str(i)), "claim": f.get("claim", "")}
                for i, f in enumerate(payload.get("findings", []))
            ]
        if "stop_reason" in props:
            out["stop_reason"] = payload.get("draft_stop_reason", "Investigation stopped.")
        if "what_changed" in props:
            out["what_changed"] = payload.get("draft_what_changed", "nothing")
        if "sar_narrative" in props:
            out["sar_narrative"] = payload.get("draft_sar_narrative", "")
        if "pattern_description" in props:
            out["pattern_description"] = payload.get("draft_pattern_description", "")
        if "agrees_with_leading_hypothesis" in props:
            out["agrees_with_leading_hypothesis"] = True
            out["reasoning"] = "Mock backend does not second-guess the computed hypothesis."
            out["alternative_pattern"] = payload.get("leading_pattern", "none")
            out["missing_evidence"] = payload.get("unknowns", [])[:3]
        for key in schema.get("required", []):
            out.setdefault(key, "" if props.get(key, {}).get("type") == "string" else [])
        return out


def _is_unavailable(exc: BaseException) -> bool:
    """503 UNAVAILABLE, which on these models means high demand, not quota.

    Distinct from 429: retrying the same model harder does not help, so the
    caller falls through to the next model instead.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 503:
        return True
    return "503" in text or "unavailable" in text or "overloaded" in text or "high demand" in text


class GoogleBackend(LLMBackend):
    """Gemini through google-genai.

    Structured output passes the JSON Schema dict straight to `response_schema`
    alongside `response_mime_type="application/json"`. Usage comes from
    `response.usage_metadata`, which carries real counts.

    Model fallback: the newest flash models answer 503 under load often enough
    that a twenty-case run will hit it. Rather than failing the case, the
    backend walks `fallback_models` in order and records which model actually
    answered, because that affects reproducibility.
    """

    provider = "google"

    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> None:
        import logging as _logging

        from google import genai  # imported here so no key means no import

        # The SDK logs an automatic-function-calling warning on every call. We
        # pass no tools, so it is pure noise in the run log.
        _logging.getLogger("google_genai.models").setLevel(_logging.ERROR)
        _logging.getLogger("google.genai.models").setLevel(_logging.ERROR)

        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.fallback_models = fallback_models or []
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.last_model_used = model

    def complete_structured(
        self, system: str, messages: list[Message], schema: dict[str, Any], cache_hint: str = ""
    ) -> LLMResult:
        from google.genai import types

        contents = [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[types.Part.from_text(text=m.content)],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        )

        last_error: BaseException | None = None
        for candidate in [self.model, *self.fallback_models]:
            try:
                response = self._client.models.generate_content(
                    model=candidate, contents=contents, config=config
                )
            except BaseException as exc:  # noqa: BLE001
                last_error = exc
                if _is_unavailable(exc):
                    continue  # next model; retrying this one harder will not help
                raise
            self.last_model_used = candidate
            text = (response.text or "").strip()
            usage = _google_usage(response)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                return LLMResult(
                    data={},
                    usage=usage,
                    provider=self.provider,
                    model=candidate,
                    raw_text=text,
                    ok=False,
                    error=f"response was not valid JSON: {exc}",
                )
            return LLMResult(
                data=data, usage=usage, provider=self.provider, model=candidate, raw_text=text
            )

        raise RuntimeError(
            f"every Gemini model returned 503: {[self.model, *self.fallback_models]}"
        ) from last_error

    def list_models(self) -> list[str]:
        """Confirms the configured model exists before a twenty-case run."""
        return [m.name for m in self._client.models.list()]


def _google_usage(response: Any) -> Usage:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return Usage(estimated=True, note="google: no usage_metadata on the response, counts unavailable")
    prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
    completion = int(getattr(meta, "candidates_token_count", 0) or 0)
    # Reasoning tokens are billed and counted, so they belong in the total.
    thoughts = int(getattr(meta, "thoughts_token_count", 0) or 0)
    total = int(getattr(meta, "total_token_count", 0) or 0) or (prompt + completion + thoughts)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion + thoughts,
        total_tokens=total,
        estimated=False,
    )


class OpenAIBackend(LLMBackend):
    """OpenAI through the openai SDK, kept thin and unused unless LLM_PROVIDER changes."""

    provider = "openai"

    def __init__(self, api_key: str, model: str, temperature: float = 0.2, max_output_tokens: int = 2048) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def complete_structured(
        self, system: str, messages: list[Message], schema: dict[str, Any], cache_hint: str = ""
    ) -> LLMResult:
        strict_schema = _strictify(schema)
        name = "response_" + hashlib.sha1(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:8]
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
            messages=[{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": name, "schema": strict_schema, "strict": True},
            },
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        try:
            data = json.loads(text)
            ok, error = True, ""
        except json.JSONDecodeError as exc:
            data, ok, error = {}, False, f"response was not valid JSON: {exc}"
        return LLMResult(
            data=data,
            usage=Usage(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
                estimated=usage is None,
                note="" if usage else "openai: no usage on the response, counts unavailable",
            ),
            provider=self.provider,
            model=self.model,
            raw_text=text,
            ok=ok,
            error=error,
        )


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict mode wants additionalProperties false and every key required."""
    if schema.get("type") != "object":
        if schema.get("type") == "array" and "items" in schema:
            return {**schema, "items": _strictify(schema["items"])}
        return schema
    props = {k: _strictify(v) for k, v in schema.get("properties", {}).items()}
    return {
        **schema,
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
    }
