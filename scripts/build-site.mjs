import { copyFileSync, cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const html = readFileSync(join(root, "public", "index.html"));
const siteMeta = readFileSync(join(root, "public", "site-meta.json"), "utf8");

rmSync(join(root, "dist"), { recursive: true, force: true });
mkdirSync(join(root, "dist", "server"), { recursive: true });
mkdirSync(join(root, "dist", "client"), { recursive: true });
mkdirSync(join(root, "dist", ".openai"), { recursive: true });
cpSync(join(root, "public"), join(root, "dist", "client"), { recursive: true });

const worker = `const SITE_META = ${siteMeta};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/site-meta.json") {
      return new Response(JSON.stringify(SITE_META), {
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "public, max-age=0, must-revalidate"
        }
      });
    }
    const assetPath = url.pathname === "/" ? "/index.html" : url.pathname;
    const assetUrl = new URL(assetPath, request.url);
    const response = await env.ASSETS.fetch(new Request(assetUrl, request));
    if (response.status !== 404) {
      return response;
    }
    return Response.redirect(url.origin + "/", 302);
  }
};
`;

writeFileSync(join(root, "dist", "server", "index.js"), worker);
writeFileSync(join(root, "dist", ".openai", "hosting.json"), readFileSync(join(root, ".openai", "hosting.json")));
console.log(`Built Sites static asset with ${html.length.toLocaleString()} bytes of HTML.`);
