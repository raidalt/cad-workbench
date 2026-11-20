import type { GeometrySelection } from "./chat-types";
import { workspacePath } from "./history";

export type Board = { revision: number | null; head: number | null; empty: boolean };

class Workspace extends EventTarget {
  board: Board | null = null;
  selection: GeometrySelection | null = null;
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
