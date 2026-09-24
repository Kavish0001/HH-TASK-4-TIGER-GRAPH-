# Evaluation report

Produced by `backend/eval/` (validate.py, graph_check.py, backtest.py, report.py). All runs used DRY_RUN=true; no LLM calls were made during evaluation.

## Headline: legitimate-verdict rate

The README states half the 20 cases are legitimate. The agent calls **4/20 legitimate (20%)**, 7/20 fraud (35%) and 9/20 uncertain (45%).

The agent does not call fraud on more than half (35%), so it is not blocking everything. 6/20 final recommendations include BLOCK_CARD and 3/20 file a SAR.

**FLAG: only 20% legitimate against an expected 50%.** With 9 uncertain verdicts, at least 6 of the expected legitimate cases sit in `uncertain` or `fraud`. The backtest below points the same way: on cleared closed cases the agent rarely says `legitimate`.

## 1. Answer-file validation

`python -m backend.eval.validate` checks every `cases/*.json` against `backend/contracts/answer.schema.json` and then the rules the schema cannot express:

- every transaction, card, customer, closed-case, case-pack and device-profile ID exists in the dataset (evidence `entity_ids` and `sar.subjects` included)
- `sar.file` agrees with `FILE_REPORT` in `next_best_actions.final`, and a filed SAR has a case (`CREATE_CASE`) behind it
- `legitimate` means empty `affected_txn_ids`, `exposure_usd` 0, `sar.file` false, pattern `none`, no block or report
- `exposure_usd` equals the summed absolute amounts of `affected_txn_ids` (from transactions.csv)
- `pattern_description` non-empty exactly when `pattern` is `undocumented`
- every route, initial and final, matches `policy.yaml`, with BLOCK_CARD L1 at or under $2,500 and L2 above
- `final` equals `initial` when `evidence_requests` is empty
- SAR required by policy 3a (fraud plus exposure over $1,000, connected cards, or undocumented) is present
- SAR false means empty narrative, subjects, dates and zero total
- `cases/` and `outputs/answers/` hold identical JSON

Failures print as `<case_id> <field>: <message>` and the script exits 1. I mutation-tested it on a scratch copy (fake txn ID, wrong BLOCK_CARD route, SAR flag flipped, wrong exposure, final != initial with no requests, missing undocumented description, fake closed-case ID): all seven were caught with the case ID and field.

**Result on the 20 graded files: 0 errors, 13 warnings.**

Warnings (judgement calls, not format failures):

- HHG-001 next_best_actions.initial[0].reason: cites no policy rule
- HHG-001 next_best_actions.final[0].reason: cites no policy rule
- HHG-004 next_best_actions.initial[0].reason: cites no policy rule
- HHG-010 next_best_actions.initial[0].reason: cites no policy rule
- HHG-011 next_best_actions.initial[0].reason: cites no policy rule
- HHG-012 next_best_actions.initial[0].reason: cites no policy rule
- HHG-012 next_best_actions.final[0].reason: cites no policy rule
- HHG-015 next_best_actions.initial[0].reason: cites no policy rule
- HHG-017 next_best_actions.initial[0].reason: cites no policy rule
- HHG-017 next_best_actions.final[0].reason: cites no policy rule
- HHG-019 next_best_actions.initial[0].reason: cites no policy rule
- HHG-019 next_best_actions.final[0].reason: cites no policy rule
- HHG-020 next_best_actions.initial[1].reason: cites no policy rule

The warnings are all action `reason` strings that state the probability but cite no rule number. README policy section 7 asks every recommendation to cite the rule, so these are worth fixing in the answer writer; they do not break the format.

## 2. Graph check

`python -m backend.eval.graph_check` reads each `graph_case_id` from TigerGraph over REST++ in `opened_at` order and compares the vertex with the answer file (verdict, pattern, exposure, affected transactions, similar cases), then counts its outgoing edges. **20/20 InvestigationCase vertices found, 0 problems.**

