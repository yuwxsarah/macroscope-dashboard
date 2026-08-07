export const watchlistSchemaSql = `
CREATE TABLE IF NOT EXISTS shared_watchlist (
  code TEXT PRIMARY KEY NOT NULL,
  added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
`;

export const watchlistMetaSchemaSql = `
CREATE TABLE IF NOT EXISTS shared_watchlist_meta (
  key TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL
)
`;
