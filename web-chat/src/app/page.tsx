import { readFileSync } from "node:fs";
import { join } from "node:path";
import { WorkspacePanel } from "./workspace-panel";

export default function Page() {
  const markup = readFileSync(join(process.cwd(), "src/app/workspace.html"), "utf8");
  return <>
    <div dangerouslySetInnerHTML={{ __html: markup }} />
    <WorkspacePanel />
  </>;
}
