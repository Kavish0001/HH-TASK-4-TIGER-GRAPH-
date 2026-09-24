"""Turns gathered tool results into a RiskAssessment.

This is the seam between the graph evidence and the two numbers the decision
rests on. It is deliberately all arithmetic: the LLM writes the explanation, it
does not pick the probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backend.models.enums import Pattern
from backend.models.internal_case import Hypothesis, RiskAssessment
from backend.scoring.confidence import compute_confidence
from backend.scoring.patterns import PatternScore, score_all_patterns
from backend.scoring.probability import compute_probability, shrink_rate
from backend.scoring.sufficiency import Sufficiency, build_unknowns, check_sufficiency
from backend.tools.mock.store import Store

# Patterns the answer file is allowed to name. The two undocumented detectors
# map onto the schema's `undocumented` value.
UNDOCUMENTED_DETECTORS = {"undocumented_structuring", "undocumented_proxy_device_ring"}


MONTHLY_MIN_DAYS = 24
MONTHLY_MAX_DAYS = 37


@dataclass
class RecurringCharge:
    strength: float
    matches: list[str]
    detail: dict[str, Any]


def detect_recurring_charge(store: Store, card_id: str, txn_id: str, as_of: str) -> RecurringCharge:
    """R7's test, as close as this dataset allows.

    The policy says "same merchant, same amount, monthly". There is no merchant
    column, so the observable part is the amount repeating on the same product
    code at a monthly cadence on the same card. The evidence text says that is
    what was measured rather than claiming a merchant match.
    """
    row = store.txn(txn_id)
    if row is None:
        return RecurringCharge(0.0, [], {"reason": "transaction not found"})
    hist = store.card_txns(card_id, as_of)
    prior = hist[hist["TransactionID"] != int(txn_id)]
    amt = float(row["TransactionAmt"])
    tol = max(0.02 * amt, 0.5)
    same = prior[
        (prior["TransactionAmt"].sub(amt).abs() <= tol)
        & (prior["ProductCD"] == row["ProductCD"])
    ].sort_values("ts")

    if len(same) < 2:
        return RecurringCharge(
            0.0,
            [],
            {"n_same_amount_and_product": int(len(same)), "tolerance_usd": round(tol, 2)},
        )

    gaps = same["ts"].diff().dropna().dt.total_seconds() / 86400
    # Billing dates drift with weekends and month length, so a monthly beat is
    # 24 to 37 days rather than a strict 30.
    monthly = gaps[(gaps >= MONTHLY_MIN_DAYS) & (gaps <= MONTHLY_MAX_DAYS)]
    cadence_share = float(len(monthly) / len(gaps)) if len(gaps) else 0.0

    # The first version counted any three same-amount charges as recurring. On
    # a card with a thousand transactions that is chance, and it cleared ten of
    # the twenty pack cases on its own. What R7 describes is the disputed charge
    # itself arriving on a monthly beat after earlier ones, so I anchor on the
    # gap from the last matching charge to the flagged one.
    flagged_ts = pd.Timestamp(row["ts"])
    last_gap = (flagged_ts - same["ts"].iloc[-1]).total_seconds() / 86400
    flagged_on_beat = MONTHLY_MIN_DAYS <= last_gap <= MONTHLY_MAX_DAYS
    # Expected chance matches: how many same-amount charges a card this busy
    # would show anyway. Dense cards need a longer chain to count.
    density = len(same) / max(len(prior), 1)

    strength = 0.0
    if flagged_on_beat and len(monthly) >= 2:
        strength = 0.85
    elif flagged_on_beat and len(monthly) >= 1:
        strength = 0.6
    elif flagged_on_beat:
        strength = 0.35
    elif len(monthly) >= 2 and cadence_share >= 0.5:
        strength = 0.25
    if density > 0.05 and strength < 0.85:
        strength *= 0.5

    return RecurringCharge(
        strength=round(strength, 3),
        matches=[str(int(t)) for t in same["TransactionID"]][-6:],
        detail={
            "n_same_amount_and_product": int(len(same)),
            "tolerance_usd": round(tol, 2),
            "median_gap_days": round(float(gaps.median()), 1) if len(gaps) else None,
            "monthly_cadence_share": round(cadence_share, 3),
            "days_since_last_match": round(float(last_gap), 1),
            "flagged_on_monthly_beat": bool(flagged_on_beat),
            "note": "no merchant column in this dataset; recurrence inferred from amount, product code and cadence",
        },
    )


def behaviour_shift_value(shift: dict[str, Any]) -> tuple[float, str]:
    """Collapse the behavior_shift tool output into one 0..1 signal."""
    if not shift:
        return 0.35, "behaviour baseline unavailable"
    score = 0.0
    parts: list[str] = []
    pct = shift.get("amount_percentile")
    if pct is not None:
        if pct >= 0.98:
            score += 0.34
            parts.append(f"amount at the {pct:.0%} percentile of the card's history")
        elif pct >= 0.90:
            score += 0.20
            parts.append(f"amount at the {pct:.0%} percentile")
        elif pct <= 0.50:
            score -= 0.06
    if shift.get("new_product_cd"):
        score += 0.16
        parts.append("product code never used on this card")
    if shift.get("new_device_profile"):
        score += 0.14
        parts.append("device profile new to this card")
    if shift.get("new_billing_region"):
        score += 0.12
        parts.append("billing region new to this card")
    if shift.get("new_channel"):
        score += 0.12
        parts.append("channel new to this card")
    if shift.get("unusual_hour"):
        score += 0.08
        parts.append("hour of day outside the card's usual pattern")
    value = min(max(0.35 + score, 0.0), 1.0)
    return round(value, 3), "; ".join(parts) or "no material deviation from the card's baseline"


def pick_leading(scores: list[PatternScore]) -> PatternScore:
    return scores[0] if scores else PatternScore(pattern=Pattern.NONE.value, score=0.0)


def answer_pattern_for(detector: str) -> str:
    if detector in UNDOCUMENTED_DETECTORS:
        return Pattern.UNDOCUMENTED.value
    return detector


@dataclass
class Assessment:
    risk: RiskAssessment
    hypotheses: list[Hypothesis]
    leading: PatternScore
    sufficiency: Sufficiency
    recurring: RecurringCharge
    answer_pattern: str


def assess_case(
    store: Store,
    *,
    trigger_type: str,
    card_id: str,
    txn_id: str,
    as_of: str,
    tools_called: list[str],
    shift: dict[str, Any],
    prior_cases: dict[str, Any],
    ring: dict[str, Any],
    independent_evidence: int,
    customer_response: str | None = None,
) -> Assessment:
    row = store.txn(txn_id)
    scores = score_all_patterns(store, card_id, txn_id, as_of)
    leading = pick_leading(scores)
    recurring = detect_recurring_charge(store, card_id, txn_id, as_of)

    prior_txns = int(len(store.card_txns(card_id, as_of))) - 1
    has_identity = bool(store.identity_for(txn_id))
    has_region = bool(row is not None and pd.notna(row["addr1"]))

    shift_value, shift_detail = behaviour_shift_value(shift)

    # Prior-case outcomes on the same entities, shrunk so two cases cannot read
    # as certainty.
    cases = prior_cases.get("cases", []) if prior_cases else []
    confirmed = sum(1 for c in cases if c.get("outcome") == "confirmed_fraud")
    similar_rate = shrink_rate(confirmed, len(cases), base=0.5, strength=4.0)

    ring_score = 0.0
    ring_detail = "no ring signal"
    ring_pattern = next((s for s in scores if s.pattern == "undocumented_proxy_device_ring"), None)
    if ring_pattern:
        ring_score = ring_pattern.score
        ring_detail = ring_pattern.narrative
    if ring and ring.get("is_ring") and ring.get("confirmed_fraud_members", 0) >= 2:
        ring_score = max(ring_score, 0.6)

    travel = 0.0
    travel_detail = ""
    region_pattern = next((s for s in scores if s.pattern == Pattern.OUT_OF_REGION_USE.value), None)
    if region_pattern and region_pattern.detail.get("reads_as_trip"):
        travel = 0.8
        travel_detail = (
            f"{region_pattern.detail.get('span_days_in_region')} days of activity in one new region with no "
            "overlapping home-region activity, which is the shape of a trip"
        )

    risk_score = float(row["risk_score"]) if row is not None else 0.3

    signals = {
        "bank_risk_score": risk_score,
        "pattern_match": leading.score,
        "shared_device_ring": ring_score,
        "behaviour_shift": shift_value,
        "similar_case_fraud_rate": similar_rate,
        "recurring_charge_match": recurring.strength,
        "travel_explanation": travel,
    }
    observed = {
        "bank_risk_score": row is not None,
        "pattern_match": True,
        "shared_device_ring": has_identity,
        "behaviour_shift": bool(shift),
        "similar_case_fraud_rate": len(cases) > 0,
        "recurring_charge_match": True,
        "travel_explanation": has_region,
    }
    details = {
        "bank_risk_score": f"bank model scored the flagged transaction {risk_score:.2f}; an input, not a verdict",
        "pattern_match": leading.narrative,
        "shared_device_ring": ring_detail,
        "behaviour_shift": shift_detail,
        "similar_case_fraud_rate": (
            f"{confirmed} of {len(cases)} closed cases on these entities were confirmed fraud, "
            f"shrunk toward the base rate to {similar_rate:.2f}"
            if cases
            else "no closed case on these entities"
        ),
        "recurring_charge_match": recurring.detail.get("note", "") if recurring.strength else "no recurring match",
        "travel_explanation": travel_detail or "no travel explanation available",
    }

    prob = compute_probability(
        trigger_type=trigger_type,
        signals=signals,
        observed=observed,
        details=details,
        customer_response=customer_response,
    )

    sufficiency = check_sufficiency(
        pattern=leading.pattern,
        tools_called=tools_called,
        pattern_coverage=leading.coverage,
        uncheckable_signals=leading.uncheckable_signals,
    )

    conf = compute_confidence(
        coverage=round(0.5 * sufficiency.coverage + 0.5 * leading.coverage, 3),
        components=prob.components,
        independent_evidence=independent_evidence,
        prior_txns=prior_txns,
        has_identity=has_identity,
        has_region=has_region,
        checked_signals=leading.present_signals + leading.absent_signals,
        missing_signals=leading.uncheckable_signals + sufficiency.missing,
    )

    conflicting = conf.signal_agreement < 0.45
    unknowns = build_unknowns(
        sufficiency=sufficiency,
        uncheckable_signals=leading.uncheckable_signals,
        has_identity=has_identity,
        has_region=has_region,
        prior_txns=prior_txns,
        customer_response=customer_response,
        conflicting=conflicting,
        similar_case_count=len(cases),
    )

    hypotheses = [
        Hypothesis(
            pattern=answer_pattern_for(s.pattern)
            if answer_pattern_for(s.pattern) in {p.value for p in Pattern}
            else Pattern.UNDOCUMENTED.value,
            score=s.score,
            present_signals=s.present_signals,
            absent_signals=s.absent_signals,
            supporting_txn_ids=s.supporting_txn_ids,
            rejected=s.pattern != leading.pattern,
            rejection_reason=(
                f"scored {s.score:.2f} against the leading hypothesis {leading.pattern} at {leading.score:.2f}"
                if s.pattern != leading.pattern
                else ""
            ),
        )
        for s in scores
    ]

    risk = RiskAssessment(
        fraud_probability=round(prob.probability, 3),
        confidence=conf.confidence,
        components=prob.components,
        unknowns=unknowns,
        evidence_coverage=conf.evidence_coverage,
        signal_agreement=conf.signal_agreement,
        prior=round(prob.prior, 3),
        method=prob.method,
        checked_signals=conf.checked_signals,
        missing_signals=conf.missing_signals,
    )

    return Assessment(
        risk=risk,
        hypotheses=hypotheses,
        leading=leading,
        sufficiency=sufficiency,
        recurring=recurring,
        answer_pattern=answer_pattern_for(leading.pattern),
    )
