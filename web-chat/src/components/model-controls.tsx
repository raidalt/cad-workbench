"use client";

import { useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { workspace, type Parameter, type Scalar } from "@/lib/workspace";

function subscribe(listener: () => void) {
  workspace.addEventListener("board", listener);
  return () => workspace.removeEventListener("board", listener);
}

function display(value: Scalar | undefined) {
  return typeof value === "number" ? String(Number(value.toFixed(6))) : String(value ?? "-");
}

function sliderStep(min: number, max: number) {
  const raw = (max - min) / 100;
  const scale = 10 ** Math.floor(Math.log10(raw));
  const unit = [1, 2, 5, 10].find(n => n * scale >= raw) ?? 10;
  return Math.max(.001, scale * unit);
}

function ParameterRow({ name, param, value, overridden, historical }: {
  name: string; param: Parameter; value: Scalar; overridden: boolean; historical: boolean;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const { min, max } = param;
  const bounded = typeof min === "number" && typeof max === "number" && max > min && typeof value === "number";
  const change = (next: Scalar) => {
    workspace.dispatchEvent(new CustomEvent("change-parameter", { detail: { name, value: next } }));
  };
  return <div className="param" title={param.description}>
    <div className="param-head">
      <span className="param-name">{name}</span>
      {overridden && <span className="chip tiny">override</span>}
      <span className="param-value">{display(draft ?? value)}</span>
      {param.unit && <span className="param-unit">{param.unit}</span>}
    </div>
    {bounded ? <>
      <input type="range" aria-label={name} min={min} max={max} step={sliderStep(min, max)}
        disabled={historical} value={draft ?? value}
        onChange={event => { const next = event.currentTarget.valueAsNumber; setDraft(next); change(next); }}
        onBlur={() => setDraft(null)} />
      <div className="param-foot"><span className="dim">{min}</span><span className="dim">{max}</span>
        <button type="button" className="param-reset" disabled={historical || !overridden}
          title={`Reset to ${display(param.default)}`} onClick={() => change(param.default)}>reset</button>
      </div>
    </> : <div className="param-foot dim">read-only</div>}
  </div>;
}

export function ModelControls({ revisions, parameters }: { revisions: HTMLElement; parameters: HTMLElement }) {
  const board = useSyncExternalStore(subscribe, () => workspace.board, () => null);
  const entries = Object.entries(board?.parameters ?? {});
  return <>
    {createPortal(<select id="revision-select" value={board?.revision ?? ""} disabled={!board?.revisions.length}
      onChange={event => workspace.dispatchEvent(new CustomEvent("view-revision", { detail: Number(event.target.value) }))}>
      {board?.revisions.map(rev => <option key={rev.n} value={rev.n}>r{rev.n}{rev.head ? " (head)" : ""}</option>)}
    </select>, revisions)}
    {createPortal(entries.length ? entries.map(([name, param]) => {
      const overrides = board!.overrides;
      const overridden = Object.hasOwn(overrides, name);
      return <ParameterRow key={name} name={name} param={param} overridden={overridden}
        historical={board!.revision !== board!.head} value={overridden ? overrides[name] : param.value ?? param.default} />;
    }) : <div className="note">{board ? "no parameters declared" : "connecting"}</div>, parameters)}
  </>;
}
