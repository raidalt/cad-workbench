import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { createServer } from "node:http";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { chromium, expect } from "@playwright/test";

const root = fileURLToPath(new URL("../../", import.meta.url));
const library = await realpath(await mkdtemp(join(tmpdir(), "cad-browser-")));
const duo = join(library, "duo");
await cp(join(root, "samples/improved-fidget-spinners/duo"), duo, { recursive: true });
const controls = join(library, "controls");
await mkdir(controls);
await writeFile(join(controls, "cad.toml"), '[project]\nname="Controls"\nentry="model.py"\n');
await writeFile(join(controls, "model.py"), "from cad import Part, Parameter\nw = Parameter('width', 20, min=10, max=30)\ndef build():\n    p = Part('plate')\n    p.box(w, 12, 5)\n    return p\n");
const original = await readFile(join(duo, "model.py"), "utf8");
const updated = original.replace("clearance = 0.35", "clearance = 0.38");
const gateway = createServer(async (req, res) => {
  for await (const chunk of req) { /* Consume the streamed request. */ }
  const content = "Adjusted both bearing clearances.\n\n" + "`".repeat(3) + "python\n" + updated + "`".repeat(3);
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  res.end("data: " + JSON.stringify({ choices: [{ delta: { content } }] }) + "\n\ndata: [DONE]\n\n");
});
gateway.listen(0, "127.0.0.1");
await once(gateway, "listening");
const child = spawn("sh", ["scripts/dev.sh", "--no-open", "--port", "0"], {
  cwd: root, env: { ...process.env, CAD_WORKBENCH_PROJECTS: library, CAD_CHAT_API_KEY: "local-smoke",
    CAD_CHAT_BASE_URL: "http://127.0.0.1:" + gateway.address().port }, stdio: ["ignore", "pipe", "inherit"],
});
const stopped = once(child, "exit");
let browser;
const deadline = setTimeout(() => child.kill(), 180000);
try {
  let url;
  for await (const line of createInterface({ input: child.stdout })) {
    url = line.match(/^cad serve .* (http:\/\/127\.0\.0\.1:\d+\/)/)?.[1];
    if (url) break;
  }
  assert.ok(url, "launcher did not print the workspace URL");
  browser = await chromium.launch({ channel: process.env.CAD_TEST_BROWSER || undefined,
    args: ["--enable-unsafe-swiftshader"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  const streams = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", req => { if (new URL(req.url()).pathname.endsWith("/api/events")) streams.push(req.url()); });
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#project-name")).toHaveText("Three-lobe spinner");
  await expect(page.locator("#chat-root textarea")).toBeVisible();
  await expect(page.locator("#status-tris")).not.toHaveText("0 tris");
  await expect(page.locator("#status-connection")).toHaveText("live");
  await expect(page.locator("#play")).not.toHaveClass(/\bon\b/);
  await expect(async () => {
    await page.locator("#gl").click();
    await expect(page.locator("#selection .tag.on")).toHaveText("selected", { timeout: 1000 });
  }).toPass({ timeout: 20000 });
  await expect(page.locator("#chat-root")).toContainText(/face \d+/);
  assert.equal(streams.length, 1, "workspace and chat share one event connection");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator("#project-name")).toHaveText("Three-lobe spinner");
  await expect(page.locator("#chat-root textarea")).toBeVisible();
  await page.getByRole("link", { name: "Projects" }).click();
  await expect(page.getByText("Three-lobe spinner", { exact: true })).toBeVisible();
  await page.locator('[data-key="duo"] .project-open').click();
  await expect(page).toHaveURL(/\/projects\/duo\//);
  await expect(page.locator("#revision-select")).toHaveValue("1", { timeout: 20000 });
  await expect(page.locator("#status-connection")).toHaveText("live");
  await expect(page.locator("#play")).not.toHaveClass(/\bon\b/);
  for (const part of ["middle", "outer"]) {
    const row = page.locator(".part-row").filter({ has: page.getByRole("button", { name: part, exact: true }) });
    await row.getByRole("checkbox").uncheck();
  }
  await page.locator("#view-preset").selectOption("front");
  await expect(async () => {
    await page.locator("#gl").click();
    await expect(page.locator("#chat-root")).toContainText(/face \d+/, { timeout: 1000 });
  }).toPass({ timeout: 20000 });
  await expect.poll(() => page.evaluate(() => window.cadWorkspace.inspection?.face?.info?.center?.length)).toBe(3);
  const bearing = await page.evaluate(() => {
    const inspection = window.cadWorkspace.inspection;
    return { part: inspection.target.part, z: inspection.face.info.center[2] };
  });
  assert.equal(bearing.part, "hub");
  assert.ok(bearing.z > 2 && bearing.z < 8, "selected a lateral bearing surface of the spindle");
  const composer = page.locator("#chat-root textarea");
  await composer.fill("Change both bearing clearances to 0.38 mm.");
  await composer.press("Enter");
  await expect(page.locator("#revision-select")).toHaveValue("2", { timeout: 45000 });
  await expect.poll(() => readFile(join(duo, "model.py"), "utf8")).toBe(updated);
  const measured = execFileSync(join(root, ".local-cad/venv/bin/python"), ["-c",
    "import json,sys; from pathlib import Path; from cad.engine import ProjectSession; print(json.dumps(ProjectSession(Path(sys.argv[1])).clearance()))", duo],
    { env: { ...process.env, PYTHONPATH: join(root, "src") }, encoding: "utf8" });
  const pairs = JSON.parse(measured).pairs.filter(pair => Math.abs(pair.min_distance - .38) < .0001);
  assert.deepEqual(pairs.map(pair => [pair.a, pair.b]).sort(), [["hub", "middle"], ["middle", "outer"]]);
  await page.locator("#tree-build").click();
  await page.locator("#revision-select").selectOption("1");
  await expect.poll(() => page.evaluate(() => window.cadWorkspace.board?.revision)).toBe(1);
  await page.locator("#revision-select").selectOption("2");
  await expect.poll(() => page.evaluate(() => window.cadWorkspace.board?.revision)).toBe(2);

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator("#status-connection")).toHaveText("live");
  const download = page.waitForEvent("download");
  await page.locator("#export-3mf").click();
  const exportedPath = await (await download).path();
  execFileSync(join(root, ".local-cad/venv/bin/python"), ["-c", [
    "import sys, zipfile, xml.etree.ElementTree as xml",
    "with zipfile.ZipFile(sys.argv[1]) as archive: model = xml.fromstring(archive.read('3D/3dmodel.model'))",
    "items = model.findall('{*}build/{*}item')",
    "assert len(items) == 1, 'expected one assembled print'",
    "assembly = model.find(\".//{*}object[@id='%s']\" % items[0].get('objectid'))",
    "components = assembly.findall('{*}components/{*}component')",
    "assert len(components) == 3 and all(c.get('transform') is None for c in components)",
    "for component in components:",
    "    mesh = model.find(\".//{*}object[@id='%s']/{*}mesh\" % component.get('objectid'))",
    "    vertices = mesh.findall('{*}vertices/{*}vertex')",
    "    for axis in ('x', 'y'):",
    "        coords = [float(v.get(axis)) for v in vertices]",
    "        assert abs(min(coords) + max(coords)) < 0.05, 'bearing was displaced on the bed'",
  ].join("\n"), exportedPath]);
  await page.getByRole("button", { name: "undo", exact: true }).click();
  await expect.poll(() => readFile(join(duo, "model.py"), "utf8")).toBe(original);
  await expect(page.locator("#revision-select")).toHaveValue("3");
  await page.locator("#tree-build").click();
  await page.locator("#btn-rebuild").click();
  await expect(page.locator("#revision-select")).toHaveValue("4");
  const worker = execFileSync("ps", ["-axo", "pid=,ppid=,command="], { encoding: "utf8" }).split("\n")
    .find(line => line.includes(" -m cad.project_worker " + duo + " ") && Number(line.trim().split(/\s+/, 2)[1]) === child.pid);
  assert.ok(worker, "expected an active worker for this test project");
  const streamCount = streams.length;
  process.kill(Number(worker.trim().split(/\s+/)[0]), "SIGKILL");
  await expect.poll(() => streams.length, { timeout: 20000 }).toBeGreaterThan(streamCount);
  await expect(page.locator("#status-connection")).toHaveText("live", { timeout: 20000 });
  await expect(page.locator("#revision-select")).toHaveValue("4");
  await page.getByRole("link", { name: "Projects" }).click();
  await page.locator('[data-key="controls"] .project-open').click();
  const slider = page.getByRole("slider", { name: "width" });
  await expect(slider).toHaveValue("20");
  await slider.focus();
  await slider.press("ArrowRight");
  await expect.poll(() => page.evaluate(() => window.cadWorkspace.board?.overrides.width)).toBeGreaterThan(20);
  await page.locator("#tree-build").click();
  await expect(page.locator("#revision-select")).toHaveValue("2");
  await page.locator("#revision-select").selectOption("1");
  await page.locator("#tree-model").click();
  await expect(slider).toBeDisabled();
  await expect(slider).toHaveValue("20");
  assert.deepEqual(errors, []);
} finally {
  clearTimeout(deadline);
  gateway.closeAllConnections();
  gateway.close();
  await browser?.close();
  child.kill();
  await stopped;
  await rm(library, { recursive: true, force: true });
}
