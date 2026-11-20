"use client";

import * as React from "react";

import { EMPTY_STARTERS, PromptInput, STARTERS } from "@/components/ai-elements/prompt-input";
import { Thread } from "@/components/chat/thread";
import type { RetryTarget } from "@/components/chat/outcome-strip";
import type {
  ApiErrorBody,
  ChatApiState,
  ChatMetrics,
  ChatMsg,
  ChatPostResponse,
  ClearChoice,
  HistoryItem,
  GeometrySelection,
  NewModelResponse,
  ServerEvent,
} from "@/lib/chat-types";
import { replyProposesClear, wantsClearBoard } from "@/lib/clear-intent";
import { parseMetrics, uid } from "@/lib/format";
import {
  loadLocal,
  mergeMessages,
  saveLocal,
  storageKey,
  workspacePath,
} from "@/lib/history";
import {
  isFailedOutcome,
  normalizeOutcome,
  normalizeTurnId,
  synthesizeOutcome,
  type AppliedEvent,
  type PendingError,
} from "@/lib/outcome";
import { workspace, type Board } from "@/lib/workspace";
import { selectionLabel } from "@/lib/selection";
import { streamStore } from "@/lib/stream-store";

type ThreadState = { messages: ChatMsg[]; running: boolean };

type ThreadAction =
  | {
      type: "hydrate";
      server?: HistoryItem[];
      local: ChatMsg[];
      running: boolean;
    }
  | { type: "merge"; server?: HistoryItem[]; running: boolean }
  | { type: "send"; message: ChatMsg }
  | { type: "append"; message: ChatMsg }
  | { type: "start" }
  | { type: "commit"; messages: ChatMsg[] }
  | { type: "metrics"; metrics: ChatMetrics }
  | { type: "turn"; id: string; turn: string }
  | { type: "turnLast"; turn: string }
  | { type: "truncate"; length: number }
  | { type: "edit"; id: string; content: string };

function threadReducer(state: ThreadState, action: ThreadAction): ThreadState {
  switch (action.type) {
    case "hydrate":
      return {
        messages: mergeMessages(action.local, action.server),
        running: action.running,
      };
    case "merge":
      return {
        messages: mergeMessages(state.messages, action.server),
        running: action.running,
      };
    case "send":
      return {
        messages: [...state.messages, action.message],
        running: true,
      };
    case "append":
      return { ...state, messages: [...state.messages, action.message] };
    case "start":
      return state.running ? state : { ...state, running: true };
    case "commit":
      return {
        messages:
          action.messages.length > 0
            ? [...state.messages, ...action.messages]
            : state.messages,
        running: false,
      };
    case "metrics": {
      for (let index = state.messages.length - 1; index >= 0; index -= 1) {
        if (state.messages[index].role !== "assistant") continue;
        const messages = state.messages.slice();
        messages[index] = { ...messages[index], metrics: action.metrics };
        return { ...state, messages };
      }
      return state;
    }
    case "turn": {
      let changed = false;
      const messages = state.messages.map((message) => {
        if (message.id === action.id && message.turn !== action.turn) {
          changed = true;
          return { ...message, turn: action.turn };
        }
        return message;
      });
      return changed ? { ...state, messages } : state;
    }
    case "turnLast": {
      for (let index = state.messages.length - 1; index >= 0; index -= 1) {
        const message = state.messages[index];
        if (message.role !== "user") continue;
        if (message.turn === action.turn) return state;
        const messages = state.messages.slice();
        messages[index] = { ...message, turn: action.turn };
        return { ...state, messages };
      }
      return state;
    }
    case "truncate": {
      const length = Math.max(0, Math.min(action.length, state.messages.length));
      if (length === state.messages.length) return state;
      return { ...state, messages: state.messages.slice(0, length) };
    }
    case "edit": {
      let changed = false;
      const messages = state.messages.map((message) => {
        if (message.id === action.id && message.content !== action.content) {
          changed = true;
          return { ...message, content: action.content };
        }
        return message;
      });
      return changed ? { ...state, messages } : state;
    }
    default:
      return state;
  }
}

