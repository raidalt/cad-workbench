import type { CandidateValidation } from "@/lib/chat-types";

function parameters(values: Record<string, string | number | boolean>): string {
  const pairs = Object.entries(values).map(([name, value]) => `${name}=${String(value)}`);
  return pairs.length ? pairs.join(", ") : "default parameters";
}

/** Persisted evidence from preflight, separate from the assistant's claims. */
export function CandidateChecks({ validation }: { validation: CandidateValidation }) {
  const samples = validation.checks.filter((check) => check.kind === "parameter_corner");
  const passed = samples.filter((check) => check.ok).length;
  const failed = samples.filter((check) => !check.ok);
  const current = validation.checks.find((check) => check.kind === "current");
  const reportedTested = validation.summary.testedCorners;
  const reportedFailed = validation.summary.failedCorners;
  const warnings = validation.checks.flatMap((check) => check.warnings.map((warning) => ({
    ...warning,
    context: check.kind === "current" ? "current geometry" : parameters(check.parameters),
  })));
  const limitations = validation.summary.limitations.length
    ? validation.summary.limitations
    : ["Sampled geometry checks do not prove the full parameter range, fit, strength, motion, or printability."];
  const summary = samples.length
    ? `${passed}/${samples.length} parameter samples passed`
    : current ? `current geometry ${current.ok ? "passed" : "failed"} · no parameter samples`
      : "no completed geometry checks";

  return (
    <details className="min-w-0 text-[10px] leading-relaxed text-[var(--dim)]" data-slot="candidate-checks">
      <summary className="cursor-pointer wrap-break-word hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
        Candidate checks · {summary}
        {warnings.length ? ` · ${warnings.length} advisories` : ""}
      </summary>
      <div className="mt-1 space-y-2 border-l border-[var(--line)] pl-2 wrap-break-word select-text">
        <p>
          {validation.ok ? "Candidate accepted by preflight." : "Candidate rejected by preflight."}
          {current ? ` Current geometry ${current.ok ? "passed" : "failed"}.` : " Current geometry result unavailable."}
          {current?.error ? ` ${current.error.message}` : ""}
          {samples.length === 0 ? " No parameter combinations were sampled." : " Counts cover the sampled endpoints only."}
        </p>
        {reportedTested !== null && reportedTested !== samples.length ? (
          <p>Server reports {reportedTested} tested combinations; {samples.length} detailed sample receipts are available.</p>
        ) : null}
        {reportedFailed !== null && reportedFailed !== failed.length ? (
          <p>Server reports {reportedFailed} failed combinations; {failed.length} detailed failure receipts are available.</p>
        ) : null}
        {failed.length ? (
          <div>
            <p className="text-[var(--warn)]">
              {validation.ok ? "Advisory" : "Failed"} parameter combinations ({failed.length})
            </p>
            <ul className="list-disc space-y-1 pl-4">
              {failed.map((check, index) => (
                <li key={index}>
                  <span className="text-[var(--text)]">{parameters(check.parameters)}</span>
                  {check.error ? ` — ${check.error.message}` : " — geometry check failed"}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {validation.warning ? <p className="text-[var(--warn)]">{validation.warning.message}</p> : null}
        {validation.error && !current?.error && failed.length === 0 ? (
          <p className="text-[var(--err)]">{validation.error.message}</p>
        ) : null}
        {warnings.length ? (
          <details>
            <summary className="cursor-pointer text-[var(--warn)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
              Build / print advisories ({warnings.length})
            </summary>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {warnings.map((warning, index) => (
                <li key={index}>
                  {warning.part ? `${warning.part} · ` : ""}{warning.context}: {warning.message}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {validation.summary.skippedParameters.length ? (
          <p>Parameters not sampled: {validation.summary.skippedParameters.join(", ")}.</p>
        ) : null}
        <div>
          <p>Limits of these checks</p>
          <ul className="list-disc space-y-1 pl-4">
            {limitations.map((limit, index) => <li key={index}>{limit}</li>)}
          </ul>
        </div>
      </div>
    </details>
  );
}
