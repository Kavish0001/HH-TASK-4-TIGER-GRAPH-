"""The fourteen policy actions, stubbed and logged.

These stand in for bank systems. Each one records what was attempted, the route
it needed, whether it was authorized and whether it ran. A refused action stays
in the record: an L2 block that the agent recommended and did not execute is
part of the investigation, not an error to be swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.models.enums import Action
from backend.policy.engine import CaseState, PolicyEngine, route_for


@dataclass
class ActionOutcome:
    action: str
    route: str
    authorized: bool
    executed: bool
    reason: str
    effect: str
    at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "route": self.route,
            "authorized": self.authorized,
            "executed": self.executed,
            "reason": self.reason,
            "effect": self.effect,
            "at": self.at.isoformat(),
        }


# What each stub would have done, phrased as the bank system would log it.
EFFECTS: dict[str, str] = {
    Action.ALLOW_TRANSACTION.value: "flagged authorization left to stand",
    Action.DECLINE_TRANSACTION.value: "flagged authorization declined, card left active",
    Action.MONITOR_CARD.value: "monitoring sensitivity raised on the card for 72 hours",
    Action.MONITOR_CONNECTED_CARDS.value: "monitoring raised on the cards sharing this origin",
    Action.WARN_CUSTOMER.value: "informational message queued to the cardholder",
    Action.VERIFY_WITH_CUSTOMER.value: "validation request sent, card active pending reply",
    Action.STEP_UP_AUTH.value: "one-time passcode required before further activity",
    Action.BLOCK_CARD.value: "card blocked and queued for reissue",
    Action.BLOCK_ALL_CARDS.value: "every card the customer holds blocked",
    Action.GENERATE_REPORT.value: "internal write-up produced, no case opened",
    Action.CREATE_CASE.value: "internal fraud case opened and written to the graph",
    Action.FILE_REPORT.value: "suspicious activity report submitted to the regulator",
    Action.ESCALATE_TO_ANALYST.value: "case handed to a human analyst with the evidence attached",
    Action.CLOSE_NO_FRAUD.value: "alert closed as legitimate",
}


class ActionRunner:
    """Executes `auto` actions and records the rest as recommendations."""

    def __init__(self, engine: PolicyEngine) -> None:
        self.engine = engine
        self.outcomes: list[ActionOutcome] = []

    def run(self, action: str, state: CaseState, reason: str) -> ActionOutcome:
        if action not in EFFECTS:
            raise ValueError(f"{action} is not a policy action")
        record = self.engine.authorize(action, state, reason)
        executed = record.authorized
        record.executed = executed
        effect = (
            EFFECTS[action]
            if executed
            else f"not executed; waits for {record.route} approval"
        )
        outcome = ActionOutcome(
            action=action,
            route=record.route,
            authorized=record.authorized,
            executed=executed,
            reason=record.reason,
            effect=effect,
        )
        self.outcomes.append(outcome)
        return outcome

    def run_all(self, actions: list[tuple[str, str]], state: CaseState) -> list[ActionOutcome]:
        return [self.run(action, state, reason) for action, reason in actions]

    def executed(self) -> list[str]:
        return [o.action for o in self.outcomes if o.executed]

    def blocked(self) -> list[ActionOutcome]:
        return [o for o in self.outcomes if not o.authorized]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [o.to_dict() for o in self.outcomes]


def preview_route(action: str, state: CaseState) -> str:
    return route_for(action, state).value
