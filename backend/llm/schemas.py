"""JSON schemas for every structured LLM call, in one place.

Both real backends consume these unchanged: Gemini through
`response_json_schema`, OpenAI through `json_schema` response format. Keeping
one copy is what makes the provider swap a config change.

Quota is the scarce resource on the free tier, so the agent makes one narrative
call per case rather than one per node. SYNTHESIS_SCHEMA is that call: summary,
evidence phrasing, stop reason, the what-changed line and the SAR narrative all
come back together.
"""

from __future__ import annotations

from typing import Any

# Gemini rejects several JSON Schema keywords, so these stay to the common
# subset: type, properties, required, items, enum, description.


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": _str(
            "Two to six sentences an analyst could read. State what was found and what it means. "
            "Do not restate the probability as a certainty."
        ),
        "evidence_claims": {
            "type": "array",
            "description": "One rewritten claim per supplied finding, in the same order as the input findings.",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": _str("The id of the finding this claim rewrites"),
                    "claim": _str("One sentence stating what the evidence shows, in plain words"),
                },
                "required": ["finding_id", "claim"],
            },
        },
        "stop_reason": _str(
            "Why the investigation ended here. Name the confidence and what is still unknown."
        ),
        "what_changed": _str(
            "One or two sentences on why the final actions differ from the initial ones, "
            "or the single word nothing."
        ),
        "sar_narrative": _str(
            "Six to twelve sentences covering who, what, when, where, how and why it is suspicious. "
            "Empty string when no report is to be filed."
        ),
        "pattern_description": _str(
            "Two or three sentences describing the pattern in your own words. "
            "Empty string unless the pattern is undocumented."
        ),
    },
    "required": [
        "summary",
        "evidence_claims",
        "stop_reason",
        "what_changed",
        "sar_narrative",
        "pattern_description",
    ],
}

HYPOTHESIS_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agrees_with_leading_hypothesis": {"type": "boolean"},
        "reasoning": _str("Why the computed leading pattern does or does not fit the evidence"),
        "alternative_pattern": {
            "type": "string",
            "enum": [
                "card_testing",
                "card_not_present_fraud",
                "card_not_present_new_device",
                "out_of_region_use",
                "account_takeover",
                "undocumented",
                "none",
            ],
            "description": "The pattern you would name instead, or the same one if you agree",
        },
        "missing_evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What you would want checked before acting",
        },
    },
    "required": ["agrees_with_leading_hypothesis", "reasoning", "alternative_pattern", "missing_evidence"],
}

SCHEMAS = {
    "synthesis": SYNTHESIS_SCHEMA,
    "hypothesis_review": HYPOTHESIS_REVIEW_SCHEMA,
}
