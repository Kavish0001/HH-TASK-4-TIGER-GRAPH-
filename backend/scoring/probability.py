"""fraud_probability: a documented log-odds blend.

Computed in Python, never by the LLM. Every component carries its raw value, the
neutral anchor at which it says nothing, its weight in log-odds units and the
resulting contribution, and all of that is written into
InternalCase.risk_assessment so a human can audit the number.

Why log-odds rather than a weighted average. A weighted average of six signals
that are each near their anchor lands near the middle of the range, which is how
an agent ends up recommending a block on every case. Log-odds starts from an
explicit prior set by the trigger and lets each signal move it by an amount
proportional to how far it is from neutral, in either direction. Exculpatory
signals carry negative weights for the same reason: 716 of the 900 cleared cases
in the bank's history were a travelling cardholder and 158 were a new phone, so
the model has to be able to move down, not only up.

Anchors and weights were set from the base rates in plans/findings/dataset.md,
not tuned against an answer key we do not have.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from backend.models.enums import CustomerResponse, TriggerType
from backend.models.internal_case import ScoreComponent

# Starting odds before any evidence. A customer telling the bank they did not
# make a charge is a stronger starting point than a model score, but it is not
# proof: R7 exists because some disputes are the cardholder's own recurring
# charge.
PRIOR_BY_TRIGGER: dict[str, float] = {
    TriggerType.RISK_SCORE.value: 0.22,
    TriggerType.CUSTOMER_REPORT.value: 0.45,
    TriggerType.ANALYST_REQUEST.value: 0.40,
}

# name -> (anchor, weight in log-odds units)
WEIGHTS: dict[str, tuple[float, float]] = {
    # The bank model is useful and wrong in both directions. 6.2 percent of
    # confirmed-fraud transactions score below 0.10, so this is deliberately the
    # smallest inculpatory weight in the blend and a low score barely suppresses.
    "bank_risk_score": (0.30, 1.2),
    # The graph analysis doing the work: how well the leading typology fits.
    "pattern_match": (0.35, 3.0),
    # A shared device is only evidence when the profile is specific, new on the
    # accounts it touches and behind a proxy.
    "shared_device_ring": (0.20, 1.2),
    "behaviour_shift": (0.35, 1.4),
    # Outcomes of retrieved prior cases on the same entities, shrunk toward the
    # base rate so one prior case cannot carry a verdict.
    "similar_case_fraud_rate": (0.50, 1.0),
    # Exculpatory.
    "recurring_charge_match": (0.0, -2.6),
    "travel_explanation": (0.0, -1.8),
}

# Applied after the customer or analyst reply comes back.
RESPONSE_SHIFT: dict[str, float] = {
    CustomerResponse.DENIED.value: 1.7,
    CustomerResponse.CONFIRMED.value: -2.4,
    CustomerResponse.NO_REPLY.value: 0.0,
}

FLOOR, CEILING = 0.02, 0.97


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def shrink_rate(hits: int, total: int, base: float = 0.5, strength: float = 4.0) -> float:
    """Pull a small-sample rate toward the base rate.

    Two prior cases both confirmed fraud is not a 100 percent fraud rate, and
    treating it as one is how an agent talks itself into blocking.
    """
    if total <= 0:
        return base
    return (hits + base * strength) / (total + strength)


@dataclass
class ProbabilityResult:
    probability: float
    prior: float
    components: list[ScoreComponent]
    logit_total: float
    method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": round(self.probability, 3),
            "prior": round(self.prior, 3),
            "logit_total": round(self.logit_total, 3),
            "method": self.method,
            "components": [c.model_dump() for c in self.components],
        }


def compute_probability(
    trigger_type: str,
    signals: dict[str, float],
    observed: dict[str, bool] | None = None,
    details: dict[str, str] | None = None,
    customer_response: str | None = None,
) -> ProbabilityResult:
    """Blend the signals into a probability.

    `signals` maps component name to a 0..1 value. A component missing from
    `signals`, or present with observed=False, contributes exactly zero: an
    unobserved signal must not act like a signal measured at its anchor plus
    noise, and it must not silently push the case either way.
    """
    observed = observed or {}
    details = details or {}
    prior = PRIOR_BY_TRIGGER.get(trigger_type, 0.30)
    total = _logit(prior)
    components: list[ScoreComponent] = []

    for name, (anchor, weight) in WEIGHTS.items():
        was_observed = observed.get(name, name in signals)
        value = float(signals.get(name, anchor))
        value = min(max(value, 0.0), 1.0)
        contribution = weight * (value - anchor) if was_observed else 0.0
        total += contribution
        components.append(
            ScoreComponent(
                name=name,
                value=round(value, 3),
                weight=abs(weight),
                contribution=round(contribution, 3),
                observed=was_observed,
                detail=details.get(name, ""),
            )
        )

    if customer_response:
        shift = RESPONSE_SHIFT.get(customer_response, 0.0)
        total += shift
        components.append(
            ScoreComponent(
                name="customer_response",
                value=1.0 if customer_response == CustomerResponse.DENIED.value else 0.0,
                weight=abs(shift),
                contribution=round(shift, 3),
                observed=True,
                detail=f"assumed response: {customer_response}",
            )
        )

    probability = min(max(_sigmoid(total), FLOOR), CEILING)
    return ProbabilityResult(
        probability=probability,
        prior=prior,
        components=components,
        logit_total=total,
        method="log-odds blend from a trigger prior, weights in backend/scoring/probability.py",
    )
