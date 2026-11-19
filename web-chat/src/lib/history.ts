import type { ChatMsg, HistoryItem } from "./chat-types";
import { normalizeSelection } from "./selection";
import { historyId } from "./format";
import {
  isFailedOutcome,
  normalizeOutcome,
  normalizeTurnId,
} from "./outcome";

/**
 * Merge the server's persisted history with the local session copy.
 *
 * Server rows carry `id` (the user turn id) and, for assistant rows, the
 * authoritative `outcome`/`failed`. Those fields must survive a reload, so the
 * merge keeps them instead of rebuilding a bare role/content pair.
 *
 * Dedupe by role + content + at first, then by role + content for local rows
 * that were committed after the last server save. Identical repeated prompts
 * are paired one-for-one, so two real turns never collapse into one.
 */
export function mergeMessages(
  local: ChatMsg[],
  server: HistoryItem[] | undefined,
): ChatMsg[] {
  const history = Array.isArray(server) ? server : [];
  const usedIds = new Set<string>();
  const merged: ChatMsg[] = history.map((item, index) => {
    const turn = item.role === "user" ? normalizeTurnId(item.id) : undefined;
    const base =
      item.id != null
        ? `hist:${item.role}:${String(item.id)}`
        : historyId(item.role, item.content, item.at);
    let id = base;
    let suffix = index;
    while (usedIds.has(id)) {
      id = `${base}:${suffix}`;
      suffix += 1;
    }
    usedIds.add(id);

    const outcome =
      item.role === "assistant" ? normalizeOutcome(item.outcome) : undefined;
    return {
      id,
      role: item.role,
      content: item.content,
      at: item.at,
      selection: item.role === "user" ? normalizeSelection(item.selection) : undefined,
      turn,
      outcome,
      failed:
        item.role === "assistant" &&
        (item.failed === true || isFailedOutcome(outcome))
          ? true
          : undefined,
    };
  });

  const exact = new Set(
    history.map((item) => `${item.role}\u0000${item.content}\u0000${item.at ?? ""}`),
  );
  const loose = new Map<string, number>();
  for (const item of history) {
    const key = `${item.role}\u0000${item.content}`;
    loose.set(key, (loose.get(key) ?? 0) + 1);
  }

  for (const message of local) {
    if (message.role === "error") {
      merged.push(message);
      continue;
    }

    const exactKey = `${message.role}\u0000${message.content}\u0000${message.at ?? ""}`;
    if (message.at != null && exact.has(exactKey)) {
      continue;
    }

    const looseKey = `${message.role}\u0000${message.content}`;
    const remaining = loose.get(looseKey) ?? 0;
    if (message.at == null && remaining > 0) {
      loose.set(looseKey, remaining - 1);
      continue;
    }

    merged.push(message);
  }

  return merged;
}

const STORAGE_PREFIX = "cad.chat:";

export function workspacePath(): string {
  if (typeof window === "undefined") return "";
  const path = new URLSearchParams(window.location.search).get("workspace") ?? "";
  return /^\/workspace\/[^/]+$/.test(path) ? path : "";
}

export function storageKey(): string {
  if (typeof window === "undefined") return `${STORAGE_PREFIX}unknown`;
  return `${STORAGE_PREFIX}${window.location.pathname}${workspacePath()}`;
}

export function loadLocal(key: string, chatId: string): ChatMsg[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    const saved = parsed as { chatId?: string; messages?: unknown } | null;
    const messages = Array.isArray(parsed) && chatId === ""
      ? parsed
      : saved?.chatId === chatId ? saved.messages : null;
    if (!Array.isArray(messages)) return [];
    return messages.filter(isChatMsg).map((message) => ({
      ...message, selection: normalizeSelection(message.selection),
      outcome: normalizeOutcome(message.outcome),
    }));
  } catch {
    return [];
  }
}

export function saveLocal(key: string, chatId: string, messages: ChatMsg[]): void {
  if (typeof window === "undefined") return;
  try {
    // Errors and assistant replies are small; cap defensively anyway.
    window.sessionStorage.setItem(key, JSON.stringify({ chatId, messages: messages.slice(-80) }));
  } catch {
    // Quota or private mode: the thread still works for this page life.
  }
}

function isChatMsg(value: unknown): value is ChatMsg {
  if (!value || typeof value !== "object") return false;
  const message = value as Partial<ChatMsg>;
  return (
    typeof message.id === "string" &&
    (message.role === "user" ||
      message.role === "assistant" ||
      message.role === "error") &&
    typeof message.content === "string"
  );
}