| Order | Case | Opened | Vertex | Evidence items on vertex | ON_CARD | CASE_INVOLVES | SIMILAR_TO | HAS_DECISION | Other |
|---|---|---|---|---|---|---|---|---|---|
| 1 | HHG-017 | 2016-11-12 00:46:24 | GC-HHG-017 | 6 | 1 | 0 | 5 | 2 | MATCHES_PATTERN 1 |
| 2 | HHG-015 | 2016-11-17 19:03:36 | GC-HHG-015 | 9 | 1 | 1 | 5 | 2 | MATCHES_PATTERN 1 |
| 3 | HHG-006 | 2016-11-22 02:30:00 | GC-HHG-006 | 9 | 1 | 4 | 5 | 4 | MATCHES_PATTERN 1 |
| 4 | HHG-014 | 2016-11-22 20:11:00 | GC-HHG-014 | 9 | 1 | 2 | 8 | 4 | FROM_DEVICE 1, CONNECTED_TO 19, MATCHES_PATTERN 1 |
| 5 | HHG-002 | 2016-11-22 23:27:07 | GC-HHG-002 | 6 | 1 | 1 | 1 | 2 | MATCHES_PATTERN 1 |
| 6 | HHG-018 | 2016-11-27 14:41:26 | GC-HHG-018 | 8 | 1 | 1 | 8 | 3 | MATCHES_PATTERN 1 |
| 7 | HHG-019 | 2016-12-01 22:28:53 | GC-HHG-019 | 9 | 1 | 0 | 4 | 2 | MATCHES_PATTERN 1 |
| 8 | HHG-010 | 2016-12-02 18:18:27 | GC-HHG-010 | 10 | 1 | 1 | 3 | 3 | MATCHES_PATTERN 1 |
| 9 | HHG-020 | 2016-12-03 12:04:26 | GC-HHG-020 | 11 | 1 | 1 | 2 | 2 | MATCHES_PATTERN 1 |
| 10 | HHG-001 | 2016-12-05 01:55:28 | GC-HHG-001 | 5 | 1 | 0 | 4 | 2 | MATCHES_PATTERN 1 |
| 11 | HHG-007 | 2016-12-05 03:46:14 | GC-HHG-007 | 8 | 1 | 1 | 8 | 4 | MATCHES_PATTERN 1 |
| 12 | HHG-005 | 2016-12-08 03:38:37 | GC-HHG-005 | 10 | 1 | 1 | 3 | 3 | MATCHES_PATTERN 1 |
| 13 | HHG-013 | 2016-12-09 05:39:29 | GC-HHG-013 | 9 | 1 | 1 | 4 | 3 | MATCHES_PATTERN 1 |
| 14 | HHG-003 | 2016-12-10 15:01:21 | GC-HHG-003 | 9 | 1 | 1 | 6 | 3 | MATCHES_PATTERN 1 |
| 15 | HHG-016 | 2016-12-12 01:39:08 | GC-HHG-016 | 8 | 1 | 1 | 3 | 2 | MATCHES_PATTERN 1 |
| 16 | HHG-012 | 2016-12-18 05:00:31 | GC-HHG-012 | 7 | 1 | 0 | 2 | 2 | MATCHES_PATTERN 1 |
| 17 | HHG-008 | 2016-12-20 03:08:56 | GC-HHG-008 | 8 | 1 | 1 | 8 | 3 | MATCHES_PATTERN 1 |
| 18 | HHG-009 | 2016-12-28 17:10:53 | GC-HHG-009 | 8 | 1 | 1 | 2 | 2 | MATCHES_PATTERN 1 |
| 19 | HHG-011 | 2016-12-29 06:27:44 | GC-HHG-011 | 8 | 1 | 1 | 8 | 2 | MATCHES_PATTERN 1 |
| 20 | HHG-004 | 2016-12-29 07:53:54 | GC-HHG-004 | 10 | 1 | 2 | 4 | 2 | MATCHES_PATTERN 1 |

