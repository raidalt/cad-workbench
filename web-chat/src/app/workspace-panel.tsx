"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { SelectionInspector } from "@/components/selection-inspector";
import { ModelControls } from "@/components/model-controls";
import { ChatApp } from "@/components/chat/chat-app";
import { workspace } from "@/lib/workspace";

export function WorkspacePanel() {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    window.cadWorkspace = workspace;
    setHost(document.getElementById("chat-root"));
    if (!document.getElementById("viewport-module")) {
      const script = document.createElement("script");
      script.id = "viewport-module";
      script.type = "module";
      script.src = "/static/app.js";
      document.body.appendChild(script);
    }
    const reload = () => setGeneration(n => n + 1);
    workspace.addEventListener("reload-chat", reload);
    return () => workspace.removeEventListener("reload-chat", reload);
  }, []);
  if (!host) return null;
  const inspector = document.getElementById("selection");
  const revisions = document.getElementById("revision-picker");
  const parameters = document.getElementById("params-list");
  return <>
    {createPortal(<ChatApp key={generation} />, host)}
    {inspector && createPortal(<SelectionInspector />, inspector)}
    {revisions && parameters && <ModelControls revisions={revisions} parameters={parameters} />}
  </>;
}
