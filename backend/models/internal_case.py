"""InternalCase: the live investigation object.

Per backend/contracts/internal_case.md this is the working type. It carries the
things the answer file cannot hold because the schema forbids extra properties:
the trigger, the status history, confidence and unknowns, the component scores
behind the probability, rejected hypotheses, the step timeline and the decision
log with authorization outcomes. The graph stores this, the dashboard renders
this over SSE, and backend/answers/write.py projects it down to AnswerFile.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models.answer import EvidenceItem, EvidenceRequestRecord, RecommendedAction, SarPart
from backend.models.enums import (
    CaseStatus,
    CustomerResponse,
    Pattern,
    Route,
    TriggerType,
    Verdict,
)


class _Model(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class Trigger(_Model):
    type: TriggerType
    source: str
    trigger_text: str
    opened_at: datetime
    flagged_txn_id: str
    card_id: str
    customer_id: str
    risk_score: float | None = None


class StatusEntry(_Model):
    status: CaseStatus
    at: datetime
    note: str = ""


class ScoreComponent(_Model):
    """One inspectable term of the fraud probability blend.

    `value` is the raw 0..1 signal, `weight` its share of the blend, and
    `contribution` the product. The dashboard renders these, and they are
    reproduced in the answer file's free-text reasons.
    """

    name: str
    value: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0)
    contribution: float
    observed: bool = True
    detail: str = ""


class RiskAssessment(_Model):
    fraud_probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    components: list[ScoreComponent] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    signal_agreement: float = Field(default=0.0, ge=0.0, le=1.0)
    prior: float = 0.0
    method: str = ""
    checked_signals: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)


class Hypothesis(_Model):
    pattern: Pattern
    score: float = Field(ge=0.0, le=1.0)
    present_signals: list[str] = Field(default_factory=list)
    absent_signals: list[str] = Field(default_factory=list)
    supporting_txn_ids: list[str] = Field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""


class AgentStep(_Model):
    index: int
    node: str
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    at: datetime = Field(default_factory=datetime.utcnow)
    duration_s: float = 0.0
    ok: bool = True


class Decision(_Model):
    """One recommended or attempted action.

    `authorized` is false for every L1 and L2 action, because the agent may not
    execute those. Blocked attempts stay in the record on purpose: the policy
    engine logs the attempt either way.
    """

    actor: str
    action: str
    route: Route
    authorized: bool
    executed: bool = False
    reason: str
    phase: str = "initial"
    approval_status: str = "pending"
    at: datetime = Field(default_factory=datetime.utcnow)


class EvidenceRequest(_Model):
    type: str
    asked_after_step: int
    assumed_response: str
    resolved_as: CustomerResponse | None = None
    basis: list[str] = Field(default_factory=list)


class RetrievedChunk(_Model):
    ref: str
    text: str
    score: float
    kind: str
    # Filled for prior-case chunks so the similar-cases panel can show the
    # outcome without a second lookup.
    outcome: str | None = None
    pattern: str | None = None
    exposure_usd: float | None = None


class InternalCase(_Model):
    case_id: str
    trigger: Trigger

    status: CaseStatus = CaseStatus.OPEN
    status_history: list[StatusEntry] = Field(default_factory=list)

    verdict: Verdict = Verdict.UNCERTAIN
    pattern: Pattern = Pattern.NONE
    pattern_description: str = ""

    risk_assessment: RiskAssessment = Field(
        default_factory=lambda: RiskAssessment(fraud_probability=0.0, confidence=0.0)
    )
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    affected_txn_ids: list[str] = Field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    exposure_usd: float = 0.0

    evidence: list[EvidenceItem] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)

    summary: str = ""
    written_to_graph: bool = False
    graph_case_id: str = ""

    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    customer_response: CustomerResponse | None = None

    initial_actions: list[RecommendedAction] = Field(default_factory=list)
    final_actions: list[RecommendedAction] = Field(default_factory=list)
    what_changed: str = "nothing"

    sar: SarPart | None = None

    steps: list[AgentStep] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)

    stop_reason: str = ""
    tool_calls: int = 0
    tokens: int = 0
    latency_s: float = 0.0

    def set_status(self, status: CaseStatus, note: str = "") -> None:
        self.status = status
        self.status_history.append(StatusEntry(status=status, at=datetime.utcnow(), note=note))

    def add_evidence(self, item: EvidenceItem) -> None:
        # Evidence refs are cited in the explanation, so duplicates would read as
        # two independent findings when they are one.
        key = (item.claim, item.ref)
        if any((e.claim, e.ref) == key for e in self.evidence):
            return
        self.evidence.append(item)

    def answer_requests(self) -> list[EvidenceRequestRecord]:
        return [
            EvidenceRequestRecord(
                type=r.type,
                asked_after_step=r.asked_after_step,
                assumed_response=r.assumed_response,
            )
            for r in self.evidence_requests
        ]
