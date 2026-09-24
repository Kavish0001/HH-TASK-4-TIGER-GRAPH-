// Generates lib/mock/cases.json: 20 InternalCase objects keyed to the real case pack
// triggers. I write the scenarios as data here instead of hand-editing JSON because the
// cases share structure (steps, evidence, decisions), and a generator keeps them
// consistent with backend/models/internal_case.py when that model changes.
//
// Triggers, customer ids, card ids, flagged txn ids and closed case ids are real rows
// from data/. Everything the agent would produce (scores, extra txns, narratives) is
// illustrative and is labelled as mock in the UI.
//
// Run: node scripts/gen-mock.mjs

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const PACK = [
  ["HHG-001", "2016-12-05T01:55:28", "risk_score", "Real-time model scored transaction 3514030 ($77.07, in billing region 444.0) at 0.61. Review and decide.", "3514030", "C12382-K1", "C12382", 0.61, 77.07],
  ["HHG-002", "2016-11-22T23:27:07", "risk_score", "Real-time model scored transaction 3478782 ($292.36, online) at 0.79. Review and decide.", "3478782", "C11891-K1", "C11891", 0.79, 292.36],
  ["HHG-003", "2016-12-10T15:01:21", "customer_report", "Customer C08623 message: 'I never made this $49.00 purchase. Please check my card.' Refers to 3530164.", "3530164", "C08623-K2", "C08623", null, 49.0],
  ["HHG-004", "2016-12-29T07:53:54", "customer_report", "Customer C08106 message: 'I never made this $128.33 purchase. Please check my card.' Refers to 3583227.", "3583227", "C08106-K1", "C08106", null, 128.33],
  ["HHG-005", "2016-12-08T03:38:37", "risk_score", "Real-time model scored transaction 3523199 ($100.07, online) at 0.54. Review and decide.", "3523199", "C02923-K1", "C02923", 0.54, 100.07],
  ["HHG-006", "2016-11-22T02:30:00", "customer_report", "Customer C07297 message: 'I never made this $482.12 purchase. Please check my card.' Refers to 3476682.", "3476682", "C07297-K1", "C07297", null, 482.12],
  ["HHG-007", "2016-12-05T03:46:14", "risk_score", "Real-time model scored transaction 3514948 ($111.92, in billing region 264.0) at 0.87. Review and decide.", "3514948", "C09933-K2", "C09933", 0.87, 111.92],
  ["HHG-008", "2016-12-20T03:08:56", "customer_report", "Customer C13171 message: 'I never made this $55.68 purchase. Please check my card.' Refers to 3558054.", "3558054", "C13171-K2", "C13171", null, 55.68],
  ["HHG-009", "2016-12-28T17:10:53", "customer_report", "Customer C08299 message: 'I never made this $30.02 purchase. Please check my card.' Refers to 3581141.", "3581141", "C08299-K1", "C08299", null, 30.02],
  ["HHG-010", "2016-12-02T18:18:27", "risk_score", "Real-time model scored transaction 3506725 ($1,000.03, online) at 0.90. Review and decide.", "3506725", "C10434-K1", "C10434", 0.9, 1000.03],
  ["HHG-011", "2016-12-29T06:27:44", "customer_report", "Customer C11923 message: 'I never made this $131.30 purchase. Please check my card.' Refers to 3583368.", "3583368", "C11923-K2", "C11923", null, 131.3],
  ["HHG-012", "2016-12-18T05:00:31", "risk_score", "Real-time model scored transaction 3553342 ($30.91, in billing region 494.0) at 0.55. Review and decide.", "3553342", "C05876-K2", "C05876", 0.55, 30.91],
  ["HHG-013", "2016-12-09T05:39:29", "risk_score", "Real-time model scored transaction 3526826 ($35.66, online) at 0.76. Review and decide.", "3526826", "C07671-K2", "C07671", 0.76, 35.66],
  ["HHG-014", "2016-11-22T20:11:00", "analyst_request", "Analyst request: several cards this month show purchases from the same unusual device profile. Review transaction 3478561 on card C13487-K1 and look for related activity.", "3478561", "C13487-K1", "C13487", null, 74.96],
  ["HHG-015", "2016-11-17T19:03:36", "risk_score", "Real-time model scored transaction 3464869 ($599.94, online) at 0.77. Review and decide.", "3464869", "C03042-K1", "C03042", 0.77, 599.94],
  ["HHG-016", "2016-12-12T01:39:08", "customer_report", "Customer C09988 message: 'I never made this $59.67 purchase. Please check my card.' Refers to 3534820.", "3534820", "C09988-K1", "C09988", null, 59.67],
  ["HHG-017", "2016-11-12T00:46:24", "risk_score", "Real-time model scored transaction 3450629 ($100.09, online) at 0.57. Review and decide.", "3450629", "C04570-K1", "C04570", 0.57, 100.09],
  ["HHG-018", "2016-11-27T14:41:26", "customer_report", "Customer C02354 message: 'I never made this $39.08 purchase. Please check my card.' Refers to 3491361.", "3491361", "C02354-K2", "C02354", null, 39.08],
  ["HHG-019", "2016-12-01T22:28:53", "risk_score", "Real-time model scored transaction 3503878 ($99.92, online) at 0.90. Review and decide.", "3503878", "C07987-K2", "C07987", 0.9, 99.92],
  ["HHG-020", "2016-12-03T12:04:26", "risk_score", "Real-time model scored transaction 3509359 ($125.08, online) at 0.52. Review and decide.", "3509359", "C12265-K2", "C12265", 0.52, 125.08],
];

