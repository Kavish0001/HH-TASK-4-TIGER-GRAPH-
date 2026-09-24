# Internal case vs answer file

Resolves a conflict between `PLAN.md` and `data/README.md`. The README wins,
because it is what the graders read.

## The conflict

`PLAN.md` section 3 specifies a case object carrying `trigger`, `risk_assessment`
with `confidence` and `unknowns[]`, a status history, and per-action approval
outcomes. None of those exist in the README's answer format, and
`answer.schema.json` is `additionalProperties: false`, so writing them into a
`cases/<case_id>.json` file makes it fail validation.

Dropping them is not an option either. Confidence separate from risk is what
drives the decision to request evidence rather than act, which is the 25 percent
next-best-action score. The dashboard needs trigger type and age for the queue.

## The resolution: two objects, one derived from the other

**`InternalCase`** is the live object. The agent builds it, the graph stores it,
the dashboard renders it over SSE. It carries everything in `PLAN.md` section 3:

- `trigger`: type (`risk_score` / `customer_report` / `analyst_request`), source,
  `trigger_text`, `opened_at`
- `status_history[]`: each entry a status plus a timestamp
- `risk_assessment`: `fraud_probability`, `confidence`, `unknowns[]`, and the
  component scores that produced the probability, so the UI can show the blend
- `hypotheses[]`: candidate patterns with scores, including the ones rejected
- `steps[]`: the agent's timeline, each with the tool called and what it returned
- `decisions[]`: actor, action, route, `authorized`, approval status
- plus every field the answer file needs

**`AnswerFile`** is a pure projection of `InternalCase`, computed once at the end
by `backend/answers/write.py`. It contains exactly the README's fields and nothing
else. One function, no hand-assembly, so the two cannot drift.

`confidence` and `unknowns` do not vanish from the submission. They belong in
`stop_reason` and in the `reason` on each recommended action, which are graded
free text and are the right place to say how sure the agent was and what it still
did not know.

## Consequences for each lane

`agent-engineer`: model `InternalCase` as the working type. Never build an answer
dict by hand. `write.py` is the only code that emits the graded shape, and it
validates against `answer.schema.json` before writing.

`design-lead`: the dashboard reads `InternalCase` over SSE, not the answer file.
The queue's trigger and age columns and the risk-and-confidence gauge pair come
from there. Render the answer file separately as a submission preview, so the demo
can show what actually gets submitted.

`graph-engineer`: the `Case` vertex stores `InternalCase`. `write_case` takes the
internal object. Later investigations retrieving a prior case get the richer
record, including what the agent was unsure about, which is worth more as memory
than the projection is.

`evaluator`: validates `cases/*.json` against `answer.schema.json` only. The
internal object is not graded and is not submitted.
