const DEFAULT_WATCHLIST = [
  "601138.SH",
  "300750.SZ",
  "600519.SH",
  "688825.SH",
  "688981.SH",
  "600353.SH",
];

const WATCHLIST_SCHEMA = `
CREATE TABLE IF NOT EXISTS shared_watchlist (
  code TEXT PRIMARY KEY NOT NULL,
  added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
`;

const WATCHLIST_META_SCHEMA = `
CREATE TABLE IF NOT EXISTS shared_watchlist_meta (
  key TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL
)
`;

function json(payload, status = 200) {
  return Response.json(payload, {
    status,
    headers: {
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

function normalizeStockCode(value) {
  const raw = String(value ?? "").trim().toUpperCase();
  const match = raw.match(/^(\d{6})(?:\.(SH|SZ|BJ))?$/);
  if (!match) return "";
  const digits = match[1];
  const exchange = match[2] || (digits.startsWith("6") ? "SH" : digits.startsWith("4") || digits.startsWith("8") ? "BJ" : "SZ");
  return `${digits}.${exchange}`;
}

async function ensureWatchlist(db) {
  await db.batch([
    db.prepare(WATCHLIST_SCHEMA),
    db.prepare(WATCHLIST_META_SCHEMA),
  ]);

  const seeded = await db
    .prepare("SELECT value FROM shared_watchlist_meta WHERE key = ?")
    .bind("defaults_seeded")
    .first();

  if (!seeded) {
    await db.batch([
      ...DEFAULT_WATCHLIST.map((code) =>
        db.prepare("INSERT OR IGNORE INTO shared_watchlist (code) VALUES (?)").bind(code)
      ),
      db
        .prepare("INSERT OR REPLACE INTO shared_watchlist_meta (key, value) VALUES (?, ?)")
        .bind("defaults_seeded", "1"),
    ]);
  }
}

async function listWatchlist(db) {
  const result = await db
    .prepare("SELECT code, added_at FROM shared_watchlist ORDER BY added_at ASC, code ASC")
    .all();
  return Array.isArray(result.results) ? result.results : [];
}

async function handleWatchlist(request, env) {
  if (!env.DB) {
    return json({ error: "共享自选股数据库暂不可用" }, 503);
  }

  await ensureWatchlist(env.DB);
  const url = new URL(request.url);

  if (request.method === "GET") {
    return json({ watchlist: await listWatchlist(env.DB), shared: true });
  }

  if (request.method === "POST") {
    let payload;
    try {
      payload = await request.json();
    } catch {
      return json({ error: "请求内容必须是 JSON" }, 400);
    }

    const code = normalizeStockCode(payload?.code);
    if (!code) {
      return json({ error: "请输入有效的6位A股代码" }, 400);
    }

    const countRow = await env.DB.prepare("SELECT COUNT(*) AS count FROM shared_watchlist").first();
    if (Number(countRow?.count || 0) >= 100) {
      return json({ error: "共享自选股最多保存100只" }, 409);
    }

    await env.DB
      .prepare("INSERT OR IGNORE INTO shared_watchlist (code) VALUES (?)")
      .bind(code)
      .run();
    return json({ watchlist: await listWatchlist(env.DB), shared: true }, 201);
  }

  if (request.method === "DELETE") {
    let requestedCode = url.searchParams.get("code") || "";
    if (!requestedCode) {
      try {
        requestedCode = (await request.json())?.code || "";
      } catch {
        requestedCode = "";
      }
    }
    const code = normalizeStockCode(requestedCode);
    if (!code) {
      return json({ error: "缺少有效的自选股代码" }, 400);
    }

    await env.DB.prepare("DELETE FROM shared_watchlist WHERE code = ?").bind(code).run();
    return json({ watchlist: await listWatchlist(env.DB), shared: true });
  }

  return json({ error: "不支持的请求方法" }, 405);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/watchlist") {
      try {
        return await handleWatchlist(request, env);
      } catch (error) {
        console.error("Shared watchlist error", error);
        return json({ error: "共享自选股同步失败，请稍后重试" }, 500);
      }
    }

    const assetPath = url.pathname === "/" ? "/index.html" : url.pathname;
    const assetUrl = new URL(assetPath, request.url);
    const response = await env.ASSETS.fetch(new Request(assetUrl, request));
    if (response.status !== 404) {
      return response;
    }
    return Response.redirect(url.origin + "/", 302);
  },
};