// Real closed cases from data/closed_cases_history.csv, used as similar-case hits.
const PRIOR = {
  "CC-0137": ["confirmed_fraud", "card_testing", 402.43, "5 small online authorizations then a larger purchase on C12982-K1. Card blocked and reissued."],
  "CC-0290": ["confirmed_fraud", "card_testing", 144.56, "8 small online authorizations on C11923-K2, same cardholder as this case. Card blocked and reissued."],
  "CC-1242": ["confirmed_fraud", "card_testing", 1421.21, "31 transactions on C11847-K2 after a testing run. Card blocked and reissued."],
  "CC-0022": ["confirmed_fraud", "card_not_present_fraud", 100.04, "Online purchase inconsistent with the cardholder's usual merchants. Card blocked."],
  "CC-0195": ["confirmed_fraud", "card_not_present_fraud", 64.65, "Single online purchase disputed by the cardholder. Card blocked."],
  "CC-0412": ["confirmed_fraud", "card_not_present_fraud", 60.8, "Online purchase on C02715-K1, cardholder denied. Card blocked."],
  "CC-0031": ["confirmed_fraud", "card_not_present_new_device", 20.36, "Online purchase from a device never seen on C11923-K2. Card blocked."],
  "CC-0332": ["confirmed_fraud", "card_not_present_new_device", 150.09, "New device, new browser, first purchase disputed. Card blocked."],
  "CC-0649": ["confirmed_fraud", "card_not_present_new_device", 300.01, "Two purchases from an unseen device profile on C04549-K1. Card blocked."],
  "CC-0016": ["confirmed_fraud", "out_of_region_use", 68.46, "Card present in a billing region with no history while the cardholder held the card."],
  "CC-0188": ["confirmed_fraud", "out_of_region_use", 557.61, "Three card-present purchases in an unfamiliar region over two days."],
  "CC-0458": ["confirmed_fraud", "out_of_region_use", 165.04, "Out of region use, three transactions. Card blocked and reissued."],
  "CC-3748": ["confirmed_fraud", "undocumented", 1905.21, "Four transactions across customers sharing an origin. Report filed."],
  "CC-0003": ["cleared", "none", 0, "Model scored a $442.92 transaction at 0.91. Cardholder confirmed travel to the billing region. Alert cleared."],
  "CC-0009": ["cleared", "none", 0, "Cardholder confirmed the purchase from a new phone. Device added to profile. Alert cleared."],
  "CC-0017": ["cleared", "none", 0, "Cardholder confirmed the purchase. Alert cleared."],
  "CC-0182": ["cleared", "none", 0, "Recurring monthly merchant, same amount. Alert cleared."],
};

const RING_DEVICE = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080";

