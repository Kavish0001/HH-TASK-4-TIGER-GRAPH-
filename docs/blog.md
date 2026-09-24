# Building a fraud investigator that knows when it is not sure

We built an agent that works a card fraud alert the way an analyst would: look at
the transaction, look at the cardholder's history, look at who else shares the
same device or billing region, check what happened in similar cases before, then
decide what to do and who has to sign off on it. It runs on TigerGraph, over the
HHGOA IEEE dataset: 590,742 transactions, 144,432 identity records, 5,565 closed
cases from July to October 2016, and 20 open benchmark cases from November and
December.

This post covers what we built, how the graph is used, which parts are agentic
and which parts are plain code on purpose, and what the data taught us.

## What we built

Given an alert, the agent:

1. Opens a case at the alert's `opened_at` and refuses to read anything later.
2. Runs a first round of graph queries (the transaction, the customer, the
   deviation from baseline, the last 48 hours on the card, prior closed cases on
   the same entities).
3. Scores five documented fraud patterns and two undocumented ones, and picks the
   follow-up queries the leading hypotheses need: shared device profile, ring
   detection, billing region history, velocity, similar cases, policy lookup.
4. Computes a fraud probability and, separately, a confidence, plus an explicit
   list of what it still does not know.
5. Recommends next best actions under the bank's policy, with the approval route
   for each one.
6. If the case is not settled, requests evidence (customer validation or step-up
   authentication), reassesses, and recommends again. Both recommendations go
   into the answer file with a "what changed" line.
7. Executes only the actions the policy routes `auto`. Anything routed `L1` or
   `L2` waits for a human. Every attempt is logged with whether it was
   authorized.
8. Drafts a SAR when the policy's filing rule holds.
9. Writes the case back to the graph, with its decisions, entity links and an
   embedding, so later cases can find it.

It produces one JSON answer file per benchmark case, and a dashboard where an
analyst can watch a case being worked, see the evidence and the graph around it,
compare the before and after recommendations, and approve or reject the actions
that need a human.

## Architecture

```
Next.js dashboard
      | REST + SSE
FastAPI
 |-- LangGraph state machine
 |     |-- MCP client -> TigerGraph MCP server -> installed GSQL queries
 |     |-- scoring (Python): probability, confidence, sufficiency, unknowns
 |     |-- policy engine (policy.yaml): routes, rules R1 to R10, SAR rule
 |     |-- GraphRAG: vector top-k in TigerGraph + entity expansion
 |     |-- case memory: ClosedCase and InvestigationCase vertices
 |     `-- simulated evidence and actions
 `-- answer writer
TigerGraph 4.2.5 Community Edition in Docker
```

The state machine is:

```
trigger -> open_case -> plan -> gather_evidence <-> assess
  -> nba_initial -> policy_check_initial
  -> [need evidence?] -> request_evidence -> reassess -> nba_final
  -> policy_check_final -> execute_or_route -> sar_check -> explain
  -> write_case -> update_memory -> finalize
```

LangGraph fits because the investigation really is a loop with a stopping rule,
and because we wanted each node to be a named step the dashboard can stream.

## How TigerGraph is used

**Schema.** Customers own cards, cards make transactions, transactions point to a
device profile, email domains, a billing region and a product code, and each
transaction points to the next one on the same card (a `NEXT` edge, ordered by
time). Closed cases link to their transactions, their card, any connected cards
and their pattern. Our own cases are `InvestigationCase` vertices with
`Decision` vertices hanging off them and `SIMILAR_TO` edges to the cases they
resemble.

We kept the Transaction vertex narrow. The source file has 393 columns, most of
them unnamed model features. We loaded the columns the investigation needs plus
a shortlist of features that correlated with closed-case outcomes after
controlling for the risk score, and described every one of them in evidence as
an unnamed model feature rather than pretending to know what it means.

**Loading.** A Python prep step writes narrow CSVs (it has to derive `card_id`,
which is not a column in the data, and the predecessor for each `NEXT` edge),
then GSQL loading jobs load them. Policy text and case embeddings go in over
REST, because the line-based loader silently drops quoted fields that span
lines: our first attempt loaded 20 of 30 policy chunks and reported zero errors.

