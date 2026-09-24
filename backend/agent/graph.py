"""The LangGraph state machine from PLAN section 7.

    trigger -> open_case -> plan -> gather_evidence <-> assess (loop)
      -> nba_initial -> policy_check_initial
      -> [need evidence?] yes -> request_evidence -> reassess -> nba_final
                          no  -> nba_final
      -> policy_check_final -> execute_or_route -> sar_check -> explain
      -> write_case -> update_memory -> finalize (answer file fields)

The graph carries one object, the AgentState, under a single key. I keep it that
way rather than spreading fields across LangGraph channels because the nodes
already share a typed dataclass, and a reducer per field would be a second
definition of the same state.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from backend.actions.registry import ActionRunner
from backend.agent import nodes
from backend.agent.state import AgentState
from backend.config import get_settings
from backend.llm.client import LLMClient, get_llm_client
from backend.models.internal_case import InternalCase, Trigger
from backend.policy.engine import PolicyEngine
from backend.tools.base import Toolset
from backend.tools.factory import build_toolset

log = logging.getLogger(__name__)


class GraphState(TypedDict):
    s: AgentState


def _wrap(fn: Callable[[AgentState], AgentState]) -> Callable[[GraphState], GraphState]:
    def node(state: GraphState) -> GraphState:
        return {"s": fn(state["s"])}

    node.__name__ = fn.__name__
    return node


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(GraphState)
    order = [
        ("trigger", nodes.trigger),
        ("open_case", nodes.open_case),
        ("plan", nodes.plan),
        ("gather_evidence", nodes.gather_evidence),
        ("assess", nodes.assess),
        ("nba_initial", nodes.nba_initial),
        ("policy_check_initial", nodes.policy_check_initial),
        ("request_evidence", nodes.request_evidence),
        ("reassess", nodes.reassess),
        ("nba_final", nodes.nba_final),
        ("policy_check_final", nodes.policy_check_final),
        ("execute_or_route", nodes.execute_or_route),
        ("sar_check", nodes.sar_check),
        ("explain", nodes.explain),
        ("write_case", nodes.write_case),
        ("update_memory", nodes.update_memory),
        ("finalize", nodes.finalize),
    ]
    for name, fn in order:
        g.add_node(name, _wrap(fn))

    g.set_entry_point("trigger")
    g.add_edge("trigger", "open_case")
    g.add_edge("open_case", "plan")
    g.add_edge("plan", "gather_evidence")
    g.add_edge("gather_evidence", "assess")
    g.add_conditional_edges(
        "assess",
        lambda st: nodes.route_after_assess(st["s"]),
        {"gather_evidence": "gather_evidence", "nba_initial": "nba_initial"},
    )
    g.add_edge("nba_initial", "policy_check_initial")
    g.add_conditional_edges(
        "policy_check_initial",
        lambda st: nodes.route_after_policy(st["s"]),
        {"request_evidence": "request_evidence", "nba_final": "nba_final"},
    )
    g.add_edge("request_evidence", "reassess")
    g.add_edge("reassess", "nba_final")
    g.add_edge("nba_final", "policy_check_final")
    g.add_edge("policy_check_final", "execute_or_route")
    g.add_edge("execute_or_route", "sar_check")
    g.add_edge("sar_check", "explain")
    g.add_edge("explain", "write_case")
    g.add_edge("write_case", "update_memory")
    g.add_edge("update_memory", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def trigger_from_row(row: dict[str, Any]) -> Trigger:
    score = row.get("risk_score")
    try:
        score = float(score) if score not in (None, "", "nan") else None
    except (TypeError, ValueError):
        score = None
    if score is not None and score != score:  # NaN from pandas
        score = None
    return Trigger(
        type=row["trigger_type"],
        source="case_pack.csv",
        trigger_text=str(row["trigger_text"]),
        opened_at=datetime.fromisoformat(str(row["opened_at"])),
        flagged_txn_id=str(int(float(row["flagged_txn_id"]))),
        card_id=str(row["card_id"]),
        customer_id=str(row["customer_id"]),
        risk_score=score,
    )


def run_case(
    row: dict[str, Any],
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    llm: LLMClient | None = None,
    tools: Toolset | None = None,
) -> InternalCase:
    """Investigate one case-pack row and return the finished InternalCase."""
    trig = trigger_from_row(row)
    case = InternalCase(case_id=str(row["case_id"]), trigger=trig)
    engine = PolicyEngine(actor="agent")
    settings = get_settings()
    state = AgentState(
        case=case,
        tools=tools or build_toolset(),
        engine=engine,
        runner=ActionRunner(engine),
        llm=llm or get_llm_client(),
        # as_of is the trigger time. Every read tool cuts at it.
        as_of=trig.opened_at.strftime("%Y-%m-%d %H:%M:%S"),
        step_budget=settings.agent_max_steps,
        emit=emit,
    )
    out = build_graph().invoke({"s": state}, {"recursion_limit": 60})
    return out["s"].case
