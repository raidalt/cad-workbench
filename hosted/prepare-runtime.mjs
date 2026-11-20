// Run once per CAD engine version using the Sandbox SDK's normal authentication.
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { Sandbox } from "@vercel/sandbox";

const repo = fileURLToPath(new URL("../", import.meta.url));
const box = await Sandbox.create({ timeout: 600000, persistent: false });
async function run(cmd, args, sudo = false) {
  const result = await box.runCommand({ cmd, args, sudo });
  if (result.exitCode) throw new Error(`Failed to prepare runtime: ${cmd} (exit ${result.exitCode})`);
}
try {
  await run("apt-get", ["update", "-qq"], true);
  await run("apt-get", ["install", "-y", "--no-install-recommends", "curl", "libgl1", "libegl1",
    "libfontconfig1", "fonts-dejavu-core", "libglu1-mesa", "libxrender1", "libxcursor1", "libxft2",
    "libxinerama1", "libxi6", "libxkbcommon0"], true);
  await box.mkDir("/vercel/sandbox/engine");
  const files = [];
  for (const entry of await readdir(join(repo, "src/cad"))) {
    if (entry.endsWith(".py")) files.push({ path: "/vercel/sandbox/engine/cad/" + entry,
      content: await readFile(join(repo, "src/cad", entry)) });
  }
  for (const file of ["pyproject.toml", "uv.lock", ".python-version"]) {
    files.push({ path: "/vercel/sandbox/engine/" + file, content: await readFile(join(repo, file)) });
  }
  await box.mkDir("/vercel/sandbox/engine/cad");
  await box.writeFiles(files);
  await run("curl", ["-fsSL", "https://astral.sh/uv/0.13.9/install.sh", "-o", "/tmp/install-uv"]);
  await run("env", ["UV_UNMANAGED_INSTALL=/tmp/cad-tools", "sh", "/tmp/install-uv"]);
  await run("env", ["UV_PROJECT_ENVIRONMENT=/vercel/sandbox/engine/venv", "/tmp/cad-tools/uv", "sync",
    "--directory", "/vercel/sandbox/engine", "--python", "3.12", "--frozen", "--no-dev", "--extra", "fea", "--no-install-project"]);
  await run("ln", ["-s", "/vercel/sandbox/engine/venv", "/opt/cad-runtime"], true);
  await run("ln", ["-s", "/vercel/sandbox/engine", "/opt/cad-src"], true);
  await run("env", ["PYTHONPATH=/opt/cad-src", "/opt/cad-runtime/bin/python", "-c", "import cad.isolated_job"]);
  const snapshot = await box.snapshot();
  console.log("CAD_SANDBOX_SNAPSHOT=" + snapshot.snapshotId);
} finally {
  await box.stop();
}