// Archetypes map to policy rules. A: denied fraud (R2). B: confirmed, cleared (R3).
// C: card testing (R5 then R2). D: no reply (R4, R8 when exposed). E: shared origin (R6).
// F: disputed but recurring (R7).
const SCN = {
  "HHG-001": { a: "D", pattern: "out_of_region_use", prob: 0.58, conf: 0.52, prior: ["CC-0016", "CC-0458", "CC-0003"] },
  "HHG-002": { a: "A", pattern: "card_not_present_fraud", prob: 0.91, conf: 0.84, extra: ["3478611", "3478702"], amounts: [12.5, 64.0], prior: ["CC-0022", "CC-0195"] },
  "HHG-003": { a: "C", pattern: "card_testing", prob: 0.93, conf: 0.88, extra: ["3529880", "3529901", "3529944"], amounts: [1.0, 1.5, 2.0], prior: ["CC-0137", "CC-1242"] },
  "HHG-004": { a: "A", pattern: "card_not_present_fraud", prob: 0.89, conf: 0.8, prior: ["CC-0412", "CC-0022"] },
  "HHG-005": { a: "B", pattern: "none", prob: 0.12, conf: 0.83, prior: ["CC-0009", "CC-0017"] },
  "HHG-006": { a: "A", pattern: "card_not_present_new_device", prob: 0.9, conf: 0.82, prior: ["CC-0332", "CC-0649"] },
  "HHG-007": { a: "A", pattern: "out_of_region_use", prob: 0.88, conf: 0.79, extra: ["3514990"], amounts: [84.2], prior: ["CC-0188", "CC-0016"] },
  "HHG-008": { a: "C", pattern: "card_testing", prob: 0.9, conf: 0.85, extra: ["3557920", "3557931", "3557966"], amounts: [0.99, 1.25, 2.1], prior: ["CC-0137", "CC-0290"] },
  "HHG-009": { a: "F", pattern: "none", prob: 0.18, conf: 0.77, prior: ["CC-0182", "CC-0017"] },
  "HHG-010": { a: "A", pattern: "card_not_present_new_device", prob: 0.94, conf: 0.86, extra: ["3506801"], amounts: [412.4], prior: ["CC-0649", "CC-0332", "CC-3748"] },
  "HHG-011": { a: "C", pattern: "card_testing", prob: 0.92, conf: 0.87, extra: ["3583101", "3583120", "3583144"], amounts: [1.0, 1.0, 3.5], prior: ["CC-0290", "CC-0031", "CC-0137"] },
  "HHG-012": { a: "B", pattern: "none", prob: 0.14, conf: 0.8, prior: ["CC-0003", "CC-0016"] },
  "HHG-013": { a: "D", pattern: "card_not_present_fraud", prob: 0.63, conf: 0.48, prior: ["CC-0195", "CC-0022"] },
  "HHG-014": { a: "E", pattern: "undocumented", prob: 0.87, conf: 0.71, prior: ["CC-3748", "CC-0649", "CC-0332"] },
  "HHG-015": { a: "D", pattern: "card_not_present_new_device", prob: 0.66, conf: 0.44, prior: ["CC-0332", "CC-0649"] },
  "HHG-016": { a: "A", pattern: "card_not_present_fraud", prob: 0.88, conf: 0.81, prior: ["CC-0022", "CC-0412"] },
  "HHG-017": { a: "B", pattern: "none", prob: 0.16, conf: 0.78, prior: ["CC-0009", "CC-0017"] },
  "HHG-018": { a: "F", pattern: "none", prob: 0.2, conf: 0.74, prior: ["CC-0182", "CC-0017"] },
  "HHG-019": { a: "A", pattern: "card_not_present_new_device", prob: 0.92, conf: 0.83, extra: ["3503810"], amounts: [48.5], prior: ["CC-0332", "CC-0031"] },
  "HHG-020": { a: "D", pattern: "card_not_present_fraud", prob: 0.55, conf: 0.5, prior: ["CC-0195", "CC-0017"] },
};

const QUEUED = new Set(["HHG-016", "HHG-017", "HHG-018", "HHG-019", "HHG-020"]);

const act = (action, route, reason) => ({ action, route, reason });
const addSec = (iso, s) => new Date(new Date(iso + "Z").getTime() + s * 1000).toISOString();

