"use client";

import * as React from "react";
import { Streamdown, type Components, type ExtraProps } from "streamdown";

import { CollapsedCode } from "@/components/ai-elements/code-block";

function FencedCode({
  children,
  className,
  node: _node,
  ...props
}: React.ComponentProps<"code"> & ExtraProps) {
  const language =
    /language-([\w-]+)/.exec(className ?? "")?.[1] ?? "python";
  const code =
    typeof children === "string"
      ? children
      : Array.isArray(children)
        ? children.join("")
        : String(children ?? "");

  return (
    <CollapsedCode code={code} language={language} title={undefined} />
  );
}

function InlineCode({
  children,
  node: _node,
  className,
  ...props
}: React.ComponentProps<"code"> & ExtraProps) {
  return (
    <code
      className={["rounded-none border border-[var(--line-soft)] bg-[var(--panel)] px-1 font-mono text-[10.5px] text-[var(--text-strong)]", className]
        .filter(Boolean)
        .join(" ")}
      {...props}
    >
      {children}
    </code>
  );
}

const components: Components = {
  code: FencedCode,
  inlineCode: InlineCode,
};

/**
 * AI Elements Response: markdown through Streamdown, with fenced code routed
 * to the collapsed Code Block. Only mounted for completed replies.
 */
export const Response = React.memo(function Response({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  return (
    <Streamdown
      mode="static"
      dir="ltr"
      controls={false}
      parseIncompleteMarkdown={false}
      components={components}
      className={className}
    >
      {children}
    </Streamdown>
  );
});
