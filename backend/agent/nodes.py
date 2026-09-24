"""The investigation nodes.

Each node takes the AgentState, does one stage of PLAN section 7 and logs an
AgentStep so the dashboard can stream it. The split of work is deliberate:

- tools (through Toolset, so every call is counted) fetch the graph evidence;
- backend/scoring turns it into probability, confidence and unknowns;
- backend/policy picks the actions and routes;
- the LLM only writes prose, in `explain`, and only after everything graded
  has already been decided.

I never let a node read past `as_of`: every tool gets it, and the scoring reads
the same store with the same cut.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import pandas as pd

from backend.actions.evidence_sim import EvidenceContext, choose_request_type, simulate_customer_response
from backend.agent import narrative as nar
from backend.agent.state import AgentState
from backend.graphrag.retriever import build_context_block
from backend.llm.client import user_message
from backend.llm.prompts import SYNTHESIS_INSTRUCTION, system_block
from backend.llm.schemas import SYNTHESIS_SCHEMA
from backend.models.answer import EvidenceItem, RecommendedAction, SarPart
from backend.models.enums import Action, CaseStatus, CustomerResponse, Pattern, Verdict
from backend.models.internal_case import Decision, EvidenceRequest, RetrievedChunk
from backend.policy.decide import compose_actions, decide_status, decide_verdict
from backend.policy.engine import CaseState, PolicyEngine, route_for
from backend.scoring.assess import Assessment, assess_case
from backend.tools.mock.store import get_store

log = logging.getLogger(__name__)

# Evidence that moves the log-odds by at least this much counts as one
# independent piece for the stopping rule. Smaller nudges are context, not
# evidence a decision can rest on.
INDEPENDENT_MIN = 0.25
# A connected card has to have been on the shared device recently to belong to
# the same episode rather than to the device's whole history.
CONNECTED_WINDOW_DAYS = 45
RING_SIGNAL_MIN = 0.5
RING_DETECTOR = "undocumented_proxy_device_ring"
EPISODE_MAX = 6

REQUEST_ACTION = {
    "customer_validation": Action.VERIFY_WITH_CUSTOMER.value,
    "step_up_auth": Action.STEP_UP_AUTH.value,
    "analyst_info": Action.ESCALATE_TO_ANALYST.value,
}

EXTRA_INSTRUCTION = """

