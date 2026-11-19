"use client";

import * as React from "react";

import { Reasoning } from "@/components/ai-elements/reasoning";
import { OutcomeStrip, type RetryTarget } from "@/components/chat/outcome-strip";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Message, MessageContent } from "@/components/ui/message";
import type { ChatMsg } from "@/lib/chat-types";
import { selectionLabel } from "@/lib/selection";
import { formatMetrics } from "@/lib/format";
import { isFailedOutcome, isRevertedOutcome } from "@/lib/outcome";
import { cn } from "@/lib/utils";

// Streamdown + the AI Elements Code Block live in their own chunk. The first
// completed reply loads it; streaming and first paint never pay for it.
const Response = React.lazy(() =>
  import("@/components/ai-elements/response").then((module) => ({
    default: module.Response,
  })),
);

function PlainReply({ text }: { text: string }) {
  return (
    <pre className="m-0 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-[var(--text)]">
      {text}
    </pre>
  );
}

/**
 * One committed turn. Memoized on the message object: appending a reply does
 * not re-render earlier turns, and a streaming delta never reaches this tree.
 * Retry props are only passed for failed turns, so toggling `running` leaves
 * every other row untouched.
 */
export const MessageItem = React.memo(function MessageItem({
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
  if (message.role === "user") {
    return (
      <Message align="end">
        <MessageContent>
          <Bubble
            align="end"
            variant="muted"
            className="[&>[data-slot=bubble-content]]:rounded-xl"
          >
            <BubbleContent className="border border-[var(--line)] bg-[var(--panel-3)] px-3 py-2 text-[13px] leading-relaxed text-[var(--text-strong)]">
              {message.selection ? (
                <div className="mb-1 text-[11px] text-[var(--primary)]" aria-label="Attached geometry">
                  {selectionLabel(message.selection)}
                </div>
              ) : null}
              {message.content}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    );
  }

  if (message.role === "error") {
    return (
      <Message>
        <MessageContent>
          <div className="border-l-2 border-[var(--err)] pl-2 text-[10.5px] leading-relaxed whitespace-pre-wrap break-words select-text text-[var(--err)]">
            {message.content}
          </div>
        </MessageContent>
      </Message>
    );
  }

  const hasProse = message.content.trim() !== "";
  const metrics = formatMetrics(message.metrics);
  const reverted = isRevertedOutcome(message.outcome);
  const failedTurn = message.failed === true || isFailedOutcome(message.outcome);
  const notApplied = reverted || failedTurn;

  const prose = hasProse ? (
    <div className="chat-prose">
      <React.Suspense fallback={<PlainReply text={message.content} />}>
        <Response>{message.content}</Response>
      </React.Suspense>
    </div>
  ) : null;

  return (
    <Message>
      <MessageContent
        className={cn(
          "gap-2 border-l border-[var(--line)] pl-3",
          message.thinking || !hasProse ? "pt-0.5" : undefined,
        )}
      >
        {message.thinking ? (
          <Reasoning text={message.thinking} running={false} />
        ) : null}
        {notApplied ? (
          <>
            {/* the authoritative result comes first: the edit did not land */}
            <OutcomeStrip
              message={message}
              retryTarget={retryTarget}
              retryRunning={retryRunning}
              onRetry={onRetry}
              onEdit={onEdit}
            />
            {hasProse ? (
              <details className="group min-w-0" data-slot="proposed-reply">
                <summary className="cursor-pointer text-[10px] text-[var(--dim)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
                  proposed reply and code — not applied
                </summary>
                <div className="mt-1 opacity-75">{prose}</div>
              </details>
            ) : null}
          </>
        ) : (
          <>
            {prose}
            <OutcomeStrip
              message={message}
              retryTarget={retryTarget}
              retryRunning={retryRunning}
              onRetry={onRetry}
              onEdit={onEdit}
            />
          </>
        )}
        {metrics ? (
          <div className="text-[9px] tabular-nums text-[var(--dim-2)]">
            {metrics}
          </div>
        ) : null}
      </MessageContent>
    </Message>
  );
});