type Meta = {
  available: boolean | null;
  model: string;
  provider: string;
  reason: string | null;
  configPath: string | null;
};

type Connection = "connecting" | "live" | "reconnecting" | "offline";

type Applied = { file: string };

type Draft = { text: string; token: number; append?: boolean };

const INITIAL_META: Meta = {
  available: null,
  model: "",
  provider: "",
  reason: null,
  configPath: null,
};

function subscribe(listener: () => void) {
  workspace.addEventListener("board", listener);
  workspace.addEventListener("selection", listener);
  return () => {
    workspace.removeEventListener("board", listener);
    workspace.removeEventListener("selection", listener);
  };
}

export function ChatApp() {
  const [thread, dispatch] = React.useReducer(threadReducer, {
    messages: [],
    running: false,
  });
  const [meta, setMeta] = React.useState<Meta>(INITIAL_META);
  const [connection, setConnection] = React.useState<Connection>("connecting");
  const [applied, setApplied] = React.useState<Applied | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [undoing, setUndoing] = React.useState(false);
  const [confirmReset, setConfirmReset] = React.useState(false);
  const [resetting, setResetting] = React.useState(false);
  const [clearChoice, setClearChoice] = React.useState<ClearChoice | null>(
    null,
  );
  const [newModelBusy, setNewModelBusy] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const selection = React.useSyncExternalStore(subscribe, () => workspace.selection, () => null);
  const [focusToken, setFocusToken] = React.useState(0);
  const [validation, setValidation] = React.useState<{ phase: string; message: string } | null>(null);
  const retryingRef = React.useRef(false);
  const board = React.useSyncExternalStore(subscribe, () => workspace.board, () => null);

  const runningRef = React.useRef(false);
  const pendingMetricsRef = React.useRef<ChatMetrics | null>(null);
  const pendingAppliedRef = React.useRef<AppliedEvent | null>(null);
  const pendingErrorRef = React.useRef<PendingError | null>(null);
  const activeTurnRef = React.useRef<string | undefined>(undefined);
  const turnStartedRef = React.useRef(0);
  const hydratedRef = React.useRef(false);
  const chatIdRef = React.useRef("");
  const resettingRef = React.useRef(false);
  const messagesRef = React.useRef<ChatMsg[]>([]);
  const draftTokenRef = React.useRef(0);
  const editCtxRef = React.useRef<{
    msgId: string;
    turn?: string;
  } | null>(null);

  const boardRef = React.useRef<Board | null>(null);

  React.useEffect(() => {
    boardRef.current = board;
  }, [board]);

  React.useEffect(() => {
    runningRef.current = thread.running;
  }, [thread.running]);

  React.useEffect(() => {
    messagesRef.current = thread.messages;
  }, [thread.messages]);

  React.useEffect(() => {
    if (!hydratedRef.current) return;
    saveLocal(storageKey(), chatIdRef.current, thread.messages);
  }, [thread.messages]);

  const restoreDraft = React.useCallback((text: string, append = false) => {
    draftTokenRef.current += 1;
    setDraft({ text, token: draftTokenRef.current, append });
  }, []);

  const switchChat = React.useCallback((chatId: string) => {
    if (chatIdRef.current === chatId) return;
    chatIdRef.current = chatId;
    activeTurnRef.current = undefined;
    editCtxRef.current = null;
    retryingRef.current = false;
    runningRef.current = false;
    streamStore.clearSilent();
    setClearChoice(null);
    setConfirmReset(false);
    setNotice(null);
    setValidation(null);
    workspace.dispatchEvent(new Event("clear-selection"));
    restoreDraft("");
    dispatch({ type: "hydrate", server: [], local: [], running: false });
  }, [restoreDraft]);

  const appendNote = React.useCallback((text: string) => {
    dispatch({
      type: "append",
      message: { id: uid(), role: "assistant", content: text, local: true },
    });
  }, []);

  const refreshBoard = React.useCallback(() => {
    workspace.dispatchEvent(new Event("refresh-board"));
  }, []);

  React.useEffect(() => {
    const focus = () => setFocusToken(token => token + 1);
    const fill = (event: Event) => {
      editCtxRef.current = null;
      restoreDraft((event as CustomEvent<string>).detail, true);
    };
    const changed = () => {
      if (editCtxRef.current) {
        editCtxRef.current = null;
        setNotice("Selection changed. The edited prompt will start a new turn.");
      }
    };
    workspace.addEventListener("focus-chat", focus);
    workspace.addEventListener("stress-draft", fill);
    workspace.addEventListener("selection", changed);
    return () => {
      workspace.removeEventListener("focus-chat", focus);
      workspace.removeEventListener("stress-draft", fill);
      workspace.removeEventListener("selection", changed);
    };
  }, [restoreDraft]);

  const clearSelection = React.useCallback(() => {
    workspace.dispatchEvent(new Event("clear-selection"));
  }, []);

  const beginTurn = React.useCallback(() => {
    setValidation(null);
    runningRef.current = true;
    pendingMetricsRef.current = null;
    pendingAppliedRef.current = null;
    pendingErrorRef.current = null;
    turnStartedRef.current = Date.now();
    streamStore.begin();
  }, []);

  const finishStream = React.useCallback(
    (errorText?: string, done?: ServerEvent, turnHint?: string) => {
      if (!runningRef.current && !streamStore.isActive()) return;
      const snapshot = streamStore.snapshotNow();
      const turn =
        normalizeTurnId(done?.turn) ??
        normalizeTurnId(turnHint) ??
        activeTurnRef.current;

      const outcome =
        normalizeOutcome(done?.outcome) ??
        synthesizeOutcome({
          applied: pendingAppliedRef.current,
          error:
            pendingErrorRef.current ??
            (errorText !== undefined ? { message: errorText } : null),
          startedAt: turnStartedRef.current || Date.now(),
          done,
        });

      const stopped = done?.stopped === true;
      const failed =
        isFailedOutcome(outcome) ||
        (errorText !== undefined && outcome === undefined);

      const hasContent =
        snapshot.text.trim() !== "" || snapshot.thinking.trim() !== "";
      const messages: ChatMsg[] = [];
      if (hasContent || errorText !== undefined || done) {
        messages.push({
          id: uid(),
          role: "assistant",
          content: snapshot.text,
          thinking: snapshot.thinking || undefined,
          metrics: pendingMetricsRef.current ?? undefined,
          turn,
          outcome,
          failed: failed || undefined,
          stopped: stopped || undefined,
          appliedFile: pendingAppliedRef.current?.file,
          local: true,
        });
      }

      const appliedFile = pendingAppliedRef.current?.file ?? "model.py";
      pendingMetricsRef.current = null;
      pendingAppliedRef.current = null;
      pendingErrorRef.current = null;
      activeTurnRef.current = undefined;
      streamStore.clearSilent();
      runningRef.current = false;
      setValidation(null);
      dispatch({ type: "commit", messages });
      if (retryingRef.current) {
        retryingRef.current = false;
        // The server decides whether this replaces the latest failed turn or
        // appends a retry of an older turn. Never truncate later user history.
        const chatId = chatIdRef.current;
        void fetch(workspacePath() + "/api/chat", { cache: "no-store" }).then(async (response) => {
          if (!response.ok) return;
          const data = await response.json() as ChatApiState;
          if (chatId !== chatIdRef.current || (data.chat_id ?? "") !== chatId ||
              runningRef.current || !Array.isArray(data.history)) return;
          dispatch({ type: "hydrate", server: data.history, local: [], running: false });
        }).catch(() => { /* Reconnect refreshes authoritative history. */ });
      }

      if (done?.applied && outcome?.verified !== false) {
        setApplied((current) => current ?? { file: appliedFile });
      }
      if (
        !stopped &&
        !failed &&
        snapshot.text.trim() !== "" &&
        replyProposesClear(snapshot.text)
      ) {
        setClearChoice((current) => current ?? { kind: "after" });
      }
      void refreshBoard();
    },
    [refreshBoard],
  );

  const postChat = React.useCallback(
    async (
      body: { message: string; retry_turn?: string; viewing?: number | null; selection?: GeometrySelection | null },
      localId?: string,
    ) => {
      const chatId = chatIdRef.current;
      try {
        const response = await fetch(workspacePath() + "/api/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            ...body,
            chat_id: chatId,
            viewing: body.viewing ?? boardRef.current?.revision ?? null,
          }),
        });
        const payload = (await response
          .json()
          .catch(() => null)) as ChatPostResponse | null;
        if (chatId !== chatIdRef.current) return;

        if (!response.ok || payload?.ok === false) {
          const detail = payload?.error;
          const text = detail?.message
            ? `${detail.message}${detail.hint ? ` (${detail.hint})` : ""}`
            : `request failed (${response.status})`;
          if (detail?.message && /still streaming/i.test(detail.message)) {
            streamStore.begin();
            runningRef.current = true;
            dispatch({ type: "start" });
            return;
          }
          retryingRef.current = false;
          setNotice(text);
          pendingErrorRef.current = { message: text, code: detail?.code };
          finishStream(text);
          return;
        }

        const turn = normalizeTurnId(payload?.turn);
        if (turn) {
          activeTurnRef.current = turn;
          if (localId) dispatch({ type: "turn", id: localId, turn });
        }
      } catch (error) {
        if (chatId !== chatIdRef.current) return;
        const text = `cannot reach the cad server: ${
          error instanceof Error ? error.message : String(error)
        }`;
        retryingRef.current = false;
        setNotice(text);
        pendingErrorRef.current = { message: text, code: "NETWORK" };
        finishStream(text);
      }
    },
    [finishStream],
  );

  React.useEffect(() => {
    let disposed = false;
    let opened = false;
    let initialLoaded = false;
    const subscriptions = new AbortController();
    let source: EventSource | null = null;
    let offlineTimer = 0;

    let loadNumber = 0;
    const load = async () => {
      const request = ++loadNumber;
      const previousChat = chatIdRef.current;
      try {
        const response = await fetch(workspacePath() + "/api/chat", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as ChatApiState;
        if (disposed || request !== loadNumber || previousChat !== chatIdRef.current) return;

        setMeta({
          available: data.available !== false,
          model: typeof data.model === "string" ? data.model : "",
          provider: typeof data.provider === "string" ? data.provider : "",
          reason: typeof data.reason === "string" ? data.reason : null,
          configPath:
            typeof data.config_path === "string" ? data.config_path : null,
        });
        initialLoaded = true;
        if (source?.readyState === EventSource.OPEN) {
          setConnection("live");
        }

        const chatId = data.chat_id ?? "";
        if (!hydratedRef.current || chatId !== chatIdRef.current) {
          if (hydratedRef.current) switchChat(chatId);
          else chatIdRef.current = chatId;
          dispatch({
            type: "hydrate",
            server: data.history,
            local: loadLocal(storageKey(), chatId),
            running: data.running === true,
          });
          hydratedRef.current = true;
          return;
        }

        dispatch({
          type: "merge",
          server: data.history,
          running: data.running === true,
        });

        // The stream ended while we were disconnected: commit what we have.
        if (data.running !== true && streamStore.isActive()) {
          finishStream();
        }
      } catch {
        if (disposed || request !== loadNumber || previousChat !== chatIdRef.current) return;
        setMeta((current) => ({
          ...current,
          available: false,
          reason: current.reason ?? "cannot reach the cad server",
        }));
        setConnection("offline");
      }
    };

    try {
      source = workspace.events;
    } catch {
      // Not during the effect itself: report it on the next task, and only if
      // this effect is still mounted.
      offlineTimer = window.setTimeout(() => {
        if (!disposed) setConnection("offline");
      }, 0);
    }

    if (source) {
      source.addEventListener("open", () => {
        if (disposed) return;
        setConnection("live");
        if (opened || !initialLoaded) {
          void load();
        }
        opened = true;
      }, { signal: subscriptions.signal });
      source.addEventListener("error", () => {
        if (disposed) return;
        setConnection(
          source && source.readyState === EventSource.CLOSED
            ? "offline"
            : "reconnecting",
        );
      }, { signal: subscriptions.signal });

      const listen = (
        name: string,
        handler: (data: ServerEvent) => void,
      ) => {
        source?.addEventListener(name, (event) => {
          try {
            handler(
              JSON.parse((event as MessageEvent<string>).data) as ServerEvent,
            );
          } catch {
            // A malformed event must not take the panel down.
          }
        }, { signal: subscriptions.signal });
      };

      listen("chat_start", (data) => {
        const turn = normalizeTurnId(data.turn);
        streamStore.begin();
        setValidation(null);
        pendingMetricsRef.current = null;
        pendingAppliedRef.current = null;
        pendingErrorRef.current = null;
        turnStartedRef.current = Date.now();
        runningRef.current = true;
        if (turn) {
          activeTurnRef.current = turn;
          if (!retryingRef.current) dispatch({ type: "turnLast", turn });
        }
        // Warm the markdown/code chunk while the reply streams, so the
        // completed message renders pretty on the first commit.
        void import("@/components/ai-elements/response");
        if (typeof data.model === "string" && data.model) {
          setMeta((current) => ({ ...current, model: data.model as string }));
        }
        dispatch({ type: "start" });
      });

      listen("chat_reset", (data) => {
        if (typeof data.chat_id !== "string") return;
        switchChat(data.chat_id);
        if (!initialLoaded) void load();
      });

      listen("chat_thinking", (data) => {
        if (!streamStore.isActive()) streamStore.begin();
        streamStore.pushThinking(
          typeof data.text === "string" ? data.text : "",
        );
        if (!runningRef.current) {
          runningRef.current = true;
          dispatch({ type: "start" });
        }
      });

      listen("chat_delta", (data) => {
        if (!streamStore.isActive()) streamStore.begin();
        streamStore.pushText(typeof data.text === "string" ? data.text : "");
        if (!runningRef.current) {
          runningRef.current = true;
          dispatch({ type: "start" });
        }
      });

      listen("chat_validation", (data) => {
        if (!data.phase || !["checking", "repairing", "passed", "failed"].includes(data.phase)) return;
        if (data.turn != null && activeTurnRef.current && String(data.turn) !== activeTurnRef.current) return;
        if (data.reset === true) streamStore.reset();
        setValidation({ phase: data.phase, message: data.message || data.phase });
      });

      listen("chat_applied", (data) => {
        const file = typeof data.file === "string" ? data.file : "model.py";
        const bytes = typeof data.bytes === "number" ? data.bytes : 0;
        pendingAppliedRef.current = {
          file,
          bytes,
          verified: data.verified === true,
          revision: typeof data.revision === "number" ? data.revision : null,
        };
        // Only announce an update while it is not already known to be reverted.
        if (data.verified !== false) setApplied({ file });
        setNotice(null);
      });

      listen("chat_undo", () => {
        setApplied(null);
        setNotice(null);
        void refreshBoard();
      });

      listen("chat_metrics", (data) => {
        const metrics = parseMetrics(data);
        if (!metrics) return;
        pendingMetricsRef.current = metrics;
        if (!runningRef.current) {
          dispatch({ type: "metrics", metrics });
        }
      });

      listen("chat_error", (data) => {
        const message =
          typeof data.message === "string" && data.message
            ? data.message
            : "the reply failed";
        pendingErrorRef.current = {
          message,
          code: typeof data.code === "string" ? data.code : undefined,
        };
        finishStream(message, data);
      });

      listen("chat_done", (data) => {
        finishStream(undefined, data);
      });

      listen("build_ok", () => {
        setApplied(null);
        void refreshBoard();
      });

      listen("state", () => {
        void refreshBoard();
      });
    }

    void load();
    void refreshBoard();

    return () => {
      disposed = true;
      if (offlineTimer) window.clearTimeout(offlineTimer);
      subscriptions.abort();
    };
  }, [finishStream, refreshBoard, switchChat]);

  const send = React.useCallback(
    async (raw: string, attached: GeometrySelection | null = workspace.selection) => {
      const message = raw.trim();
      if (!message || runningRef.current || resettingRef.current || meta.available !== true) return;

      if (wantsClearBoard(message)) {
        setClearChoice({ kind: "before", message });
        return;
      }

      const edit = editCtxRef.current;
      editCtxRef.current = null;
      setDraft(null);
      setClearChoice(null);

      let localId: string;
      if (edit?.turn) {
        localId = edit.msgId;
        retryingRef.current = true;
        dispatch({ type: "start" });
      } else {
        localId = uid();
        dispatch({
          type: "send",
          message: { id: localId, role: "user", content: message, selection: attached, local: true },
        });
      }

      beginTurn();
      activeTurnRef.current = edit?.turn;
      await postChat(
        edit?.turn ? { message, retry_turn: edit.turn } : { message, selection: attached },
        edit?.turn ? undefined : localId,
      );
    },
    [beginTurn, meta.available, postChat],
  );

  const retry = React.useCallback(
    async (target: RetryTarget) => {
      if (runningRef.current || resettingRef.current || meta.available !== true) return;
      const userIndex = messagesRef.current.findIndex(
        (message) => message.id === target.userMsgId,
      );
      if (userIndex < 0) return;

      editCtxRef.current = null;
      setDraft(null);
      setClearChoice(null);

      if (target.turn) {
        retryingRef.current = true;
        dispatch({ type: "start" });
        beginTurn();
        activeTurnRef.current = target.turn;
        await postChat({ message: target.prompt, retry_turn: target.turn });
        return;
      }
      // A request that never reached the server has no authoritative turn to
      // replace. Keep its failure receipt and repeat its original attachment.
      await send(target.prompt, target.selection ?? null);
    },
    [beginTurn, meta.available, postChat, send],
  );

  const editPrompt = React.useCallback(
    (target: RetryTarget) => {
      if (runningRef.current) return;
      const userIndex = messagesRef.current.findIndex(
        (message) => message.id === target.userMsgId,
      );
      if (userIndex < 0) return;

      // Preserve the original thread while editing. The server chooses append
      // or replacement and commits it only when this retry finishes.
      workspace.select(target.selection ?? null);
      editCtxRef.current = { msgId: target.userMsgId, turn: target.turn };
      setClearChoice(null);
      restoreDraft(target.prompt);
    },
    [restoreDraft],
  );

  const startNewModel = React.useCallback(
    async (choice?: ClearChoice | null) => {
      setNewModelBusy(true);
      try {
        const response = await fetch(workspacePath() + "/api/new-model", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({}),
        });
        const payload = (await response
          .json()
          .catch(() => null)) as NewModelResponse | null;
        if (!response.ok || payload?.ok === false) {
          const reason =
            payload?.error?.message ?? `request failed (${response.status})`;
          appendNote(`could not start a new model: ${reason}`);
          return;
        }
        if (choice?.kind === "before") {
          dispatch({
            type: "append",
            message: {
              id: uid(),
              role: "user",
              content: choice.message,
              local: true,
            },
          });
        }
        const revision =
          typeof payload?.revision === "number" ? payload.revision : null;
        appendNote(
          `started a new model${
            revision != null ? ` — r${revision}` : ""
          }. older revisions are still in the history selector.`,
        );
        clearSelection();
        void refreshBoard();
      } catch {
        appendNote("cannot reach the cad server");
      } finally {
        setNewModelBusy(false);
      }
    },
    [appendNote, clearSelection, refreshBoard],
  );

  const chooseHideParts = React.useCallback(() => {
    const choice = clearChoice;
    try {
      workspace.dispatchEvent(new Event("hide-parts"));
      clearSelection();
    } catch {
      // A blocked parent must not take the thread down.
    }
    if (choice?.kind === "before") {
      dispatch({
        type: "append",
        message: {
          id: uid(),
          role: "user",
          content: choice.message,
          local: true,
        },
      });
    }
    appendNote(
      "parts hidden in the viewer; the model and history are unchanged.",
    );
    setClearChoice(null);
  }, [appendNote, clearChoice, clearSelection]);

  const chooseNewModel = React.useCallback(() => {
    const choice = clearChoice;
    setClearChoice(null);
    void startNewModel(choice);
  }, [clearChoice, startNewModel]);

  const cancelClear = React.useCallback(() => {
    if (clearChoice?.kind === "before") {
      restoreDraft(clearChoice.message);
    }
    setClearChoice(null);
  }, [clearChoice, restoreDraft]);

  const stop = React.useCallback(() => {
    void fetch(workspacePath() + "/api/chat/stop", { method: "POST" }).catch(() => {});
  }, []);

  const resetChat = React.useCallback(async () => {
    if (resettingRef.current || runningRef.current) return;
    resettingRef.current = true;
    const chatId = chatIdRef.current;
    setResetting(true);
    setNotice(null);
    try {
      const response = await fetch(workspacePath() + "/api/chat/reset", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: chatId }),
      });
      const data = await response.json() as ApiErrorBody & { chat_id?: string };
      if (!response.ok || typeof data.chat_id !== "string") {
        throw new Error(data.error?.message ?? "Could not start a new chat.");
      }
      if (chatIdRef.current === chatId) switchChat(data.chat_id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not start a new chat.");
    } finally {
      resettingRef.current = false;
      setResetting(false);
    }
  }, [switchChat]);

  // Warm the markdown/code chunk as soon as the person starts composing, so
  // it is parsed before the first reply completes. Still never on first paint.
  const warmResponse = React.useCallback(() => {
    void import("@/components/ai-elements/response");
  }, []);

  const undo = React.useCallback(async () => {
    setUndoing(true);
    setNotice(null);
    try {
      const response = await fetch(workspacePath() + "/api/chat/undo", { method: "POST" });
      if (response.ok) {
        setApplied(null);
      } else {
        const payload = (await response
          .json()
          .catch(() => null)) as ApiErrorBody | null;
        setNotice(payload?.error?.message ?? "undo failed");
      }
    } catch {
      setNotice("cannot reach the cad server");
    } finally {
      setUndoing(false);
    }
  }, []);

  const pill =
    meta.available === false
      ? meta.reason?.includes("API key")
        ? { label: "no key", className: "border-[#4a3d28] text-[var(--warn)]" }
        : { label: "offline", className: "border-[#572b2c] text-[var(--err)]" }
      : connection === "live"
        ? { label: "live", className: "border-[#2f4a37] text-[var(--ok)]" }
        : connection === "reconnecting"
          ? {
              label: "reconnecting",
              className: "border-[#4a3d28] text-[var(--warn)]",
            }
          : connection === "offline"
            ? {
                label: "offline",
                className: "border-[#572b2c] text-[var(--err)]",
              }
            : {
                label: "connecting",
                className: "border-[var(--line)] text-[var(--dim)]",
              };

  const showSuggestions =
    meta.available === true && !thread.running && thread.messages.length === 0;
  const chatButton = "shrink-0 rounded-md px-2 py-1 text-[11px] text-[var(--primary)] hover:bg-[var(--panel-2)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50";

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--bg)] text-[var(--text)]">
      <div className="flex min-h-8 shrink-0 items-start gap-3 py-1.5 border-b border-[var(--line-soft)] px-3.5">
        <details className="min-w-0 flex-1 text-[11px] text-[var(--dim)]">
          <summary className="cursor-pointer" aria-label={`Chat connection: ${pill.label}. Show model details`}>
            <span role="status" className={`inline-flex items-center gap-1.5 capitalize ${pill.className}`}>
              <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
              {pill.label}
            </span>
          </summary>
          <span className="break-all">{meta.model || meta.provider}</span>
        </details>
        <button type="button" className={chatButton}
          onClick={() => setConfirmReset(true)}
          disabled={thread.running || resetting || connection !== "live" || thread.messages.length === 0}>
          New chat
        </button>
      </div>

      {confirmReset ? (
        <div role="group" aria-label="Start a new chat" className="shrink-0 border-b border-[var(--line)] bg-[var(--panel-2)] px-3 py-2">
          <p className="text-[12px] text-[var(--text)]">Clear the conversation and keep the current model?</p>
          <div className="mt-1 flex justify-end gap-2">
            <button type="button" className={chatButton} onClick={() => setConfirmReset(false)} disabled={resetting}>Cancel</button>
            <button type="button" className={chatButton} onClick={() => void resetChat()} disabled={resetting || thread.running}>Clear chat</button>
          </div>
        </div>
      ) : null}

      {meta.available === false ? (
        <div className="shrink-0 border-b border-[var(--line-soft)] bg-[#1a1410] px-3 py-2 text-[10px] leading-relaxed text-[var(--warn)]">
          <div className="font-medium">chat is not configured</div>
          <div className="mt-0.5 whitespace-pre-wrap break-words text-[var(--text)]">
            {meta.reason ??
              'no API key: put api_key = "..." in ~/.config/cad/chat.toml (mode 600), then restart "cad serve"'}
          </div>
          {meta.configPath ? (
            <div className="mt-1 break-all text-[9.5px] text-[var(--dim)]">
              config: {meta.configPath}
            </div>
          ) : null}
        </div>
      ) : null}

      {applied ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-[var(--line-soft)] bg-[var(--panel-2)] px-3 py-1 text-[9.5px] text-[var(--ok)]">
          <span className="inline-block size-1.5 shrink-0 bg-[var(--ok)]" />
          <span className="min-w-0 truncate">
            Updated {applied.file}
          </span>
          <button
            type="button"
            onClick={() => void undo()}
            disabled={undoing}
            className="ml-auto shrink-0 border border-[var(--line)] bg-[var(--panel)] px-1.5 py-px text-[9px] text-[var(--text)] hover:border-[var(--line-hover)] hover:text-[var(--text-strong)] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            undo
          </button>
        </div>
      ) : null}

      {notice ? (
        <div className="shrink-0 border-b border-[var(--line-soft)] px-3 py-1 text-[9.5px] text-[var(--err)]">
          {notice}
        </div>
      ) : null}

      <div className="min-h-0 flex-1">
        <Thread
          messages={thread.messages}
          running={thread.running}
          onRetry={retry}
          onEdit={editPrompt}
          boardEmpty={board?.empty === true}
          clearChoice={clearChoice}
          clearBusy={newModelBusy}
          onHideParts={chooseHideParts}
          onChooseNewModel={chooseNewModel}
          onCancelClear={cancelClear}
        />
      </div>

      {validation ? (
        <div role="status" aria-live="polite" className="cad-status shrink-0 border-t border-[var(--line)] px-3 py-2 text-xs" data-tone={validation.phase === "failed" ? "error" : undefined}>
          {validation.phase === "repairing" ? "Repairing design" : validation.phase === "checking" ? "Checking geometry" : validation.phase === "passed" ? "Geometry checked" : "Geometry check failed"}
          {validation.message ? ` · ${validation.message}` : ""}
        </div>
      ) : null}
      {selection ? (
        <div className="flex shrink-0 items-center mx-3 mt-1 justify-between gap-1 rounded-md bg-[var(--accent-soft)] px-2 py-1" data-slot="geometry-selection">
          <span className="min-w-0 break-words text-[11px] text-[var(--primary)]" role="status" aria-label="Attached geometry" title={selectionLabel(selection)}>
            {selectionLabel(selection)}
          </span>
          <button type="button" aria-label="Remove geometry selection" onClick={clearSelection} className="ml-auto shrink-0 rounded-md px-2 py-1 text-[11px] text-[var(--dim)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
            Remove
          </button>
        </div>
      ) : null}
      <PromptInput
        focusToken={focusToken}
        running={thread.running}
        available={meta.available === true}
        showSuggestions={showSuggestions}
        suggestions={board?.empty ? EMPTY_STARTERS : STARTERS}
        onSend={(text) => void send(text)}
        onStop={stop}
        onWarm={warmResponse}
        draft={draft}
      />
    </div>
  );
}
