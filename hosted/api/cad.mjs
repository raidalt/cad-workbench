// Trusted, single-owner CAD job endpoint. The bearer token belongs to the caller's server.
import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { put, issueSignedToken, presignUrl } from "@vercel/blob";
import { executeJob } from "../lib/executor.mjs";
import { blobProjects, Conflict, runProjectJob } from "../lib/projects.mjs";

const digest = value => createHash("sha256").update(value).digest();

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "private, no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  const secret = process.env.CAD_SERVICE_TOKEN;
  if (!secret || !timingSafeEqual(digest(req.headers.authorization ?? ""), digest("Bearer " + secret))) {
    return res.status(401).end();
  }
  if (req.method !== "POST") return res.status(405).end();
  try {
    const input = req.body;
    if (!input || typeof input !== "object" || typeof input.project !== "string") return res.status(400).end();
    const job = await runProjectJob(input.project, input, blobProjects,
      (request, checkpoint) => executeJob(request, checkpoint, { snapshot: process.env.CAD_SANDBOX_SNAPSHOT }));
    if (job.version) res.setHeader("X-CAD-Version", job.version);
    if (job.data.length > 4 * 1024 * 1024) {
      const pathname = `cad/${input.project}/downloads/${randomUUID()}`;
      await put(pathname, job.data, { access: "private", contentType: job.type });
      const token = await issueSignedToken({ pathname, operations: ["get"], validUntil: Date.now() + 300000 });
      const { presignedUrl } = await presignUrl(token, { pathname, operation: "get", access: "private" });
      return res.redirect(303, presignedUrl);
    }
    res.setHeader("Content-Type", job.type);
    return res.status(job.status).send(job.data);
  } catch (error) {
    if (error instanceof Conflict) return res.status(409).json({ error: error.message });
    if (error instanceof TypeError) return res.status(400).json({ error: "Invalid CAD request" });
    return res.status(502).json({ error: "CAD job could not finish" });
  }
}
