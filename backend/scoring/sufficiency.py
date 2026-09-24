"""Per-pattern sufficiency checklists, and the unknowns list.

Before the agent may recommend a customer-impacting action it has to have
actually looked at the things the leading typology rests on. The checklist is
what makes "we did not check" visible instead of silently reading as "it was not
there", and it feeds both `confidence` and the always-populated `unknowns` list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.models.enums import Pattern

# Evidence each pattern needs before an action heavier than verification is
# defensible. Names match the tool that supplies them.
REQUIRED_EVIDENCE: dict[str, list[str]] = {
    Pattern.CARD_TESTING.value: [
        "card_window",
        "velocity",
        "pattern_match:card_testing",
        "behavior_shift",
    ],
    Pattern.CARD_NOT_PRESENT_FRAUD.value: [
        "tx_context",
        "customer_profile",
        "behavior_shift",
        "pattern_match:card_not_present_fraud",
    ],
    Pattern.CARD_NOT_PRESENT_NEW_DEVICE.value: [
        "tx_context",
        "behavior_shift",
        "shared_device_profile",
        "pattern_match:card_not_present_new_device",
    ],
    Pattern.OUT_OF_REGION_USE.value: [
        "tx_context",
        "region_history",
        "customer_profile",
        "pattern_match:out_of_region_use",
    ],
    Pattern.ACCOUNT_TAKEOVER.value: [
        "tx_context",
        "customer_profile",
        "behavior_shift",
        "pattern_match:account_takeover",
    ],
    "undocumented_structuring": [
        "card_window",
        "similar_cases",
        "pattern_match:undocumented_structuring",
    ],
    "undocumented_proxy_device_ring": [
        "shared_device_profile",
        "ring_detect",
        "prior_cases_for_entities",
        "pattern_match:undocumented_proxy_device_ring",
    ],
    Pattern.NONE.value: ["tx_context", "customer_profile", "behavior_shift"],
}

# Actions that change something for the cardholder. These are the ones the
# checklist gates.
IMPACTING_ACTIONS = {"DECLINE_TRANSACTION", "BLOCK_CARD", "BLOCK_ALL_CARDS"}


@dataclass
class Sufficiency:
    pattern: str
    required: list[str]
    satisfied: list[str]
    missing: list[str]
    coverage: float
    sufficient_for_impacting_action: bool
    unknowns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "required": self.required,
            "satisfied": self.satisfied,
            "missing": self.missing,
            "coverage": self.coverage,
            "sufficient_for_impacting_action": self.sufficient_for_impacting_action,
            "unknowns": self.unknowns,
        }


def check_sufficiency(
    pattern: str,
    tools_called: list[str],
    pattern_coverage: float,
    uncheckable_signals: list[str] | None = None,
) -> Sufficiency:
    required = REQUIRED_EVIDENCE.get(pattern, REQUIRED_EVIDENCE[Pattern.NONE.value])
    called = set(tools_called)
    satisfied, missing = [], []
    for item in required:
        base = item.split(":")[0]
        if item in called or base in called:
            satisfied.append(item)
        else:
            missing.append(item)
    coverage = round(len(satisfied) / len(required), 3) if required else 0.0
    # Both halves have to hold: the agent ran the queries, and the queries came
    # back with something to read.
    sufficient = not missing and pattern_coverage >= 0.75
    return Sufficiency(
        pattern=pattern,
        required=required,
        satisfied=satisfied,
        missing=missing,
        coverage=coverage,
        sufficient_for_impacting_action=sufficient,
        unknowns=list(uncheckable_signals or []),
    )


def build_unknowns(
    sufficiency: Sufficiency,
    uncheckable_signals: list[str],
    has_identity: bool,
    has_region: bool,
    prior_txns: int,
    customer_response: str | None,
    conflicting: bool,
    similar_case_count: int,
) -> list[str]:
    """Always returns at least one entry.

    An empty unknowns list would be a claim that the agent knows everything,
    which is never true here: the dataset supplies no merchant, no customer
    reply and no names behind the V, C, D and M features.
    """
    unknowns: list[str] = []
    for item in sufficiency.missing:
        unknowns.append(f"Evidence not gathered: {item}")
    for sig in uncheckable_signals:
        unknowns.append(f"Signal could not be checked on the available data: {sig}")
    if not has_identity:
        unknowns.append(
            "No identity record on the flagged transaction, so device, proxy and screen are unknown"
        )
    if not has_region:
        unknowns.append(
            "No billing region recorded on the flagged transaction, so out-of-region reasoning is unavailable"
        )
    if prior_txns < 8:
        unknowns.append(
            f"Only {prior_txns} prior transactions on this card, too thin for a reliable amount baseline"
        )
    if customer_response is None:
        unknowns.append("Cardholder has not been asked whether they made the transaction")
    elif customer_response == "no_reply":
        unknowns.append("Cardholder did not reply, so the dispute is unresolved")
    if conflicting:
        unknowns.append("Inculpatory and exculpatory signals are close to balanced")
    if similar_case_count == 0:
        unknowns.append("No closed case on these entities to compare against")
    # Always true of this dataset, and worth saying rather than implying the
    # agent had merchant-level detail it never had.
    unknowns.append(
        "The dataset carries no merchant name, so 'same merchant' recurrence is inferred from "
        "amount, product code and cadence rather than observed directly"
    )
    return unknowns