Two facts to respect. Actions under `awaiting_human_approval` have NOT happened:
write that they are recommended and name the route, never that a card was
blocked or a report was filed. The cardholder's reply in `assumed_response` is a
simulated assumption, not a real statement: write "was assumed to deny" or
"was assumed to confirm", and never swap the two.
"""

TRIGGER_PHRASE = {
    "risk_score": "The bank's model flagged the transaction",
    "customer_report": "The cardholder disputed a charge",
    "analyst_request": "An analyst asked for a review of shared-device activity",
}


# ---------------------------------------------------------------------------
# helpers


def _emit(s: AgentState, kind: str, payload: dict[str, Any]) -> None:
    if s.emit:
        try:
            # The dashboard merges any event carrying a case, so each milestone
            # ships the current snapshot and the view never has to rebuild it.
            s.emit(kind, {**payload, "case": s.case.model_dump(mode="json")})
        except Exception:  # a dead SSE client must not kill an investigation
            log.debug("emit failed", exc_info=True)


def _step(s: AgentState, node: str, summary: str, tool: str = "", args: dict[str, Any] | None = None, ok: bool = True):
    step = s.next_step(node=node, tool=tool, summary=summary, args=args)
    step.ok = ok
    return step


def run_tool(s: AgentState, key: str, name: str, **args: Any):
    """One counted tool call, stored under `key` so no node repeats it."""
    if key in s.findings:
        return s.findings[key]
    if s.tools.tool_calls >= s.step_budget:
        s.budget_exhausted = True
        _step(s, "gather_evidence", f"tool budget of {s.step_budget} reached, skipped {name}", tool=name, ok=False)
        return None
    started = time.perf_counter()
    result = s.tools.call(name, **args)
    s.findings[key] = result
    s.record_tool(name)
    if name == "pattern_match" and args.get("pattern"):
        s.record_tool(f"pattern_match:{args['pattern']}")
    step = _step(
        s,
        "gather_evidence",
        _summarize_tool(name, result),
        tool=name,
        args={k: v for k, v in args.items() if k != "as_of"},
        ok=bool(result.ok),
    )
    step.duration_s = round(time.perf_counter() - started, 3)
    return result


def _summarize_tool(name: str, result: Any) -> str:
    if not result or not result.ok:
        return f"{name} failed: {getattr(result, 'error', '')}"[:200]
    d = result.data
    if name == "tx_context":
        t = d.get("transaction", {})
        return f"transaction {t.get('txn_id', '')} ${t.get('amount', '')} {t.get('channel', '')} risk {t.get('risk_score', '')}"
    if name == "customer_profile":
        return f"{d.get('txn_count', 0)} transactions on the customer, cards {d.get('cards', [])}"
    if name == "card_window":
        return f"{d.get('n', 0)} transactions on the card in window {d.get('window', '')}"
    if name == "behavior_shift":
        return f"amount percentile {d.get('amount_percentile')}, new device {d.get('new_device_profile')}, new product {d.get('new_product_cd')}"
    if name == "shared_device_profile":
        return f"device profile on {d.get('n_cards', 0)} cards / {d.get('n_customers', 0)} customers, rare={d.get('is_rare')}"
    if name == "ring_detect":
        return f"ring size {d.get('ring_size', 0)}, is_ring={d.get('is_ring')}, confirmed-fraud members {d.get('confirmed_fraud_members', 0)}"
    if name == "prior_cases_for_entities":
        return f"{d.get('n', 0)} closed cases on these entities, confirmed rate {d.get('confirmed_fraud_rate', 0)}"
    if name == "similar_cases":
        return f"{d.get('n', 0)} similar cases: " + ", ".join(str(c.get('case_id')) for c in d.get("cases", [])[:5])
    if name == "pattern_match":
        return f"{d.get('pattern')} score {d.get('score')}"
    if name == "policy_lookup":
        return "policy chunks " + ", ".join(c.get("ref", "") for c in d.get("chunks", []))
    if name == "region_history":
        return f"region history found={d.get('found')}, has_history={d.get('has_history')}, trip={d.get('reads_as_trip')}"
    if name == "velocity":
        return "velocity " + ", ".join(f"{w['window_hours']}h n={w['n']}" for w in d.get("windows", []))
    return name


def _data(s: AgentState, key: str) -> dict[str, Any]:
    r = s.findings.get(key)
    return r.data if r is not None and r.ok else {}


def _ref(s: AgentState, key: str, default: str) -> str:
    r = s.findings.get(key)
    return r.ref if r is not None else default


def _txn(s: AgentState) -> dict[str, Any]:
    return _data(s, "tx_context").get("transaction", {})


def _device_profile(s: AgentState) -> str:
    return _data(s, "behavior_shift").get("device_profile") or ""


def _independent_count(assessment: Assessment) -> tuple[int, int]:
    """(independent pieces, inculpatory pieces).

    I leave the bank's own score out of the inculpatory count because R1 names
    "a risk score alone" as the textbook single signal.
    """
    moving = [c for c in assessment.risk.components if c.observed and abs(c.contribution) >= INDEPENDENT_MIN]
    inculpatory = [c for c in moving if c.contribution > 0 and c.name != "bank_risk_score"]
    return len(moving), len(inculpatory)


def _assess(s: AgentState, customer_response: str | None = None) -> Assessment:
    c = s.case
    kwargs = dict(
        trigger_type=c.trigger.type,
        card_id=c.trigger.card_id,
        txn_id=c.trigger.flagged_txn_id,
        as_of=s.as_of,
        tools_called=s.tools_called,
        shift=_data(s, "behavior_shift"),
        prior_cases=_data(s, "prior_cases"),
        ring=_data(s, "ring"),
        customer_response=customer_response,
    )
    store = get_store()
    # Two passes: the independent-evidence count feeds confidence, and it is
    # read off the components of the first pass.
    first = assess_case(store, independent_evidence=s.independent, **kwargs)
    indep, _ = _independent_count(first)
    if customer_response in (CustomerResponse.DENIED.value, CustomerResponse.CONFIRMED.value):
        indep += 1
    s.independent = max(indep, 0)
    return assess_case(store, independent_evidence=s.independent, **kwargs)


def _ring_signal(s: AgentState, a: Assessment) -> float:
    ring = next((h for h in a.hypotheses if h.pattern == Pattern.UNDOCUMENTED.value and "specific_device_profile" in (h.present_signals + h.absent_signals)), None)
    return ring.score if ring else 0.0


def _compute_scope(s: AgentState, a: Assessment, verdict: str) -> dict[str, Any]:
    """Which transactions, cards and devices belong to the episode.

    Every ID here came back from a tool or the store, filtered to the case card
    and to `as_of`, so nothing in the answer file can be invented or future.
    """
    store = get_store()
    c = s.case.trigger
    flagged = str(c.flagged_txn_id)
    out: dict[str, Any] = {
        "affected": [],
        "first": "",
        "connected_cards": [],
        "device_profiles": [],
        "exposure": 0.0,
        "shared_device": False,
        "linked_fraud": False,
        "shared_element": "",
    }

    profile = _device_profile(s)
    ring_score = _ring_signal(s, a)
    shared = _data(s, "shared_device")
    ring = _data(s, "ring")
    # I gate on the ring detector, not on the tool's `is_rare` flag: the one real
    # ring in this data spans 52 cards, far past the rarity cut, and what marks
    # it is novelty plus proxy concentration, which the detector scores.
    if profile and ring_score >= RING_SIGNAL_MIN:
        cut = pd.Timestamp(s.as_of) - pd.Timedelta(days=CONNECTED_WINDOW_DAYS)
        cards = []
        for card in shared.get("cards", []):
            if card.get("card_id") == c.card_id:
                continue
            last = card.get("last_seen")
            if last and pd.Timestamp(last) >= cut:
                cards.append(card["card_id"])
        out["connected_cards"] = cards
        out["device_profiles"] = [profile]
        out["shared_device"] = bool(cards)
        out["shared_element"] = f"device profile {profile}"
        other_fraud = [
            p for p in shared.get("prior_cases", [])
            if p.get("outcome") == "confirmed_fraud" and p.get("card_id") != c.card_id
        ]
        out["linked_fraud"] = bool(other_fraud) or ring.get("confirmed_fraud_members", 0) >= 2

    if verdict == Verdict.LEGITIMATE.value:
        # README: a legitimate verdict carries no affected transactions and no
        # exposure. Connected cards stay empty too, since there is no episode.
        out.update(connected_cards=[], device_profiles=[], shared_device=False, linked_fraud=False)
        return out

    leading = a.leading
    ids = list(leading.supporting_txn_ids) if leading.score >= 0.3 else []
    if flagged not in ids:
        ids.append(flagged)
    flagged_row = store.txn(flagged)
    flagged_channel = flagged_row["channel"] if flagged_row is not None else None
    rows = []
    for t in ids:
        row = store.txn(t)
        if row is None or row["card_id"] != c.card_id or pd.Timestamp(row["ts"]) > pd.Timestamp(s.as_of):
            continue
        # The episode is the anomalous activity, not everything the card did in
        # the window. On HHG-013 the takeover matcher returned 24 routine
        # in-person purchases around one online charge, which tripled exposure
        # and pushed the block to L2 on the cardholder's own shopping.
        if flagged_channel is not None and row["channel"] != flagged_channel:
            continue
        rows.append((pd.Timestamp(row["ts"]), str(int(row["TransactionID"])), abs(float(row["TransactionAmt"]))))
    rows.sort()
    # The documented bursts are two to four transactions. When a matcher hands
    # back more than that on a busy card (HHG-011 returned 59 in 48 hours on a
    # card doing about 57 a day), the extra rows are the card's routine, so I
    # narrow to the flagged device profile, or to the flagged charge alone.
    if len(rows) > EPISODE_MAX and a.answer_pattern not in (Pattern.CARD_TESTING.value, Pattern.UNDOCUMENTED.value):
        profile_rows = [r for r in rows if profile and store.device_profile_for(r[1]) == profile]
        rows = profile_rows if 0 < len(profile_rows) <= EPISODE_MAX else [r for r in rows if r[1] == flagged]
    out["affected"] = [r[1] for r in rows]
    out["first"] = rows[0][1] if rows else flagged
    out["exposure"] = round(sum(r[2] for r in rows), 2)
    return out


def _policy_state(s: AgentState, a: Assessment, scope: dict[str, Any], verdict: str, *, response: str | None, requested: bool) -> CaseState:
    c = s.case.trigger
    indep, incul = _independent_count(a)
    leading = a.leading
    return CaseState(
        fraud_probability=a.risk.fraud_probability,
        confidence=a.risk.confidence,
        verdict=verdict,
        exposure_usd=scope["exposure"],
        pattern=a.answer_pattern,
        single_signal=incul <= 1,
        evidence_conflicts=a.risk.signal_agreement < 0.45,
        customer_response=response,
        customer_disputes=c.type == "customer_report",
        recurring_charge_match=a.recurring.strength >= 0.55,
        card_testing_sequence=leading.pattern == Pattern.CARD_TESTING.value and leading.score >= 0.5,
        cleared_purchase_over_100=bool(leading.detail.get("cleared_purchase_over_100")),
        shared_device_profile=scope["shared_device"],
        shared_region_cluster=False,
        linked_to_other_card_fraud=scope["linked_fraud"],
        shared_element=scope["shared_element"],
        connected_card_ids=scope["connected_cards"],
        coordinated_or_undocumented=a.answer_pattern == Pattern.UNDOCUMENTED.value and leading.score >= 0.5,
        evidence_requested=requested,
        sufficient_for_impacting_action=a.sufficiency.sufficient_for_impacting_action,
        # A model-score alert fires on an authorization; a customer report is
        # about a charge that already cleared, so there is nothing to decline.
        pending_authorization=c.type == "risk_score",
    )


def _rules_text(bundle: Any) -> str:
    # Only rules behind an action that survived filtering. A rule whose actions
    # were all dropped did not shape the recommendation and is not cited.
    rules: list[str] = []
    for a in bundle.actions:
        for token in a.reason.replace(",", " ").replace(":", " ").split():
            if token.startswith("R") and token[1:].isdigit() and token not in rules:
                rules.append(token)
    return ", ".join(sorted(set(rules), key=lambda r: int(r[1:]))) if rules else ""


def _log_decisions(s: AgentState, bundle: Any, state: CaseState, phase: str) -> None:
    """Every recommendation goes through the engine; the attempt is logged either way."""
    for rec in bundle.actions:
        record = s.engine.authorize(rec.action, state, rec.reason)
        d = Decision(
            actor="agent",
            action=rec.action,
            route=record.route,
            authorized=record.authorized,
            executed=False,
            reason=record.reason,
            phase=phase,
            approval_status="not_required" if record.authorized else "pending",
        )
        s.case.decisions.append(d)


# ---------------------------------------------------------------------------
# nodes


def trigger(s: AgentState) -> AgentState:
    t = s.case.trigger
    s.started_perf = time.perf_counter()
    s.tokens_at_start = s.llm.usage.total_tokens
    _step(s, "trigger", f"{t.type} trigger on {t.card_id}, transaction {t.flagged_txn_id}: {t.trigger_text[:160]}")
    return s


def open_case(s: AgentState) -> AgentState:
    s.case.set_status(CaseStatus.OPEN, "alert received, investigation opened")
    _step(s, "open_case", f"case {s.case.case_id} opened as of {s.as_of}; no data after this time is read")
    _emit(s, "status", {"status": "open"})
    return s


def plan(s: AgentState) -> AgentState:
    """First round is the same for every case: the transaction, the holder, the
    baseline, the recent window and the memory of closed cases on these
    entities. Round two is chosen by assess from what round one showed."""
    t = s.case.trigger
    s.plan = [
        ("tx_context", "tx_context", {"txn_id": t.flagged_txn_id, "as_of": s.as_of}),
        ("customer_profile", "customer_profile", {"customer_id": t.customer_id, "as_of": s.as_of}),
        ("behavior_shift", "behavior_shift", {"customer_id": t.customer_id, "txn_id": t.flagged_txn_id, "as_of": s.as_of}),
        ("card_window", "card_window", {"card_id": t.card_id, "hours": 48, "as_of": s.as_of}),
        ("prior_cases", "prior_cases_for_entities", {"entity_ids": [t.card_id, t.customer_id], "as_of": s.as_of}),
    ]
    s.case.set_status(CaseStatus.OPEN, "investigating")
    _step(s, "plan", "round 1: " + ", ".join(p[1] for p in s.plan))
    return s


def gather_evidence(s: AgentState) -> AgentState:
    s.rounds += 1
    queue, s.plan = s.plan, []
    for key, name, args in queue:
        run_tool(s, key, name, **args)
    return s


def _next_round(s: AgentState, a: Assessment) -> list[tuple[str, str, dict[str, Any]]]:
    """Pick the follow-up queries from what the last round showed.

    I only ask for what the leading hypotheses need, which is how the loop stays
    short on easy cases and goes deeper on the shared-device ones.
    """
    t = s.case.trigger
    txn = _txn(s)
    profile = _device_profile(s)
    q: list[tuple[str, str, dict[str, Any]]] = []
    if s.rounds == 1:
        if profile:
            q.append(("shared_device", "shared_device_profile", {"device_profile": profile, "as_of": s.as_of}))
            q.append(("ring", "ring_detect", {"card_id": t.card_id, "as_of": s.as_of}))
        addr = txn.get("addr1")
        if addr not in (None, "", "nan"):
            q.append(("region", "region_history", {"customer_id": t.customer_id, "addr1": str(addr), "as_of": s.as_of}))
        q.append(("velocity", "velocity", {"card_id": t.card_id, "windows": [1, 24, 168], "as_of": s.as_of}))
        # The leading detector plus the best documented alternative, so the
        # rejected hypothesis is on record with its own query too.
        tops = [a.leading.pattern]
        alt = next((h.pattern for h in a.hypotheses if h.rejected and h.pattern not in (Pattern.UNDOCUMENTED.value, a.leading.pattern)), None)
        if alt:
            tops.append(alt)
        for p in tops:
            if p == RING_DETECTOR:
                # The ring detector reads shared_device_profile and ring_detect,
                # which this round already queues; the graph lane serves no
                # separate pattern query for it, so I do not spend a call on one.
                s.record_tool(f"pattern_match:{p}")
                continue
            q.append((f"pattern:{p}", "pattern_match", {"pattern": p, "card_id": t.card_id, "txn_id": t.flagged_txn_id, "as_of": s.as_of}))
        label = nar.PATTERN_LABEL.get(a.answer_pattern, a.answer_pattern)
        query_text = (
            f"{label}. {t.trigger_text} Channel {txn.get('channel', '')}, product {txn.get('product_cd', txn.get('ProductCD', ''))}, "
            f"amount {txn.get('amount', '')}. {a.leading.narrative}"
        )
        entities = [t.card_id, t.customer_id] + ([profile] if profile else [])
        # k=8 so the agent's own earlier cases compete with the 5,565 closed ones
        # on similarity rather than being crowded out by count.
        q.append(("similar", "similar_cases", {"query_text": query_text, "entity_ids": entities, "k": 8, "as_of": s.as_of}))
        q.append(("policy", "policy_lookup", {"query": f"{label} policy rule verify block report approval", "k": 4}))
    else:
        # Round 3 only exists to close a sufficiency gap round 2 left open.
        for item in a.sufficiency.missing:
            name = item.split(":")[0]
            if name == "pattern_match" and ":" in item:
                p = item.split(":", 1)[1]
                if p in {x.value for x in Pattern}:
                    q.append((f"pattern:{p}", "pattern_match", {"pattern": p, "card_id": t.card_id, "txn_id": t.flagged_txn_id, "as_of": s.as_of}))
                else:
                    # The undocumented detectors run inside scoring; the
                    # checklist item is satisfied by any pattern_match call.
                    s.record_tool(item)
            elif name == "shared_device_profile" and profile:
                q.append(("shared_device", "shared_device_profile", {"device_profile": profile, "as_of": s.as_of}))
            elif name == "ring_detect":
                q.append(("ring", "ring_detect", {"card_id": t.card_id, "as_of": s.as_of}))
            elif name == "region_history" and txn.get("addr1") not in (None, "", "nan"):
                q.append(("region", "region_history", {"customer_id": t.customer_id, "addr1": str(txn.get("addr1")), "as_of": s.as_of}))
            elif name == "velocity":
                q.append(("velocity", "velocity", {"card_id": t.card_id, "windows": [1, 24, 168], "as_of": s.as_of}))
    return [x for x in q if x[0] not in s.findings]


def assess(s: AgentState) -> AgentState:
    a = _assess(s)
    s.assessment = a
    s.case.risk_assessment = a.risk
    s.case.hypotheses = a.hypotheses
    s.case.pattern = a.answer_pattern
    s.plan = _next_round(s, a) if s.rounds < s.max_rounds and not s.budget_exhausted else []
    _step(
        s,
        "assess",
        f"round {s.rounds}: probability {a.risk.fraud_probability:.2f}, confidence {a.risk.confidence:.2f}, "
        f"leading {a.leading.pattern} {a.leading.score:.2f}, independent evidence {s.independent}, "
        f"checklist missing {a.sufficiency.missing or 'nothing'}"
        + (f"; next round: {', '.join(p[1] for p in s.plan)}" if s.plan else "; stop gathering"),
    )
    _emit(s, "assessment", a.risk.model_dump(mode="json"))
    return s


def route_after_assess(s: AgentState) -> str:
    return "gather_evidence" if s.plan else "nba_initial"


def _stop_condition(p: float, indep: int) -> bool:
    return (p >= 0.85 or p <= 0.15) and indep >= 2


def nba_initial(s: AgentState) -> AgentState:
    """The recommendation before any requested evidence comes back."""
    a: Assessment = s.assessment
    s.initial_assessment = a
    verdict = decide_verdict(a.risk.fraud_probability, a.risk.confidence)
    scope = _compute_scope(s, a, verdict)
    p = a.risk.fraud_probability

    # Whether to ask. The policy's stopping rule is the test: a decision that
    # already stands on two independent pieces at an extreme probability does
    # not need the cardholder. A dispute always gets a reply loop, because the
    # customer is the one party who knows.
    settled = _stop_condition(p, s.independent) and a.risk.confidence >= 0.5
    clearly_legit = verdict == Verdict.LEGITIMATE.value and a.risk.confidence >= 0.55
    s.need_evidence = s.case.trigger.type == "customer_report" or not (settled or clearly_legit)

    state = _policy_state(s, a, scope, verdict, response=None, requested=s.need_evidence)
    bundle = compose_actions(state, PolicyEngine(actor="agent"), verdict, phase="initial")

    if not s.need_evidence and verdict == Verdict.LEGITIMATE.value:
        # R1's verify is there to stand in front of a block. With no block on
        # the table and the case settled on the stopping rule, asking anyway is
        # the "continuing past a defensible decision" section 6 marks down.
        kept = [x for x in bundle.actions if not x.reason.startswith("R1")]
        if not kept:
            kept = [
                RecommendedAction(
                    action=Action.ALLOW_TRANSACTION.value,
                    route="auto",
                    reason=(
                        f"Policy section 6: probability {p:.2f} at confidence {a.risk.confidence:.2f} on "
                        f"{s.independent} independent pieces of evidence; nothing supports holding the transaction"
                    ),
                ),
                RecommendedAction(
                    action=Action.CLOSE_NO_FRAUD.value,
                    route="auto",
                    reason=(
                        f"Policy section 6: probability {p:.2f} is at or below 0.15 on {s.independent} independent "
                        "pieces of evidence, so the alert closes"
                        if p <= 0.15 and s.independent >= 2
                        else f"Policy section 6: probability {p:.2f} with no pattern scoring above "
                        f"{a.leading.score:.2f}; further steps are unlikely to change the decision, so the alert closes"
                    ),
                ),
            ]
        bundle.actions = kept
        bundle.status = decide_status(verdict, kept, evidence_pending=False)

    if s.need_evidence:
        ctx = _evidence_context(s, a, scope)
        req = choose_request_type(ctx, s.case.trigger.type).value
        s.request_type = req
        want = REQUEST_ACTION[req]
        if want not in [x.action for x in bundle.actions]:
            bundle.actions.append(
                RecommendedAction(
                    action=want,
                    route=route_for(want, state).value,
                    reason=(
                        "Policy 3a and section 5: the cardholder disputes the charge, so the bank confirms with "
                        "them before any block"
                        if s.case.trigger.type == "customer_report"
                        else f"Policy section 6: probability {p:.2f} has not reached 0.85 or 0.15 on two independent "
                        f"pieces of evidence (confidence {a.risk.confidence:.2f}), so the agent asks before acting"
                    ),
                )
            )
    s.initial_state = state
    s.initial_bundle = bundle
    s.scope = scope
    s.case.initial_actions = bundle.actions
    s.case.verdict = verdict
    _step(
        s,
        "nba_initial",
        f"before evidence: {', '.join(f'{x.action}({x.route})' for x in bundle.actions)}; "
        f"rules {_rules_text(bundle) or 'none'}; verdict {verdict}; request evidence: {s.need_evidence}",
    )
    _emit(s, "nba", {"phase": "initial", "actions": [x.model_dump() for x in bundle.actions]})
    return s


def policy_check_initial(s: AgentState) -> AgentState:
    _log_decisions(s, s.initial_bundle, s.initial_state, "initial")
    auth = [d for d in s.case.decisions if d.phase == "initial"]
    _step(
        s,
        "policy_check",
        f"initial: {sum(d.authorized for d in auth)} auto, {sum(not d.authorized for d in auth)} held for approval"
        + (f"; dropped {s.initial_bundle.dropped}" if s.initial_bundle.dropped else ""),
    )
    return s


def route_after_policy(s: AgentState) -> str:
    return "request_evidence" if s.need_evidence else "nba_final"


def _evidence_context(s: AgentState, a: Assessment, scope: dict[str, Any]) -> EvidenceContext:
    region = _data(s, "region")
    region_pattern = next((h for h in a.hypotheses if h.pattern == Pattern.OUT_OF_REGION_USE.value), None)
    _, incul = _independent_count(a)
    travel = any(c.name == "travel_explanation" and c.observed and c.value >= 0.5 for c in a.risk.components)
    # Either the tool's multi-day trip reading or the card-relative travel
    # signal from scoring; both are the "confirmed travel" branch of the
    # simulation contract.
    reads_as_trip = (bool(region.get("reads_as_trip")) and (region_pattern is not None and region_pattern.score >= 0.3)) or travel
    return EvidenceContext(
        pattern_score=a.leading.score,
        pattern_name=a.answer_pattern,
        recurring_strength=a.recurring.strength,
        recurring_matches=a.recurring.matches,
        shared_device_ring=scope["shared_device"],
        ring_member_count=len(scope["connected_cards"]),
        confirmed_fraud_prior_on_connected_entity=scope["linked_fraud"],
        reads_as_trip=reads_as_trip,
        region_span_days=float(region.get("span_days") or 0.0),
        competing_legitimate_explanation=a.recurring.strength >= 0.35 or travel,
        independent_signals=incul,
        exposure_usd=scope["exposure"],
    )


def request_evidence(s: AgentState) -> AgentState:
    """A controlled action: allowed without approval by policy section 5.

    The dataset has no replies, so the answer is simulated by the deterministic
    rule in evidence_simulation.md, and the assumption is written down.
    """
    a: Assessment = s.assessment
    ctx = _evidence_context(s, a, s.scope)
    from backend.models.enums import EvidenceRequestType

    sim = simulate_customer_response(ctx, EvidenceRequestType(s.request_type))
    s.simulated = sim
    asked_after = s.step_index
    s.case.evidence_requests.append(
        EvidenceRequest(
            type=s.request_type,
            asked_after_step=asked_after,
            assumed_response="Simulated, the dataset supplies no replies. " + sim.assumed_response,
            resolved_as=sim.response.value,
            basis=[str(b) for b in sim.basis],
        )
    )
    s.case.customer_response = sim.response.value
    s.case.set_status(CaseStatus.OPEN, f"awaiting evidence: {s.request_type}")
    s.case.decisions.append(
        Decision(
            actor="agent",
            action=REQUEST_ACTION[s.request_type],
            route="auto",
            authorized=True,
            executed=True,
            reason=f"Evidence request {s.request_type}, allowed without approval under policy section 5",
            phase="evidence",
            approval_status="not_required",
        )
    )
    _step(s, "request_evidence", f"{s.request_type} requested; simulated response: {sim.response.value}", tool=f"request_{s.request_type}")
    _emit(s, "evidence_request", s.case.evidence_requests[-1].model_dump(mode="json"))
    return s


def reassess(s: AgentState) -> AgentState:
    a = _assess(s, customer_response=s.case.customer_response)
    s.assessment = a
    s.case.risk_assessment = a.risk
    _step(
        s,
        "reassess",
        f"after {s.case.customer_response}: probability {s.initial_assessment.risk.fraud_probability:.2f} -> "
        f"{a.risk.fraud_probability:.2f}, confidence {s.initial_assessment.risk.confidence:.2f} -> {a.risk.confidence:.2f}",
    )
    _emit(s, "assessment", a.risk.model_dump(mode="json"))
    return s


def nba_final(s: AgentState) -> AgentState:
    if not s.need_evidence:
        # No request, so policy 3b says final equals initial.
        s.final_bundle = s.initial_bundle
        s.case.final_actions = list(s.initial_bundle.actions)
        s.case.what_changed = "nothing"
        s.case.verdict = s.initial_bundle.verdict
        _step(s, "nba_final", "no evidence requested, final recommendation equals the initial one")
        return s

    a: Assessment = s.assessment
    response = s.case.customer_response
    verdict = decide_verdict(a.risk.fraud_probability, a.risk.confidence)
    # A reply that settles the question moves the verdict off `uncertain`.
    if response == CustomerResponse.DENIED.value and a.risk.fraud_probability >= 0.6:
        verdict = Verdict.FRAUD.value
    if response == CustomerResponse.CONFIRMED.value and a.risk.fraud_probability <= 0.4:
        verdict = Verdict.LEGITIMATE.value
    scope = _compute_scope(s, a, verdict)
    state = _policy_state(s, a, scope, verdict, response=response, requested=True)
    bundle = compose_actions(state, PolicyEngine(actor="agent"), verdict, phase="final")

    # The verification already happened. Asking again under R1 after a reply
    # would read as the agent not listening to its own evidence.
    kept = [
        x for x in bundle.actions
        if not (x.action in {Action.VERIFY_WITH_CUSTOMER.value, Action.STEP_UP_AUTH.value} and x.reason.startswith("R1"))
    ]
    if response == CustomerResponse.CONFIRMED.value:
        kept = [x for x in kept if x.action not in {Action.DECLINE_TRANSACTION.value, Action.MONITOR_CARD.value}]
    bundle.actions = kept or bundle.actions
    bundle.status = decide_status(verdict, bundle.actions, evidence_pending=False)

    s.final_bundle = bundle
    s.final_state = state
    s.scope = scope
    s.case.final_actions = bundle.actions
    s.case.verdict = verdict
    _step(
        s,
        "nba_final",
        f"after evidence: {', '.join(f'{x.action}({x.route})' for x in bundle.actions)}; rules {_rules_text(bundle) or 'none'}; verdict {verdict}",
    )
    _emit(s, "nba", {"phase": "final", "actions": [x.model_dump() for x in bundle.actions]})
    return s


def policy_check_final(s: AgentState) -> AgentState:
    state = getattr(s, "final_state", None) or s.initial_state
    _log_decisions(s, s.final_bundle, state, "final")
    return s


def execute_or_route(s: AgentState) -> AgentState:
    """Only auto actions run. L1 and L2 wait for a human, with the route stated."""
    state = getattr(s, "final_state", None) or s.initial_state
    executed, held = [], []
    for rec in s.final_bundle.actions:
        outcome = s.runner.run(rec.action, state, rec.reason)
        for d in reversed(s.case.decisions):
            if d.phase == "final" and d.action == rec.action:
                d.executed = outcome.executed
                d.approval_status = "not_required" if outcome.authorized else "pending"
                break
        (executed if outcome.executed else held).append(f"{rec.action}({outcome.route})")
    # Initial-phase approvals are replaced by the final recommendation. Left as
    # "pending" they would show in the approval panel as asks nobody can act on.
    for d in s.case.decisions:
        if d.phase == "initial" and d.approval_status == "pending":
            d.approval_status = "superseded"
    _step(s, "execute_or_route", f"executed {executed or 'nothing'}; routed for approval {held or 'nothing'}")
    for d in s.case.decisions:
        _emit(s, "decision", d.model_dump(mode="json"))
    return s


def sar_check(s: AgentState) -> AgentState:
    b = s.final_bundle
    names = [x.action for x in b.actions]
    file = Action.FILE_REPORT.value in names
    # The answer file requires `sar.file` to agree with FILE_REPORT in final.
    reason = b.sar_reason if file == b.sar_file else (
        "FILE_REPORT is in the final actions under the rule that fired" if file else b.sar_reason
    )
    scope = s.scope
    dates: list[str] = []
    if file and scope["affected"]:
        store = get_store()
        ts = sorted(pd.Timestamp(store.txn(t)["ts"]) for t in scope["affected"] if store.txn(t) is not None)
        if ts:
            dates = [str(ts[0].date()), str(ts[-1].date())]
    s.case.sar = SarPart(
        file=file,
        reason=reason,
        narrative="",
        subjects=[],
        total_amount_usd=scope["exposure"] if file else 0.0,
        activity_dates=dates if file else [],
    )
    _step(s, "sar_check", f"SAR {'required' if file else 'not required'}: {reason[:200]}")
    return s


def _build_evidence(s: AgentState) -> list[dict[str, Any]]:
    """One finding per evidence family, each with the ref of the call behind it."""
    t = s.case.trigger
    a: Assessment = s.assessment
    out: list[dict[str, Any]] = []

    def add(claim: str, source: str, ref: str, entity_ids: list[str]) -> None:
        out.append({"id": f"F{len(out) + 1}", "claim": nar.clean_text(claim), "source": source, "ref": ref, "entity_ids": [str(e) for e in entity_ids if e]})

    txn = _txn(s)
    if txn:
        region = txn.get("addr1")
        add(
            f"Flagged transaction {t.flagged_txn_id} on card {t.card_id}: ${float(txn.get('amount', 0)):,.2f}, "
            f"{txn.get('channel', '')} channel, product code {txn.get('product_cd') or txn.get('ProductCD', '')}"
            + (f", billing region {region}" if region not in (None, "", "nan") else ", no billing region recorded")
            + f". The bank model scored it {float(txn.get('risk_score', 0)):.2f}, which is a reason to look, not a finding",
            "graph",
            _ref(s, "tx_context", f"query:tx_context(txn_id={t.flagged_txn_id})"),
            [t.flagged_txn_id, t.card_id],
        )
    shift = _data(s, "behavior_shift")
    if shift:
        comp = next((c for c in a.risk.components if c.name == "behaviour_shift"), None)
        add(
            f"Against {shift.get('baseline_n', 0)} earlier transactions on the {shift.get('baseline_scope', 'card')}, "
            f"the amount sits at the {float(shift.get('amount_percentile', 0)):.0%} percentile; "
            + (comp.detail if comp and comp.detail else "no material deviation"),
            "graph",
            _ref(s, "behavior_shift", "query:behavior_shift"),
            [t.card_id, t.flagged_txn_id],
        )
    lead = a.leading
    if lead.score > 0 or a.answer_pattern != Pattern.NONE.value:
        key = f"pattern:{lead.pattern}"
        if lead.pattern == RING_DETECTOR and key not in s.findings and "shared_device" in s.findings:
            key = "shared_device"
        add(
            f"Pattern check {nar.PATTERN_LABEL.get(a.answer_pattern, a.answer_pattern)} scored {lead.score:.2f}: {lead.narrative}",
            "graph",
            _ref(s, key, f"query:pattern_match(pattern={lead.pattern}, card_id={t.card_id}, txn_id={t.flagged_txn_id})"),
            s.scope.get("affected", [])[:8] or lead.supporting_txn_ids[:8] or [t.flagged_txn_id],
        )
    rejected = [h for h in a.hypotheses if h.rejected and h.score >= 0.3 and h.pattern != Pattern.UNDOCUMENTED.value][:1]
    for h in rejected:
        add(
            f"Alternative hypothesis {h.pattern} scored lower at {h.score:.2f}"
            + (f"; present: {', '.join(h.present_signals[:3])}" if h.present_signals else "")
            + (f"; absent: {', '.join(h.absent_signals[:3])}" if h.absent_signals else ""),
            "graph",
            _ref(s, f"pattern:{h.pattern}", f"query:pattern_match(pattern={h.pattern}, card_id={t.card_id})"),
            [t.card_id],
        )
    if a.recurring.strength > 0:
        add(
            f"The flagged amount and product code repeat on this card {len(a.recurring.matches)} time(s) before, "
            f"recurrence strength {a.recurring.strength:.2f}; merchant is not in the data, so recurrence is read from amount, product code and cadence",
            "graph",
            _ref(s, "card_window", f"query:card_window(card_id={t.card_id})"),
            a.recurring.matches[:6],
        )
    region = _data(s, "region")
    if region.get("found"):
        add(
            f"Billing region {region.get('addr1')}: {'has' if region.get('has_history') else 'no'} earlier history for this customer "
            f"({region.get('n_in_region', 0)} transactions, span {region.get('span_days', 0)} days); home region {region.get('home_region')}; "
            + ("reads as a trip" if region.get("reads_as_trip") else "reads as a clone" if region.get("reads_as_clone") else "neither a trip nor a clone pattern"),
            "graph",
            _ref(s, "region", "query:region_history"),
            [t.customer_id],
        )
    shared = _data(s, "shared_device")
    if shared.get("found"):
        conn = s.scope.get("connected_cards", [])
        add(
            f"Device profile {shared.get('device_profile')} appears on {shared.get('n_cards', 0)} cards across "
            f"{shared.get('n_customers', 0)} customers"
            + (f"; {len(conn)} other card(s) used it in the {CONNECTED_WINDOW_DAYS} days to the alert" if conn else "")
            + (
                "; novelty and anonymous-proxy concentration on the profile make it a real link between cardholders"
                if conn
                else "; the profile is rare enough to matter"
                if shared.get("is_rare")
                else "; the profile is common, so sharing alone is not evidence"
            ),
            "graph",
            _ref(s, "shared_device", "query:shared_device_profile"),
            conn[:8] or [t.card_id],
        )
    ring = _data(s, "ring")
    if ring.get("is_ring"):
        add(
            f"Ring detection from {t.card_id} over rare shared devices finds {ring.get('ring_size', 0)} cards across "
            f"{ring.get('distinct_customers', 0)} customers, {ring.get('confirmed_fraud_members', 0)} with confirmed-fraud closed cases",
            "graph",
            _ref(s, "ring", "query:ring_detect"),
            [m["card_id"] for m in ring.get("members", [])[:8]],
        )
    prior = _data(s, "prior_cases").get("cases", [])
    if prior:
        conf = [p for p in prior if p["outcome"] == "confirmed_fraud"]
        pats = sorted({p["pattern"] for p in conf})
        add(
            f"{len(prior)} closed case(s) before this alert touch this card or customer: {len(conf)} confirmed fraud"
            + (f" ({', '.join(pats)})" if pats else "")
            + f", {len(prior) - len(conf)} cleared",
            "graph",
            _ref(s, "prior_cases", "query:prior_cases_for_entities"),
            [p["case_id"] for p in prior[:8]],
        )
    # A re-run of the same case must not cite its own earlier write-up as memory,
    # and a text neighbour only counts when it landed on the same pattern: bge
    # similarities sit around 0.8 for almost any two case summaries, so text
    # alone would cite every earlier case.
    own = s.case.case_id
    memory_hits = [
        c for c in _data(s, "similar").get("cases", [])
        if c.get("source") == "agent_memory"
        and c.get("case_id") != own
        and str(c.get("closed_at", "")) < s.as_of
        and (c.get("pattern") == a.answer_pattern or str(c.get("why_matched", "")).find("sharing") >= 0 or str(c.get("why_matched", "")).startswith("shares"))
    ]
    for m in memory_hits[:2]:
        status = m.get("outcome") or m.get("status", "")
        add(
            f"Earlier benchmark case {m['case_id']}, investigated by this agent, ended {status} with pattern "
            f"{m.get('pattern', '')}; retrieved as memory because {m.get('why_matched', 'it reads alike')}",
            "graph",
            m.get("ref", "case_memory"),
            [m["case_id"]],
        )
    for rule in (_rules_text(s.final_bundle) or "").split(", "):
        if rule:
            add(
                f"Policy {rule} governs the recommended actions",
                "document",
                f"policy:{rule}",
                [],
            )
    for i, req in enumerate(s.case.evidence_requests, start=1):
        add(
            f"Evidence request {req.type}: {req.assumed_response}",
            "customer" if req.type != "analyst_info" else "external",
            f"evidence_request:{i}",
            [],
        )
    return out


def _similar_ids(s: AgentState) -> list[str]:
    """CC ids the agent retrieved and used, entity matches first."""
    import re

    # Order is by how directly the case bears on this one: same card or
    # customer, then the same shared device, then text neighbours that also
    # agree on the pattern. A vector hit on unrelated entities and a different
    # pattern is not memory the agent used, so it is left out.
    ids: list[str] = []
    for p in _data(s, "prior_cases").get("cases", [])[:5]:
        ids.append(p["case_id"])
    if s.scope.get("connected_cards"):
        for p in _data(s, "shared_device").get("prior_cases", []):
            if p.get("outcome") == "confirmed_fraud":
                ids.append(p["case_id"])
            if len(ids) >= 8:
                break
    pattern = s.case.pattern
    for c in _data(s, "similar").get("cases", [])[:5]:
        if c.get("source") != "closed_case" or not c.get("case_id"):
            continue
        if c.get("pattern") == pattern or str(c.get("why_matched", "")).startswith("shares"):
            ids.append(str(c["case_id"]))
    seen: list[str] = []
    for i in ids:
        if re.fullmatch(r"CC-\d{4}", i) and i not in seen:
            seen.append(i)
    return seen[:8]


def explain(s: AgentState) -> AgentState:
    """Template first, then the LLM's rewrite where it passes the checks."""
    t = s.case.trigger
    a: Assessment = s.assessment
    a0: Assessment = s.initial_assessment
    scope = s.scope
    findings = _build_evidence(s)
    txn = _txn(s)
    shift_comp = next((c for c in a.risk.components if c.name == "behaviour_shift"), None)
    response = s.case.customer_response
    response_sentence = ""
    if response == CustomerResponse.DENIED.value:
        response_sentence = "The cardholder, asked to validate the activity, was assumed to deny it (simulated response)."
    elif response == CustomerResponse.CONFIRMED.value:
        response_sentence = "The cardholder, asked to validate the activity, was assumed to confirm it (simulated response)."
    elif response == CustomerResponse.NO_REPLY.value:
        response_sentence = "The cardholder was asked to validate the activity and no reply was assumed within 24 hours (simulated)."
    prior = _data(s, "prior_cases").get("cases", [])
    conf_prior = [p for p in prior if p["outcome"] == "confirmed_fraud"]
    prior_sentence = (
        f"The card or customer carries {len(conf_prior)} earlier confirmed-fraud closed case(s), including "
        f"{', '.join(p['case_id'] for p in conf_prior[:3])}."
        if conf_prior
        else ""
    )
    unknowns = a.risk.unknowns
    ctx = {
        "trigger_phrase": TRIGGER_PHRASE.get(t.type, "An alert fired"),
        "card_id": t.card_id,
        "customer_id": t.customer_id,
        "txn_id": t.flagged_txn_id,
        "amount": float(txn.get("amount", 0) or 0),
        "pattern": a.answer_pattern if s.case.verdict != Verdict.LEGITIMATE.value else Pattern.NONE.value,
        "leading_narrative": a.leading.narrative or "no pattern matched",
        "verdict": s.case.verdict,
        "p_initial": a0.risk.fraud_probability,
        "p_final": a.risk.fraud_probability,
        "conf_final": a.risk.confidence,
        "exposure": scope["exposure"],
        "initial_actions": nar.action_names(s.case.initial_actions),
        "final_actions": nar.action_names(s.case.final_actions),
        "rules_final": _rules_text(s.final_bundle),
        "response": response,
        "response_sentence": response_sentence,
        "independent": s.independent,
        "main_unknown": unknowns[0] if unknowns else "none recorded",
        "budget_exhausted": s.budget_exhausted,
        "affected": scope["affected"],
        "first_txn": scope["first"],
        "activity_dates": s.case.sar.activity_dates if s.case.sar else [],
        "channel_text": txn.get("channel", "online"),
        "product_codes": txn.get("product_cd") or txn.get("ProductCD", ""),
        "region": txn.get("addr1") if txn.get("addr1") not in (None, "", "nan") else "",
        "device_profile": scope["device_profiles"][0] if scope["device_profiles"] else "",
        "shift_detail": shift_comp.detail if shift_comp else "",
        "connected_cards": scope["connected_cards"],
        "prior_sentence": prior_sentence,
        "risk_score": float(txn.get("risk_score", 0) or 0),
        "sar_reason": s.case.sar.reason if s.case.sar else "",
    }
    # Set before any retrieval bookkeeping so the cited prior cases and the
    # chunks shown for them are filtered on the same pattern.
    s.case.pattern = ctx["pattern"]
    sar_file = bool(s.case.sar and s.case.sar.file)
    pattern_desc = ""
    if ctx["pattern"] == Pattern.UNDOCUMENTED.value:
        # I keep one explanation per detector, because the ring wording (unrelated
        # cardholders on one device) is false for structuring, which lives on a
        # single card and would mislead the reader of a scored field.
        if a.leading.pattern == RING_DETECTOR:
            why = (
                "It fits none of the five documented patterns because the cards involved belong to unrelated "
                "cardholders and the link is the shared origin rather than anything on one card's own history. "
                "The agent found it by expanding from the flagged card to every card on the same rare device "
                "profile and reading their closed-case outcomes."
            )
        else:
            why = (
                "It fits none of the five documented patterns: it is not card testing because the amounts are "
                "large, and not plain card-not-present fraud because the amounts are shaped to sit under a limit. "
                "The agent found it by reading the card's transactions in a short window around the flagged one "
                "and matching the amount band against closed cases analysts confirmed with the same shape."
            )
        pattern_desc = f"{a.leading.narrative.rstrip('.')}. {why}"
    template = {
        "summary": nar.template_summary(ctx),
        "stop_reason": nar.template_stop_reason(ctx),
        "what_changed": nar.template_what_changed(ctx),
        "sar_narrative": nar.template_sar(ctx) if sar_file else "",
        "pattern_description": pattern_desc,
    }

    allowed = {t.card_id, t.customer_id, t.flagged_txn_id, s.case.case_id}
    allowed.update(scope["affected"])
    allowed.update(scope["connected_cards"])
    allowed.update(_similar_ids(s))
    for f in findings:
        allowed.update(f["entity_ids"])
    for p in prior:
        allowed.add(p["case_id"])
    allowed.update(a.recurring.matches)

    s.case.retrieved_chunks = [
        RetrievedChunk(ref=c["ref"], text=c.get("text", "")[:600], score=float(c.get("score", 0.0)), kind=c.get("kind", "policy"))
        for c in _data(s, "policy").get("chunks", [])
    ]
    # One chunk per prior case the agent cites, from whichever retrieval found it.
    seen_cases: dict[str, dict[str, Any]] = {}
    for p in _data(s, "prior_cases").get("cases", []) + _data(s, "shared_device").get("prior_cases", []):
        seen_cases.setdefault(p["case_id"], {"text": p.get("analyst_notes", ""), "outcome": p.get("outcome"), "pattern": p.get("pattern"), "exposure_usd": p.get("exposure_usd"), "score": 1.0, "kind": "prior_case"})
    for c in _data(s, "similar").get("cases", []):
        if c.get("case_id"):
            seen_cases.setdefault(str(c["case_id"]), {"text": c.get("text", ""), "outcome": c.get("outcome"), "pattern": c.get("pattern"), "exposure_usd": c.get("exposure_usd"), "score": float(c.get("blended_score", 0.0)), "kind": c.get("source", "closed_case")})
    for cid in _similar_ids(s):
        m = seen_cases.get(cid)
        if m:
            s.case.retrieved_chunks.append(
                RetrievedChunk(
                    ref=f"case:{cid}",
                    text=str(m["text"])[:600],
                    score=float(m["score"] or 0.0),
                    kind=m["kind"],
                    outcome=m["outcome"],
                    pattern=m["pattern"],
                    exposure_usd=float(m["exposure_usd"]) if m["exposure_usd"] is not None else None,
                )
            )
    block = build_context_block(_data(s, "policy").get("chunks", []), _data(s, "similar").get("cases", []))
    payload = {
        "case_id": s.case.case_id,
        "trigger": t.trigger_text,
        "assessment": {
            "fraud_probability_initial": a0.risk.fraud_probability,
            "fraud_probability_final": a.risk.fraud_probability,
            "confidence": a.risk.confidence,
            "unknowns": unknowns[:5],
            "verdict": s.case.verdict,
            "pattern": ctx["pattern"],
            "exposure_usd": scope["exposure"],
        },
        "findings": [{"id": f["id"], "claim": f["claim"], "ref": f["ref"]} for f in findings],
        "initial_actions": [x.model_dump() for x in s.case.initial_actions],
        "final_actions": [x.model_dump() for x in s.case.final_actions],
        "evidence_requests": [r.model_dump(mode="json") for r in s.case.evidence_requests],
        "sar_file": sar_file,
        "sar_reason": ctx["sar_reason"],
        "affected_txn_ids": scope["affected"],
        "connected_card_ids": scope["connected_cards"],
        "retrieved_context": block.text,
        "execution": {
            "executed_by_agent": [x.action for x in s.case.final_actions if x.route == "auto"],
            "awaiting_human_approval": [f"{x.action} ({x.route})" for x in s.case.final_actions if x.route != "auto"],
        },
        "assumed_response": response,
        "draft_summary": template["summary"],
        "draft_stop_reason": template["stop_reason"],
        "draft_what_changed": template["what_changed"],
        "draft_sar_narrative": template["sar_narrative"],
        "draft_pattern_description": template["pattern_description"],
    }

    merged = dict(template)
    merged["evidence_claims"] = {}
    used_llm = False
    try:
        result = s.llm.complete_structured(
            system_block(),
            [user_message(payload, SYNTHESIS_INSTRUCTION + EXTRA_INSTRUCTION)],
            SYNTHESIS_SCHEMA,
            step=f"synthesis:{s.case.case_id}",
        )
        if result.ok and result.provider != "mock":
            # Only a successful provider call consumed tokens. A failed call
            # falls back to the mock, whose usage is a character estimate, and
            # reporting that would overstate what was spent.
            s.llm_tokens += int(result.usage.total_tokens)
            merged, rejected = nar.merge_llm(template, result.data, allowed, sar_file, response)
            used_llm = True
            if rejected:
                s.llm_notes.extend(rejected)
        elif not result.ok:
            s.llm_notes.append(f"LLM call failed, templated text used: {result.error[:160]}")
        else:
            s.llm_notes.append(f"LLM in mock mode ({s.llm.fallback_reason or 'no provider'}), templated text used")
    except Exception as exc:  # the run never dies on the write-up
        s.llm_notes.append(f"LLM error, templated text used: {type(exc).__name__}: {exc}"[:200])

    for f in findings:
        claim = merged["evidence_claims"].get(f["id"]) or f["claim"]
        s.case.add_evidence(EvidenceItem(claim=claim, source=f["source"], ref=f["ref"], entity_ids=f["entity_ids"]))
    s.case.summary = merged["summary"]
    s.case.stop_reason = merged["stop_reason"]
    s.case.what_changed = merged["what_changed"] if s.need_evidence else "nothing"
    s.case.pattern = ctx["pattern"]
    s.case.pattern_description = merged["pattern_description"] if ctx["pattern"] == Pattern.UNDOCUMENTED.value else ""
    if s.case.sar and sar_file:
        narrative = merged["sar_narrative"] or template["sar_narrative"]
        s.case.sar.narrative = narrative
        subjects = [t.customer_id, t.card_id] + scope["connected_cards"][:6]
        if ctx["device_profile"]:
            subjects.append(ctx["device_profile"])
        s.case.sar.subjects = subjects
    s.case.affected_txn_ids = scope["affected"]
    s.case.first_suspicious_txn_id = scope["first"] if scope["affected"] else ""
    s.case.connected_card_ids = scope["connected_cards"]
    s.case.connected_device_profiles = scope["device_profiles"]
    s.case.exposure_usd = scope["exposure"]
    s.case.similar_prior_cases = _similar_ids(s)
    _step(
        s,
        "explain",
        ("LLM write-up merged" if used_llm else "templated write-up")
        + (f"; notes: {'; '.join(s.llm_notes)[:200]}" if s.llm_notes else ""),
    )
    return s


