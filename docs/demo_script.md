# Demo video shot list

Target length: about 4 minutes 10 seconds (hard limits 3 to 5 minutes).

Main case: **HHG-006**. It has everything the arc needs in one case: a customer
report, graph evidence that fits no documented pattern, a simulated customer
reply that moves the probability, a recommendation that changes after the reply,
an L1 and an L2 action waiting for approval, and a SAR. Cutaway case:
**HHG-014**, for the shared-device ring in the graph view.

Before recording:

- TigerGraph up, queries installed, MCP server running, `TOOL_BACKEND=mcp`.
- Backend on :8000, frontend on :3000 with `NEXT_PUBLIC_API_URL` set.
- Run the full 20 once (`python -m backend.run_cases`) so memory is populated,
  then run HHG-006 live from the launcher. A rerun overwrites the same
  `GC-HHG-006` vertex, so it does not disturb memory for the other cases.
- GraphStudio open in a second tab on the `FraudInvestigation` graph.
- Numbers quoted below are from the answer files at the time of writing.
  Checked against the final calibrated run (HHG-006: 0.79 to 0.95, BLOCK_CARD
  added; HHG-014: 52 cardholders on the device profile).

---

## 1. The problem (0:00 to 0:20, 20s)

**Screen:** Case queue, all 20 cases, sorted by opened time.

**Voice:**
"A bank's fraud model flags transactions all day. Most high scores are
legitimate, and some fraud scores near zero. An analyst has to work out which is
which, decide what to do, and get sign-off for anything that hurts a customer.
We built an agent that does that investigation on a TigerGraph graph, and knows
when it is not sure."

## 2. Trigger a case (0:20 to 0:40, 20s)

**Screen:** Click HHG-006 in the queue. Show the trigger panel: customer report,
"I never made this $482.12 purchase", card C07297-K1, model score 0.25. Click
Run.

**Voice:**
"This one is a customer complaint. The bank's model scored the purchase 0.25, so
it never alerted. The case opens at the complaint time, and every graph query
from here on is cut at that time. Nothing later is read."

## 3. Live investigation with graph evidence (0:40 to 1:40, 60s)

**Screen:** Agent steps streaming in the timeline. Pause on:

1. Round 1: `tx_context`, `customer_profile`, `behavior_shift`, `card_window`,
   `prior_cases_for_entities`.
2. The `assess` step: leading hypothesis and "next round" list.
3. Round 2: `shared_device_profile`, `velocity`, `pattern_match`,
   `similar_cases`, `policy_lookup`.
4. Evidence panel: the structuring evidence (four online purchases of $400 to
   $500 inside 60 minutes, totalling $1,906.07), each item with its source tag
   and `ref` to the query that produced it.
5. Similar prior cases: closed cases with the same four-under-$500 shape.
   In the final run these are CC-3748, CC-3907, CC-4086, CC-4124 and CC-3841.

**Cutaway (15s of the 60):** open HHG-014, Case subgraph panel. The flagged card
connects to one device profile, which fans out to the other cards on it.

**Voice:**
"Round one is the same for every case. Round two is chosen from what round one
showed. Here the card made four online purchases inside an hour, each just
under five hundred dollars. None of the five documented patterns describes
that, but five closed cases from the autumn do, and the graph finds them.
Every evidence line points back to the GSQL query that produced it.
Here is a different case, HHG-014: the bank scored it 0.05, and the graph shows
the card on a device profile used by 52 cardholders, every transaction marked as
a new device behind an anonymous proxy. You only see that by walking out from the
card to the device and back to everyone else on it."

## 4. Uncertainty and the evidence request (1:40 to 2:25, 45s)

**Screen:** Back on HHG-006. Risk / confidence panel: probability and confidence
gauges side by side, the blend components, the unknowns list. Then the Evidence
requests panel.

**Voice:**
"The agent keeps two numbers. Fraud probability is a blend of graph signals,
computed in code. Confidence is separate: how many of the required checks were
actually done and whether the signals agree. And there is always an explicit
list of what it does not know. Before acting on this card it asks the customer.
The dataset has no real replies, so the reply is simulated by a fixed rule
from the graph evidence, and the answer file says so in plain words."

Point at: the `assumed_response` text marked "Simulated, the dataset supplies no
replies".

## 5. Next best action changes, and the approval route (2:25 to 3:10, 45s)

**Screen:** "Next best actions: before vs after evidence" panel. Before:
CREATE_CASE (auto), FILE_REPORT (L2), ESCALATE_TO_ANALYST (auto),
VERIFY_WITH_CUSTOMER (auto). After: BLOCK_CARD (L1) added, VERIFY dropped.
Read the "What changed" line (probability 0.79 to 0.95). Then the Approvals
panel: approve BLOCK_CARD, show the decision log entry with the analyst as actor.

**Voice:**
"Before the reply, the agent recommends opening a case, escalating, and filing a
report, and asks the customer. After the simulated denial, probability goes from
0.79 to 0.95 and it adds a card block. It does not block the card itself. A
block is an L1 action and a regulatory filing is L2, so both wait for a person.
Only actions the policy marks as automatic ever run. When I approve the block,
that is logged as my decision, not the agent's, and written to the graph."

## 6. SAR, the case in the graph, and memory (3:10 to 3:50, 40s)

**Screen:** SAR panel: the narrative (who, what, when, where, how, why), the
subjects, the total, the filing reason. Then GraphStudio: find the
`InvestigationCase` vertex for HHG-006 and expand its `Decision` vertices and
`CASE_INVOLVES` transactions. Then open a later case whose similar cases list an
earlier benchmark case.

The graded `similar_prior_cases` field only accepts closed-case ids (`CC-NNNN`),
so agent memory shows up in the internal case instead: HHG-003 retrieves
`GC-HHG-007`, a benchmark case the agent closed earlier in calendar time. Show
that in the case view's retrieved context, then the `GC-HHG-007` vertex in
GraphStudio.

**Voice:**
"The report is drafted because policy says to file when the pattern is
undocumented or the exposure is high. The whole case goes back into TigerGraph as
a vertex, with its decisions and the transactions it covers, and an embedding.
A case opened later can now find this one, and nothing earlier can, because
every read is cut at the trigger time."

## 7. Architecture (3:50 to 4:10, 20s)

**Screen:** The mermaid diagram from the README.

**Voice:**
"Next.js dashboard, FastAPI, a LangGraph state machine. The agent reaches
TigerGraph through an MCP server over twenty installed GSQL queries, with graph
algorithms for ring structure and TigerGraph vectors for policy and case
retrieval. Policy is a YAML file the code enforces. The language model only
writes the prose, and its text is dropped if it names anything the graph did
not return."

---

## Recording notes

- Keep the cursor still while the voice explains a panel.
- If the live run is slow, cut the waiting between steps; do not speed up the
  voice.
- Do not show `.env` or the terminal with keys.
