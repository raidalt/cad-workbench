"use client";

import { useSyncExternalStore } from "react";
import { workspace } from "@/lib/workspace";

function subscribe(listener: () => void) {
  workspace.addEventListener("inspect", listener);
  return () => workspace.removeEventListener("inspect", listener);
}

export function SelectionInspector() {
  const details = useSyncExternalStore(subscribe, () => workspace.inspection, () => null);
  const { target, face } = details ?? {};
  if (!target && !face) return <div className="note">Select a face in the viewport or a feature in the model tree.</div>;
  const named = target && target.kind !== "face";
  const label = named ? target.kind === "feature" ? "Feature" : "Part" : details?.selected ? "selected" : "hover";
  const name = named ? target.feature ?? target.part : face?.part;
  return <>
    <div className="sel-head"><span className={named || details?.selected ? "tag on" : "tag"}>{label}</span>
      <span className="sel-part" title={name}>{name ?? "-"}</span>
      {!named && <span className="dim">{face?.faceId == null ? "no face" : `face ${face.faceId}`}</span>}
    </div>
    {!named && face && <>
      {face.faceId == null ? <div className="note">this triangle has no BRep face id</div> : face.info ? <>
        <div className="refs">{face.info.refs?.length ? face.info.refs.map(ref => <span key={ref} className="chip">{ref}</span>)
          : <span className="dim">face #{face.faceId} (unnamed)</span>}</div>
        <div className="kv"><span>area</span><span className="cad-readout">{details?.area}</span>
          <span>centroid</span><span className="cad-readout">{details?.centroid}</span></div>
      </> : <div className={face.error ? "note bad" : "note"}>{face.error ?? "loading face…"}</div>}
      {details?.hover && <div className="sel-secondary">{details.hover}</div>}
    </>}
    {target && <>
      <div className="selection-actions">
        <button type="button" onClick={() => workspace.dispatchEvent(new Event("ask-selection"))}>Ask in chat</button>
        <button type="button" title="Clear selection (Escape)" onClick={() => workspace.dispatchEvent(new Event("clear-selection"))}>Clear</button>
        {details?.replay && <button type="button" onClick={() => workspace.dispatchEvent(new Event("replay-selection"))}>View build frame</button>}
      </div>
      <div className="tiny dim">r{target.revision} · {details?.historical ? "historical selection · return to current to edit" : "attached to your next chat message"}</div>
    </>}
  </>;
}
