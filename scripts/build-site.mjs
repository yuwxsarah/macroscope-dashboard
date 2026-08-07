import { copyFileSync, cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const html = readFileSync(join(root, "public", "index.html"));
rmSync(join(root, "dist"), { recursive: true, force: true });
mkdirSync(join(root, "dist", "server"), { recursive: true });
mkdirSync(join(root, "dist", "client"), { recursive: true });
mkdirSync(join(root, "dist", ".openai"), { recursive: true });
cpSync(join(root, "public"), join(root, "dist", "client"), { recursive: true });
copyFileSync(join(root, "worker", "index.js"), join(root, "dist", "server", "index.js"));
writeFileSync(join(root, "dist", ".openai", "hosting.json"), readFileSync(join(root, ".openai", "hosting.json")));
console.log(`Built Sites static asset with ${html.length.toLocaleString()} bytes of HTML.`);
