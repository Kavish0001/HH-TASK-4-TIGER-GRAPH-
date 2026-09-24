# Simulated evidence responses

The dataset supplies no customer or analyst replies. Policy section 5 says to
simulate them and record the assumption in the answer file's `evidence_requests`.

Decision: responses are **evidence-driven and deterministic**. The simulated reply
is a pure function of what the graph already showed, with no randomness and no LLM
call. Two reasons. The 20 answer files have to be reproducible, so a fixed seed or
a model role-play would both make reruns differ. And a blanket "customer always
denies" would push nearly every case to `BLOCK_CARD` plus `FILE_REPORT`, which the
README warns against: roughly half the benchmark cases are legitimate, and an
agent that blocks everything scores badly.

## The function

Input is the case state at the moment the request is made. Output is one of three
responses, which then drive rules R2, R3 and R4.

| Response | Condition | Policy path |
|---|---|---|
| `denied` | Fraud evidence is strong and independent: a matched pattern with a supporting second signal (shared device profile, ring membership, a confirmed-fraud prior case on a connected entity), and no competing legitimate explanation | R2 |
| `confirmed` | The flagged activity matches the cardholder's own established behaviour: recurring merchant, amount and cadence, or a sustained multi-day presence in the new region rather than a single spike | R3, R7 |
| `no_reply` | Evidence is thin or conflicting, and neither branch above is met | R4, and R8 when exposure is over $500 |

Ordering matters: test `confirmed` before `denied`, because a recurring-charge
match is the cheapest way to clear a case and R7 explicitly forbids blocking there.

## Recording it

Every simulated response is written into `evidence_requests[]` as
`{ type, asked_after_step, assumed_response }`, where `assumed_response` states
the assumption in plain words and names the signals it rests on. The case
`evidence` list carries a matching entry with `source: "customer"` and a `ref` of
`evidence_request:<n>`, so the explanation can cite it like any other evidence.

`next_best_actions.final` must reflect the assumed response, and `what_changed`
must say which way it moved the probability and why.

## What this does not do

It does not invent facts about the customer. The response is a reading of evidence
the agent already gathered from the graph, restated as what a cardholder would say.
Where the agent is genuinely uncertain, `no_reply` is the honest output and the
case should escalate rather than resolve.
