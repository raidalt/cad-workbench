import type { CandidateCheck, CandidateIssue, CandidateValidation } from "./chat-types";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.slice(0, 2000) : undefined;
}

function issue(value: unknown): CandidateIssue | undefined {
  const raw = record(value);
  const message = text(raw?.message) ?? text(value);
  return message ? { message, code: text(raw?.code), part: text(raw?.part) } : undefined;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.slice(0, 128).flatMap((item) => text(item) ? [text(item)!] : []) : [];
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** Keep only displayable server evidence; malformed receipts never imply a pass. */
export function normalizeValidation(value: unknown): CandidateValidation | undefined {
  const raw = record(value);
  if (!raw || typeof raw.ok !== "boolean" || !Array.isArray(raw.checks)) return undefined;
  const checks: CandidateCheck[] = [];
  for (const value of raw.checks.slice(0, 128)) {
    const check = record(value);
    if (!check || typeof check.ok !== "boolean" || !text(check.kind)) continue;
    const parameters: CandidateCheck["parameters"] = {};
    for (const [key, value] of Object.entries(record(check.parameters) ?? {}).slice(0, 64)) {
      if (typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value)) || typeof value === "string") {
        Object.defineProperty(parameters, key.slice(0, 256), {
          value: typeof value === "string" ? value.slice(0, 256) : value, enumerable: true,
        });
      }
    }
    checks.push({
      kind: text(check.kind)!, parameters, ok: check.ok, error: issue(check.error),
      warnings: Array.isArray(check.warnings) ? check.warnings.slice(0, 128).flatMap((value) => {
        const parsed = issue(value); return parsed ? [parsed] : [];
      }) : [],
    });
  }
  const summary = record(raw.summary);
  return {
    ok: raw.ok, checks, error: issue(raw.error), warning: issue(raw.warning),
    summary: {
      // Also accept our normalized shape from local session storage.
      testedCorners: count(summary?.tested_corners ?? summary?.testedCorners),
      failedCorners: count(summary?.failed_corners ?? summary?.failedCorners),
      skippedParameters: strings(summary?.skipped_parameters ?? summary?.skippedParameters),
      limitations: strings(summary?.limitations),
      cornerFailuresBlocking: typeof summary?.corner_failures_blocking === "boolean"
        ? summary.corner_failures_blocking : typeof summary?.cornerFailuresBlocking === "boolean"
          ? summary.cornerFailuresBlocking : undefined,
    },
  };
}
