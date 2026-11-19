/**
 * The in-flight reply, deliberately outside React state.
 *
 * Every `chat_delta` appends to a string and schedules ONE flush per animation
 * frame. Only `StreamingMessage` subscribes, so the committed thread never
 * re-renders while tokens arrive. On `chat_done` the app reads `snapshotNow()`,
 * commits one message, and clears silently (the streaming row unmounts).
 */

export type StreamSnapshot = {
  text: string;
  thinking: string;
  /** Bumped once per rAF flush, so useSyncExternalStore sees a new object. */
  version: number;
};

const EMPTY: StreamSnapshot = { text: "", thinking: "", version: 0 };

let text = "";
let thinking = "";
let snapshot: StreamSnapshot = EMPTY;
let active = false;
let frame: number | null = null;
let scrollFrame: number | null = null;
let scrollHandler: (() => void) | null = null;

const listeners = new Set<() => void>();

function scheduleFlush() {
  if (frame !== null || typeof window === "undefined") return;
  frame = window.requestAnimationFrame(() => {
    frame = null;
    snapshot = { text, thinking, version: snapshot.version + 1 };
    for (const listener of listeners) listener();
  });
}

function cancelFlush() {
  if (frame === null || typeof window === "undefined") return;
  window.cancelAnimationFrame(frame);
  frame = null;
}

function applyPending() {
  if (snapshot.text === text && snapshot.thinking === thinking) return;
  snapshot = { text, thinking, version: snapshot.version + 1 };
}

export const streamStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },

  getSnapshot(): StreamSnapshot {
    return snapshot;
  },

  getServerSnapshot(): StreamSnapshot {
    return EMPTY;
  },

  isActive(): boolean {
    return active;
  },

  /** Start a new turn without notifying; the caller mounts the row. */
  begin(): void {
    cancelFlush();
    text = "";
    thinking = "";
    snapshot = { text: "", thinking: "", version: snapshot.version + 1 };
    active = true;
  },

  /** Replace a failed candidate immediately before a repair starts. */
  reset(): void {
    this.begin();
    for (const listener of listeners) listener();
  },

  pushText(delta: string): void {
    if (!delta) return;
    active = true;
    text += delta;
    scheduleFlush();
  },

  pushThinking(delta: string): void {
    if (!delta) return;
    active = true;
    thinking += delta;
    scheduleFlush();
  },

  /** Read the whole reply (applying any pending deltas) before committing it. */
  snapshotNow(): { text: string; thinking: string } {
    cancelFlush();
    applyPending();
    return { text, thinking };
  },

  /** Drop the buffer after the committed message has replaced the row. */
  clearSilent(): void {
    cancelFlush();
    active = false;
    text = "";
    thinking = "";
    snapshot = { text: "", thinking: "", version: snapshot.version + 1 };
  },

  hasContent(): boolean {
    return text.length > 0 || thinking.length > 0;
  },

  /** Thread registers the viewport-aware scroll function. */
  setScroll(handler: (() => void) | null): () => void {
    scrollHandler = handler;
    return () => {
      if (scrollHandler === handler) scrollHandler = null;
    };
  },

  /** One scroll check per frame, after React has committed the new text. */
  requestScroll(): void {
    if (scrollFrame !== null || typeof window === "undefined") return;
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = null;
      scrollHandler?.();
    });
  },
};

export type StreamStore = typeof streamStore;
