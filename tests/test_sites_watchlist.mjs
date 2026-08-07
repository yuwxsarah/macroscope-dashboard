import assert from "node:assert/strict";
import worker from "../worker/index.js";

class MockStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql.replace(/\s+/g, " ").trim();
    this.params = [];
  }

  bind(...params) {
    this.params = params;
    return this;
  }

  async run() {
    if (this.sql.startsWith("CREATE TABLE")) return { success: true };
    if (this.sql.startsWith("INSERT OR IGNORE INTO shared_watchlist ")) {
      const code = this.params[0];
      if (!this.db.watchlist.has(code)) {
        this.db.sequence += 1;
        this.db.watchlist.set(code, `2026-08-07 00:00:${String(this.db.sequence).padStart(2, "0")}`);
      }
      return { success: true };
    }
    if (this.sql.startsWith("INSERT OR REPLACE INTO shared_watchlist_meta")) {
      this.db.meta.set(this.params[0], this.params[1]);
      return { success: true };
    }
    if (this.sql.startsWith("DELETE FROM shared_watchlist")) {
      this.db.watchlist.delete(this.params[0]);
      return { success: true };
    }
    throw new Error(`Unsupported run SQL: ${this.sql}`);
  }

  async first() {
    if (this.sql.startsWith("SELECT value FROM shared_watchlist_meta")) {
      const value = this.db.meta.get(this.params[0]);
      return value === undefined ? null : { value };
    }
    if (this.sql.startsWith("SELECT COUNT(*) AS count")) {
      return { count: this.db.watchlist.size };
    }
    throw new Error(`Unsupported first SQL: ${this.sql}`);
  }

  async all() {
    if (!this.sql.startsWith("SELECT code, added_at FROM shared_watchlist")) {
      throw new Error(`Unsupported all SQL: ${this.sql}`);
    }
    return {
      results: [...this.db.watchlist.entries()]
        .sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0]))
        .map(([code, added_at]) => ({ code, added_at })),
    };
  }
}

class MockD1 {
  constructor() {
    this.watchlist = new Map();
    this.meta = new Map();
    this.sequence = 0;
  }

  prepare(sql) {
    return new MockStatement(this, sql);
  }

  async batch(statements) {
    return Promise.all(statements.map((statement) => statement.run()));
  }
}

const db = new MockD1();
const env = {
  DB: db,
  ASSETS: { fetch: async () => new Response("not found", { status: 404 }) },
};

async function request(method = "GET", code) {
  const options = {
    method,
    headers: {
      "content-type": "application/json",
      origin: "https://yuwxsarah.github.io",
    },
  };
  if (method === "POST") options.body = JSON.stringify({ code });
  const url = new URL("https://example.test/api/watchlist");
  if (method === "DELETE" && Array.isArray(code)) {
    options.body = JSON.stringify({ codes: code });
  } else if (method === "DELETE") {
    url.searchParams.set("code", code);
  }
  const response = await worker.fetch(new Request(url, options), env);
  return { response, payload: await response.json() };
}

const initial = await request();
assert.equal(initial.response.status, 200);
assert.equal(initial.response.headers.get("access-control-allow-origin"), "*");
assert.equal(initial.payload.watchlist.length, 6);
assert.ok(initial.payload.watchlist.some((row) => row.code === "600353.SH"));

const preflight = await worker.fetch(
  new Request("https://example.test/api/watchlist", {
    method: "OPTIONS",
    headers: {
      origin: "https://yuwxsarah.github.io",
      "access-control-request-method": "POST",
      "access-control-request-headers": "content-type",
    },
  }),
  env
);
assert.equal(preflight.status, 204);
assert.equal(preflight.headers.get("access-control-allow-origin"), "*");
assert.match(preflight.headers.get("access-control-allow-methods"), /POST/);

const added = await request("POST", "600000");
assert.equal(added.response.status, 201);
assert.ok(added.payload.watchlist.some((row) => row.code === "600000.SH"));

const loadedAgain = await request();
assert.ok(loadedAgain.payload.watchlist.some((row) => row.code === "600000.SH"));

const removed = await request("DELETE", "600000.SH");
assert.equal(removed.response.status, 200);
assert.ok(!removed.payload.watchlist.some((row) => row.code === "600000.SH"));

await request("POST", "000001");
await request("POST", "000002");
const batchRemoved = await request("DELETE", ["000001.SZ", "000002.SZ"]);
assert.equal(batchRemoved.response.status, 200);
assert.ok(!batchRemoved.payload.watchlist.some((row) => ["000001.SZ", "000002.SZ"].includes(row.code)));

for (const row of batchRemoved.payload.watchlist) {
  await request("DELETE", row.code);
}
const empty = await request();
assert.deepEqual(empty.payload.watchlist, []);

console.log("shared watchlist worker: OK");
