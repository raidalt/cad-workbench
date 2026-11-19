"use client";

import * as React from "react";
import { CircleCheckIcon, LoaderCircleIcon } from "lucide-react";

import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace";

type ReasoningProps = {
  text: string;
  running: boolean;
};

/**
 * AI Elements Reasoning on Trace. Collapsed by default (also while streaming):
 * the thinking is plain text and is only painted when the reader opens it, so
 * no markdown work happens per token.
 */
export const Reasoning = React.memo(function Reasoning({
  text,
  running,
}: ReasoningProps) {
  const [open, setOpen] = React.useState(false);

  const line = (
    <TraceLine status={running ? "running" : undefined}>
      <TraceIcon>
        {running ? (
          <LoaderCircleIcon className="animate-spin" />
        ) : (
          <CircleCheckIcon />
        )}
      </TraceIcon>
      <TraceTitle>{running ? "thinking" : "thought"}</TraceTitle>
    </TraceLine>
  );

  if (!text) {
    return <Trace>{line}</Trace>;
  }

  return (
    <Trace>
      <TraceCollapsible
        open={open}
        onOpenChange={(next) => setOpen(next)}
      >
        <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <pre className="m-0 max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-[var(--dim)]">
            {text}
          </pre>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  );
});
