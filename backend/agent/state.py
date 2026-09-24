"""Agent state. One InternalCase plus the scratch the nodes pass along."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.actions.registry import ActionRunner
from backend.llm.client import LLMClient
from backend.models.internal_case import AgentStep, InternalCase
from backend.policy.engine import CaseState, PolicyEngine
from backend.tools.base import Toolset


@dataclass
class AgentState:
    case: InternalCase
    tools: Toolset
    engine: PolicyEngine
    runner: ActionRunner
    llm: LLMClient
    as_of: str

    step_budget: int = 24
    step_index: int = 0
    budget_exhausted: bool = False

    # Raw tool payloads, kept so later nodes do not call the same tool twice.
    findings: dict[str, Any] = field(default_factory=dict)
    tools_called: list[str] = field(default_factory=list)
    assessment: Any = None
    policy_state: CaseState | None = None
    evidence_context: Any = None
    context_block: Any = None
    emit: Callable[[str, dict[str, Any]], None] | None = None

    def next_step(self, node: str, tool: str = "", summary: str = "", args: dict[str, Any] | None = None) -> AgentStep:
        self.step_index += 1
        step = AgentStep(
            index=self.step_index,
            node=node,
            tool=tool,
            args=args or {},
            summary=summary,
        )
        self.case.steps.append(step)
        if self.emit:
            self.emit("step", step.model_dump(mode="json"))
        return step

    def spend(self, n: int = 1) -> bool:
        """Returns False once the budget is gone.

        On exhaustion the agent escalates to an analyst rather than inventing a
        conclusion it did not reach.
        """
        if self.step_index + n > self.step_budget:
            self.budget_exhausted = True
            return False
        return True

    def record_tool(self, name: str) -> None:
        if name not in self.tools_called:
            self.tools_called.append(name)