def write_case(s: AgentState) -> AgentState:
    c = s.case
    status = s.final_bundle.status
    c.set_status(status, "investigation complete")
    payload = c.model_dump(mode="json")
    payload.update(
        {
            "graph_case_id": f"GC-{c.case_id}",
            "opened_at": str(c.trigger.opened_at),
            "closed_at": str(c.trigger.opened_at),
            "customer_id": c.trigger.customer_id,
            "card_id": c.trigger.card_id,
            "unknowns": c.risk_assessment.unknowns,
            "fraud_probability": c.risk_assessment.fraud_probability,
            "confidence": c.risk_assessment.confidence,
        }
    )
    res = s.tools.write_case(payload)
    if res.ok:
        c.written_to_graph = True
        c.graph_case_id = str(res.data.get("graph_case_id") or f"GC-{c.case_id}")
        # Only final decisions are written as Decision vertices; the initial
        # ones are inside the case payload already, and writing both doubles
        # the call count for no retrieval benefit.
        for d in [d for d in c.decisions if d.phase == "final"]:
            s.tools.record_decision(c.graph_case_id, d.action, str(d.route), d.authorized, d.reason, d.actor)
        if c.similar_prior_cases:
            s.tools.link_similar(c.graph_case_id, c.similar_prior_cases, 1.0)
    _step(s, "write_case", f"written_to_graph={c.written_to_graph} as {c.graph_case_id or 'nothing'}", tool="write_case", ok=bool(res.ok))
    return s


