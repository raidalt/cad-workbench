"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";

export function Suggestions({
  items,
  disabled,
  onPick,
}: {
  items: readonly string[];
  disabled: boolean;
  onPick: (value: string) => void;
}) {
  if (items.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1 px-2.5 pb-1.5">
      {items.map((item) => (
        <Button
          key={item}
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onPick(item)}
          className="h-6 shrink-0 rounded-[2px] border-[var(--line)] bg-[var(--panel-2)] px-2 text-[9.5px] font-normal text-[var(--dim)] hover:border-[var(--line-hover)] hover:bg-[var(--panel-3)] hover:text-[var(--text-strong)]"
        >
          {item}
        </Button>
      ))}
    </div>
  );
}
