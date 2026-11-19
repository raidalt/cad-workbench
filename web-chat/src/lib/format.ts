import type { ChatMetrics } from "./chat-types";

export function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const kb = bytes / 1024;
  if (kb < 100) return `${kb.toFixed(1)} kB`;
  return `${Math.round(kb)} kB`;
}

export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export function formatMetrics(metrics?: ChatMetrics): string {
  if (!metrics) return "";
  const parts: string[] = [];
  if (typeof metrics.ttft_ms === "number") {
    parts.push(`ttft ${Math.round(metrics.ttft_ms)} ms`);
  }
  if (typeof metrics.total_ms === "number") {
    parts.push(formatMs(metrics.total_ms));
  }
  if (typeof metrics.apply_ms === "number") {
    parts.push(`apply ${Math.round(metrics.apply_ms)} ms`);
  }
  return parts.join(" · ");
}

/** FNV-1a, so server history rows keep their key across re-fetches. */
export function hashString(value: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

export function historyId(role: string, content: string, at?: number): string {
  return `hist:${role}:${hashString(`${role}\u0000${content}\u0000${at ?? ""}`)}`;
}

export function parseMetrics(event: {
  ttft_ms?: unknown;
  total_ms?: unknown;
  apply_ms?: unknown;
}): ChatMetrics | null {
  const out: ChatMetrics = {};
  if (typeof event.ttft_ms === "number" && Number.isFinite(event.ttft_ms)) {
    out.ttft_ms = event.ttft_ms;
  }
  if (typeof event.total_ms === "number" && Number.isFinite(event.total_ms)) {
    out.total_ms = event.total_ms;
  }
  if (typeof event.apply_ms === "number" && Number.isFinite(event.apply_ms)) {
    out.apply_ms = event.apply_ms;
  }
  return Object.keys(out).length > 0 ? out : null;
}