Evidence is stored as `evidence_json` on the vertex; decisions are separate `Decision` vertices linked by `HAS_DECISION`. Legitimate cases correctly have no CASE_INVOLVES edges.

## 3. Backtest on closed cases

`python -m backend.eval.backtest`. Fixed seed, 25 cleared and 25 confirmed cases (4 of each known pattern plus 5 undocumented), run through the unchanged agent with TOOL_BACKEND=mock and DRY_RUN=true. No thresholds were tuned.

Leakage control: each closed case becomes a trigger at its own `opened_at`; every mock read tool cuts at that time and closed-case memory is filtered on `closed_at <= as_of`, so the case's own record is never visible. The script asserts this per case and also checks the agent's `similar_prior_cases` never names the case itself (self-leaks found: 0). Agent-written memory went to a scratch file, so the backtest neither reads nor pollutes the benchmark memory.

| Metric | Value |
|---|---|
| Cases run (errors) | 50 (0) |
| Fraud calls: TP / FP / FN | 9 / 9 / 16 |
| **Precision on fraud calls** | 50% |
| **Recall on fraud calls** (uncertain counts as a miss) | 36% |
| Recall if uncertain on a confirmed case counts as caught | 96% |
| Fraud-call rate overall | 36% |
| **Legitimate verdict on cleared cases** | 4% |
| Verdicts on cleared | {"fraud": 9, "uncertain": 15, "legitimate": 1} |
| Verdicts on confirmed | {"uncertain": 15, "fraud": 9, "legitimate": 1} |
| **Pattern accuracy, confirmed cases** | 48% |
| Pattern accuracy, all cases (none on cleared counts) | 26% |
| Trigger types derived | {"risk_score": 25, "customer_report": 25} |

Per pattern (confirmed cases):

| True pattern | n | Called fraud | Pattern correct |
|---|---|---|---|
| account_takeover | 4 | 0 | 1 |
| card_not_present_fraud | 4 | 1 | 0 |
| card_not_present_new_device | 4 | 2 | 3 |
| card_testing | 4 | 0 | 1 |
| out_of_region_use | 4 | 1 | 2 |
| undocumented | 5 | 5 | 5 |

Most common pattern confusions (truth -> agent): card_not_present_fraud -> card_not_present_new_device (4); card_testing -> card_not_present_fraud (3); account_takeover -> out_of_region_use (2); card_not_present_new_device -> account_takeover (1); out_of_region_use -> account_takeover (1); account_takeover -> none (1).

What the backtest says:

- **The verdict does not separate cleared from confirmed cases.** Mean fraud probability is 0.64 on cleared cases and 0.65 on confirmed ones, and the verdict mix is almost the same in both groups. Precision on fraud calls (50%) is what a coin would give on a balanced sample.
- **Legitimate is almost never called on a cleared case (4%).** Most cleared cases land in `uncertain`, and several get `fraud` with probability near 0.9, usually as card_not_present_new_device. This matches the low legitimate rate on the 20 benchmark cases and is the main thing to fix: the new-device signal is scored as strong even though the README says people buy new phones.
- **Recall is low if `uncertain` counts as a miss (36%) and high if it does not (96%).** The agent rarely lets a confirmed case go (one `legitimate`), but it defers most of them.
- **Pattern accuracy** is best on undocumented and card_not_present_new_device, and weakest on card_not_present_fraud (labelled as the new-device variant), card_testing (labelled as plain card-not-present) and account_takeover (split across other labels). The confusion line above has the counts.
- **Trigger type is confounded with outcome in the history.** Every cleared case note says the model scored it; every confirmed case note says the cardholder reported it. The derived triggers therefore split exactly by outcome ({"customer_report": {"cleared": 0, "confirmed_fraud": 25}, "risk_score": {"cleared": 25, "confirmed_fraud": 0}}). The verdict mix is still the same in both groups, so the agent is not simply reading the trigger type, but a customer report and a risk-score alert take different paths through evidence requests, and that difference is baked into these numbers.

