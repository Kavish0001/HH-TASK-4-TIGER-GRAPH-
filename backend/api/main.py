"""FastAPI app for the analyst dashboard.

    GET  /health
    GET  /cases                     every case-pack case as an InternalCase (blank until run)
    POST /cases                     open an ad hoc case from a trigger (launcher)
    GET  /cases/{id}                one InternalCase, live while a run is in flight
    GET  /cases/{id}/answer         the graded answer-file projection (submission preview)
    POST /cases/{id}/run            SSE stream of agent steps; ?stream=false starts a
                                    background run and returns 202 for polling
    POST /cases/{id}/approve        approve or reject an L1/L2 action

Run with:  uvicorn backend.api.main:app --port 8000

The dashboard renders InternalCase, not the answer file, per
backend/contracts/internal_case.md. I serve the answer file separately so the
demo can show exactly what gets submitted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.agent.graph import run_case, trigger_from_row
from backend.answers.write import answer_dict, load_internal, write_answer, write_internal
from backend.config import get_settings
from backend.models.internal_case import Decision, InternalCase
from backend.tools.factory import build_toolset

log = logging.getLogger("backend.api")

@asynccontextmanager
async def lifespan(_: FastAPI):
    _warm()
    yield


app = FastAPI(title="HHGOA fraud investigation agent", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # The dashboard runs on localhost:3000 in dev; the regex keeps a deployed
    # frontend working without a code change. No cookies, so no credentials.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=".*",
    allow_methods=["*"],
    allow_headers=["*"],
)

# One investigation at a time. Case memory is ordered by trigger time, and two
# runs writing it concurrently would make a demo run depend on click order.
_RUN_LOCK = threading.Lock()
_ROWS: dict[str, dict[str, Any]] = {}
_CASES: dict[str, InternalCase] = {}
_LIVE: dict[str, dict[str, Any]] = {}
_RUNNING: set[str] = set()


class ApprovalRequest(BaseModel):
    action: str
    decision: str | None = None
    approved: bool | None = None
    analyst: str = "analyst"
    note: str = ""


class NewCaseRequest(BaseModel):
    trigger_type: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    risk_score: float | None = None
    trigger_text: str = ""
    opened_at: str | None = None


def _load_rows() -> dict[str, dict[str, Any]]:
    if not _ROWS:
        df = pd.read_csv(get_settings().data_dir / "case_pack.csv")
        for row in df.to_dict(orient="records"):
            _ROWS[row["case_id"]] = row
    return _ROWS


def _blank(case_id: str) -> InternalCase:
    row = _load_rows()[case_id]
    return InternalCase(case_id=case_id, trigger=trigger_from_row(row))


def _get(case_id: str) -> dict[str, Any]:
    if case_id not in _load_rows():
        raise HTTPException(404, f"unknown case {case_id}")
    if case_id in _LIVE and case_id in _RUNNING:
        return _LIVE[case_id]
    if case_id not in _CASES:
        stored = load_internal(case_id)
        if stored is not None:
            _CASES[case_id] = stored
    case = _CASES.get(case_id) or _blank(case_id)
    return case.model_dump(mode="json")


def _warm() -> None:
    # Loading the store and the embedding model takes about twenty seconds. I do
    # it in the background at boot so the first demo click does not pay for it.
    def warm() -> None:
        try:
            from backend.graphrag.index import get_index
            from backend.tools.mock.store import get_store

            get_store()
            get_index()
            log.info("store and GraphRAG index warm")
        except Exception:
            log.exception("warm-up failed; the first run will load lazily")

    threading.Thread(target=warm, daemon=True).start()


@app.get("/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {"ok": True, "tool_backend": s.tool_backend, "llm_provider": s.llm_provider, "dry_run": s.dry_run}


@app.get("/cases")
def list_cases() -> list[dict[str, Any]]:
    rows = _load_rows()
    ordered = sorted(rows, key=lambda cid: str(rows[cid]["opened_at"]))
    return [_get(cid) for cid in ordered]


@app.post("/cases")
def create_case(req: NewCaseRequest) -> dict[str, Any]:
    """An ad hoc trigger from the launcher. Not a graded case, so no answer file."""
    from backend.tools.mock.store import get_store

    txn = get_store().txn(req.flagged_txn_id)
    if txn is None:
        raise HTTPException(404, f"transaction {req.flagged_txn_id} not found")
    opened = req.opened_at or str(pd.Timestamp(txn["ts"]) + timedelta(hours=1))
    n = sum(1 for k in _load_rows() if k.startswith("ADHOC-")) + 1
    case_id = f"ADHOC-{n:03d}"
    _ROWS[case_id] = {
        "case_id": case_id,
        "opened_at": opened[:19],
        "trigger_type": req.trigger_type,
        "trigger_text": req.trigger_text or f"{req.trigger_type} on transaction {req.flagged_txn_id}",
        "flagged_txn_id": req.flagged_txn_id,
        "card_id": req.card_id,
        "customer_id": req.customer_id,
        "risk_score": req.risk_score,
    }
    return {"case_id": case_id}


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    return _get(case_id)


@app.get("/cases/{case_id}/answer")
def get_answer(case_id: str) -> dict[str, Any]:
    _get(case_id)
    case = _CASES.get(case_id) or load_internal(case_id)
    if case is None:
        raise HTTPException(404, f"{case_id} has not been run yet")
    return answer_dict(case)


def _execute(case_id: str, emit) -> InternalCase:
    row = _load_rows()[case_id]
    with _RUN_LOCK:
        _RUNNING.add(case_id)
        _LIVE[case_id] = _blank(case_id).model_dump(mode="json")
        try:
            case = run_case(row, emit=emit)
            if case_id.startswith("HHG-"):
                write_answer(case)
            write_internal(case)
            _CASES[case_id] = case
            return case
        finally:
            _RUNNING.discard(case_id)


def _live_emit(case_id: str, sink=None):
    def emit(kind: str, payload: dict[str, Any]) -> None:
        snap = payload.get("case")
        if snap:
            _LIVE[case_id] = snap
        elif kind == "step":
            live = _LIVE.setdefault(case_id, {})
            live.setdefault("steps", []).append(payload)
        if sink:
            sink(kind, payload)

    return emit


@app.post("/cases/{case_id}/run")
async def run(case_id: str, background: BackgroundTasks, stream: bool = Query(True)):
    _get(case_id)
    if case_id in _RUNNING:
        raise HTTPException(409, f"{case_id} is already running")

    if not stream:
        background.add_task(_execute, case_id, _live_emit(case_id))
        return JSONResponse({"case_id": case_id, "status": "started", "poll": f"/cases/{case_id}"}, status_code=202)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def sink(kind: str, payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))

    def worker() -> None:
        try:
            case = _execute(case_id, _live_emit(case_id, sink))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", case.model_dump(mode="json")))
        except Exception as exc:  # the stream reports the failure instead of hanging
            log.exception("run %s failed", case_id)
            loop.call_soon_threadsafe(queue.put_nowait, ("error", {"message": f"{type(exc).__name__}: {exc}"}))

    threading.Thread(target=worker, daemon=True).start()

    async def events():
        while True:
            kind, payload = await queue.get()
            if kind == "step":
                data = {k: v for k, v in payload.items() if k != "case"}
            elif kind in ("done", "error"):
                data = payload
            else:
                # Milestones carry the whole case so the view can merge it.
                data = payload.get("case", payload)
            yield {"event": kind, "data": json.dumps(data, default=str)}
            if kind in ("done", "error"):
                break

    return EventSourceResponse(events(), ping=15)


@app.post("/cases/{case_id}/approve")
def approve(case_id: str, req: ApprovalRequest) -> dict[str, Any]:
    """A human decision on an L1 or L2 recommendation.

    The agent never executes these itself. An approval is logged as the
    analyst's decision, executed on their authority, and written to the graph
    as its own Decision, so the record shows who allowed what.
    """
    _get(case_id)
    case = _CASES.get(case_id) or load_internal(case_id)
    if case is None:
        raise HTTPException(409, f"{case_id} has not been run yet")
    approved = req.approved if req.approved is not None else (req.decision or "").lower() in {"approve", "approved", "yes"}
    targets = [d for d in case.decisions if d.action == req.action and d.phase == "final" and not d.authorized]
    if not targets:
        raise HTTPException(404, f"no {req.action} awaiting approval on {case_id}")
    status = "approved" if approved else "rejected"
    for d in targets:
        d.approval_status = status
        d.executed = approved
    route = targets[0].route
    case.decisions.append(
        Decision(
            actor=req.analyst or "analyst",
            action=req.action,
            route=route,
            authorized=approved,
            executed=approved,
            reason=req.note or f"{route} {status} by {req.analyst}",
            phase="approval",
            approval_status=status,
            at=datetime.utcnow(),
        )
    )
    if case.written_to_graph and case.graph_case_id:
        try:
            build_toolset().record_decision(case.graph_case_id, req.action, str(route), approved, req.note or status, req.analyst)
        except Exception:
            log.exception("record_decision failed for approval on %s", case_id)
    _CASES[case_id] = case
    write_internal(case)
    return case.model_dump(mode="json")
