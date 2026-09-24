"""Policy engine. Reads backend/contracts/policy.yaml, decides routes, applies
R1 to R10, and gates execution.

Two things this file guarantees:

1. Routing is a function of case state, not a lookup. BLOCK_CARD is L1 at or
   below $2,500 of exposure and L2 above it, so the same action name routes two
   different ways in the same run.
2. Only `auto` actions may execute. L1 and L2 are recorded as recommendations
   with the route stated and are never executed. Every attempt is logged with
   `authorized` true or false, including the blocked ones, so a refused action
   is still visible in the case record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.config import get_settings
from backend.models.answer import RecommendedAction
from backend.models.enums import Action, CustomerResponse, Route, Verdict

BLOCK_CARD_L1_CEILING = 2500.0
SAR_EXPOSURE_TRIGGER = 1000.0
R4_ESCALATION_EXPOSURE = 500.0
R8_EXPOSURE = 500.0
R1_PROBABILITY = 0.70
R5_CLEARED_PURCHASE = 100.0
CASE_OPEN_PROBABILITY = 0.30


@lru_cache(maxsize=1)
def load_policy() -> dict[str, Any]:
    path: Path = get_settings().contracts_dir / "policy.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class CaseState:
    """Everything a policy predicate is allowed to look at.

    Keeping this explicit is what makes R1 to R10 unit-testable without building
    a whole InternalCase.
    """

    fraud_probability: float = 0.0
    confidence: float = 0.0
    verdict: str = Verdict.UNCERTAIN.value
    exposure_usd: float = 0.0
    pattern: str = "none"
    single_signal: bool = True
    evidence_conflicts: bool = False
    customer_response: str | None = None
    customer_disputes: bool = False
    recurring_charge_match: bool = False
    card_testing_sequence: bool = False
    cleared_purchase_over_100: bool = False
    shared_device_profile: bool = False
    shared_region_cluster: bool = False
    linked_to_other_card_fraud: bool = False
    shared_element: str = ""
    connected_card_ids: list[str] = field(default_factory=list)
    coordinated_or_undocumented: bool = False
    confirmed_fraud_card_count: int = 0
    credentials_confirmed_compromised: bool = False
    evidence_requested: bool = False
    sufficient_for_impacting_action: bool = True
    pending_authorization: bool = False


@dataclass
class RuleHit:
    rule: str
    title: str
    actions: list[str]
    reason: str


@dataclass
class AuthorizationRecord:
    action: str
    route: str
    authorized: bool
    executed: bool
    reason: str
    actor: str


# ---------------------------------------------------------------------------
# routing


def route_for(action: str, state: CaseState) -> Route:
    """The approval route for one action given the case state."""
    policy = load_policy()
    routing = policy["routing"]
    if action == Action.BLOCK_CARD.value:
        return Route.L1 if state.exposure_usd <= BLOCK_CARD_L1_CEILING else Route.L2
    if action in routing["l2"]:
        return Route.L2
    if action in routing["l1"]:
        return Route.L1
    if action in routing["auto"]:
        return Route.AUTO
    raise ValueError(f"{action} is not a policy action")


def may_execute(action: str, state: CaseState) -> bool:
    return route_for(action, state) is Route.AUTO


# ---------------------------------------------------------------------------
# rules R1 to R10, each a predicate plus the actions it recommends


def r1_weak_signal(state: CaseState) -> RuleHit | None:
    """Verify before you block on a weak signal."""
    if state.single_signal and state.fraud_probability < R1_PROBABILITY:
        return RuleHit(
            "R1",
            "Verify before you block on a weak signal",
            [Action.VERIFY_WITH_CUSTOMER.value, Action.STEP_UP_AUTH.value],
            f"R1: the case rests on a single signal and probability is {state.fraud_probability:.2f}, "
            "below 0.70, so verification comes before any block",
        )
    return None


def r1_forbids_block(state: CaseState) -> bool:
    """True when R1 bars a block outright."""
    return state.single_signal and state.fraud_probability < R1_PROBABILITY


def r2_customer_denies(state: CaseState) -> RuleHit | None:
    if state.customer_response != CustomerResponse.DENIED.value:
        return None
    actions = [Action.BLOCK_CARD.value, Action.CREATE_CASE.value]
    reason = "R2: the cardholder states they did not make the transaction"
    if r2_add_file_report(state):
        actions.append(Action.FILE_REPORT.value)
        reason += "; exposure or a shared link meets the reporting threshold"
    return RuleHit("R2", "Customer denies the transaction", actions, reason)


def r2_add_file_report(state: CaseState) -> bool:
    return (
        state.exposure_usd > SAR_EXPOSURE_TRIGGER
        or state.shared_device_profile
        or state.linked_to_other_card_fraud
    )


def r3_customer_confirms(state: CaseState) -> RuleHit | None:
    if state.customer_response != CustomerResponse.CONFIRMED.value:
        return None
    return RuleHit(
        "R3",
        "Customer confirms the transaction",
        [Action.CLOSE_NO_FRAUD.value],
        "R3: the cardholder confirms the transaction, so the alert closes as legitimate",
    )


def r4_no_reply(state: CaseState) -> RuleHit | None:
    if state.customer_response != CustomerResponse.NO_REPLY.value:
        return None
    actions = [Action.MONITOR_CARD.value]
    if state.pending_authorization:
        actions.append(Action.DECLINE_TRANSACTION.value)
    reason = "R4: no reply within 24 hours"
    if state.exposure_usd > R4_ESCALATION_EXPOSURE:
        actions.append(Action.ESCALATE_TO_ANALYST.value)
        reason += f"; exposure ${state.exposure_usd:,.2f} is over $500 so the case escalates"
    return RuleHit("R4", "No reply within 24 hours", actions, reason)


def r5_card_testing(state: CaseState) -> RuleHit | None:
    if not state.card_testing_sequence:
        return None
    actions = [Action.DECLINE_TRANSACTION.value, Action.STEP_UP_AUTH.value]
    reason = (
        "R5: three or more small online authorizations on this card within an hour followed by a "
        "larger purchase"
    )
    if state.cleared_purchase_over_100:
        actions.insert(0, Action.BLOCK_CARD.value)
        reason += "; a purchase over $100 has already cleared"
    return RuleHit("R5", "Card testing", actions, reason)


def r6_shared_origin(state: CaseState) -> RuleHit | None:
    shared = state.shared_device_profile or state.shared_region_cluster or state.linked_to_other_card_fraud
    if not (shared and state.linked_to_other_card_fraud):
        # R6 is about several cards showing fraud from one origin. A shared
        # element with no fraud on the other cards is not R6, and treating it as
        # one would fire on almost every online case in this dataset.
        return None
    element = state.shared_element or "a shared origin"
    return RuleHit(
        "R6",
        "Shared origin",
        [Action.CREATE_CASE.value, Action.FILE_REPORT.value, Action.MONITOR_CONNECTED_CARDS.value],
        f"R6: several cards show fraud from {element}",
    )


def r7_disputed_but_legitimate(state: CaseState) -> RuleHit | None:
    if not (state.customer_disputes and state.recurring_charge_match):
        return None
    return RuleHit(
        "R7",
        "Disputed but legitimate",
        [Action.CREATE_CASE.value, Action.VERIFY_WITH_CUSTOMER.value, Action.WARN_CUSTOMER.value],
        "R7: the disputed charge matches this card's own recurring pattern, so the card is not blocked",
    )


def r7_forbids_block(state: CaseState) -> bool:
    return bool(state.customer_disputes and state.recurring_charge_match)


def r8_uncertain_and_exposed(state: CaseState) -> RuleHit | None:
    if state.verdict != Verdict.UNCERTAIN.value:
        return None
    if not (state.exposure_usd > R8_EXPOSURE or state.evidence_conflicts):
        return None
    why = (
        f"exposure ${state.exposure_usd:,.2f} is over $500"
        if state.exposure_usd > R8_EXPOSURE
        else "the evidence conflicts"
    )
    return RuleHit(
        "R8",
        "Escalate when uncertain and exposed",
        [Action.ESCALATE_TO_ANALYST.value],
        f"R8: the verdict is uncertain and {why}",
    )


def r9_undocumented(state: CaseState) -> RuleHit | None:
    if not (state.pattern == "undocumented" and state.coordinated_or_undocumented):
        return None
    return RuleHit(
        "R9",
        "Undocumented patterns",
        [Action.CREATE_CASE.value, Action.FILE_REPORT.value, Action.ESCALATE_TO_ANALYST.value],
        "R9: the evidence shows coordinated or repeated abuse that fits none of the five documented patterns",
    )


def r10_block_all_cards_allowed(state: CaseState) -> bool:
    """R10 is a gate, not a recommendation. It only ever says no."""
    return state.confirmed_fraud_card_count >= 2 or state.credentials_confirmed_compromised


def r10_violation(actions: list[str], state: CaseState) -> bool:
    return Action.BLOCK_ALL_CARDS.value in actions and not r10_block_all_cards_allowed(state)


ALL_RULES = (
    r1_weak_signal,
    r2_customer_denies,
    r3_customer_confirms,
    r4_no_reply,
    r5_card_testing,
    r6_shared_origin,
    r7_disputed_but_legitimate,
    r8_uncertain_and_exposed,
    r9_undocumented,
)


def evaluate_rules(state: CaseState) -> list[RuleHit]:
    return [hit for rule in ALL_RULES if (hit := rule(state)) is not None]


# ---------------------------------------------------------------------------
# case and SAR predicates


def should_create_case(state: CaseState) -> bool:
    return (
        state.fraud_probability >= CASE_OPEN_PROBABILITY
        or state.evidence_requested
        or state.customer_disputes
    )


def sar_decision(state: CaseState) -> tuple[bool, str]:
    """File when fraud is confirmed or strongly suspected AND a trigger holds.

    The bank's own history is unambiguous on the trigger half: across all 4,665
    confirmed cases, a report was filed exactly when exposure exceeded $1,000 or
    the case named connected cards, with no exceptions. That reproduces policy
    3a's first two triggers, and 3a adds the shared-origin and undocumented ones.
    """
    suspected = state.verdict == Verdict.FRAUD.value or state.fraud_probability >= 0.70
    if not suspected:
        return False, (
            f"Policy 3a: a report requires fraud confirmed or strongly suspected. Assessed probability "
            f"{state.fraud_probability:.2f} with verdict {state.verdict}, so no report."
        )

    triggers: list[str] = []
    if state.exposure_usd > SAR_EXPOSURE_TRIGGER:
        triggers.append(f"exposure ${state.exposure_usd:,.2f} exceeds $1,000")
    if state.connected_card_ids:
        triggers.append(f"the case names {len(state.connected_card_ids)} connected card(s)")
    if state.shared_device_profile:
        triggers.append("the activity connects to a shared device profile")
    if state.shared_region_cluster:
        triggers.append("the activity connects to a shared region cluster")
    if state.linked_to_other_card_fraud:
        triggers.append("the activity connects to another customer's fraud")
    if state.coordinated_or_undocumented:
        triggers.append("the pattern is coordinated or undocumented, rule R9")

    if not triggers:
        return False, (
            "Policy 3a: fraud is suspected but no filing trigger holds. Exposure is at or below $1,000, "
            "no connected card, no shared origin and the pattern is documented. Case only, no report."
        )
    return True, "Policy 3a: " + "; ".join(triggers) + "."


# ---------------------------------------------------------------------------
# authorization


class PolicyEngine:
    """Gates execution and keeps the decision log."""

    def __init__(self, actor: str = "agent") -> None:
        self.actor = actor
        self.log: list[AuthorizationRecord] = []

    def authorize(self, action: str, state: CaseState, reason: str) -> AuthorizationRecord:
        route = route_for(action, state)
        authorized = route is Route.AUTO
        blocked_reason = reason
        if action == Action.BLOCK_ALL_CARDS.value and not r10_block_all_cards_allowed(state):
            authorized = False
            blocked_reason = (
                "R10: BLOCK_ALL_CARDS refused. Fewer than two of the customer's cards show confirmed "
                "fraud and credentials are not confirmed compromised."
            )
        if action in {Action.BLOCK_CARD.value, Action.BLOCK_ALL_CARDS.value} and r7_forbids_block(state):
            authorized = False
            blocked_reason = "R7: a block is refused on a charge matching the cardholder's own recurring pattern."
        if action in {Action.BLOCK_CARD.value, Action.BLOCK_ALL_CARDS.value} and r1_forbids_block(state):
            authorized = False
            blocked_reason = (
                f"R1: a block is refused on a single signal at probability {state.fraud_probability:.2f}."
            )
        record = AuthorizationRecord(
            action=action,
            route=route.value,
            authorized=authorized,
            executed=False,
            reason=blocked_reason,
            actor=self.actor,
        )
        self.log.append(record)
        return record

    def recommend(self, action: str, state: CaseState, reason: str) -> RecommendedAction:
        """Build the graded recommendation object, with the route stated."""
        return RecommendedAction(action=action, route=route_for(action, state).value, reason=reason)


def filter_forbidden(actions: list[str], state: CaseState) -> tuple[list[str], list[str]]:
    """Strip actions the rules forbid outright, and say which went and why."""
    kept: list[str] = []
    dropped: list[str] = []
    for action in actions:
        if action == Action.BLOCK_ALL_CARDS.value and not r10_block_all_cards_allowed(state):
            dropped.append(f"{action} dropped by R10")
            continue
        if action in {Action.BLOCK_CARD.value, Action.BLOCK_ALL_CARDS.value} and r7_forbids_block(state):
            dropped.append(f"{action} dropped by R7")
            continue
        if action in {Action.BLOCK_CARD.value, Action.BLOCK_ALL_CARDS.value} and r1_forbids_block(state):
            dropped.append(f"{action} dropped by R1")
            continue
        if action not in kept:
            kept.append(action)
    return kept, dropped
