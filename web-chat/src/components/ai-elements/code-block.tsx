"use client";

import * as React from "react";
import { CodeIcon, FileCodeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from "@/components/ui/code-block";

type CollapsedCodeProps = {
  code: string;
  language?: string;
  title?: string;
};

/**
 * AI Elements Code Block, collapsed by default. The copy button always works;
 * the toggle reveals the file. The syntax highlighter is a tiny dynamic chunk
 * loaded only once a completed reply actually contains a python block.
 */
export const CollapsedCode = React.memo(function CollapsedCode({
  code,
  language = "python",
  title,
}: CollapsedCodeProps) {
  const [open, setOpen] = React.useState(false);
  const [Highlighter, setHighlighter] = React.useState<React.ComponentType<{
    code: string;
  }> | null>(null);
  const panelId = React.useId();

  const isPython = /^(python|py)$/i.test(language);
  const source = code.endsWith("\n") ? code.slice(0, -1) : code;
  const lineCount = React.useMemo(
    () => source.split("\n").length,
    [source],
  );
  const label =
    title ?? (isPython ? "model.py" : language ? language : "code");

  React.useEffect(() => {
    if (!isPython) return;
    let live = true;
    // Separate chunk; never on first paint, never while streaming.
    import("./python-highlight")
      .then((module) => {
        if (live) setHighlighter(() => module.default);
      })
      .catch(() => {
        // Plain text stays readable if the chunk cannot load.
      });
    return () => {
      live = false;
    };
  }, [isPython]);

  return (
    <CodeBlock
      size="sm"
      className="mt-1 gap-0 border border-[var(--line-soft)] bg-[var(--panel)] ring-0"
    >
      <CodeBlockHeader className="border-b border-[var(--line-soft)] px-2 py-1.5">
        <CodeBlockTitle className="font-mono text-[10px] font-normal text-[var(--dim)]">
          <FileCodeIcon className="size-3" />
          {label}
          <span className="text-[var(--dim-2)]"> · {lineCount} lines</span>
        </CodeBlockTitle>
        <CodeBlockActions className="static opacity-100">
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-expanded={open}
            aria-controls={panelId}
            aria-label={open ? "hide code" : "show code"}
            title={open ? "hide code" : "show code"}
            onClick={() => setOpen((value) => !value)}
          >
            <CodeIcon />
          </Button>
          <CodeBlockCopyButton value={source} />
        </CodeBlockActions>
      </CodeBlockHeader>
      {open ? (
        <CodeBlockContent
          id={panelId}
          className="max-h-[420px] overflow-auto px-2 py-2 font-mono text-[10.5px] leading-relaxed"
        >
          {Highlighter ? <Highlighter code={source} /> : source}
        </CodeBlockContent>
      ) : (
        <div className="px-2 py-1.5 text-[9.5px] text-[var(--dim-2)]">
          <button
            type="button"
            className="text-[var(--accent)] hover:underline"
            aria-controls={panelId}
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            show code
          </button>
        </div>
      )}
    </CodeBlock>
  );
});