**Queries.** Twenty installed GSQL queries do the investigation: entity queries
(`customer_profile`, `tx_context`, `card_window`, `velocity`, `behavior_shift`,
`region_history`), network queries (`shared_device_profile`, `ring_detect`),
memory queries (`prior_cases_for_entities`, `similar_cases`, `policy_search`),
five pattern matchers, and four writers (`write_case`, `append_evidence`,
`record_decision`, `link_similar`). Every read takes `as_of` and returns nothing
later than it. Every result carries a `ref` string that goes straight into the
evidence list, so each claim in an answer file points at the query that
produced it.

**Graph algorithms.** From the GDS library we run WCC, Louvain and PageRank over
a card-to-card projection: two cards are linked when they share a specific,
rare device profile, using only transactions before November 1 so nothing from
the benchmark window leaks in. WCC alone glued the shared-device ring into a
component of over 100 cards. Louvain split that apart: one of the ring's cards
lands in a 22-card community alongside other cards from the same device (more
on that device below). Per-case ring detection still
happens in `ring_detect` at the case's own `as_of`, because a precomputed global
component cannot respect a per-case time cut.

**Vectors.** `PolicyChunk`, `FraudPattern`, `ClosedCase` and `InvestigationCase`
carry 384-dimension embeddings (bge-small, local, no API key) with HNSW cosine
indexes. `similar_cases` blends vector similarity with entity overlap: plain
text similarity kept matching cases that were worded the same (the analyst notes
come from a small set of templates) but had nothing to do with each other, so a
shared card, customer or device profile adds to the score.

**MCP.** We run two MCP servers over the same installed queries. Ours exposes 16
tools named for investigation steps, with descriptions that say what each tool
returns and when to use it. The official `tigergraph-mcp` server is configured
too, limited to read-only tools, for ad hoc inspection. We wrote our own because
the official one exposes generic tools like `run_installed_query`, which means
the model has to know our query names and parameter types, and the same server
can run arbitrary GSQL, which an investigating agent should not be able to do.

## The agentic parts, and the parts we kept out of the model

**Uncertainty is two numbers.** Fraud probability is a log-odds blend of the
bank's score, the leading pattern's match score, shared-device and ring signals,
the fraud rate among similar prior cases, behaviour shift, and any legitimate
explanation (a recurring charge, a trip). Confidence is separate: it comes from
how many of the leading pattern's required signals were actually checked, how
well the signals agree, how many independent pieces of evidence there are, and
how much history the card has. High probability with low confidence is exactly
the case where the agent should ask rather than act.

**Stopping is a rule.** The agent stops gathering when the probability is at or
above 0.85 or at or below 0.15 with at least two independent pieces of evidence,
when the sufficiency checklist for the leading pattern is complete, or when the
step budget runs out. The stop reason goes into the answer file.

**Evidence requests are simulated honestly.** The dataset supplies no customer
replies. We made the simulated reply a pure function of what the graph already
showed: `confirmed` when the activity matches the cardholder's own habits,
`denied` when a pattern match is backed by an independent second signal,
`no_reply` when the evidence is thin or conflicting. No randomness, no model
role-play, and the assumption is written into `evidence_requests` in plain words.
A blanket "the customer always denies it" would have pushed almost every case to
a block, and the dataset README says half the cases are legitimate.

**Policy is data.** Actions, approval routes, rules R1 to R10 and the SAR
triggers live in `policy.yaml`, and the engine applies them the same way every
run. The agent cannot execute `BLOCK_CARD`, `DECLINE_TRANSACTION`,
`FILE_REPORT` or `BLOCK_ALL_CARDS`; it recommends them with the route, and the
dashboard's approve button records the analyst's decision as its own `Decision`
vertex.

**Memory is the graph.** Benchmark cases run in `opened_at` order. Each finished
case is written as an `InvestigationCase` with an embedding, and a later case can
retrieve it through `similar_cases` or `prior_cases_for_entities` once its
`opened_at` is past.

**The LLM writes, it does not decide.** Tool choice, scores, thresholds, routes
and the SAR decision are code. The LLM gets the finished assessment and a
cited, deduplicated context block and rewrites the summary, stop reason, "what
changed" and SAR narrative. Its text is discarded if it contains an ID the agent
never saw, claims an approval-gated action already happened, contradicts the
assumed customer reply, or puts the SAR outside six to twelve sentences. The
whole pipeline runs with the LLM switched off, and the answer files are then
fully reproducible.