function actionsFor(a, ctx) {
  const { exposure, card, prob, conf } = ctx;
  const blockRoute = exposure > 2500 ? "L2" : "L1";
  const p = prob.toFixed(2);
  switch (a) {
    case "A":
      return {
        initial: [
          act("VERIFY_WITH_CUSTOMER", "auto", `R1: probability ${p} rests on the model score and one behavioural signal, so I ask the cardholder before blocking.`),
          act("DECLINE_TRANSACTION", "L1", "Hold the flagged authorization while verification is pending. Card stays active."),
          act("MONITOR_CARD", "auto", `Raise monitoring on ${card} for 72 hours while the question is open.`),
        ],
        response: "denied",
        final: [
          act("BLOCK_CARD", blockRoute, `R2: cardholder denied the transaction. Exposure $${exposure.toFixed(2)} ${exposure > 2500 ? "> 2500, so L2" : "<= 2500, so L1"}.`),
          act("CREATE_CASE", "auto", "R2: open a fraud case and write it to the graph with the denial as evidence."),
          ...(exposure > 1000 ? [act("FILE_REPORT", "L2", `R2 and SAR trigger: exposure $${exposure.toFixed(2)} exceeds $1,000.`)] : []),
        ],
        changed: `Customer denied the transaction. VERIFY_WITH_CUSTOMER and MONITOR_CARD dropped as settled; BLOCK_CARD added at ${blockRoute} and CREATE_CASE added under R2${exposure > 1000 ? ", plus FILE_REPORT because exposure exceeds $1,000" : ""}. DECLINE_TRANSACTION dropped since the block supersedes it.`,
        status: "closed_fraud",
        verdict: "fraud",
      };
    case "B":
      return {
        initial: [
          act("VERIFY_WITH_CUSTOMER", "auto", `R1: probability ${p} on a single signal. Blocking would breach R1, so I verify.`),
          act("STEP_UP_AUTH", "auto", "R1: a one-time passcode costs the cardholder little and settles possession."),
        ],
        response: "confirmed",
        final: [act("CLOSE_NO_FRAUD", "auto", "R3: cardholder confirmed the transaction. Matches the pattern of prior cleared cases.")],
        changed: "Customer confirmed the purchase. Verification actions dropped as answered; CLOSE_NO_FRAUD added under R3.",
        status: "closed_legitimate",
        verdict: "legitimate",
      };
    case "C":
      return {
        initial: [
          act("DECLINE_TRANSACTION", "L1", "R5: three sub-$5 online authorizations within an hour, then this larger purchase."),
          act("STEP_UP_AUTH", "auto", "R5: challenge further authorizations on the card."),
          act("CREATE_CASE", "auto", "Customer disputes a charge, so a case opens regardless of score."),
        ],
        response: "denied",
        final: [
          act("BLOCK_CARD", blockRoute, `R5 upgrade and R2: a purchase over $100 cleared after the testing run and the customer denies it. Exposure $${exposure.toFixed(2)}.`),
          act("CREATE_CASE", "auto", "R2: record the testing run and the denial in the graph."),
        ],
        changed: "Customer denial confirmed the testing run. DECLINE_TRANSACTION and STEP_UP_AUTH replaced by BLOCK_CARD under the R5 upgrade; CREATE_CASE kept.",
        status: "closed_fraud",
        verdict: "fraud",
      };
    case "D": {
      const esc = exposure > 500;
      return {
        initial: [
          act("VERIFY_WITH_CUSTOMER", "auto", `R1: probability ${p} with confidence ${conf.toFixed(2)}. Too weak to block, too strong to allow.`),
          act("MONITOR_CARD", "auto", `Watch ${card} for follow-on activity while waiting.`),
        ],
        response: "no_reply",
        final: [
          act("MONITOR_CARD", "auto", "R4: no reply within 24 hours, keep monitoring."),
          act("DECLINE_TRANSACTION", "L1", "R4: decline the flagged authorization while it stays unverified."),
          ...(esc ? [act("ESCALATE_TO_ANALYST", "auto", `R4 and R8: verdict uncertain with exposure $${exposure.toFixed(2)} > $500.`)] : []),
        ],
        changed: `No reply within 24 hours. VERIFY_WITH_CUSTOMER dropped; DECLINE_TRANSACTION added at L1 under R4${esc ? ", and ESCALATE_TO_ANALYST added because exposure exceeds $500 (R8)" : ""}. MONITOR_CARD kept.`,
        status: esc ? "escalated" : "open",
        verdict: "uncertain",
      };
    }
    case "E":
      return {
        initial: [
          act("CREATE_CASE", "auto", "Analyst request with a named device profile. Open the case before gathering more."),
          act("MONITOR_CONNECTED_CARDS", "auto", "R6: monitor every card seen on the shared device profile while I test the ring."),
          act("VERIFY_WITH_CUSTOMER", "auto", "Ask the holder of C13487-K1 whether 3478561 was theirs."),
        ],
        response: "denied",
        final: [
          act("BLOCK_CARD", "L1", "R2: holder of C13487-K1 denied 3478561. Exposure on the ring transactions $439.61 <= 2500, so L1."),
          act("CREATE_CASE", "auto", "R6: case names the shared device profile and links all ring cards."),
          act("MONITOR_CONNECTED_CARDS", "auto", "R6: five other cards used the same device profile in the window."),
          act("FILE_REPORT", "L2", "R6 and R9: coordinated use across customers from one device profile. SAR required."),
        ],
        changed: "Holder of C13487-K1 denied the transaction, and ring detection tied five more cards to the same device profile. VERIFY_WITH_CUSTOMER dropped as answered; BLOCK_CARD added at L1 and FILE_REPORT added at L2 under R6 and R9. CREATE_CASE and MONITOR_CONNECTED_CARDS kept.",
        status: "escalated",
        verdict: "fraud",
      };
    case "F":
      return {
        initial: [
          act("CREATE_CASE", "auto", "Customer disputes a charge, so a case opens."),
          act("VERIFY_WITH_CUSTOMER", "auto", "R7: the charge matches a monthly recurring merchant and amount on this card. Ask before acting."),
          act("WARN_CUSTOMER", "auto", "R7: tell the cardholder the charge matches a recurring payment."),
        ],
        response: "confirmed",
        final: [act("CLOSE_NO_FRAUD", "auto", "R3 and R7: cardholder recognised the recurring subscription once reminded.")],
        changed: "Customer recognised the recurring charge. VERIFY_WITH_CUSTOMER and WARN_CUSTOMER dropped; CLOSE_NO_FRAUD added under R3. No block at any point, per R7.",
        status: "closed_legitimate",
        verdict: "legitimate",
      };
  }
}

