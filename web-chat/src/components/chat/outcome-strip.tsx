"use client";

import * as React from "react";

import type { ChatMsg, GeometrySelection } from "@/lib/chat-types";
import { describeOutcome, type OutcomeTone } from "@/lib/outcome";
import { CandidateChecks } from "./candidate-checks";
import { cn } from "@/lib/utils";

/** What a failed turn needs to retry or re-edit itself. */
export type RetryTarget = {
  assistantId: string;
  userMsgId: string;
  prompt: string;
  selection?: GeometrySelection | null;
  turn?: string;
};

const TONE: Record<
  OutcomeTone,
  { border: string; dot: string; text: string; title: string }
> = {
  ok: {
    border: "border-[#2f4a37]",
    dot: "bg-[var(--ok)]",
    text: "text-[var(--ok)]",
    title: "build passed",
  },
  err: {
    border: "border-[#572b2c]",
    dot: "bg-[var(--err)]",
    text: "text-[var(--err)]",
    title: "build failed",
  },
  warn: {
    border: "border-[#4a3d28]",
    dot: "bg-[var(--warn)]",
    text: "text-[var(--warn)]",
    title: "no reply",
  },
  dim: {
    border: "border-[var(--line)]",
    dot: "bg-[var(--dim-2)]",
    text: "text-[var(--dim)]",
    title: "turn result",
  },
};

/**
 * The per-turn result strip. It is driven by the structured `outcome`, never
 * by the model's words, so it is correct after a reload as well. The summary
 * clamps to two lines and carries the full text as a tooltip; the error text
 * stays selectable.
 */
export function OutcomeStrip({
  message,
  retryTarget,
  retryRunning,
  onRetry,
  onEdit,
}: {
  message: ChatMsg;
  retryTarget?: RetryTarget;
  retryRunning?: boolean;
  onRetry?: (target: RetryTarget) => void;
  onEdit?: (target: RetryTarget) => void;
}) {
  const stopped = message.stopped === true;
  const view = stopped
    ? null
    : describeOutcome(message.outcome, { file: message.appliedFile });

  if (!view && !stopped) return null;

  const tone = TONE[stopped ? "dim" : view!.tone];
  const text = stopped ? "stopped" : view!.text;
  const detail = stopped ? undefined : view!.detail;
  const title = [text, detail].filter(Boolean).join(" — ");

  const canRecover =
    message.failed === true &&
    retryTarget !== undefined &&
    onRetry !== undefined &&
    onEdit !== undefined;

  return (
    <div
      className={cn(
        "flex max-w-full min-w-0 flex-col gap-1 border-l-2 pl-2",
        tone.border,
      )}
      data-slot="outcome-strip"
    >
      <div
        className={cn(
          "flex min-w-0 flex-wrap items-start gap-x-1.5 gap-y-0.5 text-[10px] leading-snug",
          tone.text,
        )}
        title={title}
      >
        <span
          aria-hidden="true"
          className={cn("mt-[5px] inline-block size-1.5 shrink-0", tone.dot)}
        />
        <span className="line-clamp-2 min-w-0 wrap-break-word select-text">
          {text}
        </span>
        {detail ? (
          <span className="line-clamp-2 min-w-0 wrap-break-word select-text text-[var(--dim)]">
            · {detail}
          </span>
        ) : null}
      </div>

      {!stopped && message.outcome?.validation ? (
        <CandidateChecks validation={message.outcome.validation} />
      ) : null}

      {canRecover ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => onRetry(retryTarget)}
            disabled={retryRunning}
            aria-label="Retry this prompt"
            className="border border-[#4a3d28] bg-[var(--panel-2)] px-1.5 py-px text-[9.5px] text-[var(--warn)] hover:border-[#6a5638] hover:text-[#e2bd7a] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {retryRunning ? "retrying…" : "retry"}
          </button>
          <button
            type="button"
            onClick={() => onEdit(retryTarget)}
            disabled={retryRunning}
            aria-label="Edit this prompt in the composer"
            className="border border-[var(--line)] bg-[var(--panel-2)] px-1.5 py-px text-[9.5px] text-[var(--text)] hover:border-[var(--line-hover)] hover:text-[var(--text-strong)] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            edit prompt
          </button>
          {message.outcome?.revision != null ? (
            <span className="text-[9.5px] text-[var(--dim)]">
              r{message.outcome.revision} created
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