We went back and forth on letting the model pick tools. We kept it in code
because the graded output depends on which evidence was gathered, and we wanted
the same case to produce the same investigation every time.

## What we learned from the data

**The risk score is a reason to look, not an answer.** One benchmark transaction
scored 0.05 and sits on the most clearly fraudulent device in the dataset. Among
closed-case transactions, about 6 percent of confirmed fraud scored below 0.10.

**A shared device is usually not evidence.** About half of all device profiles
are shared by more than one card, and the biggest ones are generic browser
strings shared by hundreds of unrelated cards. Nine of the twenty flagged
transactions sit on a profile shared by 100 or more cards. Treating that as a
ring would have been a steady source of false positives. What does count is a
specific, complete profile, marked new, behind an anonymous proxy, in a narrow
window.

**The one ring in the data is very clear once you ask the graph the right
question.** A Samsung SM-G935F profile on Chrome for Android appears on 52 cards
belonging to 52 different customers, marked new on every transaction and behind
an anonymous proxy on every transaction. Four closed cases describe it as an
undocumented pattern. Benchmark case HHG-014 is on that device. You do not find
it from the card's own history; you find it by walking from the card to the
device and back out to everyone else on it.

**False alarms in this bank have a shape.** The 900 cleared closed cases reduce
to three reasons: the cardholder was travelling (716), bought a new phone (158),
or confirmed an unusual purchase (26). So "new region" and "new device" are the
two signals most likely to be innocent, and the agent weighs them against the
cardholder's own spread of regions and devices instead of flagging novelty.

**Literal rules can be traps.** Policy R5 describes card testing as three or more
small online authorizations within an hour followed by a larger purchase. One
benchmark card (HHG-011) has over 10,000 online transactions and fires that
literal rule on almost any day of the year. The small runs are its normal
behaviour. The pattern matcher compares against the card's own baseline instead.

**Some patterns are not in the policy.** Besides the device ring, five closed
cases describe four online purchases within forty minutes, each just under $500.
The same shape recurs in the benchmark window (HHG-006). We score it as its own
undocumented pattern and route it the way R9 says: create a case, file a report,
escalate, and describe the pattern in plain words.

**Not every artifact is evidence.** The seeded undocumented cases share a
data-generation fingerprint in their timestamps. We used it as a private sanity
check on our own detectors and kept it out of every evidence item and SAR. A
filing that cites a timestamp's seconds field would not survive a reviewer.

**Tooling notes.** In TigerGraph 4.2.5, vector attributes have to be added with
`ALTER VERTEX ... ADD VECTOR ATTRIBUTE` inside a separate schema change job, and
loading jobs have no `CREATE OR REPLACE`, so a reload starts by dropping them.
Loading-job edges that can reference a blank key need a `WHERE` clause, or the
loader creates a vertex with an empty id and hangs tens of thousands of edges on
it without reporting anything.

## Results

The evaluator's report is in `outputs/eval_report.md`: answer file validation
against the schema, a check that each of the 20 cases exists as a vertex in the
graph, and a backtest on closed cases.

TODO(verify): fill this section from `outputs/eval_report.md` once it exists. It
had not been written when this draft was. Do not add numbers that are not in it.

## What we would improve

- **Real replies.** Every customer and step-up response is simulated. With a real
  channel the "after evidence" recommendation would rest on something the
  customer actually said.
- **Calibration.** The probability weights are set by hand and checked against
  closed cases. The closed cases are not a random sample (every cleared case is a
  single high-score transaction), so a proper calibration needs outcome data the
  dataset does not have.
- **Model-chosen follow-ups.** The follow-up plan is rule based. A version where
  the model proposes queries and code checks them against the sufficiency list
  would handle unusual cases better, at the cost of reproducibility.
- **Merchant data.** The dataset has no merchant names, so "same merchant,
  monthly" for recurring charges is inferred from amount, product code and
  cadence.
- **Undocumented pattern discovery at scale.** Louvain over the device projection
  found the ring. Running community detection over confirmed-fraud cases across
  more edge types (email domain, billing region) could find patterns we did not
  already suspect.
- **A live graph explorer.** The dashboard shows the case's own subgraph. An
  explorer that expands from any node would help an analyst check the agent's
  reasoning.

## Code

https://github.com/Kavish0001/HH-TASK-4-TIGER-GRAPH-