function build(row, forceFull = false) {
  const [case_id, opened, type, text, txn, card, cust, score, amount] = row;
  const trigger = {
    type,
    source: type === "risk_score" ? "realtime_model_v3" : type === "customer_report" ? "customer_channel" : "analyst_console",
    trigger_text: text,
    opened_at: opened + "Z",
    flagged_txn_id: txn,
    card_id: card,
    customer_id: cust,
    risk_score: score,
  };
  const base = {
    case_id,
    trigger,
    status: "open",
    status_history: [{ status: "open", at: opened + "Z", note: "Case opened from trigger" }],
    verdict: "uncertain",
    pattern: "none",
    pattern_description: "",
    risk_assessment: { fraud_probability: 0, confidence: 0, components: [], unknowns: [], evidence_coverage: 0, signal_agreement: 0, prior: 0, method: "", checked_signals: [], missing_signals: [] },
    hypotheses: [],
    affected_txn_ids: [],
    first_suspicious_txn_id: "",
    connected_card_ids: [],
    connected_device_profiles: [],
    exposure_usd: 0,
    evidence: [],
    similar_prior_cases: [],
    retrieved_chunks: [],
    summary: "",
    written_to_graph: false,
    graph_case_id: "",
    evidence_requests: [],
    customer_response: null,
    initial_actions: [],
    final_actions: [],
    what_changed: "nothing",
    sar: null,
    steps: [],
    decisions: [],
    stop_reason: "",
    tool_calls: 0,
    tokens: 0,
    latency_s: 0,
  };
  if (QUEUED.has(case_id) && !forceFull) return base;

  const s = SCN[case_id];
  const ring = s.a === "E";
  const extra = s.extra ?? [];
  const amounts = s.amounts ?? [];
  const affected = s.a === "B" || s.a === "F" ? [] : ring ? ["3460634", "3478561", "3489320"] : [...extra, txn];
  const exposure = s.a === "B" || s.a === "F" ? 0 : ring ? 112.37 + 74.96 + 252.28 : amounts.reduce((x, y) => x + y, 0) + amount;
  const ctx = { exposure, card, prob: s.prob, conf: s.conf };
  const plan = actionsFor(s.a, ctx);
  // An L2 action cannot be closed out by the agent, so the case waits on a human.
  if (plan.final.some((a) => a.route === "L2")) plan.status = "escalated";

  const ringCards = ["C10326-K1", "C08168-K1", "C01996-K1", "C09906-K1", "C13291-K1"];
  const connected_card_ids = ring ? ringCards : [];
  const devices = ring ? [RING_DEVICE] : s.a === "A" && s.pattern === "card_not_present_new_device" ? ["iOS Device | iOS 11.1.2 | mobile safari 11.0 | 1334x750"] : [];

  const components = [
    { name: "model_score", value: score ?? 0.5, weight: 0.25, observed: score != null, detail: score != null ? `Real-time score ${score}` : "No model score on a customer or analyst trigger, prior used" },
    { name: "velocity", value: s.a === "C" ? 0.92 : s.a === "A" ? 0.55 : 0.2, weight: 0.2, observed: true, detail: s.a === "C" ? "4 auths in 52 minutes on the card" : "Velocity within the card's 90 day band" },
    { name: "behavior_shift", value: ["A", "E"].includes(s.a) ? 0.78 : s.a === "D" ? 0.6 : 0.15, weight: 0.2, observed: true, detail: ["A", "E"].includes(s.a) ? "Amount and merchant category outside the customer's usual range" : "Mostly consistent with history" },
    { name: "device_novelty", value: devices.length ? 0.85 : 0.3, weight: 0.15, observed: s.a !== "D", detail: devices.length ? "Device profile never seen on this card before the window" : s.a === "D" ? "No identity record for the flagged transaction" : "Known device" },
    { name: "shared_origin", value: ring ? 0.95 : 0.05, weight: 0.2, observed: true, detail: ring ? "Device profile shared by 6 cards across 6 customers in 14 days" : "No shared origin found" },
  ].map((c) => ({ ...c, contribution: +(c.value * c.weight).toFixed(3) }));

  const unknowns =
    s.a === "D"
      ? ["No identity record for the flagged transaction, so device novelty is unobserved", "Customer has not answered verification", s.pattern === "out_of_region_use" ? "No travel history on file for billing region 444" : "Merchant category for the flagged transaction is missing"]
      : ring
        ? ["Whether the device is one physical phone or a common model with a shared browser build", "Holders of the five connected cards have not been contacted", "Two ring transactions have no billing region"]
        : s.a === "B" || s.a === "F"
          ? ["Merchant descriptor not verified against the acquirer"]
          : ["Whether other cards held by the customer saw the same merchant"];

  const hypotheses = [
    { pattern: s.pattern === "none" ? "card_not_present_fraud" : s.pattern, score: s.pattern === "none" ? 0.14 : s.prob, present_signals: s.pattern === "none" ? [] : ["behavior_shift", "model_score"], absent_signals: s.pattern === "none" ? ["behavior_shift", "device_novelty"] : [], supporting_txn_ids: affected, rejected: s.pattern === "none", rejection_reason: s.pattern === "none" ? "Customer confirmed; behaviour consistent with history" : "" },
    { pattern: "account_takeover", score: 0.11, present_signals: [], absent_signals: ["credential_change", "new_email_domain"], supporting_txn_ids: [], rejected: true, rejection_reason: "No credential or contact change in the window" },
    ...(ring ? [{ pattern: "card_not_present_new_device", score: 0.62, present_signals: ["device_novelty"], absent_signals: [], supporting_txn_ids: ["3478561"], rejected: true, rejection_reason: "Explains one card, not six; shared origin fits better" }] : []),
  ];

  const ev = [];
  ev.push({ claim: `Flagged transaction ${txn} for $${amount.toFixed(2)} on ${card}`, source: "graph", ref: `tx_context:${txn}`, entity_ids: [txn, card] });
  if (s.a === "C") ev.push({ claim: `${extra.length} online authorizations under $5 on ${card} within 52 minutes before ${txn}`, source: "graph", ref: `velocity:${card}:1h`, entity_ids: [...extra, card] });
  if (["A", "E"].includes(s.a)) ev.push({ claim: `Amount and merchant category fall outside ${cust}'s 90 day spending band`, source: "graph", ref: `behavior_shift:${cust}:${txn}`, entity_ids: [cust, txn] });
  if (devices.length) ev.push({ claim: `Transaction came from device profile ${devices[0]}, not seen on ${card} before`, source: "graph", ref: `shared_device_profile:${ring ? "SM-G935F" : "iOS"}`, entity_ids: [devices[0], card] });
  if (ring) ev.push({ claim: `Ring detection: 6 cards across 6 customers used the same device profile between 2016-11-14 and 2016-11-26`, source: "graph", ref: "ring_detect:device_profile", entity_ids: [RING_DEVICE, card, ...ringCards] });
  if (s.a === "D" && s.pattern === "out_of_region_use") ev.push({ claim: `No prior card-present activity for ${cust} in billing region 444`, source: "graph", ref: `region_history:${cust}:444`, entity_ids: [cust] });
  if (s.a === "B" || s.a === "F") ev.push({ claim: s.a === "F" ? `Same merchant and amount charged to ${card} on the same day of each of the last 4 months` : `Purchase amount and channel consistent with ${cust}'s history`, source: "graph", ref: `card_window:${card}:120d`, entity_ids: [card] });
  const rule = { A: "R2", B: "R3", C: "R5", D: "R4", E: "R6", F: "R7" }[s.a];
  ev.push({ claim: `Policy ${rule} applies to this case`, source: "document", ref: `policy.yaml#${rule}`, entity_ids: [] });
  ev.push({ claim: `Most similar prior case ${s.prior[0]} closed ${PRIOR[s.prior[0]][0]} (${PRIOR[s.prior[0]][1]})`, source: "graph", ref: `similar_cases:${s.prior[0]}`, entity_ids: [s.prior[0]] });
  const respText = { denied: "Cardholder denies making the transaction", confirmed: "Cardholder confirms the transaction", no_reply: "No reply within 24 hours" }[plan.response];
  ev.push({ claim: `${respText} (simulated response, see evidence request)`, source: "customer", ref: "evidence_request:1", entity_ids: [cust] });

  const t0 = opened + "";
  const stepDefs = [
    ["intake", "", {}, `Read trigger: ${type} on ${card}, flagged ${txn}`],
    ["gather", "customer_profile", { customer_id: cust }, `Customer ${cust}: ${ring ? 1 : 2} cards, 90 day median ticket $${(amount * 0.6).toFixed(2)}`],
    ["gather", "tx_context", { txn_id: txn }, `${txn}: $${amount.toFixed(2)}, ${type === "risk_score" && text.includes("online") ? "online" : "channel from record"}`],
    ["gather", "velocity", { card_id: card, windows: [1, 24] }, s.a === "C" ? `${extra.length + 1} auths in 1h, ${extra.length} under $5` : "Velocity within band"],
    ["gather", "behavior_shift", { customer_id: cust, txn_id: txn }, ["A", "E"].includes(s.a) ? "Shift score 0.78" : "Shift score low"],
    ...(devices.length ? [["gather", "shared_device_profile", { device_profile: devices[0] }, ring ? "Profile seen on 6 cards in 14 days" : "Profile new to this card"]] : []),
    ...(ring ? [["gather", "ring_detect", { card_id: card, device_profile: RING_DEVICE }, "Ring of 6 cards linked through one device profile"]] : []),
    ["retrieve", "similar_cases", { k: 3 }, `Top match ${s.prior[0]} (${PRIOR[s.prior[0]][1]})`],
    ["retrieve", "policy_lookup", { query: rule }, `Retrieved ${rule} and SAR triggers`],
    ["assess", "", {}, `Probability ${s.prob.toFixed(2)}, confidence ${s.conf.toFixed(2)}, ${unknowns.length} unknowns`],
    ["decide_initial", "", {}, `${plan.initial.length} actions before evidence`],
    ["request_evidence", "", { type: "customer_validation" }, "Asked the cardholder to confirm or deny"],
    ["resolve_evidence", "", {}, `Simulated response: ${plan.response}`],
    ["decide_final", "", {}, `${plan.final.length} actions after evidence`],
    ["sar", "", {}, "SAR decision"],
    ["write_graph", "write_case", { case_id }, `Wrote Case vertex GC-${case_id.slice(4)}`],
  ];
  let t = 0;
  const steps = stepDefs.map(([node, tool, args, summary], i) => {
    const d = tool ? +(0.4 + ((i * 37) % 11) / 10).toFixed(2) : +(0.1 + ((i * 13) % 5) / 10).toFixed(2);
    t += d + 0.3;
    return { index: i + 1, node, tool, args, summary, at: addSec(t0, 60 + t), duration_s: d, ok: true };
  });
  const reqStep = steps.find((x) => x.node === "request_evidence").index;

  const sarRequired = plan.final.some((a) => a.action === "FILE_REPORT");
  const dates = affected.length ? [opened.slice(0, 10), opened.slice(0, 10)] : [];
  if (ring) dates.splice(0, 2, "2016-11-15", "2016-11-26");
  const sar = sarRequired
    ? {
        file: true,
        reason: ring ? "Suspicion strong and the activity connects to a shared device profile across customers (R6, R9)." : `Suspicion strong and exposure $${exposure.toFixed(2)} exceeds $1,000.`,
        narrative: ring
          ? "Between 2016-11-15 and 2016-11-26, card C13487-K1, held under issuer grouping C13487, made three online purchases totaling $439.61 from device profile SM-G935F Build/NRD90M running Android 7.0 and chrome 62.0 for android. The same device profile was used by five other cards held under five other issuer groupings in the same window. The holder of C13487-K1 denied transaction 3478561 when asked. None of the six cards had used this device profile before 2016-11-14. The purchases were online, card not present, and each fell outside the holder's usual spending band. The pattern of one device serving many unrelated cards in a short window is consistent with coordinated use of compromised card credentials. The case has been escalated and the connected cards placed under monitoring."
          : `On ${opened.slice(0, 10)}, card ${card}, held under issuer grouping ${cust}, was used for ${affected.length} online purchases totaling $${exposure.toFixed(2)}. The flagged transaction ${txn} for $${amount.toFixed(2)} was scored at ${score} by the real-time model. The purchases came from a device profile not previously seen on this card. The amounts fall well outside the holder's 90 day spending band. The cardholder denied the transaction when asked. The activity is consistent with card not present fraud using stolen card details, and exposure exceeds the $1,000 reporting threshold.`,
        subjects: ring ? [card, ...ringCards, RING_DEVICE] : [card, cust],
        total_amount_usd: +exposure.toFixed(2),
        activity_dates: dates,
      }
    : {
        file: false,
        reason: plan.verdict === "legitimate" ? "Cardholder confirmed; no suspicion, so no SAR." : plan.verdict === "uncertain" ? "Suspicion not established and no SAR trigger holds." : `Fraud confirmed but no SAR trigger holds: exposure $${exposure.toFixed(2)} is under $1,000 and no shared origin was found.`,
        narrative: "",
        subjects: [],
        total_amount_usd: 0,
        activity_dates: [],
      };

  const decisions = [
    ...plan.initial.map((a) => ({ actor: "agent", action: a.action, route: a.route, authorized: a.route === "auto", executed: a.route === "auto", reason: a.reason, phase: "initial", approval_status: a.route === "auto" ? "not_required" : "superseded", at: steps.find((x) => x.node === "decide_initial").at })),
    ...plan.final.map((a) => ({ actor: "agent", action: a.action, route: a.route, authorized: a.route === "auto", executed: a.route === "auto", reason: a.reason, phase: "final", approval_status: a.route === "auto" ? "not_required" : plan.status.startsWith("closed") ? "approved" : "pending", at: steps.find((x) => x.node === "decide_final").at })),
  ];

  const end = steps[steps.length - 1].at;
  const status_history = [
    base.status_history[0],
    ...(plan.status !== "open" ? [{ status: plan.status, at: end, note: plan.status === "escalated" ? "Escalated for human review" : "Closed after evidence" }] : []),
  ];

  const summary = ring
    ? "One Android device profile served six cards across six issuer groupings in twelve days. The holder of C13487-K1 denied 3478561. Treated as a coordinated misuse pattern not in the documented set; SAR drafted and L1/L2 actions await approval."
    : plan.verdict === "legitimate"
      ? `The flagged ${txn} on ${card} matched the cardholder's own history and the cardholder confirmed it. Closed legitimate.`
      : plan.verdict === "uncertain"
        ? `${txn} on ${card} scored ${score}, but with ${unknowns.length} open unknowns and no reply from the cardholder the verdict stays uncertain.`
        : `${affected.length} transaction(s) on ${card} totaling $${exposure.toFixed(2)} fit ${s.pattern}. Cardholder denied the flagged ${txn}.`;

  return {
    ...base,
    status: plan.status,
    status_history,
    verdict: plan.verdict,
    pattern: s.pattern,
    pattern_description: ring ? "Several unrelated cards transacting online from one device profile in a short window, with no prior use of that profile by any of them." : s.pattern === "none" ? "" : `Consistent with ${s.pattern.replaceAll("_", " ")}.`,
    risk_assessment: {
      fraud_probability: s.prob,
      confidence: s.conf,
      components,
      unknowns,
      evidence_coverage: +(s.conf - 0.05).toFixed(2),
      signal_agreement: +(s.conf + 0.04 > 1 ? 0.97 : s.conf + 0.04).toFixed(2),
      prior: 0.08,
      method: "weighted blend of observed signals, confidence from coverage and agreement",
      checked_signals: components.filter((c) => c.observed).map((c) => c.name),
      missing_signals: components.filter((c) => !c.observed).map((c) => c.name),
    },
    hypotheses,
    affected_txn_ids: affected,
    first_suspicious_txn_id: affected[0] ?? "",
    connected_card_ids,
    connected_device_profiles: devices,
    exposure_usd: +exposure.toFixed(2),
    evidence: ev,
    similar_prior_cases: s.prior,
    retrieved_chunks: [
      ...s.prior.map((id, i) => ({ ref: id, text: PRIOR[id][3], score: +(0.91 - i * 0.07).toFixed(2), kind: "prior_case", outcome: PRIOR[id][0], pattern: PRIOR[id][1], exposure_usd: PRIOR[id][2] })),
      { ref: `policy.yaml#${rule}`, text: `Rule ${rule} from policy.yaml`, score: 0.83, kind: "policy" },
    ],
    summary,
    written_to_graph: true,
    graph_case_id: `GC-${case_id.slice(4)}`,
    evidence_requests: [
      {
        type: "customer_validation",
        asked_after_step: reqStep,
        assumed_response: { denied: "Cardholder says they did not make the transaction.", confirmed: "Cardholder confirms the transaction was theirs.", no_reply: "No reply within 24 hours." }[plan.response],
        resolved_as: plan.response,
        basis: [`Simulated per evidence_simulation.md from ${s.prior[0]} outcome and pattern fit`],
      },
    ],
    customer_response: plan.response,
    initial_actions: plan.initial,
    final_actions: plan.final,
    what_changed: plan.changed,
    sar,
    steps,
    decisions,
    stop_reason:
      plan.verdict === "uncertain"
        ? `Stopped after the verification window closed with no reply; further steps were unlikely to change the decision. Confidence ${s.conf.toFixed(2)} with ${unknowns.length} unknowns left open.`
        : `A verification response settled the question. Probability ${s.prob.toFixed(2)}, confidence ${s.conf.toFixed(2)}.`,
    tool_calls: steps.filter((x) => x.tool).length,
    tokens: 9000 + steps.length * 612,
    latency_s: +t.toFixed(1),
  };
}

// cases.json is what the queue starts with (five cases not yet run). results.json holds
// a finished run for every case, which the mock stream replays step by step.
const cases = PACK.map((r) => build(r));
const results = PACK.map((r) => build(r, true));
writeFileSync(join(here, "..", "lib", "mock", "cases.json"), JSON.stringify(cases, null, 2) + "\n");
writeFileSync(join(here, "..", "lib", "mock", "results.json"), JSON.stringify(results, null, 2) + "\n");
console.log(`wrote ${cases.length} cases`);
