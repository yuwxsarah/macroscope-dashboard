import assert from "node:assert/strict";
import fs from "node:fs";

const html = fs.readFileSync("public/index.html", "utf8");

for (const expected of [
  'id="watchlistActionToggle"',
  'id="watchlistDeleteSelect"',
  "新增 / 删除自选股",
]) {
  assert.ok(html.includes(expected), `missing watchlist UI marker: ${expected}`);
}

for (const removed of [
  "messageAddCode",
  "messageWatchlist",
  "data-remove-watchlist",
  "watchlist-inline-remove",
]) {
  assert.ok(!html.includes(removed), `legacy watchlist UI remains: ${removed}`);
}

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1])
  .filter(Boolean);

for (const script of inlineScripts) new Function(script);

console.log("single-button watchlist UI: OK");
