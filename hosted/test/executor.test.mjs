import assert from "node:assert/strict";
import { Readable } from "node:stream";
import test from "node:test";
import { executeJob } from "../lib/executor.mjs";
import { actions, runProjectJob, Conflict } from "../lib/projects.mjs";

for (const action of actions) {
  test(action + " runs behind the same isolated boundary", async () => {
    let stopped = false;
    const create = async options => {
      assert.deepEqual(options, { source: { type: "snapshot", snapshotId: "clean-runtime" },
        networkPolicy: "deny-all", persistent: false, ports: [], timeout: 120000 });
      return {
        mkDir: async () => {},
        writeFiles: async files => {
          assert.deepEqual(files.map(f => f.path), ["/tmp/cad-job/request.json", "/tmp/cad-job/project.tar.gz"]);
          assert.deepEqual(JSON.parse(files[0].content), { action, args: {} });
        },
        runCommand: async command => {
          assert.deepEqual(command.env, { PYTHONPATH: "/opt/cad-src" });
          assert.deepEqual(command.args, ["-m", "cad.isolated_job", "/tmp/cad-job"]);
          return { exitCode: 0 };
        },
        readFile: async ({path}) => Readable.from([Buffer.from(path.endsWith("result.json")
          ? JSON.stringify({ status: 200, type: "application/json" }) : "content")]),
        stop: async () => { stopped = true; },
      };
    };
    const result = await executeJob({ action, args: {} }, Buffer.from("checkpoint"), { snapshot: "clean-runtime", create });
    assert.equal(Boolean(result.candidate), ["build", "undo"].includes(action));
    assert.ok(stopped);
  });
}

test("only a successful write against the current version can publish", async () => {
  let version = "v1";
  const store = { head: async () => ({ version }), read: async () => Buffer.alloc(0),
    publish: async (_project, head) => {
      if (head.version !== version) throw new Conflict();
      return version = "v2";
    } };
  const good = async () => ({ status: 200, candidate: Buffer.from("opaque") });
  const change = { base: "v1", action: "build" };
  await runProjectJob("demo", { action: "export" }, store, good);
  await runProjectJob("demo", change, store, async () => ({ status: 422 }));
  assert.equal(version, "v1");
  const edits = await Promise.allSettled([runProjectJob("demo", change, store, good),
    runProjectJob("demo", change, store, good)]);
  assert.equal(edits.filter(edit => edit.status === "fulfilled").length, 1);
  assert.ok(edits.find(edit => edit.status === "rejected").reason instanceof Conflict);
  await assert.rejects(runProjectJob("demo", change, store, good), Conflict);
});

test("a crashed worker is stopped", async () => {
  let stopped = false;
  await assert.rejects(executeJob({action: "build"}, null, { snapshot: "runtime", create: async () => ({
    mkDir: async () => { throw new Error("worker stopped"); }, stop: async () => { stopped = true; },
  }) }), /worker stopped/);
  assert.ok(stopped);
});

test("a cached export stays bound to its project, version and format", async () => {
  const artifacts = new Map();
  const store = {
    head: async () => ({ version: "first" }),
    read: async () => Buffer.alloc(0),
    artifact: async (project, key) => artifacts.get(project + key),
    saveArtifact: async (project, key, result) => artifacts.set(project + key, result),
  };
  let calls = 0;
  const execute = async () => ({ status: 200, type: "application/octet-stream", data: Buffer.from(String(++calls)) });
  const read = project => runProjectJob(project, { action: "export", args: { format: "3mf" } }, store, execute);
  assert.equal((await read("one")).data.toString(), "1");
  assert.equal((await read("one")).data.toString(), "1");
  assert.equal((await read("two")).data.toString(), "2");
  store.head = async () => ({ version: "second" });
  assert.equal((await read("one")).data.toString(), "3");
});
