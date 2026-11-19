import { normalizeValidation } from "./candidate-validation";
import type { ServerEvent, TurnOutcome } from "./chat-types";

function toInt(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.trunc(value)
    : null;
}

function toNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function toText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

/** Server turn ids may arrive as strings or numbers; the wire uses strings. */
export function normalizeTurnId(value: unknown): string | undefined {
  if (typeof value === "string" && value !== "") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

/**
 * Accept `history[].outcome` / `chat_done.outcome` only when it really is the
 * contract shape. A missing or malformed outcome stays `undefined`, so the UI
 * never invents a result the server did not report.
 */
export function normalizeOutcome(value: unknown): TurnOutcome | undefined {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Record<string, unknown>;

  const errorRaw = raw.error;
  let error: TurnOutcome["error"] = null;
  if (errorRaw && typeof errorRaw === "object") {
    const details = errorRaw as Record<string, unknown>;
    const message = toText(details.message) ?? "the reply failed";
    error = {
      message,
      code: toText(details.code),
      hint: toText(details.hint),
    };
  } else if (toText(errorRaw)) {
    error = { message: toText(errorRaw) as string };
  }

  return {
    validation: normalizeValidation(raw.validation),
    applied: raw.applied === true,
    verified: raw.verified === true,
    revision: toInt(raw.revision),
    activeRevision: toInt(raw.active_revision ?? raw.activeRevision),
    reverted: raw.reverted === true,
    error,
    ms: toNumber(raw.ms) ?? 0,
    build_ms: toNumber(raw.build_ms),
  };
}

export type AppliedEvent = {
  file: string;
  bytes: number;
  verified: boolean;
  revision: number | null;
};

export type PendingError = { message: string; code?: string };

/**
 * Fallback outcome for a server that sends the older event set (no
 * `chat_done.outcome`). Verification is only true when the `chat_applied`
 * event explicitly said so; anything else stays unverified.
 */
export function synthesizeOutcome(args: {
  applied: AppliedEvent | null;
  error: PendingError | null;
  startedAt: number;
  done?: ServerEvent;
}): TurnOutcome | undefined {
  const { applied, error, done } = args;
  const appliedFlag = applied !== null || done?.applied === true;
  if (!appliedFlag && !error && !done) return undefined;

  const verified = applied?.verified === true;
  const reverted =
    error !== null && applied !== null && applied.verified !== true;
  return {
    applied: appliedFlag,
    verified,
    revision: applied?.revision ?? toInt(done?.revision),
    activeRevision: null,
    reverted,
    error: error
      ? { message: error.message, code: error.code }
      : null,
    ms: Math.max(0, Date.now() - args.startedAt),
    build_ms: null,
  };
}

/**
 * A reverted edit: it touched the file, the build failed, and the previous
 * script was put back. It is still a failed turn (the server records
 * `failed: true`), but the strip words it as a revert, not a "no reply".
 */
export function isRevertedOutcome(outcome?: TurnOutcome): boolean {
  if (!outcome) return false;
  if (outcome.reverted) return true;
  return outcome.applied && !outcome.verified && outcome.error !== null;
}

/**
 * A failed turn worth retrying: an error with no verified result. Reverted
 * edits count too: the server records them with `failed: true`, and a retry is
 * exactly how the person tries a different change.
 */
export function isFailedOutcome(outcome?: TurnOutcome): boolean {
  return !!outcome && !outcome.verified && (!!outcome.error || outcome.validation?.ok === false);
}

export type OutcomeTone = "ok" | "err" | "warn" | "dim";

export type OutcomeView = {
  tone: OutcomeTone;
  /** The one-line summary (wraps to two lines). */
  text: string;
  /** The second, dimmer fragment: file name, error code, hint. */
  detail?: string;
  failed: boolean;
  reverted: boolean;
  revision: number | null;
};

/**
 * Turn an outcome into the strip's words. The wording is driven only by the
 * structured fields, never by the assistant's prose.
 */
export function describeOutcome(
  outcome: TurnOutcome | undefined,
  options: { file?: string } = {},
): OutcomeView | null {
  if (!outcome) return null;
  const file = options.file || "model.py";

  if (outcome.applied && outcome.verified) {
    const head: string[] = [];
    if (outcome.revision != null) head.push(`r${outcome.revision}`);
    if (outcome.build_ms != null) {
      head.push(`built in ${Math.round(outcome.build_ms)} ms`);
    }
    return {
      tone: "ok",
      text: head.length > 0 ? head.join(" · ") : "build passed",
      detail: `updated ${file}`,
      failed: false,
      reverted: false,
      revision: outcome.revision,
    };
  }

  if (isRevertedOutcome(outcome)) {
    // What was on screen when the build failed, recorded at failure time. Never
    // read this from the current viewport: navigating history would rewrite it.
    const shown = outcome.activeRevision ?? null;
    return {
      tone: "err",
      text: shown != null
        ? `build failed — change reverted; r${shown} stayed visible`
        : "build failed — change reverted",
      detail: outcome.error
        ? `${outcome.error.code ? `${outcome.error.code}: ` : ""}${
            outcome.error.message
          }`
        : undefined,
      failed: false,
      reverted: true,
      revision: null,
    };
  }

  if (outcome.applied) {
    // Applied, but no verification result was reported: say only that.
    return {
      tone: "dim",
      text: `updated ${file}`,
      detail:
        outcome.revision != null ? `r${outcome.revision}` : "not verified",
      failed: false,
      reverted: false,
      revision: outcome.revision,
    };
  }

  if (outcome.error?.code === "STOPPED") {
    return {
      tone: "dim",
      text: "stopped",
      failed: false,
      reverted: false,
      revision: outcome.revision,
    };
  }

  if (outcome.validation?.ok === false && !outcome.applied) {
    return {
      tone: "err", text: "Candidate checks failed — proposal not applied",
      detail: outcome.error?.message ?? outcome.validation.error?.message,
      failed: true, reverted: false, revision: null,
    };
  }

  if (outcome.error) {
    return {
      tone: "warn",
      text: outcome.error.message || "no reply",
      detail: [
        outcome.error.code,
        outcome.error.hint,
        outcome.revision != null ? `r${outcome.revision} created` : undefined,
      ]
        .filter(Boolean)
        .join(" · ") || undefined,
      failed: true,
      reverted: false,
      revision: outcome.revision,
    };
  }

  return null;
}
