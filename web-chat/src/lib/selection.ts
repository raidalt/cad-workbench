import type { GeometrySelection } from "./chat-types";

/** Validate only the transport shape; the server resolves geometry authoritatively. */
export function normalizeSelection(value: unknown): GeometrySelection | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<GeometrySelection>;
  if (!Number.isSafeInteger(item.revision) || (item.revision ?? 0) < 1 ||
      typeof item.part !== "string" || !item.part.trim() || item.part.length > 256 ||
      !["part", "face", "feature"].includes(item.kind ?? "")) return null;
  if (item.kind === "face" && (!Number.isSafeInteger(item.face) || (item.face ?? -1) < 0)) return null;
  if (item.kind === "feature" && (typeof item.feature !== "string" || !item.feature.trim())) return null;
  return {
    revision: item.revision!, part: item.part, kind: item.kind!,
    ...(Number.isSafeInteger(item.face) && (item.face ?? -1) >= 0 ? { face: item.face } : {}),
    ...(typeof item.ref === "string" ? { ref: item.ref.slice(0, 512) } : {}),
    ...(typeof item.feature === "string" ? { feature: item.feature.slice(0, 256) } : {}),
  };
}

export function selectionLabel(selection: GeometrySelection): string {
  const detail = selection.kind === "face"
    ? `face ${selection.face}${selection.ref ? ` · ${selection.ref}` : ""}`
    : selection.kind === "feature" ? selection.feature : "part";
  return `${selection.part} · ${detail} · r${selection.revision}`;
}
