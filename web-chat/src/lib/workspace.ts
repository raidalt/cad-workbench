import type { GeometrySelection } from "./chat-types";
import { workspacePath } from "./history";

export type Scalar = number | boolean | string | null;
export type Parameter = { default: Scalar; value?: Scalar; min?: number; max?: number; unit?: string; description?: string };
export type Board = {
  revision: number | null; head: number | null; empty: boolean; revisions: { n: number; head: boolean }[];
  parameters: Record<string, Parameter>; overrides: Record<string, Scalar>;
};

type Inspection = {
  target: GeometrySelection | null; selected: boolean; historical: boolean; replay: boolean;
  face: { part: string; faceId: number | null; error?: string; info?: { refs?: string[] } } | null;
  area: string; centroid: string; hover: string | null;
};

class Workspace extends EventTarget {
  board: Board | null = null;
  selection: GeometrySelection | null = null;
  inspection: Inspection | null = null;

  inspect(details: Inspection) {
    this.inspection = details;
    this.dispatchEvent(new Event("inspect"));
  }

  private stream?: EventSource;

  get events() {
    return this.stream ??= new EventSource(workspacePath() + "/api/events");
  }

  showBoard(board: Board) {
    this.board = board;
    if (this.selection && this.selection.revision !== board.revision) this.select(null);
    this.dispatchEvent(new Event("board"));
  }

  select(selection: GeometrySelection | null) {
    this.selection = selection;
    this.dispatchEvent(new Event("selection"));
  }
}

export const workspace = new Workspace();
declare global {
  interface Window { cadWorkspace: Workspace }
}
