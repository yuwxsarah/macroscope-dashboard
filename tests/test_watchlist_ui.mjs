import assert from "node:assert/strict";
import fs from "node:fs";

const html = fs.readFileSync("public/index.html", "utf8");

for (const expected of [
  'id="watchlistActionToggle"',
  'id="watchlistDeleteSelect"',
  "新增 / 删除自选股",
  "https://macroscope-shared-dashboard.yuwxsarah.chatgpt.site/api/watchlist",
  '"watchlist_defaults":[]',
]) {
  assert.ok(html.includes(expected), `missing watchlist UI marker: ${expected}`);
}

for (const removed of [
  "messageAddCode",
  "messageWatchlist",
  "data-remove-watchlist",
  "watchlist-inline-remove",
  "let watchlist=[...defaults]",
  "macroscope-message-watchlist-v1';\n  const defaults=",
]) {
  assert.ok(!html.includes(removed), `legacy watchlist UI remains: ${removed}`);
}

for (const expected of [
  "macroscope-message-watchlist-v2-shared-snapshot",
  "正在同步最新共享自选股，请稍候",
  "saveLocalWatchlist();",
  "localStorage.removeItem(legacyStorageKey)",
]) {
  assert.ok(html.includes(expected), `missing latest-watchlist bootstrap marker: ${expected}`);
}

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1])
  .filter(Boolean);

for (const script of inlineScripts) new Function(script);

console.log("single-button watchlist UI: OK");
