"""Simulated evidence responses, per backend/contracts/evidence_simulation.md.

The dataset supplies no customer or analyst replies. The simulated reply is a
pure function of what the graph already showed: no randomness, no model call.
Two reasons, both in the contract. The 20 answer files have to be reproducible.
And a blanket "customer always denies" would push nearly every case to
BLOCK_CARD plus FILE_REPORT, which is the failure mode the README warns about.

Ordering is fixed: `confirmed` is tested before `denied`, because a
recurring-charge match is the cheapest way to clear a case and R7 forbids
blocking there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.models.enums import CustomerResponse, EvidenceRequestType

# A recurring match at or above this strength settles the case the cheap way.
CONFIRMED_RECURRENCE = 0.55
# Denial requires a matched pattern plus a genuinely independent second signal.
DENIED_PATTERN_SCORE = 0.55


@dataclass
class SimulatedResponse:
    response: CustomerResponse
    assumed_response: str
    basis: list[str] = field(default_factory=list)
    request_type: EvidenceRequestType = EvidenceRequestType.CUSTOMER_VALIDATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response.value,
            "assumed_response": self.assumed_response,
            "basis": self.basis,
            "type": self.request_type.value,
        }


@dataclass
class EvidenceContext:
    """The case state the rule reads. Nothing outside this is consulted."""

    pattern_score: float = 0.0
    pattern_name: str = "none"
    recurring_strength: float = 0.0
    recurring_matches: list[str] = field(default_factory=list)
    shared_device_ring: bool = False
    ring_member_count: int = 0
    confirmed_fraud_prior_on_connected_entity: bool = False
    reads_as_trip: bool = False
    region_span_days: float = 0.0
    competing_legitimate_explanation: bool = False
    independent_signals: int = 0
    exposure_usd: float = 0.0


def _second_signal(ctx: EvidenceContext) -> str | None:
    if ctx.shared_device_ring and ctx.ring_member_count >= 2:
        return f"a device profile shared with {ctx.ring_member_count} other cards"
    if ctx.confirmed_fraud_prior_on_connected_entity:
        return "a confirmed-fraud closed case on a connected entity"
    if ctx.independent_signals >= 2:
        return f"{ctx.independent_signals} independent signals pointing the same way"
    return None


def simulate_customer_response(
    ctx: EvidenceContext, request_type: EvidenceRequestType = EvidenceRequestType.CUSTOMER_VALIDATION
) -> SimulatedResponse:
    # 1. confirmed, tested first
    if ctx.recurring_strength >= CONFIRMED_RECURRENCE:
        return SimulatedResponse(
            response=CustomerResponse.CONFIRMED,
            assumed_response=(
                "Assumed the cardholder confirms the charge. The flagged amount and product code repeat on "
                f"this card at a monthly cadence across {len(ctx.recurring_matches)} earlier transactions, which "
                "is the cardholder's own recurring pattern rather than an unrecognized charge."
            ),
            basis=["recurring_charge_match", *ctx.recurring_matches[:4]],
            request_type=request_type,
        )
    if ctx.reads_as_trip:
        return SimulatedResponse(
            response=CustomerResponse.CONFIRMED,
            assumed_response=(
                "Assumed the cardholder confirms the purchases and states they were travelling. "
                + (
                    f"Activity in the new billing region runs across {ctx.region_span_days:.0f} days with no "
                    "overlapping activity in the home region, which is a trip rather than a cloned card."
                    if ctx.region_span_days >= 1
                    else "The charge is billed in a region this card rarely uses, with no activity elsewhere in "
                    "the same twelve hours, which is a trip rather than a cloned card."
                )
            ),
            basis=["away_from_home_region", "no_overlapping_home_activity"],
            request_type=request_type,
        )

    # 2. denied, needs a matched pattern and a genuinely independent second signal
    second = _second_signal(ctx)
    if (
        ctx.pattern_score >= DENIED_PATTERN_SCORE
        and second is not None
        and not ctx.competing_legitimate_explanation
    ):
        return SimulatedResponse(
            response=CustomerResponse.DENIED,
            assumed_response=(
                "Assumed the cardholder denies the transaction and still holds the card. The activity matches the "
                f"{ctx.pattern_name} pattern and is corroborated by {second}, with no competing legitimate "
                "explanation in the cardholder's own history."
            ),
            basis=[f"pattern:{ctx.pattern_name}", second],
            request_type=request_type,
        )

    # 3. no reply, the honest output when the evidence is thin or conflicting
    return SimulatedResponse(
        response=CustomerResponse.NO_REPLY,
        assumed_response=(
            "Assumed no reply within 24 hours. The evidence is thin or conflicting: the leading pattern scores "
            f"{ctx.pattern_score:.2f} with no independent corroboration, and there is no recurring-charge or "
            "travel explanation either, so neither a denial nor a confirmation can be assumed."
        ),
        basis=["insufficient_corroboration"],
        request_type=request_type,
    )


def choose_request_type(ctx: EvidenceContext, trigger_type: str) -> EvidenceRequestType:
    """Which of the three allowed requests fits the case.

    Step-up authentication is the right ask when the question is whether the
    person at the keyboard is the cardholder. Validation is the right ask when
    the question is whether a specific charge is theirs. An analyst request is
    for a ring, where the answer is not the cardholder's to give.
    """
    if ctx.shared_device_ring and ctx.ring_member_count >= 3:
        return EvidenceRequestType.ANALYST_INFO
    if trigger_type == "customer_report":
        return EvidenceRequestType.CUSTOMER_VALIDATION
    if ctx.pattern_name in {"card_testing", "account_takeover", "card_not_present_new_device"}:
        return EvidenceRequestType.STEP_UP_AUTH
    return EvidenceRequestType.CUSTOMER_VALIDATION
