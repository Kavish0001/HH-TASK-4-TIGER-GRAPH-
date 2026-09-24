// Mirrors backend/models/enums.py, backend/models/answer.py and
// backend/models/internal_case.py. Field names and enum literals are graded strings, so I
// copy them exactly rather than renaming for the UI. Datetimes arrive as ISO strings.

export type CaseStatus = "open" | "closed_fraud" | "closed_legitimate" | "escalated";
export type Verdict = "fraud" | "legitimate" | "uncertain";
export type Pattern =
  | "card_testing"
  | "card_not_present_fraud"
  | "card_not_present_new_device"
  | "out_of_region_use"
  | "account_takeover"
  | "undocumented"
  | "none";
export type EvidenceSource = "graph" | "document" | "customer" | "external";
export type EvidenceRequestType = "customer_validation" | "step_up_auth" | "analyst_info";
export type Route = "auto" | "L1" | "L2";
export type Action =
  | "ALLOW_TRANSACTION"
  | "DECLINE_TRANSACTION"
  | "MONITOR_CARD"
  | "MONITOR_CONNECTED_CARDS"
  | "WARN_CUSTOMER"
  | "VERIFY_WITH_CUSTOMER"
  | "STEP_UP_AUTH"
  | "BLOCK_CARD"
  | "BLOCK_ALL_CARDS"
  | "GENERATE_REPORT"
  | "CREATE_CASE"
  | "FILE_REPORT"
  | "ESCALATE_TO_ANALYST"
  | "CLOSE_NO_FRAUD";
export type TriggerType = "risk_score" | "customer_report" | "analyst_request";
export type CustomerResponse = "denied" | "confirmed" | "no_reply";

// ---- answer.py shapes --------------------------------------------------------

export interface EvidenceItem {
  claim: string;
  source: EvidenceSource;
  ref: string;
  entity_ids: string[];
}

export interface RecommendedAction {
  action: Action;
  route: Route;
  reason: string;
}

export interface SarPart {
  file: boolean;
  reason: string;
  narrative: string;
  subjects: string[];
  total_amount_usd: number;
  activity_dates: string[];
}

export interface EvidenceRequestRecord {
  type: EvidenceRequestType;
  asked_after_step: number;
  assumed_response: string;
}

/** The graded answer file (answer.schema.json). The UI renders it as a submission preview. */
export interface AnswerFile {
  case_id: string;
  case: {
    status: CaseStatus;
    verdict: Verdict;
    fraud_probability: number;
    pattern: Pattern;
    pattern_description: string;
    affected_txn_ids: string[];
    first_suspicious_txn_id: string;
    connected_card_ids: string[];
    connected_device_profiles: string[];
    exposure_usd: number;
    evidence: EvidenceItem[];
    similar_prior_cases: string[];
    summary: string;
    written_to_graph: boolean;
    graph_case_id: string;
  };
  evidence_requests: EvidenceRequestRecord[];
  next_best_actions: { initial: RecommendedAction[]; final: RecommendedAction[]; what_changed: string };
  sar: SarPart;
  stop_reason: string;
  tool_calls: number;
  tokens: number;
  latency_s: number;
}

// ---- internal_case.py shapes -------------------------------------------------

export interface Trigger {
  type: TriggerType;
  source: string;
  trigger_text: string;
  opened_at: string;
  flagged_txn_id: string;
  card_id: string;
  customer_id: string;
  risk_score: number | null;
}

export interface StatusEntry {
  status: CaseStatus;
  at: string;
  note: string;
}

export interface ScoreComponent {
  name: string;
  value: number;
  weight: number;
  contribution: number;
  observed: boolean;
  detail: string;
}

export interface RiskAssessment {
  fraud_probability: number;
  confidence: number;
  components: ScoreComponent[];
  unknowns: string[];
  evidence_coverage: number;
  signal_agreement: number;
  prior: number;
  method: string;
  checked_signals: string[];
  missing_signals: string[];
}

export interface Hypothesis {
  pattern: Pattern;
  score: number;
  present_signals: string[];
  absent_signals: string[];
  supporting_txn_ids: string[];
  rejected: boolean;
  rejection_reason: string;
}

export interface AgentStep {
  index: number;
  node: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  at: string;
  duration_s: number;
  ok: boolean;
}

/** approval_status is a free string on the backend. Values I have seen or expect. */
export type ApprovalStatus = "pending" | "approved" | "rejected" | "not_required" | "superseded" | string;

export interface Decision {
  actor: string;
  action: Action;
  route: Route;
  authorized: boolean;
  executed: boolean;
  reason: string;
  phase: "initial" | "final" | string;
  approval_status: ApprovalStatus;
  at: string;
}

export interface EvidenceRequest {
  type: EvidenceRequestType;
  asked_after_step: number;
  assumed_response: string;
  resolved_as: CustomerResponse | null;
  basis: string[];
}

export interface RetrievedChunk {
  ref: string;
  text: string;
  score: number;
  kind: string;
  // Filled for prior-case chunks (kind "prior_case") so the similar-cases panel can show
  // the outcome without a second lookup. Null on other chunk kinds.
  outcome?: string | null;
  pattern?: string | null;
  exposure_usd?: number | null;
}

export interface InternalCase {
  case_id: string;
  trigger: Trigger;
  status: CaseStatus;
  status_history: StatusEntry[];
  verdict: Verdict;
  pattern: Pattern;
  pattern_description: string;
  risk_assessment: RiskAssessment;
  hypotheses: Hypothesis[];
  affected_txn_ids: string[];
  first_suspicious_txn_id: string;
  connected_card_ids: string[];
  connected_device_profiles: string[];
  exposure_usd: number;
  evidence: EvidenceItem[];
  similar_prior_cases: string[];
  retrieved_chunks: RetrievedChunk[];
  summary: string;
  written_to_graph: boolean;
  graph_case_id: string;
  evidence_requests: EvidenceRequest[];
  customer_response: CustomerResponse | null;
  initial_actions: RecommendedAction[];
  final_actions: RecommendedAction[];
  what_changed: string;
  sar: SarPart | null;
  steps: AgentStep[];
  decisions: Decision[];
  stop_reason: string;
  tool_calls: number;
  tokens: number;
  latency_s: number;
}

// ---- client-side shapes ------------------------------------------------------

export type RunState = "idle" | "connecting" | "streaming" | "polling" | "done" | "error";

/** One parsed server-sent event. `step` payloads are AgentStep; `case` payloads are a full or partial InternalCase. */
export type StreamEvent =
  | { type: "step"; data: AgentStep }
  | { type: "case"; data: Partial<InternalCase> }
  | { type: "done"; data?: Partial<InternalCase> }
  | { type: "error"; data: { message: string } }
  | { type: string; data: unknown };

export interface ApprovalRequest {
  action: Action;
  decision: "approve" | "reject";
  approved: boolean;
  analyst: string;
  note: string;
}

export interface NewCaseRequest {
  trigger_type: TriggerType;
  flagged_txn_id: string;
  card_id: string;
  customer_id: string;
  risk_score: number | null;
  trigger_text: string;
}
