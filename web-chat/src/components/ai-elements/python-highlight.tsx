"use client";

import * as React from "react";

import { CodeBlockLine } from "@/components/ui/code-block";

type Kind =
  | "plain"
  | "comment"
  | "string"
  | "keyword"
  | "number"
  | "decorator"
  | "builtin";

type Token = { text: string; kind: Kind };

const CLASS: Record<Kind, string | undefined> = {
  plain: undefined,
  comment: "python-comment",
  string: "python-string",
  keyword: "python-keyword",
  number: "python-number",
  decorator: "python-decorator",
  builtin: "python-builtin",
};

// Strings and comments first, then decorators, keywords, numbers, builtins.
const PATTERN =
  /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|#[^\n]*)|(@[A-Za-z_]\w*)|(\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|raise|pass|break|continue|lambda|yield|global|nonlocal|assert|del|is|None|True|False|async|await|match|case)\b)|(\b\d+(?:\.\d+)?\b)|(\b(?:Part|Parameter|Profile|Study|print|len|range|float|int|str|list|dict|tuple|set|round|abs|min|max|sum)\b)/g;

function tokenize(code: string): Token[][] {
  const re = new RegExp(PATTERN.source, "g");
  const tokens: Token[] = [];
  let last = 0;

  for (let match = re.exec(code); match; match = re.exec(code)) {
    if (match.index > last) {
      tokens.push({ text: code.slice(last, match.index), kind: "plain" });
    }
    const value = match[0];
    let kind: Kind = "plain";
    if (match[1]) {
      kind = value.startsWith("#") ? "comment" : "string";
    } else if (match[2]) {
      kind = "decorator";
    } else if (match[3]) {
      kind = "keyword";
    } else if (match[4]) {
      kind = "number";
    } else if (match[5]) {
      kind = "builtin";
    }
    tokens.push({ text: value, kind });
    last = re.lastIndex;
  }
  if (last < code.length) {
    tokens.push({ text: code.slice(last), kind: "plain" });
  }

  const lines: Token[][] = [[]];
  for (const token of tokens) {
    const parts = token.text.split("\n");
    parts.forEach((part, index) => {
      if (index > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ text: part, kind: token.kind });
    });
  }
  return lines;
}

/**
 * A deliberately small Python tokenizer. It is not Tree-sitter: it colors
 * comments, strings, keywords, numbers, decorators and the cad builtins used
 * in model.py. That keeps the lazy chunk tiny instead of shipping Shiki.
 */
export default function HighlightedPython({ code }: { code: string }) {
  const lines = React.useMemo(() => tokenize(code), [code]);

  return (
    <>
      {lines.map((line, index) => (
        <CodeBlockLine key={index}>
          {line.length === 0
            ? undefined
            : line.map((token, tokenIndex) => (
                <span key={tokenIndex} className={CLASS[token.kind]}>
                  {token.text}
                </span>
              ))}
        </CodeBlockLine>
      ))}
    </>
  );
}
