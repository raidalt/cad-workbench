export type ChatRole = "user" | "assistant";

export type GeometrySelection = {
  revision: number;
  part: string;
  kind: "part" | "face" | "feature";
  face?: number;
  ref?: string;
  feature?: string;
};

export type ChatMetrics = {
  ttft_ms?: number;
  total_ms?: number;
  apply_ms?: number;
};

export type TurnError = {
  code?: string;
  message: string;
  hint?: string;
};

/**
 * The authoritative result of one assistant turn, as reported by the server
 * (`chat_done.outcome` / `history[].outcome`). Never inferred from the prose.
 */
export type CandidateIssue = { message: string; code?: string; part?: string };

export type CandidateCheck = {
  kind: string;
  parameters: Record<string, string | number | boolean>;
  ok: boolean;
  error?: CandidateIssue;
  warnings: CandidateIssue[];
};

export type CandidateValidation = {
  ok: boolean;
  checks: CandidateCheck[];
  summary: {
    testedCorners: number | null;
    failedCorners: number | null;
    skippedParameters: string[];
    limitations: string[];
    cornerFailuresBlocking?: boolean;
  };
  error?: CandidateIssue;
  warning?: CandidateIssue;
};

export type TurnOutcome = {
  validation?: CandidateValidation;
  applied: boolean;
  verified: boolean;
  revision: number | null;
  /** What was on screen when the turn failed (engine-reported, never inferred). */
  activeRevision: number | null;
  reverted: boolean;
  error: TurnError | null;
  ms: number;
  build_ms: number | null;
};

export type ChatMsg = {
  id: string;
  role: ChatRole | "error";
  content: string;
  thinking?: string;
  /** Geometry attached when this user prompt was sent. */
  selection?: GeometrySelection | null;
  /** Unix seconds, only for messages that came from the server history. */
  at?: number;
  metrics?: ChatMetrics;
  /** Server turn id: the user turn this row belongs to (also on the reply). */
  turn?: string;
  /** Assistant only: authoritative outcome, hydrated or built from events. */
  outcome?: TurnOutcome;
  /** Assistant only: the turn ended in an error and can be retried/edited. */
  failed?: boolean;
  /** Assistant only: the turn was stopped by the person. */
  stopped?: boolean;
  /** Assistant only: file the `chat_applied` event named, for the strip. */
  appliedFile?: string;
  /** Appended locally (notes / advisory turns), never paired for retry. */
  local?: boolean;
};

export type HistoryItem = {
  selection?: GeometrySelection | null;
  id?: string | number;
  role: ChatRole;
  content: string;
  at?: number;
  failed?: boolean;
  outcome?: TurnOutcome | null;
};

export type ChatApiState = {
  available?: boolean;
  provider?: string;
  url?: string;
  model?: string;
  models?: string[];
  config_path?: string;
  reason?: string | null;
  running?: boolean;
  chat_id?: string;
  history?: HistoryItem[];
};

export type ApiErrorBody = {
  ok?: false;
  error?: { code?: string; message?: string; hint?: string };
};

export type ChatPostResponse = ApiErrorBody & {
  ok?: boolean;
  started?: boolean;
  /** The new (or retried) user turn id. */
  turn?: string | number;
};

/** `GET /api/state`: only the fields the chat panel needs. */
export type StateApi = {
  revision?: number | null;
  head?: number | null;
  empty?: boolean;
  parts?: Record<string, unknown>;
};

export type NewModelResponse = ApiErrorBody & {
  ok?: boolean;
  revision?: number | null;
  backup?: string | null;
};

export type ServerEvent = {
  chat_id?: string;
  reset?: boolean;
  phase?: "checking" | "repairing" | "passed" | "failed";
  t?: string;
  model?: string;
  text?: string;
  file?: string;
  backup?: string | null;
  bytes?: number;
  restored?: string;
  from?: string;
  message?: string;
  code?: string;
  applied?: boolean;
  stopped?: boolean;
  ttft_ms?: number;
  total_ms?: number;
  apply_ms?: number;
  chars?: number;
  /** Turn identity, on chat_start / chat_error / chat_done. */
  turn?: string | number;
  verified?: boolean;
  revision?: number | null;
  /** `chat_done` carries the authoritative per-turn outcome. */
  outcome?: TurnOutcome | null;
  /** `state` events wrap their payload. */
  info?: Record<string, unknown>;
};

export type ClearChoice =
  | { kind: "before"; message: string }
  | { kind: "after" };
