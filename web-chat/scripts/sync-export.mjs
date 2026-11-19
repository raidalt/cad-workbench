import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Copy the static export into the Python package's web root so the wheel ships
// it and `cad serve` can answer /static/chat/ without Node.
const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const from = resolve(root, "out");
const to = resolve(root, "..", "src", "cad", "web", "chat");

if (!existsSync(from)) {
  console.error(`sync-export: ${from} does not exist; run \`next build\` first`);
  process.exit(1);
}

rmSync(to, { recursive: true, force: true });
mkdirSync(to, { recursive: true });
cpSync(from, to, { recursive: true });

console.log(`sync-export: copied ${from} -> ${to}`);
