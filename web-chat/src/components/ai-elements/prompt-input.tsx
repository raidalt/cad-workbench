"use client";

import * as React from "react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";

import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import { Suggestions } from "@/components/ai-elements/suggestion";

type PromptInputProps = {
  focusToken?: number;
  running: boolean;
  available: boolean;
  showSuggestions: boolean;
  suggestions: readonly string[];
  onSend: (text: string) => void;
  onStop: () => void;
  onWarm?: () => void;
  /**
   * A one-shot draft from outside (Retry/Edit or a cancelled clear choice).
   * The textarea stays uncontrolled; a new token writes the value and focuses.
   */
  draft?: { text: string; token: number; append?: boolean } | null;
};

/**
 * AI Elements Prompt Input. The textarea is uncontrolled: no value/onChange
 * state, so typing never re-renders the app or the thread while a reply
 * streams. Enter sends, Shift+Enter adds a line, Escape blurs.
 */
export function PromptInput({
  focusToken,
  running,
  available,
  showSuggestions,
  suggestions,
  onSend,
  onStop,
  onWarm,
  draft,
}: PromptInputProps) {
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const warmedRef = React.useRef(false);
  const disabled = running || !available;
  const [hasText, setHasText] = React.useState(false);

  const warm = React.useCallback(() => {
    if (warmedRef.current) return;
    warmedRef.current = true;
    onWarm?.();
  }, [onWarm]);

  const resize = React.useCallback((element: HTMLTextAreaElement) => {
    setHasText(Boolean(element.value.trim()));
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 120)}px`;
  }, []);

  // External draft: write once per token, focus, caret at the end. Typing
  // remains uncontrolled, so this never re-renders on input.
  React.useEffect(() => {
    if (!draft) return;
    const element = textareaRef.current;
    if (!element) return;
    const existing = draft.append ? element.value.trimEnd() : "";
    element.value = existing ? `${existing}\n\n${draft.text}` : draft.text;
    resize(element);
    element.focus();
    element.setSelectionRange(element.value.length, element.value.length);
  }, [draft, resize]);

  React.useEffect(() => {
    if (focusToken) textareaRef.current?.focus();
  }, [focusToken]);

  const submit = React.useCallback(() => {
    const element = textareaRef.current;
    if (!element) return;
    const text = element.value.trim();
    if (!text || running || !available) return;
    element.value = "";
    setHasText(false);
    element.style.height = "";
    onSend(text);
  }, [available, onSend, running]);

  const pickSuggestion = React.useCallback(
    (value: string) => {
      if (running || !available) return;
      const element = textareaRef.current;
      if (!element) return;
      const prefix = element.value.trimEnd();
      element.value = prefix ? `${prefix}\n\n${value}` : value;
      resize(element);
      element.closest("form")?.parentElement?.querySelector("details")?.removeAttribute("open");
      element.focus();
    },
    [available, resize, running],
  );

  return (
    <div className="shrink-0 border-t border-[var(--line)] bg-[var(--panel)] pt-1.5">
      {showSuggestions ? (
        <details className="mx-3 mb-2 text-xs text-[var(--dim)]">
          <summary>Example prompts</summary>
          <Suggestions
            items={suggestions}
            disabled={disabled}
            onPick={pickSuggestion}
          />
        </details>
      ) : null}

      <form
        className="px-3 pb-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <InputGroup className="cad-surface cad-composer">
          <InputGroupTextarea
            ref={textareaRef}
            rows={2}
            disabled={disabled}
            aria-label="chat message"
            placeholder={
              available
                ? running
                  ? "waiting for the reply"
                  : "Describe a part or request a change…"
                : "chat unavailable"
            }
            onInput={(event) => {
              warm();
              resize(event.currentTarget);
            }}
            onFocus={warm}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                event.currentTarget.blur();
              }
            }}
            className="min-h-[48px] max-h-[120px] resize-none rounded-none bg-transparent px-3 py-2.5 text-[13px] leading-relaxed text-[var(--text)] placeholder:text-[var(--dim-2)] disabled:opacity-60 md:text-[13px]"
          />
          <InputGroupAddon
            align="block-end"
            className="gap-2 px-2 pb-1.5 pt-0.5"
          >
            <span className="min-w-0 truncate text-[10px] text-[var(--dim-2)]">
              {running ? "reply streaming" : "Enter to send · Shift+Enter for a new line"}
            </span>
            <div className="ml-auto flex items-center gap-1">
              {running ? (
                <InputGroupButton
                  type="button"
                  size="icon-sm"
                  variant="outline"
                  aria-label="Stop the reply"
                  title="stop"
                  onClick={onStop}
                  className="rounded-[2px] border-[var(--line)]"
                >
                  <SquareIcon />
                </InputGroupButton>
              ) : (
                <InputGroupButton
                  type="submit"
                  size="icon-sm"
                  variant="outline"
                  aria-label="Send"
                  disabled={disabled || !hasText}
                  className="cad-button-primary"
                >
                  <ArrowUpIcon />
                </InputGroupButton>
              )}
            </div>
          </InputGroupAddon>
        </InputGroup>
      </form>
    </div>
  );
}

export const STARTERS = [
  "make the wall 3 mm",
  "add a 4 mm hole in the centre",
  "what is wrong for printing?",
] as const;

export const EMPTY_STARTERS = [
  "Design a 60 × 40 × 4 mm mounting plate with four corner holes",
  "Make an L-bracket with two mounting holes",
] as const;
