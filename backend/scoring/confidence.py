"""confidence: how much the probability can be relied on. Not the probability.

These two numbers answer different questions. `fraud_probability` is how likely
the activity is fraud. `confidence` is how much of the evidence the leading
hypothesis calls for was actually available, and how well the signals that were
available agree with each other.

The case that matters is high probability with low confidence. That is an agent
that has one loud signal and no corroboration, and policy R1 says it must verify
before it blocks. Collapsing the two numbers into one removes the only thing
that distinguishes "act" from "ask", so they are kept apart everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.models.internal_case import ScoreComponent

# How much each ingredient counts toward confidence.
W_COVERAGE = 0.40
W_AGREEMENT = 0.25
W_CORROBORATION = 0.20
W_BASELINE = 0.15


@dataclass
class ConfidenceResult:
    confidence: float
    evidence_coverage: float
    signal_agreement: float
    corroboration: float
    baseline_quality: float
    checked_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 3),
            "evidence_coverage": round(self.evidence_coverage, 3),
            "signal_agreement": round(self.signal_agreement, 3),
            "corroboration": round(self.corroboration, 3),
            "baseline_quality": round(self.baseline_quality, 3),
            "checked_signals": self.checked_signals,
            "missing_signals": self.missing_signals,
        }


def signal_agreement(components: list[ScoreComponent]) -> float:
    """1.0 when every contributing signal points the same way, 0.0 when the
    inculpatory and exculpatory mass are equal.

    Conflicting evidence is a named condition in R8, so this number is also what
    tells the agent to escalate rather than decide.
    """
    moved = [c for c in components if c.observed and abs(c.contribution) > 1e-6]
    if not moved:
        return 0.0
    up = sum(c.contribution for c in moved if c.contribution > 0)
    down = -sum(c.contribution for c in moved if c.contribution < 0)
    if up + down == 0:
        return 0.0
    return round(abs(up - down) / (up + down), 3)


def corroboration_score(independent_evidence: int) -> float:
    """The stopping rule wants at least two independent pieces of evidence.

    One piece is not enough to stop on, three is comfortable, and more than four
    adds little.
    """
    if independent_evidence <= 0:
        return 0.0
    if independent_evidence == 1:
        return 0.30
    if independent_evidence == 2:
        return 0.65
    if independent_evidence == 3:
        return 0.85
    return 1.0


def baseline_quality(prior_txns: int, has_identity: bool, has_region: bool) -> float:
    """How much history there was to judge "unusual for this cardholder" against.

    A card with six prior transactions cannot support an amount percentile, and
    a ProductCD C transaction has no billing region at all on 94.8 percent of
    rows, so the region branch was never available.
    """
    if prior_txns >= 60:
        volume = 1.0
    elif prior_txns >= 20:
        volume = 0.8
    elif prior_txns >= 8:
        volume = 0.55
    elif prior_txns >= 3:
        volume = 0.3
    else:
        volume = 0.1
    channels = (0.6 if has_identity else 0.0) + (0.4 if has_region else 0.0)
    return round(0.6 * volume + 0.4 * channels, 3)


def compute_confidence(
    coverage: float,
    components: list[ScoreComponent],
    independent_evidence: int,
    prior_txns: int,
    has_identity: bool,
    has_region: bool,
    checked_signals: list[str] | None = None,
    missing_signals: list[str] | None = None,
) -> ConfidenceResult:
    agreement = signal_agreement(components)
    corrob = corroboration_score(independent_evidence)
    baseline = baseline_quality(prior_txns, has_identity, has_region)
    confidence = (
        W_COVERAGE * coverage
        + W_AGREEMENT * agreement
        + W_CORROBORATION * corrob
        + W_BASELINE * baseline
    )
    return ConfidenceResult(
        confidence=round(min(max(confidence, 0.0), 1.0), 3),
        evidence_coverage=coverage,
        signal_agreement=agreement,
        corroboration=corrob,
        baseline_quality=baseline,
        checked_signals=checked_signals or [],
        missing_signals=missing_signals or [],
    )
