"use client";

import * as React from "react";

import type { ClearChoice } from "@/lib/chat-types";

/**
 * The inline choice card for an ambiguous "clear the board" request. It stays
 * inside the thread, is dismissed after a choice, and is keyboard accessible
 * (first action focused, Escape cancels).
 */
export function ClearChoiceCard({
  choice,
  busy,
  onHideParts,
  onNewModel,
  onCancel,
}: {
  choice: ClearChoice;
  busy: boolean;
  onHideParts: () => void;
  onNewModel: () => void;
  onCancel: () => void;
}) {
  const firstRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    firstRef.current?.focus();
  }, []);

  return (
    <div
      role="group"
      aria-label="choose how to clear the board"
      data-slot="clear-choice"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
      className="flex max-w-full min-w-0 flex-col gap-2 border border-[#4a3d28] bg-[#1a1410] px-2.5 py-2"
    >
      <div className="flex flex-col gap-0.5">
        <div className="text-[10px] font-medium text-[var(--warn)]">
          clear the board?
        </div>
        <div className="text-[10px] leading-relaxed text-[var(--text)]">
          {choice.kind === "before"
            ? `“${choice.message}” can mean two different things. pick one:`
            : "that reply looks like it wants to clear the board. pick one:"}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            ref={firstRef}
            type="button"
            onClick={onHideParts}
            className="border border-[var(--line)] bg-[var(--panel-2)] px-1.5 py-0.5 text-[10px] text-[var(--text)] hover:border-[var(--line-hover)] hover:text-[var(--text-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            hide parts
          </button>
          <button
            type="button"
            onClick={onNewModel}
            disabled={busy}
            className="border border-[var(--line-hover)] bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] text-[var(--primary)] hover:bg-[var(--panel-3)] hover:text-[var(--text-strong)] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {busy ? "starting…" : "start a new model"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="border border-transparent px-1.5 py-0.5 text-[10px] text-[var(--dim)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            cancel
          </button>
        </div>
        <div className="text-[9.5px] leading-relaxed text-[var(--dim-2)]">
          <span className="text-[var(--dim)]">hide parts</span> hides them in
          the viewer; the model and history are unchanged.{" "}
          <span className="text-[var(--dim)]">new model</span> starts an empty
          model; older revisions stay in the history selector.
        </div>
      </div>
    </div>
  );
}
