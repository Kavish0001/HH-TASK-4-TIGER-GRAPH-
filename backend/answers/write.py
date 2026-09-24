"""InternalCase to AnswerFile: the only code that emits the graded shape.

Per backend/contracts/internal_case.md the answer file is a pure projection. I
never build the dict by hand anywhere else, and I validate twice before
writing: once through the Pydantic AnswerFile (extra="forbid"), once against
answer.schema.json itself, because the schema is what the graders' validator
reads and a model that drifted from it would pass the first check silently.

Output goes to `cases/<case_id>.json` (the README's required folder) and is
mirrored to `outputs/answers/` where the orchestration plan expects it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from backend.config import get_settings
from backend.models.answer import (
    AnswerFile,
    CasePart,
    EvidenceItem,
    NextBestActions,
    RecommendedAction,
    SarPart,
)
from backend.models.enums import Action
from backend.models.internal_case import InternalCase


@lru_cache(maxsize=1)
def answer_schema() -> dict[str, Any]:
    path = get_settings().contracts_dir / "answer.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _no_sar(reason: str) -> SarPart:
    return SarPart(file=False, reason=reason or "Policy 3a: no filing trigger holds", narrative="", subjects=[], total_amount_usd=0.0, activity_dates=[])


def project(case: InternalCase) -> AnswerFile:
    initial = list(case.initial_actions)
    final = list(case.final_actions) or initial
    if not initial:
        # The schema needs at least one action. An empty list would mean the
        # agent never reached a decision, which the escalation route covers.
        initial = [RecommendedAction(action=Action.ESCALATE_TO_ANALYST.value, route="auto", reason="R8: no decision reached")]
        final = final or initial

    legit = case.verdict == "legitimate"
    sar = case.sar or _no_sar("")
    file_report = any(a.action == Action.FILE_REPORT.value for a in final)
    if not file_report or legit:
        sar = _no_sar(sar.reason)
    else:
        sar = SarPart(
            file=True,
            reason=sar.reason,
            narrative=sar.narrative,
            subjects=list(dict.fromkeys(sar.subjects)),
            total_amount_usd=round(sar.total_amount_usd, 2),
            activity_dates=sar.activity_dates[:2],
        )

    part = CasePart(
        status=case.status,
        verdict=case.verdict,
        fraud_probability=round(float(case.risk_assessment.fraud_probability), 3),
        pattern=case.pattern if not legit else "none",
        pattern_description=case.pattern_description if case.pattern == "undocumented" and not legit else "",
        # README notes: a legitimate verdict carries no affected transactions
        # and no exposure.
        affected_txn_ids=[] if legit else list(case.affected_txn_ids),
        first_suspicious_txn_id="" if legit else case.first_suspicious_txn_id,
        connected_card_ids=[] if legit else list(case.connected_card_ids),
        connected_device_profiles=[] if legit else list(case.connected_device_profiles),
        exposure_usd=0.0 if legit else round(float(case.exposure_usd), 2),
        evidence=[EvidenceItem(**e.model_dump()) for e in case.evidence],
        similar_prior_cases=list(case.similar_prior_cases),
        summary=case.summary or "Investigation completed.",
        written_to_graph=case.written_to_graph,
        graph_case_id=case.graph_case_id,
    )
    same = [a.model_dump() for a in initial] == [a.model_dump() for a in final]
    nba = NextBestActions(
        initial=initial,
        final=final,
        what_changed=case.what_changed if (case.evidence_requests or not same) else "nothing",
    )
    return AnswerFile(
        case_id=case.case_id,
        case=part,
        evidence_requests=case.answer_requests(),
        next_best_actions=nba,
        sar=sar,
        stop_reason=case.stop_reason or "Investigation completed.",
        tool_calls=int(case.tool_calls),
        tokens=int(case.tokens),
        latency_s=round(float(case.latency_s), 2),
    )


def validate(answer: dict[str, Any]) -> None:
    """Raises jsonschema.ValidationError on any mismatch. Fail loudly."""
    jsonschema.validate(answer, answer_schema())
    # One cross-field rule the schema cannot express.
    has_file = any(a["action"] == "FILE_REPORT" for a in answer["next_best_actions"]["final"])
    if has_file != answer["sar"]["file"]:
        raise ValueError(f"{answer['case_id']}: sar.file={answer['sar']['file']} disagrees with FILE_REPORT in final")


def answer_dict(case: InternalCase) -> dict[str, Any]:
    data = project(case).model_dump(mode="json")
    validate(data)
    return data


def write_answer(case: InternalCase, dirs: list[Path] | None = None) -> list[Path]:
    settings = get_settings()
    data = answer_dict(case)
    targets = dirs or [settings.cases_dir, settings.repo_root / "outputs" / "answers"]
    written = []
    for d in targets:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{case.case_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


def internal_dir() -> Path:
    return get_settings().repo_root / "outputs" / "internal"


def write_internal(case: InternalCase) -> Path:
    """The full InternalCase, which is what the dashboard renders."""
    d = internal_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{case.case_id}.json"
    path.write_text(case.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_internal(case_id: str) -> InternalCase | None:
    path = internal_dir() / f"{case_id}.json"
    if not path.exists():
        return None
    return InternalCase.model_validate_json(path.read_text(encoding="utf-8"))