## 4. The 20 benchmark cases, in `opened_at` order

| Case | Opened | Verdict | p | Pattern | Exposure | SAR | NBA before | NBA after |
|---|---|---|---|---|---|---|---|---|
| HHG-017 | 2016-11-12 00:46:24 | legitimate | 0.24 | none | $0.00 | no | ALLOW_TRANSACTION (auto), CLOSE_NO_FRAUD (auto) | unchanged |
| HHG-015 | 2016-11-17 19:03:36 | fraud | 0.94 | card_not_present_new_device | $599.94 | no | BLOCK_CARD (L1), CREATE_CASE (auto), STEP_UP_AUTH (auto) | BLOCK_CARD (L1), CREATE_CASE (auto) |
| HHG-006 | 2016-11-22 02:30:00 | fraud | 0.95 | undocumented | $1,906.07 | yes | CREATE_CASE (auto), FILE_REPORT (L2), ESCALATE_TO_ANALYST (auto), VERIFY_WITH_CUSTOMER (auto) | BLOCK_CARD (L1), CREATE_CASE (auto), FILE_REPORT (L2), ESCALATE_TO_ANALYST (auto) |
| HHG-014 | 2016-11-22 20:11:00 | fraud | 0.86 | undocumented | $187.33 | yes | CREATE_CASE (auto), FILE_REPORT (L2), MONITOR_CONNECTED_CARDS (auto), ESCALATE_TO_ANALYST (auto) | unchanged |
| HHG-002 | 2016-11-22 23:27:07 | uncertain | 0.50 | card_not_present_fraud | $292.36 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto) | CREATE_CASE (auto), MONITOR_CARD (auto) |
| HHG-018 | 2016-11-27 14:41:26 | uncertain | 0.48 | account_takeover | $39.08 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto), ESCALATE_TO_ANALYST (auto) | CREATE_CASE (auto), MONITOR_CARD (auto), ESCALATE_TO_ANALYST (auto) |
| HHG-019 | 2016-12-01 22:28:53 | legitimate | 0.17 | none | $0.00 | no | ALLOW_TRANSACTION (auto), CLOSE_NO_FRAUD (auto) | unchanged |
| HHG-010 | 2016-12-02 18:18:27 | fraud | 0.93 | card_not_present_new_device | $1,000.03 | yes | BLOCK_CARD (L1), CREATE_CASE (auto), FILE_REPORT (L2), STEP_UP_AUTH (auto) | BLOCK_CARD (L1), CREATE_CASE (auto), FILE_REPORT (L2) |
| HHG-020 | 2016-12-03 12:04:26 | fraud | 0.87 | card_not_present_new_device | $125.08 | no | CREATE_CASE (auto), MONITOR_CARD (auto), STEP_UP_AUTH (auto) | BLOCK_CARD (L1), CREATE_CASE (auto) |
| HHG-001 | 2016-12-05 01:55:28 | legitimate | 0.21 | none | $0.00 | no | ALLOW_TRANSACTION (auto), CLOSE_NO_FRAUD (auto) | unchanged |
| HHG-007 | 2016-12-05 03:46:14 | uncertain | 0.31 | out_of_region_use | $111.92 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto), ESCALATE_TO_ANALYST (auto) | DECLINE_TRANSACTION (L1), CREATE_CASE (auto), MONITOR_CARD (auto), ESCALATE_TO_ANALYST (auto) |
| HHG-005 | 2016-12-08 03:38:37 | uncertain | 0.41 | card_not_present_new_device | $100.07 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto) | DECLINE_TRANSACTION (L1), CREATE_CASE (auto), MONITOR_CARD (auto) |
| HHG-013 | 2016-12-09 05:39:29 | uncertain | 0.55 | account_takeover | $35.66 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto) | DECLINE_TRANSACTION (L1), CREATE_CASE (auto), MONITOR_CARD (auto) |
| HHG-003 | 2016-12-10 15:01:21 | uncertain | 0.37 | out_of_region_use | $49.00 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto), ESCALATE_TO_ANALYST (auto) | CREATE_CASE (auto), MONITOR_CARD (auto), ESCALATE_TO_ANALYST (auto) |
| HHG-016 | 2016-12-12 01:39:08 | uncertain | 0.57 | card_not_present_new_device | $59.67 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto) | CREATE_CASE (auto), MONITOR_CARD (auto) |
| HHG-012 | 2016-12-18 05:00:31 | legitimate | 0.17 | none | $0.00 | no | ALLOW_TRANSACTION (auto), CLOSE_NO_FRAUD (auto) | unchanged |
| HHG-008 | 2016-12-20 03:08:56 | uncertain | 0.42 | card_not_present_fraud | $55.68 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto), ESCALATE_TO_ANALYST (auto) | CREATE_CASE (auto), MONITOR_CARD (auto), ESCALATE_TO_ANALYST (auto) |
| HHG-009 | 2016-12-28 17:10:53 | uncertain | 0.29 | card_not_present_fraud | $30.02 | no | STEP_UP_AUTH (auto), VERIFY_WITH_CUSTOMER (auto), CREATE_CASE (auto) | CREATE_CASE (auto), MONITOR_CARD (auto) |
| HHG-011 | 2016-12-29 06:27:44 | fraud | 0.96 | card_not_present_new_device | $131.30 | no | BLOCK_CARD (L1), CREATE_CASE (auto), VERIFY_WITH_CUSTOMER (auto) | BLOCK_CARD (L1), CREATE_CASE (auto) |
| HHG-004 | 2016-12-29 07:53:54 | fraud | 0.97 | card_not_present_new_device | $221.19 | no | BLOCK_CARD (L1), CREATE_CASE (auto), VERIFY_WITH_CUSTOMER (auto) | BLOCK_CARD (L1), CREATE_CASE (auto) |