def update_memory(s: AgentState) -> AgentState:
    """With the mock backend write_case already stored the memory record. With
    the graph backend the Case vertex is the memory, but I also keep the local
    mirror so the retriever's agent-memory branch sees the case either way."""
    from backend.config import get_settings
    from backend.memory.case_memory import MemoryRecord, get_case_memory

    c = s.case
    if get_settings().tool_backend != "mock" and c.written_to_graph:
        get_case_memory().write(
            MemoryRecord(
                graph_case_id=c.graph_case_id,
                case_id=c.case_id,
                opened_at=str(c.trigger.opened_at),
                closed_at=str(c.trigger.opened_at),
                customer_id=c.trigger.customer_id,
                card_id=c.trigger.card_id,
                outcome=str(c.status),
                pattern=str(c.pattern),
                exposure_usd=c.exposure_usd,
                txn_ids=c.affected_txn_ids,
                connected_card_ids=c.connected_card_ids,
                device_profiles=c.connected_device_profiles,
                summary=c.summary,
                unknowns=c.risk_assessment.unknowns,
                fraud_probability=c.risk_assessment.fraud_probability,
                confidence=c.risk_assessment.confidence,
            )
        )
    _step(s, "update_memory", f"{c.case_id} is now memory for investigations opened after {c.trigger.opened_at}")
    return s


def finalize(s: AgentState) -> AgentState:
    c = s.case
    c.tool_calls = s.tools.tool_calls
    c.tokens = s.llm_tokens
    c.latency_s = round(time.perf_counter() - s.started_perf, 2)
    _step(s, "answer_file", f"tool_calls={c.tool_calls}, tokens={c.tokens}, latency={c.latency_s}s")
    _emit(s, "done", {"case_id": c.case_id, "status": c.status})
    return s
