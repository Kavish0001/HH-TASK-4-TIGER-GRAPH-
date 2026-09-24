"""Agent invariants I want to fail loudly on.

Run: python -m pytest backend/tests -q
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from backend.answers.write import answer_dict, validate
from backend.config import get_settings


def _pack() -> list[dict]:
    df = pd.read_csv(get_settings().data_dir / "case_pack.csv")
    return df.to_dict(orient="records")


@pytest.mark.parametrize("row", _pack(), ids=lambda r: r["case_id"])
def test_written_answer_files_validate(row):
    path = get_settings().cases_dir / f"{row['case_id']}.json"
    if not path.exists():
        pytest.skip("run python -m backend.run_cases first")
    validate(json.loads(path.read_text(encoding="utf-8")))


def test_one_case_end_to_end_respects_as_of():
    from backend.agent.graph import run_case
    from backend.tools.factory import build_toolset

    row = next(r for r in _pack() if r["case_id"] == "HHG-014")
    tools = build_toolset()
    case = run_case(row, tools=tools)
    as_of = str(pd.Timestamp(row["opened_at"]))
    # Every read tool must be cut at the trigger time; a later as_of is leakage.
    for call in tools.calls:
        if "as_of" in call.args:
            assert str(pd.Timestamp(call.args["as_of"])) == as_of, call
    answer = answer_dict(case)
    assert answer["tool_calls"] == len(tools.calls)
    # FILE_REPORT in final and sar.file must agree; validate() checks it too.
    assert answer["sar"]["file"] == any(a["action"] == "FILE_REPORT" for a in answer["next_best_actions"]["final"])
    # L1 and L2 actions are never executed by the agent.
    for d in case.decisions:
        if d.actor == "agent" and d.route in ("L1", "L2"):
            assert not d.executed
