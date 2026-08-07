CREATE TABLE IF NOT EXISTS `shared_watchlist` (
  `code` text PRIMARY KEY NOT NULL,
  `added_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `shared_watchlist_meta` (
  `key` text PRIMARY KEY NOT NULL,
  `value` text NOT NULL
);
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist` (`code`) VALUES ('601138.SH');
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist` (`code`) VALUES ('300750.SZ');
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist` (`code`) VALUES ('600519.SH');
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist` (`code`) VALUES ('688825.SH');
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist` (`code`) VALUES ('688981.SH');
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist` (`code`) VALUES ('600353.SH');
--> statement-breakpoint
INSERT OR IGNORE INTO `shared_watchlist_meta` (`key`, `value`) VALUES ('defaults_seeded', '1');
--> statement-breakpoint
PRAGMA optimize;
