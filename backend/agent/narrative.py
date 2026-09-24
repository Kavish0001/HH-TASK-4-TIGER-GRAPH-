"""Templated write-up, and the guarded merge of the LLM's version over it.

I build every free-text field deterministically first. The LLM is then allowed
to rewrite them, but its text only replaces the template when it passes checks:
no identifier the agent never saw, SAR length inside the six to twelve sentence
range, no em-dashes. That way a quota error, a 503 or a hallucinated card number
degrades the prose, never the answer file.
"""

from __future__ import annotations

import re
from typing import Any

PATTERN_LABEL = {
    "card_testing": "card testing",
    "card_not_present_fraud": "card-not-present fraud",
    "card_not_present_new_device": "card-not-present fraud from a new device",
    "out_of_region_use": "out-of-region use",
    "account_takeover": "account takeover",
    "undocumented": "undocumented coordinated abuse",
    "none": "no fraud pattern",
}

# Anything shaped like a dataset identifier. If the LLM writes one of these that
# is not in the allowed set, I throw its text away rather than risk a made-up ID
# reaching a graded field.
_ID_PATTERNS = [
    re.compile(r"\bC\d{5}-K\d+\b"),
    re.compile(r"\bC\d{5}\b"),
    re.compile(r"\bCC-\d{4}\b"),
    re.compile(r"\bHHG-\d{3}\b"),
    re.compile(r"\b\d{7}\b"),
]


def clean_text(text: str) -> str:
    """House rule: no em-dashes anywhere, including model output."""
    text = text.replace("\u2014", ", ").replace("\u2013", "-")
    return re.sub(r"\s+,", ",", text).strip()


def ids_in(text: str) -> set[str]:
    found: set[str] = set()
    for pat in _ID_PATTERNS:
        found.update(pat.findall(text))
    # A card id also contains a customer id; count the customer as seen.
    for card in [f for f in found if "-K" in f]:
        found.add(card.split("-K")[0])
    return found


def ids_allowed(text: str, allowed: set[str]) -> bool:
    expanded = set(allowed)
    for a in list(allowed):
        if "-K" in a:
            expanded.add(a.split("-K")[0])
    return ids_in(text) <= expanded


def sentence_count(text: str) -> int:
    parts = [p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$])", text.strip()) if p.strip()]
    return len(parts)


def money(x: float) -> str:
    return f"${x:,.2f}"


def join_list(items: list[str], limit: int = 6) -> str:
    items = list(items)
    if not items:
        return ""
    shown = items[:limit]
    extra = len(items) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text += f" and {extra} more"
    return text


def action_names(actions: list[Any]) -> list[str]:
    return [a.action if hasattr(a, "action") else a["action"] for a in actions]


def template_summary(ctx: dict[str, Any]) -> str:
    label = PATTERN_LABEL.get(ctx["pattern"], ctx["pattern"])
    s = [
        f"{ctx['trigger_phrase']} on card {ctx['card_id']}, transaction {ctx['txn_id']} ({money(ctx['amount'])}).",
    ]
    if ctx["verdict"] == "legitimate":
        s.append(
            f"The graph evidence does not support fraud: {ctx['leading_narrative'].rstrip('.')}."
        )
    else:
        s.append(f"The leading hypothesis is {label}: {ctx['leading_narrative'].rstrip('.')}.")
    if ctx.get("response_sentence"):
        s.append(ctx["response_sentence"])
    s.append(
        f"Assessed fraud probability is {ctx['p_final']:.2f} at confidence {ctx['conf_final']:.2f}, "
        f"verdict {ctx['verdict']}, exposure {money(ctx['exposure'])}."
    )
    s.append(f"Final recommendation: {join_list(ctx['final_actions'], 8)} ({ctx['rules_final'] or 'policy 3a'}).")
    return " ".join(s)


def template_stop_reason(ctx: dict[str, Any]) -> str:
    p, conf, indep = ctx["p_final"], ctx["conf_final"], ctx["independent"]
    unknown = ctx["main_unknown"]
    if ctx.get("budget_exhausted"):
        head = "The tool budget ran out before the checklist was complete, so the case goes to an analyst"
    elif ctx.get("response") in {"denied", "confirmed"}:
        head = (
            f"The assumed cardholder response ({ctx['response']}) settles the question under policy section 6"
        )
    elif p >= 0.85 and indep >= 2:
        head = f"Probability {p:.2f} is at or above 0.85 with {indep} independent pieces of evidence"
    elif p <= 0.15 and indep >= 2:
        head = f"Probability {p:.2f} is at or below 0.15 with {indep} independent pieces of evidence"
    elif ctx.get("response") == "no_reply":
        head = (
            "No reply was assumed, and further graph queries are unlikely to change the decision, so the case "
            "rests on R4 and R8 rather than on more querying"
        )
    else:
        head = "Further graph steps are unlikely to change the decision"
    return f"{head}. Confidence {conf:.2f}. Main open question: {unknown}"


def template_what_changed(ctx: dict[str, Any]) -> str:
    if not ctx.get("response"):
        return "nothing"
    init, final = ctx["initial_actions"], ctx["final_actions"]
    p0, p1 = ctx["p_initial"], ctx["p_final"]
    if abs(p1 - p0) < 0.005:
        moved = f"The assumed response ({ctx['response']}) left probability at {p1:.2f}"
    else:
        moved = f"The assumed response ({ctx['response']}) moved probability from {p0:.2f} to {p1:.2f}"
    if init == final:
        return f"{moved} and did not change the recommended actions."
    return (
        f"{moved}, so the recommendation moved from {join_list(init, 8)} to {join_list(final, 8)} "
        f"under {ctx['rules_final'] or 'policy 3a'}."
    )


