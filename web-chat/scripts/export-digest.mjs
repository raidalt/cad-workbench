import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const output = join(root, "../src/cad/web/chat");
const receipt = join(output, "build-digest.json");

function digest(base, paths) {
  const hash = createHash("sha256");
  function visit(path) {
    if (path === receipt) return;
    if (statSync(path).isDirectory()) {
      for (const name of readdirSync(path).sort()) visit(join(path, name));
    } else {
      const contents = readFileSync(path);
      hash.update(relative(base, path) + "\0" + contents.length + "\0");
      hash.update(contents);
    }
  }
  for (const path of paths) visit(join(base, path));
  return hash.digest("hex");
}
const fingerprint = {
  source: digest(root, ["src", "scripts", "next.config.ts", "tsconfig.json", "package.json",
    "package-lock.json", "../src/cad/web/design-system.css"]),
  assets: digest(output, ["."]),
};
if (process.argv.includes("--write")) {
  writeFileSync(receipt, JSON.stringify(fingerprint, null, 2) + "\n");
} else if (JSON.stringify(JSON.parse(readFileSync(receipt, "utf8"))) !== JSON.stringify(fingerprint)) {
  throw new Error("Packaged workspace differs from its build inputs or outputs. Run npm run build in web-chat.");
}
