"use client";

import * as React from "react";

import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { MessageItem } from "@/components/chat/message-item";
import { StreamingMessage } from "@/components/chat/streaming-message";
import { ClearChoiceCard } from "@/components/chat/clear-choice";
import type { RetryTarget } from "@/components/chat/outcome-strip";
import type { ChatMsg, ClearChoice } from "@/lib/chat-types";
import { streamStore } from "@/lib/stream-store";

type ThreadProps = {
  messages: ChatMsg[];
  running: boolean;
  onRetry: (target: RetryTarget) => void;
  onEdit: (target: RetryTarget) => void;
  boardEmpty: boolean;
  clearChoice: ClearChoice | null;
  clearBusy: boolean;
  onHideParts: () => void;
  onChooseNewModel: () => void;
  onCancelClear: () => void;
};

/**
 * AI Elements Chat: Message Scroller with one memoized row per committed
 * turn, plus one streaming row while the reply is in flight. Autoscroll runs
 * in rAF and only when the viewport is within 80 px of the bottom.
 */
export const Thread = React.memo(function Thread({
  messages,
  running,
  onRetry,
  onEdit,
  boardEmpty,
  clearChoice,
  clearBusy,
  onHideParts,
  onChooseNewModel,
  onCancelClear,
}: ThreadProps) {
  const viewportRef = React.useRef<HTMLDivElement>(null);

  // Pair each failed reply with the user turn that produced it. This is what
  // keeps Retry/Edit pinned to the right turn id after a reload.
  const retryTargets = React.useMemo(() => {
    const targets = new Map<string, RetryTarget>();
    let lastUser: ChatMsg | null = null;
    for (const message of messages) {
      if (message.role === "user") {
        lastUser = message;
        continue;
      }
      if (
        message.role === "assistant" &&
        message.failed === true &&
        lastUser !== null
      ) {
        targets.set(message.id, {
          assistantId: message.id,
          userMsgId: lastUser.id,
          prompt: lastUser.content,
          selection: lastUser.selection,
          turn: lastUser.turn,
        });
      }
    }
    return targets;
  }, [messages]);

  const scrollIfNearBottom = React.useCallback(() => {
    const element = viewportRef.current;
    if (!element) return;
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    if (distance <= 80) {
      element.scrollTop = element.scrollHeight;
    }
  }, []);

  React.useEffect(
    () => streamStore.setScroll(scrollIfNearBottom),
    [scrollIfNearBottom],
  );

  React.useLayoutEffect(() => {
    streamStore.requestScroll();
  }, [messages.length, running, clearChoice]);

  const showHint =
    !running && messages.length === 0 && !boardEmpty && !clearChoice;

  return (
    <MessageScrollerProvider
      scrollEdgeThreshold={80}
      defaultScrollPosition="end"
    >
      <MessageScroller className="h-full">
        <MessageScrollerViewport
          ref={viewportRef}
          aria-label="chat thread"
          className="[scrollbar-gutter:stable]"
        >
          <MessageScrollerContent className="gap-4 px-3 py-3">
            {messages.map((message) => {
              const target = message.failed
                ? retryTargets.get(message.id)
                : undefined;
              return (
                <MessageScrollerItem key={message.id} messageId={message.id}>
                  <MessageItem
                    message={message}
                    retryTarget={target}
                    retryRunning={target ? running : undefined}
                    onRetry={target ? onRetry : undefined}
                    onEdit={target ? onEdit : undefined}
                  />
                </MessageScrollerItem>
              );
            })}

            {running ? (
              <MessageScrollerItem>
                <StreamingMessage />
              </MessageScrollerItem>
            ) : null}

            {clearChoice ? (
              <MessageScrollerItem>
                <ClearChoiceCard
                  choice={clearChoice}
                  busy={clearBusy}
                  onHideParts={onHideParts}
                  onNewModel={onChooseNewModel}
                  onCancel={onCancelClear}
                />
              </MessageScrollerItem>
            ) : null}

            {showHint ? (
              <MessageScrollerItem>
                <div className="px-1 py-2 text-[10px] leading-relaxed text-[var(--dim)]">
                  Select a part, face, or feature to ask about it. Chat checks
                  proposed geometry before updating the model.
                </div>
              </MessageScrollerItem>
            ) : null}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScroller>
    </MessageScrollerProvider>
  );
});
