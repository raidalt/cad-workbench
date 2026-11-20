import { Sandbox } from "@vercel/sandbox";

const work = "/tmp/cad-job";
const maxCheckpoint = 128 * 1024 * 1024;

async function readBounded(box, path, limit) {
  const stream = await box.readFile({ path });
  if (!stream) throw new Error("CAD worker did not produce the requested output");
  const chunks = [];
  let size = 0;
  for await (const chunk of stream) {
    size += chunk.length;
    if (size > limit) throw new Error("CAD worker output exceeds its size limit");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export async function executeJob(request, checkpoint, { snapshot, create = options => Sandbox.create(options) }) {
  if (!snapshot) throw new Error("CAD_SANDBOX_SNAPSHOT is required");
  const box = await create({ source: { type: "snapshot", snapshotId: snapshot },
    networkPolicy: "deny-all", persistent: false, ports: [], timeout: 120000 });
  try {
    await box.mkDir(work);
    const files = [{ path: work + "/request.json", content: Buffer.from(JSON.stringify(request)) }];
    if (checkpoint) files.push({ path: work + "/project.tar.gz", content: checkpoint });
    await box.writeFiles(files);
    const command = await box.runCommand({ cmd: "/opt/cad-runtime/bin/python",
      args: ["-m", "cad.isolated_job", work], env: { PYTHONPATH: "/opt/cad-src" } });
    if (command.exitCode !== 0) throw new Error("CAD worker exited before finishing");
    const result = JSON.parse(await readBounded(box, work + "/result.json", 4096));
    if (![200, 400, 404, 409, 422].includes(result.status)) throw new Error("Invalid CAD worker response");
    const type = ["application/json", "image/png"].includes(result.type) ? result.type : "application/octet-stream";
    const data = await readBounded(box, work + "/result.bin", maxCheckpoint);
    const changed = result.status === 200 && ["build", "undo"].includes(request.action);
    return { status: result.status, type, data,
      candidate: changed ? await readBounded(box, work + "/candidate.tar.gz", maxCheckpoint) : null };
  } finally {
    await box.stop();
  }
}
