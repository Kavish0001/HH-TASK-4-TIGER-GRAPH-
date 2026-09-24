# Tool contract

Every tool the agent can call. `graph-engineer` implements the graph tools as
installed GSQL queries exposed through TigerGraph MCP. `agent-engineer` codes
against these shapes and may mock them until the MCP server is live.

Two rules hold for every tool here.

Tool descriptions are what the LLM selects from, so the `description` shipped in
MCP must say what the tool returns and when to reach for it, not just what it is.

Every ID a tool returns must be a real dataset ID. Made-up IDs score zero in the
answer files, and an ID that entered through a tool result will end up there.

## Conventions

- `card_id` is `C01234-K1`. `customer_id` is `C01234`. `txn_id` is the
  `TransactionID` value as a string.
- `device_profile` is the composed string `DeviceInfo | OS | browser | screen`,
  matching what the answer format expects in `connected_device_profiles`.
- Every tool takes an `as_of` timestamp and must not return rows later than it.
  Benchmark cases are investigated at their `opened_at`, and reading past it is
  leakage. This is not optional on any read tool.
- Results are compact JSON. No tool returns raw 393-column rows.
- Every result carries `ref`, a string the agent copies into
  `evidence[].ref`, for example `query:card_window(card_id=C00377-K1, hours=2)`.

## Read tools

| Tool | Input | Returns |
|---|---|---|
| `customer_profile` | `customer_id`, `as_of` | Cards held, transaction count, amount stats (mean, median, p95, max), distinct `addr1` values with counts, distinct product codes, distinct device profiles, channel split, first and last transaction dates |
| `tx_context` | `txn_id`, `as_of` | The transaction plus its card, customer, device profile, billing region, email domains, product code, amount, risk score, channel, and the previous and next transactions on that card |
| `card_window` | `card_id`, `hours` or `days`, `as_of` | Transactions on the card inside the window, ordered by time, with amount, product code, channel, device profile and risk score. The R5 card-testing tool |
| `velocity` | `card_id`, `windows` (list of hours), `as_of` | Count and summed amount per window, plus flags for first-seen device profile, first-seen billing region, first-seen product code and first-seen recipient email in that window |
| `behavior_shift` | `customer_id`, `txn_id`, `as_of` | Deviation of the flagged transaction from the customer baseline: amount z-score and percentile, whether the product code is new, whether the hour of day is unusual, whether the device profile is new, whether the billing region is new |
| `shared_device_profile` | `device_profile`, `as_of` | Other cards and customers that used this device profile, with first and last seen per card, plus any closed cases touching them and their outcomes. The R6 tool |
| `region_history` | `customer_id`, `addr1`, `as_of` | Whether the customer has history in this billing region, transaction count and date range there, and whether activity continued at home during the same period. Distinguishes a trip from a clone for pattern 4 |
| `ring_detect` | seed `card_id` or `device_profile`, `as_of` | Weakly connected component over shared device profile, billing region and recipient email edges. Returns members, ring size, the shared elements, and each member's prior case outcomes |
| `prior_cases_for_entities` | `entity_ids` (cards, customers, device profiles), `as_of` | Closed cases touching any of these entities, with outcome, pattern, exposure, actions taken and analyst notes |
| `similar_cases` | `case_embedding`, `entity_ids`, `k`, `as_of` | Vector top-k over closed case embeddings blended with entity overlap. Returns case id, similarity, outcome, pattern, and why it matched. Feeds `similar_prior_cases` |
| `pattern_match` | `pattern` (one of the five), `card_id`, `txn_id`, `as_of` | Match score 0 to 1, which required signals were present, which were absent, and the transaction IDs supporting the match |
| `policy_lookup` | `query`, `k` | Top-k chunks from the fraud policy, the pattern descriptions and the regulatory documents, each with a citable `ref`. The document half of GraphRAG |

`similar_cases` and `prior_cases_for_entities` are the case memory read path. Once
a benchmark case is written back, later benchmark cases must be able to retrieve
it, which is why the 20 run in `opened_at` order.

## Write tools

| Tool | Input | Returns |
|---|---|---|
| `write_case` | full case object per `answer.schema.json` part 1 | `graph_case_id`, the created `Case` vertex ID |
| `append_evidence` | `graph_case_id`, evidence item | ok |
| `record_decision` | `graph_case_id`, `action`, `route`, `authorized`, `reason`, actor | ok |
| `link_similar` | `graph_case_id`, `[case_id]`, score | ok |

`written_to_graph` and `graph_case_id` in the answer file come from `write_case`.
A case the agent claims to have written must actually exist as a vertex: the
evaluator checks all 20.

## Simulated action tools

Not graph tools. These stand in for bank systems and are stubbed, per the task
brief, which allows actions to be simulated or mocked. Each one logs to the case
decision record and returns immediately.

Evidence gathering, allowed without approval: `request_customer_validation`,
`request_step_up_auth`, `request_analyst_input`. Responses are not supplied by the
dataset, so these resolve through the deterministic rule in
`evidence_simulation.md` and the assumption is recorded in `evidence_requests`.

Executable actions: one per policy action name. The policy engine gates them. Only
actions routed `auto` may execute; `L1` and `L2` are recorded as recommendations
with the route stated and never executed. Every attempt is logged with whether it
was authorized, so a blocked attempt is still visible in the case record.

## Instrumentation

`tool_calls`, `tokens` and `latency_s` are required answer fields. The tool layer
increments the call counter, so no tool call can escape the count. Wrap every tool
at one place rather than counting at call sites.

## Graph implementation notes (graph-engineer)

What the installed queries actually do where the table above leaves room. The
MCP tool names and argument names match `backend/tools/base.py`.

- `similar_cases` takes `query_text` (embedded with bge-small, query prefix) or
  a precomputed `case_embedding`. Score is cosine plus 0.45 per shared entity,
  capped at 0.9, the same blend as `backend/graphrag/retriever.py`.
- `pattern_match` accepts `card_testing`, `card_not_present_fraud`,
  `card_not_present_new_device`, `out_of_region_use`, `account_takeover`, and
  `undocumented_structuring` (alias `undocumented`). Output adds `coverage` and
  `uncheckable_signals`, matching `backend/scoring/patterns.py`.
- `ring_detect` follows a device profile only if it is rare (8 customers or
  fewer), complete and New or proxied, or New behind an anonymous proxy on most
  rows. One hop by default. On HHG-014 it returns the 44 cards on the SM-G935F
  profile up to that case's `opened_at`.
- `shared_device_profile` adds `signature` (new, proxy and anonymous shares)
  and `parts_present`, because breadth alone is not evidence.
- Visibility: a `ClosedCase` is visible when `closed_at <= as_of`; an agent
  case (`InvestigationCase`) when its `opened_at < as_of`. The agent case's
  `outcome` field carries its `status`.
- `write_case` stores only ids that resolve to real vertices and returns the
  rest in `ids_not_in_dataset`. `graph_case_id` defaults to `GC-<case_id>`, so
  a rerun overwrites rather than duplicates.