## 5. Limitations

- **Templated narratives.** The Gemini daily quota ran out before the final run, so `summary`, SAR `narrative`, `what_changed` and `stop_reason` come from deterministic templates in `backend/agent/narrative.py`, not from the LLM. In this run verdicts, patterns, probabilities, actions, routes and SAR decisions all came from the deterministic scoring and policy code, and the backtest ran the same way, so the backtest measures the same decision path that produced the 20 answers. `tokens` is 0 in every answer for the same reason.
- **Mock versus graph.** The 20 answers were produced over MCP against TigerGraph. The backtest used the mock backend (pandas over a CSV slice), so no synthetic backtest cases were written into the benchmark graph. The mock slice holds the sampled customers plus every transaction sharing a device profile with them, so device-breadth and ring evidence are comparable, but region-cluster and email-domain neighbourhoods outside that slice are thinner than in the graph. Backtest numbers are an estimate of the logic, not a replay of the graph run.
- **Synthetic triggers.** Closed cases carry no trigger row. I derived one per case: the flagged transaction is the latest case transaction at or before `opened_at`, and the trigger type comes from the analyst note (cardholder report versus model score). Because trigger type is confounded with outcome (section 3), the backtest cannot tell how the agent would treat a cleared case that arrived as a customer report.
- **Simulated evidence responses.** Customer and analyst replies are simulated by the agent (backend/contracts/evidence_simulation.md), so every `final` recommendation rests on an assumption stated in `evidence_requests`.
- **Small sample.** 50 backtest cases, 4 per known pattern. Per-pattern numbers carry wide error bars; card_testing and undocumented have only 16 and 9 cases in the whole history.
- **Answer files can be rewritten.** `cases/HHG-006.json` and `outputs/internal/HHG-006.json` were rewritten by another process during this evaluation (not by the eval scripts). Validation was re-run after that; rerun `python -m backend.eval.validate` before submitting.
