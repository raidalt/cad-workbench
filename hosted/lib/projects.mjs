import { createHash, randomUUID } from "node:crypto";
import { get, put } from "@vercel/blob";

export const actions = new Set(["validate", "build", "undo", "state", "mesh", "faces", "print", "fea", "export", "preview", "clearance"]);

export class Conflict extends Error {}

export async function runProjectJob(project, input, store, execute) {
  if (typeof project !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(project) || !actions.has(input.action)) throw new TypeError("Invalid project or operation");
  const head = await store.head(project);
  const mutates = ["build", "undo"].includes(input.action);
  if (mutates && input.base !== (head?.version ?? null)) throw new Conflict("Project changed. Reload before applying the edit.");
  if (!head && !["build", "validate"].includes(input.action)) throw new Conflict("Build the project first.");
  const args = input.args ?? {};
  if (typeof args !== "object" || Array.isArray(args)) throw new TypeError("Invalid job arguments");
  const cacheable = head && ["mesh", "preview", "export"].includes(input.action);
  const key = cacheable && createHash("sha256").update(JSON.stringify(
    [head.version, input.action, Object.entries(args).sort(([a], [b]) => a.localeCompare(b))]
  )).digest("hex");
  if (key && store.artifact) {
    const saved = await store.artifact(project, key);
    if (saved) return { ...saved, version: head.version, status: 200 };
  }
  const previous = head ? await store.read(project, head.version) : null;
  const result = await execute({ action: input.action, args }, previous);
  if (key && result.status === 200 && store.saveArtifact) {
    await store.saveArtifact(project, key, result);
  }
  let version = head?.version ?? null;
  if (mutates && result.status === 200) {
    if (!result.candidate) throw new Error("Worker did not return a checkpoint");
    version = await store.publish(project, head, result.candidate);
  }
  return { status: result.status, type: result.type, data: result.data, version };
}

const options = { access: "private" };
export const blobProjects = {
  async artifact(project, key) {
    const result = await get(`cad/${project}/artifacts/${key}`, options);
    if (!result) return null;
    return { type: result.blob.contentType, data: Buffer.from(await new Response(result.stream).arrayBuffer()) };
  },
  async saveArtifact(project, key, result) {
    await put(`cad/${project}/artifacts/${key}`, result.data,
      { ...options, contentType: result.type, allowOverwrite: true });
  },
  async head(project) {
    const result = await get(`cad/${project}/head.json`, { ...options, useCache: false });
    if (!result) return null;
    const head = await new Response(result.stream).json();
    if (typeof head.version !== "string" || !/^[a-f0-9-]{36}$/.test(head.version)) throw new Error("Invalid checkpoint");
    return { version: head.version, etag: result.blob.etag };
  },
  async read(project, version) {
    const result = await get(`cad/${project}/revisions/${version}`, options);
    if (!result) throw new Error("Missing published CAD checkpoint");
    return Buffer.from(await new Response(result.stream).arrayBuffer());
  },
  async publish(project, head, data) {
    const version = randomUUID();
    const checkpoint = await put(`cad/${project}/revisions/${version}`, data, options);
    try {
      await put(`cad/${project}/head.json`, JSON.stringify({ version }), { ...options, contentType: "application/json",
        ...(head ? { allowOverwrite: true, ifMatch: head.etag } : { allowOverwrite: false }) });
    } catch (error) {
      // A transport failure can follow a successful write. Never delete a
      // checkpoint whose publication is ambiguous.
      if (error.name === "BlobPreconditionFailedError" || error.name === "BlobPathnameMismatchError") {
        const { del } = await import("@vercel/blob");
        await del(checkpoint.url);
        throw new Conflict("Another edit was published first.");
      }
      const current = await this.head(project);
      if (current?.version === version) return version;
      if ((current?.version ?? null) !== (head?.version ?? null)) {
        throw new Conflict("Project changed during publication.");
      }
      throw error;
    }
    return version;
  },
};
