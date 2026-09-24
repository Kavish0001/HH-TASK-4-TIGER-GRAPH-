"""The graded shape, and nothing else.

Every field, every enum and every constraint here is copied from
backend/contracts/answer.schema.json, which is additionalProperties: false.
Anything the agent wants to carry that is not in this file lives on
InternalCase instead. backend/answers/write.py is the only module allowed to
construct an AnswerFile.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.models.enums import (
    Action,
    CaseStatus,
    EvidenceRequestType,
    EvidenceSource,
    Pattern,
    Route,
    Verdict,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class EvidenceItem(_Strict):
    claim: str = Field(min_length=1)
    source: EvidenceSource
    ref: str = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)


class CasePart(_Strict):
    status: CaseStatus
    verdict: Verdict
    fraud_probability: float = Field(ge=0.0, le=1.0)
    pattern: Pattern
    pattern_description: str = ""
    affected_txn_ids: list[str] = Field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    exposure_usd: float = Field(ge=0.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    written_to_graph: bool = False
    graph_case_id: str = ""


class EvidenceRequestRecord(_Strict):
    type: EvidenceRequestType
    asked_after_step: int = Field(ge=0)
    assumed_response: str = Field(min_length=1)


class RecommendedAction(_Strict):
    action: Action
    route: Route
    reason: str = Field(min_length=1)


class NextBestActions(_Strict):
    initial: list[RecommendedAction] = Field(min_length=1)
    final: list[RecommendedAction] = Field(min_length=1)
    what_changed: str = Field(min_length=1)


class SarPart(_Strict):
    file: bool
    reason: str = Field(min_length=1)
    narrative: str = ""
    subjects: list[str] = Field(default_factory=list)
    total_amount_usd: float = Field(ge=0.0)
    activity_dates: list[str] = Field(default_factory=list, max_length=2)


class AnswerFile(_Strict):
    case_id: str = Field(pattern=r"^HHG-[0-9]{3}$")
    case: CasePart
    evidence_requests: list[EvidenceRequestRecord] = Field(default_factory=list)
    next_best_actions: NextBestActions
    sar: SarPart
    stop_reason: str = Field(min_length=1)
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    latency_s: float = Field(ge=0.0)