def template_sar(ctx: dict[str, Any]) -> str:
    label = PATTERN_LABEL.get(ctx["pattern"], ctx["pattern"])
    d0, d1 = (ctx["activity_dates"] + ["", ""])[:2]
    when = f"on {d0}" if d0 == d1 else f"between {d0} and {d1}"
    s: list[str] = []
    s.append(
        f"{when[0].upper() + when[1:]}, card {ctx['card_id']} held under customer {ctx['customer_id']} was used "
        f"for {len(ctx['affected'])} transaction(s) totalling {money(ctx['exposure'])} that this investigation "
        f"attributes to a single episode of {label}."
    )
    s.append(
        f"The transactions are {join_list(ctx['affected'], 10)}, and the first suspicious one is "
        f"{ctx['first_txn']}."
    )
    where = f"They were {ctx['channel_text']} transactions under product code(s) {ctx['product_codes']}"
    if ctx.get("region"):
        where += f", billed in region code {ctx['region']}"
    if ctx.get("device_profile"):
        where += f", made from device profile {ctx['device_profile']}"
    s.append(where + ".")
    s.append(f"How it was carried out: {ctx['leading_narrative'].rstrip('.')}.")
    if ctx.get("shift_detail") and not ctx["shift_detail"].startswith("no material"):
        s.append(f"The activity departs from the card's own history: {ctx['shift_detail'].rstrip('.')}.")
    if ctx.get("connected_cards"):
        s.append(
            f"The same device profile was used by {len(ctx['connected_cards'])} other card(s) in the same period, "
            f"including {join_list(ctx['connected_cards'], 6)}, which points to a common actor across cardholders."
        )
    if ctx.get("prior_sentence"):
        s.append(ctx["prior_sentence"])
    if ctx.get("response_sentence"):
        s.append(ctx["response_sentence"])
    s.append(
        f"The bank's detection model scored the flagged transaction {ctx['risk_score']:.2f}; that score prompted "
        "the review and was not relied on as a finding."
    )
    s.append(f"It is reported because {ctx['sar_reason'].rstrip('.')}.")
    s.append(f"Actions recommended: {join_list(ctx['final_actions'], 8)}.")
    # Pad with a factual line if a sparse case came in under six sentences.
    if len(s) < 6:
        s.append(f"Assessed fraud probability is {ctx['p_final']:.2f} at confidence {ctx['conf_final']:.2f}.")
    return " ".join(s[:12])


# L1 and L2 actions are recommendations waiting on a human. Prose that says a
# card "has been blocked" or a report "was filed" states something the agent
# is not allowed to have done, so that text is rejected outright.
_EXECUTED_CLAIM = re.compile(
    r"(has been|have been|was|were|is now|are now)\s+(blocked|declined|filed|reissued|submitted)", re.I
)


def contradicts_response(text: str, response: str | None) -> bool:
    low = text.lower()
    if response == "denied":
        return bool(re.search(r"confirm(ed|s)?", low)) and "den" not in low
    if response == "confirmed":
        return bool(re.search(r"den(ied|ies|y)", low))
    return False


def merge_llm(
    template: dict[str, Any],
    llm_data: dict[str, Any],
    allowed_ids: set[str],
    sar_file: bool,
    response: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Take the LLM's field only when it is non-empty and passes the checks.

    Returns the merged fields and the list of fields that fell back, which goes
    into the case timeline so a reviewer can see where the model was overruled.
    """
    out = dict(template)
    rejected: list[str] = []
    for key in ("summary", "stop_reason", "what_changed", "pattern_description", "sar_narrative"):
        val = llm_data.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        val = clean_text(val)
        if _EXECUTED_CLAIM.search(val):
            rejected.append(f"{key}: claims an approval-gated action already happened")
            continue
        if key in ("what_changed", "summary", "stop_reason") and contradicts_response(val, response):
            rejected.append(f"{key}: contradicts the assumed response {response}")
            continue
        if not ids_allowed(val, allowed_ids):
            rejected.append(f"{key}: unknown identifier {sorted(ids_in(val) - allowed_ids)[:3]}")
            continue
        if key == "sar_narrative":
            if not sar_file:
                continue
            n = sentence_count(val)
            if n < 6 or n > 12:
                rejected.append(f"sar_narrative: {n} sentences, outside 6 to 12")
                continue
        if key == "pattern_description" and not template.get("pattern_description"):
            continue
        if key == "what_changed" and template.get("what_changed") == "nothing":
            continue
        if key == "summary" and sentence_count(val) > 6:
            rejected.append("summary: longer than six sentences")
            continue
        out[key] = val
    claims = {}
    for item in llm_data.get("evidence_claims") or []:
        if not isinstance(item, dict):
            continue
        fid, claim = str(item.get("finding_id", "")), item.get("claim", "")
        if claim and isinstance(claim, str):
            claim = clean_text(claim)
            if ids_allowed(claim, allowed_ids) and not _EXECUTED_CLAIM.search(claim):
                claims[fid] = claim
    out["evidence_claims"] = claims
    return out, rejected
