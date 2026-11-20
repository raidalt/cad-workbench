import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";
import handler, { handleJob } from "../api/cad.mjs";

const repo = fileURLToPath(new URL("../../", import.meta.url));

function response() {
  return {
    headers: {}, code: 200,
    setHeader(key, value) { this.headers[key] = value; },
    status(code) { this.code = code; return this; },
    send(data) { this.data = data; return this; },
    json(data) { this.data = data; return this; },
    end() { return this; },
  };
}

test("public entry requires server credentials before it contacts storage or compute", async () => {
  const res = response();
  await handler({ method: "POST", headers: {} }, res);
  assert.equal(res.code, 401);
});

test("authenticated controller validates, publishes, reads and rejects stale revisions", {
  skip: !process.env.CAD_TEST_PYTHON && "set CAD_TEST_PYTHON to an installed CAD interpreter",
}, async () => {
  const source = await readFile(join(repo, "samples/improved-fidget-spinners/duo/model.py"), "utf8");
  let checkpoint = null;
  const store = {
    head: async () => checkpoint ? { version: "v1" } : null,
    read: async () => checkpoint,
    publish: async (_project, _base, candidate) => { checkpoint = candidate; return "v1"; },
  };
  const execute = async (request, previous) => {
    const work = await mkdtemp(join(tmpdir(), "cad-controller-"));
    try {
      await writeFile(join(work, "request.json"), JSON.stringify(request));
      if (previous) await writeFile(join(work, "project.tar.gz"), previous);
      await promisify(execFile)(process.env.CAD_TEST_PYTHON, ["-m", "cad.isolated_job", work], {
        env: { PYTHONPATH: join(repo, "src"), HOME: work, PATH: "/usr/bin:/bin", CAD_DISABLE_SLICER: "1" },
      });
      const result = JSON.parse(await readFile(join(work, "result.json"), "utf8"));
      return { ...result, data: await readFile(join(work, "result.bin")),
        candidate: await readFile(join(work, "candidate.tar.gz")).catch(() => null) };
    } finally {
      await rm(work, { recursive: true, force: true });
    }
  };
  const previousToken = process.env.CAD_SERVICE_TOKEN;
  process.env.CAD_SERVICE_TOKEN = "test-only";
  async function call(body) {
    const res = response();
    await handleJob({ method: "POST", headers: { authorization: "Bearer test-only" }, body: { project: "duo", ...body } },
      res, store, execute);
    return res;
  }
  try {
    const invalid = await call({ action: "build", args: { source: "def build(): raise ValueError('bad part')" }, base: null });
    assert.equal(invalid.code, 422);
    assert.equal(checkpoint, null);
    const build = await call({ action: "build", args: { source }, base: null });
    assert.equal(build.code, 200);
    assert.equal(build.headers["X-CAD-Version"], "v1");
    const result = await call({ action: "export", args: { format: "3mf" } });
    assert.equal(result.data.subarray(0, 2).toString(), "PK");
    assert.equal((await call({ action: "undo", base: "stale" })).code, 409);
  } finally {
    if (previousToken === undefined) delete process.env.CAD_SERVICE_TOKEN;
    else process.env.CAD_SERVICE_TOKEN = previousToken;
  }
});
