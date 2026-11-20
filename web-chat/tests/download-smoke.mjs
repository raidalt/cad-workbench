import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { chromium, expect } from "@playwright/test";

const root = fileURLToPath(new URL("../../", import.meta.url));
const library = await mkdtemp(join(tmpdir(), "cad-browser-"));
const child = spawn("sh", ["scripts/dev.sh", "--no-open", "--port", "0"], {
  cwd: root, env: { ...process.env, CAD_WORKBENCH_PROJECTS: library }, stdio: ["ignore", "pipe", "inherit"],
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
  assert.deepEqual(errors, []);
} finally {
  clearTimeout(deadline);
  await browser?.close();
  child.kill();
  await stopped;
  await rm(library, { recursive: true, force: true });
}
