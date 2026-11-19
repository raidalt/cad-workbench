"use client";

import * as React from "react";
import { useSyncExternalStore } from "react";

import { Reasoning } from "@/components/ai-elements/reasoning";
import { Message, MessageContent } from "@/components/ui/message";
import { streamStore } from "@/lib/stream-store";

/**
 * The only component that re-renders while tokens arrive. It subscribes to the
 * module-level store; the reply is plain text until `chat_done` commits it and
 * unmounts this row.
 */
export function StreamingMessage() {
  const snapshot = useSyncExternalStore(
    streamStore.subscribe,
    streamStore.getSnapshot,
    streamStore.getServerSnapshot,
  );

  React.useLayoutEffect(() => {
    streamStore.requestScroll();
  }, [snapshot.version]);

  return (
    <Message>
      <MessageContent className="gap-2 border-l border-[var(--line)] pl-3 pt-0.5">
        <Reasoning text={snapshot.thinking} running />
        {snapshot.text ? (
          <pre className="m-0 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-[var(--text)]">
            {snapshot.text}
          </pre>
        ) : (
          <div className="text-[10px] text-[var(--dim-2)]">
            waiting for the reply
          </div>
        )}
      </MessageContent>
    </Message>
  );
}
