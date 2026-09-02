from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from jinja2 import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils import DATA_DIR, PUBLIC_DIR, dataframe_to_records, ensure_dirs, load_settings, read_csv_safe  # noqa: E402

MESSAGE_SCHEDULE = ["06:20", "08:00", "10:10", "11:40", "15:20", "16:40", "17:00", "18:40", "19:10"]


def read_status() -> dict[str, Any]:
    path = DATA_DIR / "status.json"
    if not path.exists():
        return {"overall_status": "empty", "datasets": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"overall_status": "empty", "datasets": {}}
    except Exception:
        return {"overall_status": "empty", "datasets": {}}


def read_messages() -> dict[str, Any]:
    path = DATA_DIR / "messages.json"
    fallback = {
        "updated_at": None,
        "timezone": "Asia/Shanghai",
        "schedule": MESSAGE_SCHEDULE,
        "watchlist_defaults": [],
        "sources": [],
        "items": [],
        "report_templates": [],
    }
    if not path.exists():
        return fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return fallback
        return {
            **fallback,
            **payload,
            "schedule": MESSAGE_SCHEDULE,
            # The shared database is the only source of truth for the watchlist.
            # Never embed the historical starter symbols into a new page load.
            "watchlist_defaults": [],
        }
    except Exception:
        return fallback


def read_market_messages() -> list[dict[str, Any]]:
    path = DATA_DIR / "market_messages.csv"
    columns = [
        "published_at", "category", "title", "summary", "source", "source_url",
        "symbol", "stock_name", "importance", "status", "source_type", "item_id",
    ]
    frame = read_csv_safe(path, columns=columns)
    if frame.empty:
        return []
    if "published_at" in frame.columns:
        frame = frame.sort_values(["published_at", "item_id"], ascending=[True, True])
    records = dataframe_to_records(frame, max_rows=25000)
    for record in records:
        symbol = str(record.get("symbol") or "").strip()
        record["symbols"] = [symbol] if symbol else []
        record["id"] = record.get("item_id") or record.get("id")
    return records


def normalize_a_share_symbol(code: Any, exchange: Any = "") -> str:
    raw = str(code or "").strip().upper()
    suffix = raw.rsplit(".", 1)[1] if "." in raw else ""
    digits = "".join(char for char in raw.split(".", 1)[0] if char.isdigit())
    if len(digits) != 6:
        return ""
    market = str(exchange or suffix).strip().upper()
    if market not in {"SH", "SZ", "BJ"}:
        market = "SH" if digits.startswith("6") else "BJ" if digits.startswith(("4", "8")) else "SZ"
    return f"{digits}.{market}"


def clean_stock_name(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() in {"nan", "none", "null"} else text


def read_stock_name_map() -> dict[str, str]:
    """Build a stable A-share code/name map before the message feed is truncated."""
    output: dict[str, str] = {}

    message_frame = read_csv_safe(DATA_DIR / "market_messages.csv")
    if not message_frame.empty and {"symbol", "stock_name"}.issubset(message_frame.columns):
        named = message_frame.dropna(subset=["symbol", "stock_name"])
        for row in named.to_dict(orient="records"):
            symbol = normalize_a_share_symbol(row.get("symbol"))
            name = clean_stock_name(row.get("stock_name"))
            if symbol and name:
                output[symbol] = name

    market_frame = read_csv_safe(DATA_DIR / "market.csv")
    if not market_frame.empty and {"symbol", "name"}.issubset(market_frame.columns):
        named = market_frame.dropna(subset=["symbol", "name"]).drop_duplicates("symbol", keep="last")
        for row in named.to_dict(orient="records"):
            symbol = normalize_a_share_symbol(row.get("symbol"))
            name = clean_stock_name(row.get("name"))
            if symbol and name:
                output[symbol] = name

    universe = read_csv_safe(DATA_DIR / "a_share_universe.csv")
    if not universe.empty and {"code", "name"}.issubset(universe.columns):
        for row in universe.to_dict(orient="records"):
            symbol = normalize_a_share_symbol(row.get("code"), row.get("exchange"))
            name = clean_stock_name(row.get("name"))
            if symbol and name:
                output[symbol] = name

    return dict(sorted(output.items()))


def read_market_tracking() -> dict[str, Any]:
    path = DATA_DIR / "market_tracking.json"
    fallback = {
        "updated_at": None,
        "timezone": "Asia/Shanghai",
        "schedule": ["每日随公开数据刷新"],
        "framework_source": "二级框架.rtf",
        "framework_sections": [],
        "process_steps": [],
        "tracking": {
            "trade_date": None,
            "summary": {},
            "modules": [],
            "risk_modules": [],
            "signals": {},
        },
        "method_note": "等待首次自动评分。",
    }
    if not path.exists():
        return fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return fallback
        return {**fallback, **payload}
    except Exception:
        return fallback


def latest_market_cards(market: pd.DataFrame) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if market.empty:
        return cards
    for symbol, group in market.groupby("symbol"):
        group = group.sort_values("trade_date").copy()
        group["close"] = pd.to_numeric(group["close"], errors="coerce")
        group = group.dropna(subset=["close"])
        if group.empty:
            continue
        last = group.iloc[-1]
        current_year = str(last["trade_date"])[:4]
        year_group = group[group["trade_date"].astype(str).str.startswith(current_year)]
        ytd_base = float(year_group.iloc[0]["close"]) if not year_group.empty else np.nan
        cutoff = pd.to_datetime(str(last["trade_date"]), format="%Y%m%d", errors="coerce") - pd.DateOffset(days=365)
        dates = pd.to_datetime(group["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
        year_ago_group = group[dates >= cutoff] if pd.notna(cutoff) else group
        one_year_base = float(year_ago_group.iloc[0]["close"]) if not year_ago_group.empty else np.nan
        daily_pct = float(last.get("pct_change")) if pd.notna(last.get("pct_change")) else (
            (float(last["close"]) / float(group.iloc[-2]["close"]) - 1) * 100 if len(group) > 1 else np.nan
        )
        cards.append({
            "symbol": str(symbol),
            "name": str(last.get("name", symbol)),
            "market": str(last.get("market", "")),
            "currency": str(last.get("currency", "")) if pd.notna(last.get("currency")) else "",
            "asset_group": str(last.get("asset_group", "")) if pd.notna(last.get("asset_group")) else "",
            "trade_date": str(last["trade_date"]),
            "close": float(last["close"]),
            "daily_pct": daily_pct,
            "ytd_pct": (float(last["close"]) / ytd_base - 1) * 100 if np.isfinite(ytd_base) and ytd_base else np.nan,
            "one_year_pct": (float(last["close"]) / one_year_base - 1) * 100 if np.isfinite(one_year_base) and one_year_base else np.nan,
            "source": str(last.get("source", "")),
        })
    order = {"US_INDEX": 0, "FX_INDEX": 1, "CN_INDEX": 2, "US_EQUITY": 3, "KR_EQUITY": 4}
    return sorted(cards, key=lambda x: (order.get(x["market"], 9), x["name"]))


def valuation_summary(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if frame.empty:
        return output
    for code, group in frame.groupby("index_code"):
        group = group.sort_values("trade_date").copy()
        values = pd.to_numeric(group["pe_ttm"], errors="coerce").dropna()
        if values.empty:
            continue
        current = float(values.iloc[-1])
        previous = float(values.iloc[-2]) if len(values) > 1 else np.nan
        output.append({
            "index_code": str(code),
            "index_name": str(group.iloc[-1]["index_name"]),
            "trade_date": str(group.iloc[-1]["trade_date"]),
            "current_pe": current,
            "previous_pe": previous if np.isfinite(previous) else np.nan,
            "pe_change": current - previous if np.isfinite(previous) else np.nan,
            "pe_mean": float(values.mean()),
            "pe_median": float(values.median()),
            "pe_q25": float(values.quantile(.25)),
            "pe_q75": float(values.quantile(.75)),
            "pe_percentile": float((values <= current).mean() * 100),
            "source": str(group.iloc[-1].get("source", "")),
        })
    return output


def grouped_tail_records(
    frame: pd.DataFrame,
    group_column: str,
    rows_per_group: int,
    sort_column: str = "trade_date",
) -> list[dict[str, Any]]:
    """Keep the most recent rows for every group instead of truncating whole groups.

    A global ``tail(max_rows)`` after sorting by symbol can silently remove complete
    symbols from the page payload. This helper preserves an equal recent history for
    every index, stock, or series while keeping the generated HTML reasonably small.
    """
    if frame.empty:
        return []
    if group_column not in frame.columns:
        return dataframe_to_records(frame)

    pieces: list[pd.DataFrame] = []
    for _, group in frame.groupby(group_column, sort=False, dropna=False):
        current = group.copy()
        if sort_column in current.columns:
            current = current.sort_values(sort_column)
        pieces.append(current.tail(rows_per_group))

    if not pieces:
        return []
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    sort_columns = [
        column
        for column in [group_column, sort_column]
        if column in combined.columns
    ]
    if sort_columns:
        combined = combined.sort_values(sort_columns)
    return dataframe_to_records(combined)


# v6.0.4: preserve every symbol, lazy-render hidden tabs, and repair 10/20-week deviation charts.
HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 64 64%27%3E%3Crect width=%2764%27 height=%2764%27 rx=%2714%27 fill=%27%23172c58%27/%3E%3Cpath d=%27M13 43V21h7l12 14 12-14h7v22h-7V31L32 45 20 31v12z%27 fill=%27white%27/%3E%3C/svg%3E">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script src="market-messages-data.js"></script>
<style>
:root{--bg:#f3f6fb;--panel:#fff;--ink:#172033;--muted:#6d7890;--line:#e4e9f2;--blue:#3167e3;--navy:#172c58;--cyan:#1e9ca5;--purple:#7456d8;--amber:#c98a18;--up:#d64242;--down:#159567;--shadow:0 14px 36px rgba(25,42,80,.08)}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#eef3fb 0,#f7f9fc 320px);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink)}
.wrap{max-width:1500px;margin:0 auto;padding:24px}.hero{background:radial-gradient(circle at 80% 0,#345ea8 0,transparent 30%),linear-gradient(135deg,#102243,#1c3b72 62%,#235b83);color:white;border-radius:24px;padding:28px 30px;box-shadow:var(--shadow);position:relative;overflow:hidden}.hero h1{margin:0;font-size:30px;letter-spacing:.5px}.hero p{margin:8px 0 0;color:#cbd8ee}.hero-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.badge{padding:7px 11px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.09);border-radius:999px;font-size:12px;color:#e8eef9}
.tabs{display:flex;gap:8px;overflow:auto;padding:18px 0 12px}.tab{border:0;background:#e8edf6;color:#55627a;padding:10px 15px;border-radius:11px;font-weight:700;cursor:pointer;white-space:nowrap}.tab.active{background:var(--navy);color:#fff}.panel{display:none}.panel.active{display:block}.grid{display:grid;gap:16px}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.kpis{grid-template-columns:repeat(6,minmax(0,1fr));margin-bottom:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);padding:17px;min-width:0}.card h3{margin:0 0 12px;font-size:16px}.card h3 small{font-weight:400;color:var(--muted);margin-left:6px}.chart{height:380px}.chart.tall{height:470px}.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.market-chart-card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow);min-width:0}.market-chart-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:4px}.market-chart-title{font-size:15px;font-weight:800}.market-chart-meta{font-size:11px;color:var(--muted);margin-top:3px}.market-chart-latest{text-align:right;font-size:13px;font-weight:800;white-space:nowrap}.market-single-chart{height:315px}.chart-placeholder{height:100%;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px;background:#f8faff;border-radius:10px;border:1px dashed var(--line);padding:18px;text-align:center}.kpi{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:15px;box-shadow:var(--shadow);min-height:112px}.kpi-label{font-size:12px;color:var(--muted);font-weight:700}.kpi-value{font-size:25px;font-weight:800;margin-top:8px;line-height:1}.kpi-note{font-size:11px;color:#8a94a8;margin-top:10px}.positive,.up{color:var(--up)!important}.negative,.down{color:var(--down)!important}.neutral{color:var(--muted)!important}.kpi-value,.asset-price,.asset-metrics b,.multiple{transition:color .25s ease}.asset-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;align-items:stretch}.asset{border:1px solid var(--line);border-radius:15px;padding:14px;background:linear-gradient(180deg,#fff,#fbfcff);min-width:0;overflow:hidden}.asset-top{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:8px}.asset-top>div{min-width:0}.asset-top>b{font-size:13px;line-height:1.35;white-space:nowrap}.asset-name{font-weight:800;font-size:14px;line-height:1.35;word-break:keep-all;overflow-wrap:break-word}.asset-symbol{font-size:11px;color:var(--muted);margin-top:3px;line-height:1.35;overflow-wrap:anywhere}.asset-price{font-size:24px;font-weight:800;line-height:1.15;margin:12px 0;overflow-wrap:anywhere}.asset-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(74px,1fr));gap:6px}.asset-metrics div{background:#f4f6fa;border-radius:9px;padding:7px;min-width:0}.asset-metrics span{display:block;font-size:10px;color:var(--muted);white-space:nowrap}.asset-metrics b{display:block;font-size:11px;line-height:1.25;white-space:nowrap}.hint{background:#f6f8fc;border:1px solid var(--line);border-radius:12px;padding:11px 13px;color:#66738a;font-size:12px;line-height:1.6;margin:12px 0}.valuation-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.valuation-card{border:1px solid var(--line);border-radius:14px;padding:13px;cursor:pointer}.valuation-card.selected{border-color:var(--blue);box-shadow:0 0 0 2px rgba(49,103,227,.10)}.multiple{font-size:25px;font-weight:800;margin:9px 0}.stat-row{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.stat{background:#f5f7fb;border-radius:7px;padding:6px;font-size:10px;color:var(--muted)}.stat b{display:block;color:var(--ink);font-size:11px;margin-top:2px}.bar{height:5px;background:#edf0f5;border-radius:5px;margin-top:10px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--down),var(--amber),var(--up))}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:13px}table{width:100%;border-collapse:collapse;font-size:12px;min-width:760px}th{background:#f3f6fb;color:#59667d;text-align:left;padding:10px;position:sticky;top:0}td{border-top:1px solid var(--line);padding:9px 10px}tr:hover td{background:#fafcff}.source-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.source-card{border:1px solid var(--line);border-radius:14px;padding:13px;background:#fff}.source-status{font-weight:800}.source-status.success,.source-status.cached{color:var(--down)}.source-status.failed{color:var(--up)}.source-status.partial{color:var(--amber)}.message-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}.message-input{height:38px;border:1px solid var(--line);border-radius:10px;padding:0 11px;font:inherit;min-width:240px;background:#fff}.message-button{height:38px;border:0;border-radius:10px;background:var(--navy);color:#fff;font-weight:800;padding:0 13px;cursor:pointer}.message-button.secondary{background:#e9eef7;color:#4d5a72}.message-chipbar,.message-categorybar{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}.message-chip,.message-category{border:1px solid var(--line);background:#f6f8fc;color:#56627a;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:700}.message-chip button{border:0;background:transparent;color:#7a8598;margin-left:5px;cursor:pointer;font-weight:900}.message-category{cursor:pointer}.message-category.active{background:var(--navy);border-color:var(--navy);color:#fff}.message-list{display:grid;gap:10px}.message-item{border:1px solid var(--line);border-radius:14px;padding:13px;background:#fff;min-width:0}.message-item.watch-hit{border-color:rgba(49,103,227,.55);box-shadow:0 0 0 2px rgba(49,103,227,.08)}.message-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.message-title{font-weight:850;line-height:1.45}.message-meta{color:var(--muted);font-size:11px;line-height:1.5;margin-top:4px}.message-summary{font-size:12px;line-height:1.65;margin-top:8px;color:#364157}.message-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}.source-pill{display:inline-flex;align-items:center;gap:4px;border-radius:999px;padding:5px 8px;background:#f2f5fa;color:#5d687d;font-size:11px;text-decoration:none}.message-priority{font-size:11px;font-weight:800;border-radius:999px;padding:4px 8px;white-space:nowrap}.message-priority.high{background:#fff0f0;color:var(--up)}.message-priority.medium{background:#fff7e6;color:var(--amber)}.message-priority.normal{background:#eef8f4;color:var(--down)}.message-source-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}.message-source{border:1px solid var(--line);border-radius:12px;padding:11px;background:#fff}.message-source a{color:var(--blue);font-weight:800;text-decoration:none}.message-source div{font-size:11px;color:var(--muted);line-height:1.5;margin-top:5px}.message-check{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;font-weight:700}.message-subhead{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin:14px 0 8px}.message-subhead b{font-size:14px}.message-subhead span{font-size:11px;color:var(--muted);line-height:1.5}.message-watch-table{margin-bottom:14px}.message-watch-table table{min-width:980px}.message-watch-table a{color:var(--blue);font-weight:800;text-decoration:none}.message-watch-action{border:0;border-radius:999px;background:#eef3ff;color:var(--blue);font-weight:900;font-size:11px;padding:6px 10px;cursor:pointer}.message-watch-action:hover{background:#dfe8ff}.empty{display:none;padding:28px;text-align:center;color:var(--muted)}.footer{padding:25px 0;color:var(--muted);font-size:11px;line-height:1.7}
.watchlist-manager{border:1px solid #dce5f5;border-radius:15px;background:linear-gradient(180deg,#f8faff,#fff);padding:14px;margin:12px 0 16px}.watchlist-manager-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px}.watchlist-manager-head b{font-size:14px;color:var(--navy)}.watchlist-manager-head span{font-size:11px;color:var(--muted);line-height:1.5;text-align:right}.watchlist-manager-form{display:flex;flex-wrap:wrap;gap:9px;align-items:center}.watchlist-sync-status[data-state="shared"]{color:var(--down)}.watchlist-sync-status[data-state="error"]{color:var(--amber)}.message-button:disabled{opacity:.6;cursor:wait}.watchlist-action-wrap{position:relative;display:inline-block}.watchlist-action-toggle{display:inline-flex;align-items:center;gap:8px}.watchlist-action-toggle i{font-style:normal;font-size:10px;transition:transform .18s ease}.watchlist-action-toggle[aria-expanded="true"] i{transform:rotate(180deg)}.watchlist-action-menu{position:absolute;z-index:35;top:calc(100% + 7px);left:0;width:min(390px,calc(100vw - 52px));padding:13px;border:1px solid var(--line);border-radius:13px;background:#fff;box-shadow:0 18px 42px rgba(25,42,80,.18)}.watchlist-action-menu[hidden]{display:none}.watchlist-action-field{display:block}.watchlist-action-field+ .watchlist-action-field{margin-top:13px;padding-top:13px;border-top:1px solid var(--line)}.watchlist-action-field b{display:block;margin-bottom:7px;color:var(--navy);font-size:12px}.watchlist-action-field .message-input{width:100%;min-width:0}.watchlist-action-help{display:block;margin-top:6px;color:var(--muted);font-size:10px;line-height:1.45}.watchlist-delete-select{width:100%;height:38px;border:1px solid var(--line);border-radius:10px;padding:0 10px;background:#fff;color:var(--ink);font:inherit;font-size:12px}
.tracking-hero{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(280px,.85fr);gap:16px;margin-bottom:16px}.tracking-score{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.score-tile{border:1px solid var(--line);border-radius:14px;background:#f7f9fd;padding:12px;min-width:0}.score-tile span{display:block;color:var(--muted);font-size:11px;font-weight:800}.score-tile b{display:block;margin-top:8px;font-size:26px;line-height:1}.risk-pill{display:inline-flex;border-radius:999px;padding:6px 10px;font-size:12px;font-weight:900}.risk-green{background:#eaf8f1;color:var(--down)}.risk-yellow{background:#fff8df;color:#9f7112}.risk-orange{background:#fff0e6;color:#bc6816}.risk-red{background:#fff0f0;color:var(--up)}.module-list{display:grid;gap:9px}.module-row{border:1px solid var(--line);border-radius:12px;padding:10px;background:#fff}.module-head{display:flex;justify-content:space-between;gap:10px;font-size:12px;font-weight:900}.module-basis{margin-top:6px;color:var(--muted);font-size:11px;line-height:1.55}.framework-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.framework-card{border:1px solid var(--line);border-radius:14px;padding:13px;background:#fff}.framework-card b{display:block;margin-bottom:6px}.framework-card p{margin:0 0 9px;color:#59667d;font-size:12px;line-height:1.55}.framework-card ul{margin:0;padding-left:18px;color:#364157;font-size:12px;line-height:1.75}.process-strip{display:flex;flex-wrap:wrap;gap:8px}.process-step{border:1px solid var(--line);background:#f6f8fc;border-radius:999px;padding:8px 11px;font-size:12px;font-weight:800;color:#55627a}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.asset-grid{grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}.valuation-grid{grid-template-columns:repeat(2,1fr)}.g2,.g3,.g4,.tracking-hero{grid-template-columns:1fr}.source-grid{grid-template-columns:1fr 1fr}.chart-grid{grid-template-columns:1fr}}
@media(max-width:650px){.wrap{padding:12px}.hero{padding:22px 18px;border-radius:18px}.hero h1{font-size:23px}.kpis,.tracking-score{grid-template-columns:repeat(2,1fr)}.asset-grid,.valuation-grid,.source-grid{grid-template-columns:1fr}.asset-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.chart{height:330px}.market-single-chart{height:290px}}
.message-item{cursor:pointer;transition:border-color .18s ease,box-shadow .18s ease,transform .18s ease}.message-item:hover{border-color:rgba(49,103,227,.48);box-shadow:0 10px 24px rgba(25,42,80,.08);transform:translateY(-1px)}
.refresh-button{border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.14);color:#fff;border-radius:999px;padding:7px 12px;font-size:12px;font-weight:800;cursor:pointer}.refresh-button:hover{background:rgba(255,255,255,.22)}.refresh-button:disabled{opacity:.65;cursor:wait}.refresh-status{align-self:center;color:#cbd8ee;font-size:12px}.badge.warn{background:rgba(255,240,180,.16);border-color:rgba(255,240,180,.32);color:#fff2bd}.badge.good{background:rgba(202,255,226,.13);border-color:rgba(202,255,226,.28);color:#d9ffeb}
</style>
</head>
<body><div class="wrap">
<section class="hero"><h1>{{ title }}</h1><p>公开数据自动更新 · 本地一键抓取 / 在线定时发布 · 无需用户Token</p><div class="hero-meta"><span class="badge">版本 {{ version }}</span><span class="badge" id="statusBadge">状态读取中</span><span class="badge" id="updatedBadge">更新时间读取中</span><span class="badge" id="freshnessBadge">新鲜度读取中</span><span class="badge">统一口径：红涨绿跌</span><button class="refresh-button" id="refreshDataButton" type="button" aria-label="抓取并刷新最新数据">刷新最新数据</button><span class="refresh-status" id="refreshStatus" role="status" aria-live="polite"></span></div></section>
<nav class="tabs">
<button class="tab active" data-tab="overview">总览</button><button class="tab" data-tab="tracking">大盘跟踪</button><button class="tab" data-tab="macro">宏观与资金</button><button class="tab" data-tab="messages">讯息</button><button class="tab" data-tab="global">全球市场</button><button class="tab" data-tab="ashare">A股情绪与杠杆</button><button class="tab" data-tab="valuation">估值与偏离度</button><button class="tab" data-tab="fund">基金募集</button><button class="tab" data-tab="health">数据状态与口径</button>
</nav>

<section class="panel active" id="overview"><div class="grid kpis" id="overviewKpis"></div><div class="grid g2"><div class="card"><h3>主要指数最新实际点位</h3><div id="overviewMarketCards" class="asset-grid"></div></div><div class="card"><h3>A股市场温度</h3><div id="overviewBreadth" class="chart"></div></div></div></section>

<section class="panel" id="tracking">
  <div class="tracking-hero">
    <div class="card">
      <h3>大盘总控台 <small>大盘决定仓位，风控决定生存</small></h3>
      <div id="trackingScoreTiles" class="tracking-score"></div>
      <div id="trackingPositionNote" class="hint"></div>
      <div id="trackingProcess" class="process-strip"></div>
    </div>
    <div class="card">
      <h3>每日更新说明</h3>
      <div id="trackingUpdateNote" class="hint"></div>
      <div id="trackingSignalTable" class="table-wrap"></div>
    </div>
  </div>
  <div class="grid g2">
    <div class="card"><h3>大盘评分与风险预警历史 <small>沪深300点位使用右轴</small></h3><div id="trackingHistoryChart" class="chart"></div></div>
    <div class="card"><h3>七大模块评分</h3><div id="trackingModuleChart" class="chart"></div></div>
    <div class="card"><h3>风险领先预警模型</h3><div id="trackingRiskChart" class="chart"></div></div>
    <div class="card"><h3>模块依据</h3><div id="trackingModuleList" class="module-list"></div></div>
  </div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>大盘评分与仓位/集中度映射</h3><div id="trackingOpportunityRules" class="table-wrap"></div></div>
    <div class="card"><h3>风险状态与仓位折扣</h3><div id="trackingRiskRules" class="table-wrap"></div></div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3>领先、同步、滞后风险信号</h3>
    <div id="trackingSignalGroups" class="framework-grid"></div>
  </div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>机会-风险状态矩阵</h3><div id="trackingStateMatrix" class="table-wrap"></div></div>
    <div class="card"><h3>Choice日频模型后续字段</h3><div id="trackingChoiceModel" class="framework-grid"></div></div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3>七大模块子指标</h3>
    <div id="trackingModuleBlueprint" class="framework-grid"></div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3>二级市场投研框架</h3>
    <div id="trackingFramework" class="framework-grid"></div>
  </div>
</section>

<section class="panel" id="macro"><div class="grid kpis" id="macroKpis"></div><div class="grid g2"><div class="card"><h3>M1、M2与剪刀差</h3><div id="moneyChart" class="chart"></div></div><div class="card"><h3>银行间资金利率</h3><div id="liquidityKpis" class="grid g3" style="margin-bottom:12px"></div><div id="liquidityChart" class="chart"></div></div><div class="card"><h3>美元净流动性 <small>SOMA/总资产 − TGA − RRP，单位万亿美元</small></h3><div id="macroUsdLiquidityKpis" class="grid g2" style="margin-bottom:12px"></div><div id="macroUsdLiquidityChart" class="chart"></div></div><div class="card"><h3>社会融资规模</h3><div id="socialChart" class="chart"></div></div><div class="card"><h3>PMI <small>制造业、非制造业与50荣枯线</small></h3><div id="pmiChart" class="chart"></div></div><div class="card"><h3>美国PCE与联邦基金利率 <small>PCE为价格指数同比，利率为月度有效利率</small></h3><div id="pcePolicyKpis" class="grid g2" style="margin-bottom:12px"></div><div id="pcePolicyChart" class="chart"></div></div></div></section>

<section class="panel" id="messages">
  <div class="grid kpis" id="messageKpis"></div>
  <div class="card">
    <h3>自选讯息推送 <small>公告、重大新闻、公众号更新、行业事项、研究点评</small></h3>
    <div class="hint" id="messageUpdateNote"></div>
    <div class="message-toolbar">
      <input id="messageSearchInput" class="message-input" placeholder="搜索代码/公司/关键词">
      <button id="messageClearSearch" class="message-button secondary">清空搜索</button>
      <label class="message-check"><input type="checkbox" id="messageOnlyWatch"> 只看自选相关</label>
    </div>
    <div class="watchlist-manager" aria-label="共享自选股管理">
      <div class="watchlist-manager-head">
        <b>共享自选股管理</b>
        <span id="watchlistSyncStatus" class="watchlist-sync-status" data-state="loading">正在同步共享自选股…</span>
      </div>
      <div class="watchlist-manager-form">
        <div id="watchlistActionWrap" class="watchlist-action-wrap">
          <button id="watchlistActionToggle" class="message-button watchlist-action-toggle" type="button" aria-expanded="false" aria-controls="watchlistActionMenu">新增 / 删除自选股 <i>▾</i></button>
          <div id="watchlistActionMenu" class="watchlist-action-menu" hidden>
            <label class="watchlist-action-field">
              <b>新增自选股</b>
              <input id="messageCodeInput" class="message-input" placeholder="输入6位股票代码，如 300750">
              <span class="watchlist-action-help">输入代码后按 Enter 即可新增。</span>
            </label>
            <label class="watchlist-action-field">
              <b>删除自选股</b>
              <select id="watchlistDeleteSelect" class="watchlist-delete-select" aria-label="选择要删除的自选股"></select>
              <span class="watchlist-action-help">从名单中选择股票，确认后立即删除。</span>
            </label>
          </div>
        </div>
        <span class="asset-symbol">当前共 <b id="watchlistInlineCount">0</b> 只；新增或删除后会立即同步并长期保留。</span>
      </div>
    </div>
    <div class="message-subhead">
      <b>自选股讯息汇总</b>
      <span>每只自选股单独汇总近一个月资讯；点击标题打开原文，点击筛选查看明细。</span>
    </div>
    <div id="messageWatchSummary" class="table-wrap message-watch-table"></div>
    <div id="messageCategoryTabs" class="message-categorybar"></div>
    <div id="messageList" class="message-list"></div>
  </div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>已接入来源目录</h3><div id="messageSources" class="message-source-list"></div></div>
    <div class="card"><h3>新研究报告或点评标准化</h3><div id="messageReportTemplate" class="table-wrap"></div></div>
  </div>
</section>

<section class="panel" id="global"><div class="card"><h3>全部指数最新行情 <small>按实际点位显示</small></h3><div id="globalCards" class="asset-grid"></div></div><div style="margin-top:16px"><h3 style="margin:0 0 12px">全部指数走势图 <small style="font-weight:400;color:var(--muted)">每个指数单独成图，Y轴为实际点位</small></h3><div id="indexChartGrid" class="chart-grid"></div></div><div class="card" style="margin-top:16px"><h3>美国国债收益率 <small>完整历史 · 可选择1年/5年/10年/全部</small></h3><div id="treasuryKpis" class="grid g2" style="margin-bottom:12px"></div><div id="treasuryChart" class="chart"></div></div><div class="card" style="margin-top:16px"><h3>美元净流动性 <small>SOMA/总资产 − TGA − RRP，单位万亿美元</small></h3><div id="usdLiquidityKpis" class="grid g2" style="margin-bottom:12px"></div><div id="usdLiquidityChart" class="chart"></div></div><div class="card" style="margin-top:16px"><h3>全部股票最新行情 <small>包含美光、三星电子、SK海力士</small></h3><div id="techCards" class="asset-grid"></div></div><div style="margin-top:16px"><h3 style="margin:0 0 12px">全部股票走势图 <small style="font-weight:400;color:var(--muted)">每只股票单独成图，Y轴为实际价格</small></h3><div id="stockChartGrid" class="chart-grid"></div></div></section>

<section class="panel" id="ashare"><div class="grid kpis" id="ashareKpis"></div><div class="hint">交易拥挤度 = 当日A股成交额排名前5%的股票成交额合计 ÷ 沪深A股全部股票成交额。历史回填使用当前仍上市股票回溯，可能存在退市样本缺失；日常收盘更新使用当日完整股票池。</div><div class="grid g2"><div class="card"><h3>交易拥挤度历史</h3><div id="crowdingChart" class="chart"></div></div><div class="card"><h3>上涨、下跌和平盘家数</h3><div id="breadthChart" class="chart"></div></div><div class="card"><h3>A股成交额与广义换手率</h3><div id="turnoverChart" class="chart"></div></div><div class="card"><h3>两融余额 / 市场总市值</h3><div id="leverageChart" class="chart"></div></div></div></section>

<section class="panel" id="valuation"><div class="card"><h3>指数历史估值</h3><div id="valuationCards" class="valuation-grid"></div><div id="valuationChart" class="chart tall"></div></div><div class="card" style="margin-top:16px"><h3>指数偏离度 <small>(收盘价/10周或20周均线−1)×100%</small></h3><div id="deviationCards" class="asset-grid"></div><div id="deviationChart" class="chart tall"></div></div></section>

<section class="panel" id="fund"><div class="grid g2"><div class="card"><h3>单只新成立基金募集规模 <small>估算口径</small></h3><div id="fundChart" class="chart"></div></div><div class="card"><h3>口径说明</h3><div class="hint">免费公开源可稳定取得“募集份额（亿份）”，但不存在统一、连续、免费的“单只基金每日净申购金额”字段。网站按常见初始面值1元/份，将募集份额近似展示为募集规模（亿元），并明确标注，不把它冒充存续期净申购额。</div><div id="fundKpis" class="grid g2"></div></div></div><div class="card" style="margin-top:16px"><h3>最新新成立基金</h3><div class="table-wrap"><table><thead><tr><th>成立日期</th><th>基金代码</th><th>基金名称</th><th>类型</th><th>基金公司</th><th>募集份额（亿份）</th><th>估算规模（亿元）</th></tr></thead><tbody id="fundTable"></tbody></table></div></div></section>

<section class="panel" id="health"><div class="card"><h3>数据模块状态</h3><div id="sourceCards" class="source-grid"></div></div><div class="card" style="margin-top:16px"><h3>主要数据来源与口径</h3><div class="table-wrap"><table><thead><tr><th>模块</th><th>数据源</th><th>更新口径</th></tr></thead><tbody>
<tr><td>M1/M2、社融、PMI、CPI</td><td>人民银行、国家统计局及公开适配器</td><td>月度，发布后更新</td></tr><tr><td>美国PCE</td><td>美国经济分析局 BEA NIPA 2.8.4</td><td>月度PCE价格指数同比</td></tr><tr><td>联邦基金利率</td><td>美联储 H.15</td><td>月度有效联邦基金利率</td></tr><tr><td>DR001/DR007</td><td>中国货币网</td><td>存款类机构质押式回购加权利率；不以FDR替代</td></tr><tr><td>隔夜Shibor</td><td>中国货币网 / AKShare</td><td>每个工作日官方发布</td></tr><tr><td>美债收益率</td><td>美联储H.15 / FRED</td><td>DGS2、DGS10，单位%</td></tr><tr><td>美元净流动性</td><td>FRED；备用：美联储H.4.1、纽约联储逆回购API</td><td>周度；SOMA或总资产减TGA和RRP，单位万亿美元</td></tr><tr><td>全球指数与股票</td><td>Yahoo Finance / yfinance</td><td>日线复权收盘；红涨绿跌</td></tr><tr><td>大盘跟踪</td><td>宏观、资金、估值、A股情绪、两融、全球市场公开数据</td><td>每日随数据刷新自动重算大盘评分、风险预警和建议仓位</td></tr><tr><td>A股情绪、成交额、市值</td><td>东方财富公开行情 / AKShare</td><td>收盘后统计沪深A股</td></tr><tr><td>两融余额</td><td>上交所、深交所 / AKShare</td><td>沪深两市融资融券余额合计</td></tr><tr><td>讯息推送</td><td>交易所、巨潮资讯、公司官网、财经网站、公众号公开转载、研报入口</td><td>北京时间08:00和17:00刷新，输入自选代码后优先标记相关消息</td></tr><tr><td>基金募集</td><td>天天基金 / AKShare</td><td>募集份额（亿份），1元/份近似规模</td></tr></tbody></table></div></div></section>

<div class="footer">{{ disclaimer }}<br>该网站只展示公开数据与计算结果。公开网页接口可能调整，系统会保留上一次成功缓存并在“数据状态”中披露失败。红色统一表示上涨/正收益，绿色统一表示下跌/负收益。</div>
</div>
<script>
const DATA={{ payload|safe }};
if(Array.isArray(window.MACROSCOPE_MARKET_ITEMS)){DATA.messages.market_items=window.MACROSCOPE_MARKET_ITEMS}
const C={blue:'#3167e3',navy:'#172c58',cyan:'#1e9ca5',purple:'#7456d8',amber:'#c98a18',up:'#d64242',down:'#159567',muted:'#78849a',grid:'#e8ecf3'};
const CONFIG={responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']};
const baseLayout={paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei',color:'#536078',size:11},margin:{l:48,r:30,t:18,b:42},legend:{orientation:'h',y:1.13},hovermode:'x unified'};
const layout=x=>Object.assign({},baseLayout,x||{});
const DAY_MS=24*60*60*1000;
const K_BAR={width:DAY_MS*.42};
const kAxis={type:'date',tickformat:'%m-%d',showgrid:false};
const kBarLayout=x=>layout(Object.assign({bargap:.62,bargroupgap:.18,xaxis:kAxis},x||{}));
const fmt=(v,d=2)=>v===null||v===undefined||Number.isNaN(Number(v))?'—':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const signed=(v,d=2)=>v===null||v===undefined||Number.isNaN(Number(v))?'—':`${Number(v)>=0?'+':''}${fmt(v,d)}%`;
const cnDate=v=>{const s=String(v||'');return s.length===8?`${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`:s.length===6?`${s.slice(0,4)}-${s.slice(4,6)}`:s||'—'};
const cls=v=>Number(v)>0?'positive':Number(v)<0?'negative':'neutral';
const trendText=(v,d=2)=>v===null||v===undefined||Number.isNaN(Number(v))?'':`较上期 ${Number(v)>=0?'+':''}${fmt(v,d)}`;
function kpi(label,value,unit,note,klass=''){return `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value ${klass}">${value}${value==='—'?'':` <small style="font-size:12px;color:#7d899f">${unit}</small>`}</div><div class="kpi-note">${note||''}</div></div>`}
function latest(rows,key){for(let i=rows.length-1;i>=0;i--){const v=rows[i][key];if(v!==null&&v!==undefined&&!Number.isNaN(Number(v)))return {v:Number(v),row:rows[i]}}return {v:null,row:rows[rows.length-1]||{}}}
function latestDelta(rows,key){const valid=[];for(const row of rows){const v=row[key];if(v!==null&&v!==undefined&&!Number.isNaN(Number(v)))valid.push({v:Number(v),row})}if(!valid.length)return {v:null,delta:null,pct:null,row:rows[rows.length-1]||{}};const last=valid[valid.length-1],prev=valid.length>1?valid[valid.length-2]:null;return {v:last.v,delta:prev?last.v-prev.v:null,pct:prev&&prev.v!==0?(last.v/prev.v-1)*100:null,row:last.row}}
function noteWithDelta(row,dateKey,delta,digits=2){const date=cnDate(row?.[dateKey]);const trend=trendText(delta,digits);return trend?`${date} · ${trend}`:date}
function esc(value){return String(value??'').replace(/[&<>"']/g,match=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[match]))}
function normalizeStockCode(value){
  const raw=String(value||'').trim().toUpperCase().replace(/\s+/g,'');
  if(!raw)return '';
  if(/^\d{6}$/.test(raw)){
    if(raw.startsWith('6')||raw.startsWith('9'))return `${raw}.SH`;
    if(raw.startsWith('0')||raw.startsWith('3'))return `${raw}.SZ`;
    if(raw.startsWith('4')||raw.startsWith('8'))return `${raw}.BJ`;
  }
  return raw;
}
function marketRows(symbol,max=520){return DATA.market.filter(r=>r.symbol===symbol&&r.close!==null&&r.close!==undefined&&!Number.isNaN(Number(r.close))).sort((a,b)=>String(a.trade_date).localeCompare(String(b.trade_date))).slice(-max)}
function safeId(value){return String(value).replace(/[^a-zA-Z0-9_-]/g,'_')}
function marketUnit(card){if(card.currency)return card.currency;return ['US_INDEX','FX_INDEX','CN_INDEX'].includes(card.market)?'点':''}
function marketTickFormat(card){return card.currency==='KRW'?',.0f':',.2f'}
function renderMarketChartGrid(containerId,cards,prefix,maxRows=520){
  const container=document.getElementById(containerId);
  if(!container)return;
  if(!cards.length){
    container.innerHTML='<div class="hint">当前没有可展示的指数或股票数据。请在“数据状态与口径”查看对应数据源状态。</div>';
    return;
  }
  container.innerHTML=cards.map(card=>{
    const id=`${prefix}-${safeId(card.symbol)}`;
    const color=cls(card.daily_pct);
    return `<div class="market-chart-card"><div class="market-chart-head"><div><div class="market-chart-title">${card.name}</div><div class="market-chart-meta">${card.symbol} · ${marketUnit(card)} · ${cnDate(card.trade_date)}</div></div><div class="market-chart-latest ${color}">${fmt(card.close,card.currency==='KRW'?0:2)}<br><span style="font-size:11px">${signed(card.daily_pct)}</span></div></div><div id="${id}" class="market-single-chart"></div></div>`;
  }).join('');
  cards.forEach(card=>{
    const rows=marketRows(card.symbol,maxRows);
    const id=`${prefix}-${safeId(card.symbol)}`;
    const target=document.getElementById(id);
    if(!target)return;
    if(!rows.length){
      target.innerHTML='<div class="chart-placeholder">最新价格卡片存在，但网页负载中没有该标的的历史序列。重新运行主工作流后应自动恢复。</div>';
      return;
    }
    const direction=Number(card.daily_pct)>0?C.up:Number(card.daily_pct)<0?C.down:C.blue;
    const unit=marketUnit(card);
    const values=rows.map(r=>Number(r.close));
    const trace={
      x:rows.map(r=>cnDate(r.trade_date)),
      y:values,
      name:card.name,
      mode:'lines',
      line:{color:direction,width:2.2},
      hovertemplate:`%{x}<br>%{y:${marketTickFormat(card)}} ${unit}<extra>${card.name}</extra>`
    };
    const latestPoint={
      x:[cnDate(rows[rows.length-1].trade_date)],
      y:[Number(rows[rows.length-1].close)],
      name:'最新',
      mode:'markers',
      marker:{color:direction,size:7},
      hovertemplate:`最新 %{x}<br>%{y:${marketTickFormat(card)}} ${unit}<extra></extra>`
    };
    Plotly.newPlot(id,[trace,latestPoint],layout({
      showlegend:false,
      margin:{l:62,r:18,t:12,b:45},
      xaxis:{gridcolor:C.grid,showgrid:false},
      yaxis:{
        title:unit||'实际值',
        gridcolor:C.grid,
        autorange:true,
        rangemode:'normal',
        tickformat:marketTickFormat(card),
        separatethousands:true
      }
    }),CONFIG);
  });
}
function assetCard(c){const color=cls(c.daily_pct);return `<div class="asset"><div class="asset-top"><div><div class="asset-name">${c.name}</div><div class="asset-symbol">${c.symbol} · ${cnDate(c.trade_date)}</div></div><b class="${color}">${signed(c.daily_pct)}</b></div><div class="asset-price ${color}">${fmt(c.close,2)} <small style="font-size:11px;color:#7d899f">${c.currency||''}</small></div><div class="asset-metrics"><div><span>当日</span><b class="${color}">${signed(c.daily_pct)}</b></div><div><span>年初至今</span><b class="${cls(c.ytd_pct)}">${signed(c.ytd_pct)}</b></div><div><span>近一年</span><b class="${cls(c.one_year_pct)}">${signed(c.one_year_pct)}</b></div></div></div>`}

function dateFromKey(value){
  const s=String(value||'').replace(/\D/g,'');
  if(s.length>=8)return new Date(Date.UTC(Number(s.slice(0,4)),Number(s.slice(4,6))-1,Number(s.slice(6,8))));
  return null;
}
function freshnessSummary(){
  const datasets=DATA.status.datasets||{};
  const keys=['market','global_macro','liquidity','valuation','breadth','leverage','deviation','messages','market_tracking'];
  const today=new Date();
  const todayUtc=Date.UTC(today.getUTCFullYear(),today.getUTCMonth(),today.getUTCDate());
  const ages=keys.map(key=>{
    const d=dateFromKey(datasets[key]?.latest_date);
    if(!d)return null;
    return Math.max(0,Math.floor((todayUtc-d.getTime())/DAY_MS));
  }).filter(x=>x!==null);
  if(!ages.length)return {label:'等待数据',klass:'warn'};
  const maxAge=Math.max(...ages);
  if(maxAge<=1)return {label:'核心数据已刷新',klass:'good'};
  if(maxAge<=3)return {label:`部分数据延迟${maxAge}天内`,klass:'warn'};
  return {label:`有数据延迟${maxAge}天`,klass:'warn'};
}
function setRefreshStatus(text,autoClear=true){
  const node=document.getElementById('refreshStatus');
  if(!node)return;
  node.textContent=text||'';
  if(text&&autoClear)setTimeout(()=>{if(node.textContent===text)node.textContent=''},4200);
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function localRefreshStatus(){
  try{
    const url=new URL('/api/refresh-status',window.location.origin);
    url.searchParams.set('_',Date.now());
    const response=await fetch(url.href,{cache:'no-store',headers:{Accept:'application/json'}});
    const type=response.headers.get('content-type')||'';
    if(!response.ok||!type.includes('application/json'))return null;
    const payload=await response.json();
    return payload&&payload.local_refresh_api===true?payload:null;
  }catch(error){
    return null;
  }
}
function localProgressText(status){
  const progress=Number.isFinite(Number(status?.progress))?` ${Math.round(Number(status.progress))}%`:'';
  return `${status?.message||status?.step||'正在刷新数据…'}${status?.running?progress:''}`;
}
async function monitorLocalRefresh(){
  for(let attempt=0;attempt<2400;attempt+=1){
    const status=await localRefreshStatus();
    if(!status)throw new Error('local api unavailable');
    setRefreshStatus(localProgressText(status),false);
    if(!status.running){
      if(status.state==='error'){
        setRefreshStatus(status.message||'本地刷新失败，请查看启动窗口中的错误信息',false);
        return;
      }
      if(status.state==='success'||status.state==='partial'){
        setRefreshStatus(`${status.message||'数据刷新完成'}，正在载入…`,false);
        await sleep(700);
        const nextUrl=new URL(window.location.href);
        nextUrl.searchParams.set('v',Date.now());
        window.location.href=nextUrl.href;
        return;
      }
    }
    await sleep(1500);
  }
  throw new Error('local refresh timeout');
}
async function tryLocalRefresh(){
  const status=await localRefreshStatus();
  if(!status)return false;
  setRefreshStatus(status.running?localProgressText(status):'正在启动本地数据刷新…',false);
  const response=await fetch('/api/refresh',{
    method:'POST',
    cache:'no-store',
    headers:{Accept:'application/json'}
  });
  if(!response.ok)throw new Error(`local refresh ${response.status}`);
  await monitorLocalRefresh();
  return true;
}
async function checkPublishedVersion(){
  setRefreshStatus('正在检查线上最新版本...',false);
  const metaUrl=new URL('site-meta.json',window.location.href);
  metaUrl.searchParams.set('_',Date.now());
  const response=await fetch(metaUrl.href,{cache:'no-store'});
  if(!response.ok)throw new Error(`meta ${response.status}`);
  const meta=await response.json();
  if(meta.updated_at&&meta.updated_at!==DATA.status.updated_at){
    setRefreshStatus('发现新版本，正在刷新...',false);
    const nextUrl=new URL(window.location.href);
    nextUrl.searchParams.set('v',Date.now());
    window.location.href=nextUrl.href;
    return;
  }
  setRefreshStatus('当前已是最新发布版本');
}
async function checkLatestData(){
  const button=document.getElementById('refreshDataButton');
  if(button)button.disabled=true;
  try{
    const handledLocally=await tryLocalRefresh();
    if(!handledLocally)await checkPublishedVersion();
  }catch(error){
    setRefreshStatus('刷新失败，请确认本地更新窗口仍在运行',false);
  }finally{
    if(button)button.disabled=false;
  }
}
document.getElementById('statusBadge').textContent=`数据状态：${DATA.status.overall_status||'empty'}`;
document.getElementById('updatedBadge').textContent=`生成：${DATA.status.updated_at?DATA.status.updated_at.replace('T',' '):'尚未更新'}`;
const fresh=freshnessSummary();
const freshnessBadge=document.getElementById('freshnessBadge');
freshnessBadge.textContent=fresh.label;
freshnessBadge.classList.add(fresh.klass);
document.getElementById('refreshDataButton').addEventListener('click',checkLatestData);

function riskClass(level){
  const key=String(level||'');
  if(key.includes('绿'))return 'risk-green';
  if(key.includes('黄'))return 'risk-yellow';
  if(key.includes('橙'))return 'risk-orange';
  if(key.includes('深红'))return 'risk-red';
  if(key.includes('红'))return 'risk-red';
  return 'risk-yellow';
}
function simpleTable(headers,rows){
  return `<table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function renderTracking(){
  const payload=DATA.market_tracking||{};
  const tracking=payload.tracking||{};
  const summary=tracking.summary||{};
  const modules=tracking.modules||[];
  const risks=tracking.risk_modules||[];
  const history=DATA.market_tracking_history||[];
  const signals=tracking.signals||{};
  const riskLevel=summary.risk_level||'等待更新';
  document.getElementById('trackingScoreTiles').innerHTML=[
    `<div class="score-tile"><span>大盘评分</span><b>${fmt(summary.market_score,1)}</b><div class="asset-symbol">0-100，越高越适合承担风险</div></div>`,
    `<div class="score-tile"><span>风险预警</span><b>${fmt(summary.risk_score,1)}</b><div style="margin-top:8px"><span class="risk-pill ${riskClass(riskLevel)}">${esc(riskLevel)}</span></div></div>`,
    `<div class="score-tile"><span>风险折扣后仓位</span><b>${esc(summary.suggested_position_range||`${fmt(summary.suggested_position_pct,1)}%`)}</b><div class="asset-symbol">${esc(summary.market_phase||'等待更新')}</div></div>`,
    `<div class="score-tile"><span>理论仓位</span><b>${esc(summary.base_position_range||`${fmt(summary.base_position_pct,1)}%`)}</b><div class="asset-symbol">由大盘机会评分决定</div></div>`,
    `<div class="score-tile"><span>风险折扣</span><b>${esc(summary.risk_discount_range||`${fmt(Number(summary.risk_discount||0)*100,0)}%`)}</b><div class="asset-symbol">${esc(summary.risk_action||'由预警颜色决定')}</div></div>`,
    `<div class="score-tile"><span>风格判断</span><b style="font-size:20px">${esc(summary.style_signal||'等待更新')}</b><div class="asset-symbol">${esc(summary.style_reason||'')}</div></div>`
  ].join('');
  document.getElementById('trackingPositionNote').innerHTML=`${esc(summary.formula||'实际仓位 = 大盘建议仓位 × 风险折扣')}。当前阶段：<b>${esc(summary.market_phase||'等待更新')}</b>；${esc(summary.concentration_note||'')} ${summary.liquidity_gate?`<br><b>${esc(summary.liquidity_gate)}</b>`:''} 数据日期：${cnDate(tracking.trade_date)}。`;
  document.getElementById('trackingProcess').innerHTML=(payload.process_steps||[]).map((step,index)=>`<span class="process-step">${index+1}. ${esc(step)}</span>`).join('');
  document.getElementById('trackingUpdateNote').innerHTML=`${esc(payload.method_note||'')}<br>更新时间：${esc(payload.updated_at||'等待更新')}；更新安排：${esc((payload.schedule||[]).join('、'))}。`;
  document.getElementById('trackingSignalTable').innerHTML=`<table><thead><tr><th>监测项</th><th>最新值</th></tr></thead><tbody>
    <tr><td>上涨家数占比</td><td>${fmt(signals.up_ratio_pct)}%</td></tr>
    <tr><td>A股成交额</td><td>${fmt(signals.total_amount_trillion,3)} 万亿元</td></tr>
    <tr><td>成交额/总市值</td><td>${fmt(signals.broad_turnover_pct,3)}%</td></tr>
    <tr><td>交易拥挤度</td><td>${fmt(signals.crowding_pct)}%</td></tr>
    <tr><td>两融余额/总市值</td><td>${fmt(signals.margin_to_market_cap_pct,3)}%</td></tr>
    <tr><td>主要指数20日动量</td><td>${fmt(signals.index_momentum_20d_pct)}%</td></tr>
    <tr><td>美国10年期国债</td><td>${fmt(signals.dgs10_pct,3)}%</td></tr>
  </tbody></table>`;
  if(history.length){
    const historyDates=history.map(x=>cnDate(x.trade_date));
    const csi300ByDate=new Map(marketRows('000300.SH',520).map(x=>[
      String(x.trade_date??'').replace(/\D/g,''),
      Number(x.close)
    ]));
    const csi300Values=history.map(x=>{
      const value=csi300ByDate.get(String(x.trade_date??'').replace(/\D/g,''));
      return Number.isFinite(value)?value:null;
    });
    const historyTraces=[
      {x:historyDates,y:history.map(x=>x.market_score),name:'大盘评分',mode:'lines',line:{color:C.blue,width:2.4}},
      {x:historyDates,y:history.map(x=>x.risk_score),name:'风险分数',mode:'lines',line:{color:C.up,width:2.1}},
      {x:historyDates,y:history.map(x=>x.suggested_position_pct),name:'建议仓位',mode:'lines',line:{color:C.amber,width:2,dash:'dot'}}
    ];
    if(csi300Values.some(value=>Number.isFinite(value))){
      historyTraces.push({
        x:historyDates,
        y:csi300Values,
        name:'沪深300',
        mode:'lines',
        yaxis:'y2',
        connectgaps:false,
        line:{color:C.navy,width:2.3,dash:'dash'},
        hovertemplate:'沪深300<br>%{x}<br>%{y:,.2f} 点<extra></extra>'
      });
    }
    Plotly.newPlot('trackingHistoryChart',historyTraces,layout({
      margin:{l:48,r:66,t:18,b:42},
      yaxis:{title:'分数 / %',gridcolor:C.grid,range:[0,100]},
      yaxis2:{title:'沪深300点位',overlaying:'y',side:'right',showgrid:false,tickformat:',.0f',color:C.navy},
      shapes:[{type:'line',x0:0,x1:1,xref:'paper',y0:55,y1:55,line:{color:C.muted,dash:'dot'}},{type:'line',x0:0,x1:1,xref:'paper',y0:75,y1:75,line:{color:C.up,dash:'dot'}}]
    }),CONFIG);
  }
  if(modules.length){
    Plotly.newPlot('trackingModuleChart',[{x:modules.map(x=>x.name),y:modules.map(x=>x.raw_score),type:'bar',marker:{color:modules.map(x=>Number(x.raw_score)>=3?'rgba(49,103,227,.68)':Number(x.raw_score)>=2?'rgba(201,138,24,.65)':'rgba(214,66,66,.62)')},text:modules.map(x=>`${fmt(x.raw_score,1)}/5 · 加权${fmt(x.weighted_score,1)}`),textposition:'auto',name:'原始评分'}],layout({margin:{l:44,r:20,t:16,b:90},yaxis:{title:'原始分 / 5',gridcolor:C.grid,range:[0,5]},xaxis:{tickangle:-25}}),CONFIG);
  }
  if(risks.length){
    Plotly.newPlot('trackingRiskChart',[{x:risks.map(x=>x.name),y:risks.map(x=>x.risk),type:'bar',marker:{color:risks.map(x=>Number(x.raw_risk)>=70?C.up:Number(x.raw_risk)>=50?C.amber:'rgba(21,149,103,.62)')},text:risks.map(x=>`${fmt(x.risk,1)} / 权重${x.weight}`),textposition:'auto',name:'风险贡献分'}],layout({margin:{l:44,r:20,t:16,b:90},yaxis:{title:'贡献分',gridcolor:C.grid,range:[0,22]},xaxis:{tickangle:-25}}),CONFIG);
  }
  document.getElementById('trackingModuleList').innerHTML=modules.map(item=>`<div class="module-row"><div class="module-head"><span>${esc(item.name)} · 权重${item.weight}</span><span>${fmt(item.raw_score,1)}/5 · 加权${fmt(item.weighted_score,1)} · ${esc(item.status||'')}</span></div><div class="module-basis">${(item.basis||[]).map(esc).join('<br>')}</div></div>`).join('');
  document.getElementById('trackingOpportunityRules').innerHTML=simpleTable(['大盘评分','市场阶段','总仓位','单股上限','单行业上限','单主题上限'],(payload.opportunity_bands||[]).map(row=>[esc(row.range),esc(row.stage),esc(row.position),esc(row.single_stock),esc(row.industry),esc(row.theme)]));
  document.getElementById('trackingRiskRules').innerHTML=simpleTable(['风险分','状态','动作','仓位折扣'],(payload.risk_bands||[]).map(row=>[esc(row.range),`<span class="risk-pill ${riskClass(row.status)}">${esc(row.status)}</span>`,esc(row.action),esc(row.discount)]));
  document.getElementById('trackingSignalGroups').innerHTML=(payload.signal_groups||[]).map(group=>`<div class="framework-card"><b>${esc(group.name)}</b><p>${esc(group.action||'')}</p><ul>${(group.items||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></div>`).join('');
  document.getElementById('trackingStateMatrix').innerHTML=simpleTable(['机会评分','风险分','含义','动作'],(payload.state_matrix||[]).map(row=>[esc(row.opportunity),esc(row.risk),esc(row.meaning),esc(row.action)]));
  document.getElementById('trackingChoiceModel').innerHTML=(payload.choice_daily_model||[]).map(item=>`<div class="framework-card"><b>${esc(item.dataset)}</b><ul>${(item.fields||[]).map(field=>`<li>${esc(field)}</li>`).join('')}</ul></div>`).join('');
  document.getElementById('trackingModuleBlueprint').innerHTML=(payload.module_blueprint||[]).map(item=>`<div class="framework-card"><b>${esc(item.name)} · 权重${esc(item.weight)}</b><ul>${(item.subitems||[]).map(field=>`<li>${esc(field)}</li>`).join('')}</ul></div>`).join('');
  document.getElementById('trackingFramework').innerHTML=(payload.framework_sections||[]).map(section=>`<div class="framework-card"><b>${esc(section.title)}</b><p>${esc(section.subtitle||'')}</p><ul>${(section.items||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></div>`).join('');
}

function renderOverview(){const m=DATA.macro,b=DATA.breadth,c=DATA.crowding,l=DATA.liquidity,gm=DATA.global_macro,cards=DATA.market_cards;const gap=latestDelta(m,'m1_m2_gap_pp'),dr7=latestDelta(l,'dr007_pct'),crowd=latestDelta(c,'crowding_pct'),up=latest(b,'up_count'),turn=latestDelta(b,'broad_turnover_pct'),t10=latestDelta(gm.filter(x=>x.series==='DGS10'),'value_pct');document.getElementById('overviewKpis').innerHTML=[kpi('M1−M2剪刀差',fmt(gap.v),'个百分点',noteWithDelta(gap.row,'month',gap.delta),cls(gap.delta)),kpi('DR007',fmt(dr7.v),'%',noteWithDelta(dr7.row,'trade_date',dr7.delta),cls(dr7.delta)),kpi('A股交易拥挤度',fmt(crowd.v),'%',noteWithDelta(crowd.row,'trade_date',crowd.delta),cls(crowd.delta)),kpi('A股上涨家数',fmt(up.v,0),'只',cnDate(up.row.trade_date),'positive'),kpi('成交额/总市值',fmt(turn.v),'%',noteWithDelta(turn.row,'trade_date',turn.delta),cls(turn.delta)),kpi('美国10年期国债',fmt(t10.v),'%',noteWithDelta(t10.row,'trade_date',t10.delta),cls(t10.delta))].join('');const mainSymbols=['^IXIC','^GSPC','^DJI','DX-Y.NYB','000688.SH','931087.CSI'];const mainCards=mainSymbols.map(sym=>cards.find(c=>c.symbol===sym)).filter(Boolean);document.getElementById('overviewMarketCards').innerHTML=mainCards.map(assetCard).join('');if(b.length){const recent=b.slice(-80);Plotly.newPlot('overviewBreadth',[{x:recent.map(x=>cnDate(x.trade_date)),y:recent.map(x=>x.up_count),name:'上涨',type:'bar',...K_BAR,marker:{color:C.up}},{x:recent.map(x=>cnDate(x.trade_date)),y:recent.map(x=>-Number(x.down_count||0)),name:'下跌（负轴）',type:'bar',...K_BAR,marker:{color:C.down}}],kBarLayout({barmode:'relative',yaxis:{title:'家数',gridcolor:C.grid}}),CONFIG)}}

function renderUsdLiquidity(kpiId,chartId){
  const g=DATA.global_macro;
  const liqSoma=g.filter(x=>x.series==='NET_USD_LIQUIDITY_SOMA');
  const liqAssets=g.filter(x=>x.series==='NET_USD_LIQUIDITY_TOTAL_ASSETS');
  const soma=latestDelta(liqSoma,'value_pct');
  const assets=latestDelta(liqAssets,'value_pct');
  const kpis=document.getElementById(kpiId);
  if(kpis)kpis.innerHTML=[
    kpi('SOMA口径',fmt(soma.v),'万亿美元',noteWithDelta(soma.row,'trade_date',soma.delta),cls(soma.delta)),
    kpi('总资产口径',fmt(assets.v),'万亿美元',noteWithDelta(assets.row,'trade_date',assets.delta),cls(assets.delta))
  ].join('');
  const chart=document.getElementById(chartId);
  if(chart&&(liqSoma.length||liqAssets.length))Plotly.newPlot(chartId,[
    {x:liqSoma.map(x=>cnDate(x.trade_date)),y:liqSoma.map(x=>x.value_pct),name:'SOMA - TGA - RRP',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.blue,width:2.3}},
    {x:liqAssets.map(x=>cnDate(x.trade_date)),y:liqAssets.map(x=>x.value_pct),name:'总资产 - TGA - RRP',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.cyan,width:2,dash:'dot'}}
  ],layout({margin:{l:62,r:28,t:28,b:48},xaxis:{type:'date',showgrid:false,rangeselector:{x:0,y:1.14,xanchor:'left',buttons:[{count:1,label:'1年',step:'year',stepmode:'backward'},{count:3,label:'3年',step:'year',stepmode:'backward'},{count:5,label:'5年',step:'year',stepmode:'backward'},{step:'all',label:'全部'}]}},yaxis:{title:'万亿美元',gridcolor:C.grid,autorange:true,rangemode:'normal'},hovermode:'x unified'}),CONFIG);
}

function renderPcePolicy(){
  const g=DATA.global_macro;
  const pce=g.filter(row=>row.series==='PCE_YOY');
  const fed=g.filter(row=>row.series==='FEDFUNDS');
  const pceLatest=latestDelta(pce,'value_pct');
  const fedLatest=latestDelta(fed,'value_pct');
  const kpis=document.getElementById('pcePolicyKpis');
  if(kpis)kpis.innerHTML=[
    kpi('美国PCE同比',fmt(pceLatest.v),'%',noteWithDelta(pceLatest.row,'trade_date',pceLatest.delta),'neutral'),
    kpi('有效联邦基金利率',fmt(fedLatest.v),'%',noteWithDelta(fedLatest.row,'trade_date',fedLatest.delta),'neutral')
  ].join('');
  const chart=document.getElementById('pcePolicyChart');
  if(chart&&(pce.length||fed.length))Plotly.newPlot('pcePolicyChart',[
    {x:pce.map(row=>cnDate(row.trade_date)),y:pce.map(row=>row.value_pct),name:'PCE同比',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.amber,width:2.4}},
    {x:fed.map(row=>cnDate(row.trade_date)),y:fed.map(row=>row.value_pct),name:'有效联邦基金利率',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.purple,width:2.2}}
  ],layout({margin:{l:58,r:28,t:28,b:48},xaxis:{type:'date',showgrid:false,rangeselector:{x:0,y:1.14,xanchor:'left',buttons:[{count:1,label:'1年',step:'year',stepmode:'backward'},{count:5,label:'5年',step:'year',stepmode:'backward'},{count:10,label:'10年',step:'year',stepmode:'backward'},{step:'all',label:'全部'}]}},yaxis:{title:'%',gridcolor:C.grid,zeroline:true},hovermode:'x unified'}),CONFIG);
}

function renderMacro(){
  const m=DATA.macro,l=DATA.liquidity;
  if(m.length){
    const a=latestDelta(m,'m1_yoy_pct'),b=latestDelta(m,'m2_yoy_pct'),gap=latestDelta(m,'m1_m2_gap_pp'),sf=latestDelta(m,'sf_stock_yoy_pct'),pmi=latestDelta(m,'pmi_manufacturing'),cpi=latestDelta(m,'cpi_yoy_pct');
    document.getElementById('macroKpis').innerHTML=[kpi('M1同比',fmt(a.v),'%',noteWithDelta(a.row,'month',a.delta),cls(a.delta)),kpi('M2同比',fmt(b.v),'%',noteWithDelta(b.row,'month',b.delta),cls(b.delta)),kpi('剪刀差',fmt(gap.v),'个百分点',noteWithDelta(gap.row,'month',gap.delta),cls(gap.delta)),kpi('社融存量同比',fmt(sf.v),'%',noteWithDelta(sf.row,'month',sf.delta),cls(sf.delta)),kpi('制造业PMI',fmt(pmi.v),'点',noteWithDelta(pmi.row,'month',pmi.delta),cls(pmi.delta)),kpi('CPI同比',fmt(cpi.v),'%',noteWithDelta(cpi.row,'month',cpi.delta),cls(cpi.delta))].join('');
    const x=m.map(row=>cnDate(row.month));
    Plotly.newPlot('moneyChart',[{x,y:m.map(row=>row.m1_yoy_pct),name:'M1同比',mode:'lines',line:{color:C.blue,width:2.5}},{x,y:m.map(row=>row.m2_yoy_pct),name:'M2同比',mode:'lines',line:{color:C.cyan,width:2.5}},{x,y:m.map(row=>row.m1_m2_gap_pp),name:'剪刀差',type:'bar',marker:{color:'rgba(201,138,24,.35)'}}],layout({yaxis:{title:'% / 百分点',gridcolor:C.grid},barmode:'relative'}),CONFIG);
    Plotly.newPlot('socialChart',[{x,y:m.map(row=>row.sf_increment_trillion),name:'当月社融增量',type:'bar',marker:{color:'rgba(49,103,227,.35)'}},{x,y:m.map(row=>row.sf_stock_trillion),name:'社融存量',mode:'lines',line:{color:C.navy,width:2.3}},{x,y:m.map(row=>row.sf_stock_yoy_pct),name:'存量同比',mode:'lines',line:{color:C.up,width:2},yaxis:'y2'}],layout({yaxis:{title:'万亿元',gridcolor:C.grid},yaxis2:{title:'%',overlaying:'y',side:'right',showgrid:false}}),CONFIG);
    Plotly.newPlot('pmiChart',[{x,y:m.map(row=>row.pmi_manufacturing),name:'制造业PMI',mode:'lines',line:{color:C.blue,width:2.3}},{x,y:m.map(row=>row.pmi_non_manufacturing),name:'非制造业PMI',mode:'lines',line:{color:C.cyan,width:2.1}}],layout({yaxis:{title:'PMI点',gridcolor:C.grid},shapes:[{type:'line',x0:x[0],x1:x[x.length-1],y0:50,y1:50,line:{color:C.muted,dash:'dot'}}]}),CONFIG);
  }
  renderPcePolicy();
  const dr1=latestDelta(l,'dr001_pct'),dr7=latestDelta(l,'dr007_pct'),shi=latestDelta(l,'shibor_on_pct');
  document.getElementById('liquidityKpis').innerHTML=[kpi('DR001',fmt(dr1.v),'%',noteWithDelta(dr1.row,'trade_date',dr1.delta),cls(dr1.delta)),kpi('DR007',fmt(dr7.v),'%',noteWithDelta(dr7.row,'trade_date',dr7.delta),cls(dr7.delta)),kpi('隔夜Shibor',fmt(shi.v),'%',noteWithDelta(shi.row,'trade_date',shi.delta),cls(shi.delta))].join('');
  if(l.length)Plotly.newPlot('liquidityChart',[{x:l.map(row=>cnDate(row.trade_date)),y:l.map(row=>row.dr001_pct),name:'DR001',mode:'lines',line:{color:C.blue,width:2.2}},{x:l.map(row=>cnDate(row.trade_date)),y:l.map(row=>row.dr007_pct),name:'DR007',mode:'lines',line:{color:C.purple,width:2.5}},{x:l.map(row=>cnDate(row.trade_date)),y:l.map(row=>row.shibor_on_pct),name:'隔夜Shibor',mode:'lines',line:{color:C.amber,width:2}}],layout({yaxis:{title:'%',gridcolor:C.grid}}),CONFIG);
  renderUsdLiquidity('macroUsdLiquidityKpis','macroUsdLiquidityChart');
}

function renderGlobal(){
  const cards=DATA.market_cards;
  const indices=cards.filter(c=>['US_INDEX','FX_INDEX','CN_INDEX'].includes(c.market));
  const stocks=cards.filter(c=>['US_EQUITY','KR_EQUITY'].includes(c.market));
  document.getElementById('globalCards').innerHTML=indices.map(assetCard).join('');
  document.getElementById('techCards').innerHTML=stocks.map(assetCard).join('');
  renderMarketChartGrid('indexChartGrid',indices,'index-chart',520);
  renderMarketChartGrid('stockChartGrid',stocks,'stock-chart',520);

  const g=DATA.global_macro;
  const g2=g.filter(x=>x.series==='DGS2');
  const g10=g.filter(x=>x.series==='DGS10');
  const t2=latestDelta(g2,'value_pct');
  const t10=latestDelta(g10,'value_pct');
  document.getElementById('treasuryKpis').innerHTML=[
    kpi('美国2年期国债收益率',fmt(t2.v),'%',noteWithDelta(t2.row,'trade_date',t2.delta),cls(t2.delta)),
    kpi('美国10年期国债收益率',fmt(t10.v),'%',noteWithDelta(t10.row,'trade_date',t10.delta),cls(t10.delta))
  ].join('');
  if(g2.length||g10.length)Plotly.newPlot('treasuryChart',[
    {x:g2.map(x=>cnDate(x.trade_date)),y:g2.map(x=>x.value_pct),name:'美国2年期',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.blue,width:1.8}},
    {x:g10.map(x=>cnDate(x.trade_date)),y:g10.map(x=>x.value_pct),name:'美国10年期',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.purple,width:2.1}}
  ],layout({margin:{l:58,r:28,t:28,b:48},xaxis:{type:'date',showgrid:false,rangeselector:{x:0,y:1.14,xanchor:'left',buttons:[{count:1,label:'1年',step:'year',stepmode:'backward'},{count:5,label:'5年',step:'year',stepmode:'backward'},{count:10,label:'10年',step:'year',stepmode:'backward'},{count:20,label:'20年',step:'year',stepmode:'backward'},{step:'all',label:'全部'}]}},yaxis:{title:'收益率（%）',gridcolor:C.grid,autorange:true,rangemode:'normal'},hovermode:'x unified'}),CONFIG);

  const liqSoma=g.filter(x=>x.series==='NET_USD_LIQUIDITY_SOMA');
  const liqAssets=g.filter(x=>x.series==='NET_USD_LIQUIDITY_TOTAL_ASSETS');
  const soma=latestDelta(liqSoma,'value_pct');
  const assets=latestDelta(liqAssets,'value_pct');
  const usdLiquidityKpis=document.getElementById('usdLiquidityKpis');
  if(usdLiquidityKpis)usdLiquidityKpis.innerHTML=[
    kpi('SOMA口径',fmt(soma.v),'万亿美元',noteWithDelta(soma.row,'trade_date',soma.delta),cls(soma.delta)),
    kpi('总资产口径',fmt(assets.v),'万亿美元',noteWithDelta(assets.row,'trade_date',assets.delta),cls(assets.delta))
  ].join('');
  if(liqSoma.length||liqAssets.length)Plotly.newPlot('usdLiquidityChart',[
    {x:liqSoma.map(x=>cnDate(x.trade_date)),y:liqSoma.map(x=>x.value_pct),name:'SOMA - TGA - RRP',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.blue,width:2.3}},
    {x:liqAssets.map(x=>cnDate(x.trade_date)),y:liqAssets.map(x=>x.value_pct),name:'总资产 - TGA - RRP',type:'scattergl',mode:'lines',connectgaps:false,line:{color:C.cyan,width:2,dash:'dot'}}
  ],layout({margin:{l:62,r:28,t:28,b:48},xaxis:{type:'date',showgrid:false,rangeselector:{x:0,y:1.14,xanchor:'left',buttons:[{count:1,label:'1年',step:'year',stepmode:'backward'},{count:3,label:'3年',step:'year',stepmode:'backward'},{count:5,label:'5年',step:'year',stepmode:'backward'},{step:'all',label:'全部'}]}},yaxis:{title:'万亿美元',gridcolor:C.grid,autorange:true,rangemode:'normal'},hovermode:'x unified'}),CONFIG);
}

function renderAshare(){const c=DATA.crowding,b=DATA.breadth,l=DATA.leverage;const crowd=latestDelta(c,'crowding_pct'),up=latest(b,'up_count'),down=latest(b,'down_count'),amount=latestDelta(b,'total_amount_trillion'),turn=latestDelta(b,'broad_turnover_pct'),margin=latestDelta(l,'margin_balance_trillion'),ratio=latestDelta(l,'margin_to_market_cap_pct');document.getElementById('ashareKpis').innerHTML=[kpi('交易拥挤度',fmt(crowd.v),'%',noteWithDelta(crowd.row,'trade_date',crowd.delta),cls(crowd.delta)),kpi('上涨家数',fmt(up.v,0),'只',cnDate(up.row.trade_date),'positive'),kpi('下跌家数',fmt(down.v,0),'只',cnDate(down.row.trade_date),'negative'),kpi('两市成交额',fmt(amount.v),'万亿元',noteWithDelta(amount.row,'trade_date',amount.delta),cls(amount.delta)),kpi('成交额/总市值',fmt(turn.v),'%',noteWithDelta(turn.row,'trade_date',turn.delta),cls(turn.delta)),kpi('两融余额/总市值',fmt(ratio.v),'%',`${noteWithDelta(ratio.row,'trade_date',ratio.delta)} · 两融${fmt(margin.v)}万亿元`,cls(ratio.delta))].join('');if(c.length)Plotly.newPlot('crowdingChart',[{x:c.map(r=>cnDate(r.trade_date)),y:c.map(r=>r.crowding_pct),name:'前5%成交额占比',mode:'lines',fill:'tozeroy',line:{color:C.purple,width:2.3},fillcolor:'rgba(116,86,216,.12)'}],layout({yaxis:{title:'%',gridcolor:C.grid}}),CONFIG);if(b.length){Plotly.newPlot('breadthChart',[{x:b.map(r=>cnDate(r.trade_date)),y:b.map(r=>r.up_count),name:'上涨',type:'bar',...K_BAR,marker:{color:C.up}},{x:b.map(r=>cnDate(r.trade_date)),y:b.map(r=>r.down_count),name:'下跌',type:'bar',...K_BAR,marker:{color:C.down}},{x:b.map(r=>cnDate(r.trade_date)),y:b.map(r=>r.flat_count),name:'平盘',type:'bar',...K_BAR,marker:{color:C.muted}}],kBarLayout({barmode:'stack',yaxis:{title:'家数',gridcolor:C.grid}}),CONFIG);Plotly.newPlot('turnoverChart',[{x:b.map(r=>cnDate(r.trade_date)),y:b.map(r=>r.total_amount_trillion),name:'两市成交额',type:'bar',...K_BAR,marker:{color:'rgba(49,103,227,.35)'}},{x:b.map(r=>cnDate(r.trade_date)),y:b.map(r=>r.broad_turnover_pct),name:'成交额/总市值',mode:'lines',line:{color:C.amber,width:2.2},yaxis:'y2'}],kBarLayout({yaxis:{title:'万亿元',gridcolor:C.grid},yaxis2:{title:'%',overlaying:'y',side:'right',showgrid:false}}),CONFIG)}if(l.length)Plotly.newPlot('leverageChart',[{x:l.map(r=>cnDate(r.trade_date)),y:l.map(r=>r.margin_balance_trillion),name:'两融余额',type:'bar',...K_BAR,marker:{color:'rgba(30,156,165,.36)'}},{x:l.map(r=>cnDate(r.trade_date)),y:l.map(r=>r.margin_to_market_cap_pct),name:'两融/总市值',mode:'lines',line:{color:C.up,width:2.2},yaxis:'y2'}],kBarLayout({yaxis:{title:'万亿元',gridcolor:C.grid},yaxis2:{title:'%',overlaying:'y',side:'right',showgrid:false}}),CONFIG)}

let selectedVal=null;

function rollingWeeklyDeviation(rows){
  const sorted=[...rows]
    .filter(r=>r.period==='week'&&r.close!==null&&r.close!==undefined&&!Number.isNaN(Number(r.close)))
    .sort((a,b)=>String(a.trade_date).localeCompare(String(b.trade_date)));
  return sorted.map((row,index)=>{
    const closes=sorted.slice(0,index+1).map(x=>Number(x.close));
    const last10=closes.slice(-10);
    const last20=closes.slice(-20);
    const ma10=last10.length===10?last10.reduce((a,b)=>a+b,0)/10:null;
    const ma20=last20.length===20?last20.reduce((a,b)=>a+b,0)/20:null;
    const close=Number(row.close);
    return {
      ...row,
      ma10,
      ma20,
      dev10w_pct:ma10?((close/ma10)-1)*100:null,
      dev20w_pct:ma20?((close/ma20)-1)*100:null
    };
  });
}

function renderValuation(){
  const s=DATA.valuation_summary;
  if(s.length){
    selectedVal=selectedVal||s[0].index_code;
    document.getElementById('valuationCards').innerHTML=s.map(v=>`<div class="valuation-card ${v.index_code===selectedVal?'selected':''}" data-code="${v.index_code}"><b>${v.index_name}</b><div class="asset-symbol">${cnDate(v.trade_date)}</div><div class="multiple ${cls(v.pe_change)}">${fmt(v.current_pe)} <small style="font-size:11px;color:#7d899f">倍</small></div><div class="stat-row"><div class="stat">均值<b>${fmt(v.pe_mean)}</b></div><div class="stat">中位数<b>${fmt(v.pe_median)}</b></div><div class="stat">分位<b>${fmt(v.pe_percentile,0)}%</b></div></div><div class="bar"><i style="width:${Math.min(100,Math.max(0,v.pe_percentile||0))}%"></i></div></div>`).join('');
    document.querySelectorAll('.valuation-card').forEach(x=>x.onclick=()=>{
      selectedVal=x.dataset.code;
      renderValuation();
    });
    const rows=DATA.valuation.filter(x=>x.index_code===selectedVal);
    const meta=s.find(x=>x.index_code===selectedVal);
    if(rows.length&&meta){
      Plotly.newPlot('valuationChart',[
        {x:rows.map(r=>cnDate(r.trade_date)),y:rows.map(r=>r.pe_ttm),name:`${meta.index_name} PE TTM`,mode:'lines',line:{color:C.blue,width:2.2}},
        {x:rows.map(r=>cnDate(r.trade_date)),y:rows.map(()=>meta.pe_mean),name:'历史均值',mode:'lines',line:{color:C.amber,dash:'dash'}},
        {x:rows.map(r=>cnDate(r.trade_date)),y:rows.map(()=>meta.pe_q25),name:'25%分位',mode:'lines',line:{color:C.down,dash:'dot'}},
        {x:rows.map(r=>cnDate(r.trade_date)),y:rows.map(()=>meta.pe_q75),name:'75%分位',mode:'lines',line:{color:C.up,dash:'dot'}}
      ],layout({yaxis:{title:'倍',gridcolor:C.grid}}),CONFIG);
    }
  }

  const symbols=[...new Set(DATA.deviation.map(x=>x.symbol))];
  const seriesBySymbol={};
  const latestRows=[];
  symbols.forEach(symbol=>{
    const computed=rollingWeeklyDeviation(DATA.deviation.filter(x=>x.symbol===symbol));
    if(!computed.length)return;
    seriesBySymbol[symbol]=computed;
    const row={...computed[computed.length-1]};
    row.close_change=computed.length>1?Number(row.close)-Number(computed[computed.length-2].close):null;
    latestRows.push(row);
  });

  const cards=document.getElementById('deviationCards');
  if(cards){
    cards.innerHTML=latestRows.length?latestRows.map(x=>`<div class="asset"><div class="asset-name">${x.name}</div><div class="asset-symbol">${cnDate(x.trade_date)} · 周K实际收盘重新计算</div><div class="asset-metrics" style="margin-top:12px"><div><span>偏离10周</span><b class="${cls(x.dev10w_pct)}">${signed(x.dev10w_pct)}</b></div><div><span>偏离20周</span><b class="${cls(x.dev20w_pct)}">${signed(x.dev20w_pct)}</b></div><div><span>收盘</span><b class="${cls(x.close_change)}">${fmt(x.close)}</b></div></div></div>`).join(''):'<div class="hint">当前没有足够的周K数据计算10周和20周偏离度。</div>';
  }

  const traces=[];
  latestRows.forEach(x=>{
    const g=(seriesBySymbol[x.symbol]||[]).slice(-160);
    traces.push({
      x:g.map(r=>cnDate(r.trade_date)),
      y:g.map(r=>r.dev10w_pct),
      name:`${x.name} 10周`,
      mode:'lines',
      line:{width:1.8}
    });
    traces.push({
      x:g.map(r=>cnDate(r.trade_date)),
      y:g.map(r=>r.dev20w_pct),
      name:`${x.name} 20周`,
      mode:'lines',
      line:{width:1.2,dash:'dot'}
    });
  });
  if(traces.length){
    Plotly.newPlot('deviationChart',traces,layout({
      yaxis:{title:'%',gridcolor:C.grid},
      shapes:[{type:'line',x0:0,x1:1,xref:'paper',y0:0,y1:0,line:{color:C.muted,dash:'dot'}}]
    }),CONFIG);
  }
}
function renderFund(){const f=DATA.fund;if(!f.length)return;const sorted=[...f].sort((a,b)=>String(a.founded_date).localeCompare(String(b.founded_date)));const recent=sorted.slice(-30).sort((a,b)=>Number(b.estimated_raised_amount_100m)-Number(a.estimated_raised_amount_100m)).slice(0,15);Plotly.newPlot('fundChart',[{x:recent.map(x=>x.fund_name),y:recent.map(x=>x.estimated_raised_amount_100m),type:'bar',marker:{color:'rgba(116,86,216,.65)'},name:'估算募集规模'}],layout({margin:{l:50,r:20,t:15,b:120},yaxis:{title:'亿元',gridcolor:C.grid},xaxis:{tickangle:-35}}),CONFIG);const last=sorted[sorted.length-1],largest=recent[0];document.getElementById('fundKpis').innerHTML=[kpi('最新成立基金',last.fund_name,'',`${cnDate(last.founded_date)} · ${fmt(last.raised_shares_100m)}亿份`),kpi('近30条最大募集',largest?.fund_name||'—','',largest?`${fmt(largest.estimated_raised_amount_100m)}亿元（估算）`:'' )].join('');document.getElementById('fundTable').innerHTML=sorted.slice(-80).reverse().map(x=>`<tr><td>${cnDate(x.founded_date)}</td><td>${x.fund_code}</td><td>${x.fund_name}</td><td>${x.fund_type||''}</td><td>${x.fund_company||''}</td><td>${fmt(x.raised_shares_100m)}</td><td>${fmt(x.estimated_raised_amount_100m)}</td></tr>`).join('')}

async function renderMessages(){
  const msg=DATA.messages||{};
  const stockNames=DATA.stock_names||{};
  const manualItems=msg.items||[];
  const marketItems=msg.market_items||[];
  const items=[...manualItems,...marketItems].sort((a,b)=>String(b.published_at||'').localeCompare(String(a.published_at||'')));
  const categoryOrder=['公告','个股资讯','重大新闻','公众号更新发布','行业重要事项','新研究报告或点评'];
  const presentCategories=new Set(items.map(item=>item.category).filter(Boolean));
  const categories=['全部',...categoryOrder.filter(cat=>presentCategories.has(cat))];
  const storageKey='macroscope-message-watchlist-v2-shared-snapshot';
  const legacyStorageKey='macroscope-message-watchlist-v1';
  const watchlistApi='https://macroscope-shared-dashboard.yuwxsarah.chatgpt.site/api/watchlist';
  let watchlist=[];
  let watchlistMode='loading';
  let activeCategory='全部';
  function readLocalWatchlist(){
    try{
      const stored=JSON.parse(localStorage.getItem(storageKey)||'{}');
      const rows=Array.isArray(stored)?stored:stored?.watchlist;
      if(Array.isArray(rows))return [...new Set(rows.map(normalizeStockCode).filter(Boolean))];
    }catch(error){}
    return [];
  }
  function saveLocalWatchlist(){
    try{
      localStorage.setItem(storageKey,JSON.stringify({watchlist,synced_at:new Date().toISOString()}));
      localStorage.removeItem(legacyStorageKey);
    }catch(error){}
  }
  function setWatchlistStatus(text,state='loading'){
    const node=document.getElementById('watchlistSyncStatus');
    if(!node)return;
    node.textContent=text;
    node.dataset.state=state;
  }
  function watchlistFromPayload(payload){
    const rows=Array.isArray(payload?.watchlist)?payload.watchlist:[];
    return [...new Set(rows.map(row=>normalizeStockCode(typeof row==='string'?row:row?.code)).filter(Boolean))];
  }
  async function loadSharedWatchlist(){
    try{
      const response=await fetch(watchlistApi,{headers:{Accept:'application/json'},cache:'no-store'});
      if(!response.ok)throw new Error(`watchlist ${response.status}`);
      const payload=await response.json();
      watchlist=watchlistFromPayload(payload);
      watchlistMode='shared';
      saveLocalWatchlist();
      setWatchlistStatus('共享同步已开启 · 所有人看到同一份自选股','shared');
    }catch(error){
      watchlist=readLocalWatchlist();
      watchlistMode='local';
      setWatchlistStatus(watchlist.length?'共享服务暂不可用 · 显示上次成功同步的名单':'共享服务暂不可用 · 暂无已同步名单','error');
    }
  }
  async function persistSharedWatchlist(method,code){
    const normalizedCodes=[...new Set((Array.isArray(code)?code:[code]).map(normalizeStockCode).filter(Boolean))];
    if(!normalizedCodes.length)return false;
    setWatchlistStatus(method==='POST'?'正在新增并同步…':'正在删除并同步…','loading');
    if(watchlistMode==='shared'){
      const url=new URL(watchlistApi,window.location.href);
      if(method==='DELETE'&&normalizedCodes.length===1)url.searchParams.set('code',normalizedCodes[0]);
      const response=await fetch(url.href,{
        method,
        cache:'no-store',
        headers:{Accept:'application/json','Content-Type':'application/json'},
        body:method==='POST'?JSON.stringify({code:normalizedCodes[0]}):method==='DELETE'&&normalizedCodes.length>1?JSON.stringify({codes:normalizedCodes}):undefined
      });
      const payload=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(payload.error||`watchlist ${response.status}`);
      watchlist=watchlistFromPayload(payload);
      saveLocalWatchlist();
      setWatchlistStatus('已同步 · 其他访问者刷新后即可看到','shared');
      return true;
    }
    const deleting=new Set(normalizedCodes);
    watchlist=method==='POST'?[...new Set([...watchlist,normalizedCodes[0]])]:watchlist.filter(item=>!deleting.has(item));
    saveLocalWatchlist();
    setWatchlistStatus('已保存到本机；使用共享版链接可跨设备同步','error');
    return true;
  }
  function itemSymbols(item){
    const raw=item.symbols??item.symbol??'';
    const values=Array.isArray(raw)?raw:String(raw).split(/[|,，;；\s]+/);
    return values.map(normalizeStockCode).filter(Boolean);
  }
  function itemText(item){
    return `${item.title||''} ${item.summary||''} ${item.stock_name||''} ${item.source||''} ${item.category||''} ${itemSymbols(item).join(' ')}`.toUpperCase();
  }
  function hitsWatch(item){
    if(!watchlist.length)return false;
    const symbols=itemSymbols(item);
    const text=itemText(item);
    return watchlist.some(code=>symbols.includes(code)||symbols.includes(code.split('.')[0])||text.includes(code)||text.includes(code.split('.')[0]));
  }
  function hitsSearch(item){
    const raw=document.getElementById('messageSearchInput')?.value||'';
    const query=normalizeStockCode(raw)||String(raw).trim().toUpperCase();
    if(!query)return true;
    const text=itemText(item);
    return text.includes(query)||text.includes(query.split('.')[0]);
  }
  function priorityLabel(value){
    const key=String(value||'normal');
    return key==='high'?'高重要':key==='medium'?'中重要':'常规';
  }
  function messageTime(value){
    const raw=String(value||'');
    if(!raw)return '—';
    if(raw.includes('T'))return raw.slice(0,16).replace('T',' ');
    return cnDate(raw);
  }
  function watchMatches(code,item){
    const normalized=normalizeStockCode(code);
    if(!normalized)return false;
    const base=normalized.split('.')[0];
    const symbols=itemSymbols(item);
    const text=itemText(item);
    return symbols.includes(normalized)||symbols.includes(base)||text.includes(normalized)||text.includes(base);
  }
  function watchDisplayName(code,rows){
    const named=rows.find(row=>row.stock_name);
    return stockNames[normalizeStockCode(code)]||named?.stock_name||code;
  }
  function renderKpis(){
    const countBy=cat=>cat==='全部'?items.length:items.filter(x=>x.category===cat).length;
    const covered=new Set(marketItems.flatMap(item=>itemSymbols(item)).filter(Boolean)).size;
    const dates=marketItems.map(x=>String(x.published_at||'')).filter(Boolean).sort();
    const range=dates.length?`${cnDate(dates[0])} 至 ${cnDate(dates[dates.length-1])}`:'等待更新';
    document.getElementById('messageKpis').innerHTML=[
      kpi('近一个月全市场资讯',marketItems.length,'条',range),
      kpi('覆盖个股',covered,'只','公告全市场按日抓取，个股新闻按股票池抓取'),
      kpi('公告',countBy('公告'),'条',`最近更新 ${cnDate(msg.updated_at)}`),
      kpi('个股资讯',countBy('个股资讯'),'条','东方财富个股新闻'),
      kpi('精选讯息',manualItems.length,'条','重大新闻、公众号、行业事项、研报点评'),
      kpi('共享自选代码',watchlistMode==='loading'&&!watchlist.length?'—':watchlist.length,watchlistMode==='loading'&&!watchlist.length?'':'个',watchlistMode==='loading'?'正在读取最新共享名单':watchlistMode==='shared'?'云端长期保存，所有访问者同步':'显示上次成功同步的名单')
    ].join('');
  }
  function closeWatchlistMenu(){
    const menu=document.getElementById('watchlistActionMenu');
    const toggle=document.getElementById('watchlistActionToggle');
    if(menu)menu.hidden=true;
    if(toggle)toggle.setAttribute('aria-expanded','false');
  }
  function renderWatchlist(){
    const select=document.getElementById('watchlistDeleteSelect');
    const count=document.getElementById('watchlistInlineCount');
    if(count)count.textContent=watchlistMode==='loading'&&!watchlist.length?'…':watchlist.length;
    if(select){
      const current=select.value;
      const placeholder=watchlist.length?'请选择要删除的自选股':'当前没有自选股';
      select.innerHTML=`<option value="">${placeholder}</option>`+watchlist.map(code=>{
        const name=stockNames[code]||'';
        return `<option value="${esc(code)}">${esc(code)}${name?` · ${esc(name)}`:''}</option>`;
      }).join('');
      if(watchlist.includes(current))select.value=current;
      select.disabled=watchlistMode==='loading'||!watchlist.length;
    }
    renderWatchSummary();
  }
  function renderWatchSummary(){
    const target=document.getElementById('messageWatchSummary');
    if(!target)return;
    if(watchlistMode==='loading'&&!watchlist.length){
      target.innerHTML='<div class="hint">正在同步最新共享自选股，请稍候…</div>';
      return;
    }
    if(!watchlist.length){
      target.innerHTML='<div class="hint">暂未加入自选股，添加后这里会按股票单独列出资讯汇总。</div>';
      return;
    }
    const rows=watchlist.map(code=>{
      const related=items.filter(item=>watchMatches(code,item));
      const countBy=fn=>related.filter(fn).length;
      return {
        code,
        name:watchDisplayName(code,related),
        latest:related[0]||null,
        announcement:countBy(item=>String(item.category||'')==='公告'),
        news:countBy(item=>['个股资讯','重大新闻','公众号更新发布','行业重要事项'].includes(String(item.category||''))),
        research:countBy(item=>String(item.category||'').includes('研究')||String(item.category||'').includes('点评')),
        high:countBy(item=>String(item.importance||'normal')==='high'),
        total:related.length
      };
    });
    target.innerHTML=`<table><thead><tr><th>自选股</th><th>匹配名称</th><th>最新时间</th><th>最新消息</th><th>公告</th><th>新闻/事项</th><th>研报点评</th><th>高重要</th><th>总数</th><th>操作</th></tr></thead><tbody>${rows.map(row=>{
      const latest=row.latest;
      const title=latest?.title||'暂无近一个月资讯';
      const url=esc(latest?.source_url||'#');
      const link=latest?`<a href="${url}" target="_blank" rel="noopener">${esc(title)}</a>`:esc(title);
      return `<tr><td><b>${esc(row.code)}</b></td><td>${esc(row.name)}</td><td>${messageTime(latest?.published_at)}</td><td>${link}</td><td>${row.announcement}</td><td>${row.news}</td><td>${row.research}</td><td>${row.high}</td><td>${row.total}</td><td><button class="message-watch-action" data-watch-filter="${esc(row.code)}">筛选</button></td></tr>`;
    }).join('')}</tbody></table>`;
    document.querySelectorAll('#messageWatchSummary [data-watch-filter]').forEach(btn=>btn.onclick=()=>{
      const code=btn.dataset.watchFilter||'';
      const search=document.getElementById('messageSearchInput');
      const onlyWatch=document.getElementById('messageOnlyWatch');
      if(search)search.value=code;
      if(onlyWatch)onlyWatch.checked=true;
      activeCategory='全部';
      renderCategories();
      renderRows();
      document.getElementById('messageList')?.scrollIntoView({behavior:'smooth',block:'start'});
    });
  }
  function renderCategories(){
    document.getElementById('messageCategoryTabs').innerHTML=categories.map(cat=>`<button class="message-category ${cat===activeCategory?'active':''}" data-cat="${esc(cat)}">${esc(cat)}</button>`).join('');
    document.querySelectorAll('#messageCategoryTabs button').forEach(btn=>btn.onclick=()=>{
      activeCategory=btn.dataset.cat;
      renderCategories();
      renderRows();
    });
  }
  function renderRows(){
    const onlyWatch=document.getElementById('messageOnlyWatch')?.checked;
    const filtered=items.filter(item=>(activeCategory==='全部'||item.category===activeCategory)&&hitsSearch(item)&&(!onlyWatch||hitsWatch(item)));
    const visible=filtered.slice(0,360);
    const limitNote=filtered.length>visible.length?`<div class="hint">当前筛选命中 ${filtered.length} 条，先展示最新 ${visible.length} 条。输入股票代码、公司名或关键词可以继续缩小范围。</div>`:'';
    document.getElementById('messageList').innerHTML=filtered.length?limitNote+visible.map(item=>{
      const hit=hitsWatch(item);
      const url=esc(item.source_url||'#');
      const symbols=itemSymbols(item);
      const stockName=item.stock_name?` · ${esc(item.stock_name)}`:'';
      return `<div class="message-item ${hit?'watch-hit':''}" data-url="${url}" role="link" tabindex="0" title="点击打开原文"><div class="message-head"><div><div class="message-title">${esc(item.title)}</div><div class="message-meta">${esc(item.category||'')} · ${cnDate(item.published_at)} · ${esc(item.source||'')}${stockName}</div></div><span class="message-priority ${esc(item.importance||'normal')}">${priorityLabel(item.importance)}</span></div><div class="message-summary">${esc(item.summary)}</div><div class="message-tags"><a class="source-pill" href="${url}" target="_blank" rel="noopener">打开原文：${esc(item.source||'查看')}</a>${symbols.map(x=>`<span class="source-pill">${esc(x)}</span>`).join('')}${hit?'<span class="source-pill">自选相关</span>':''}</div></div>`;
    }).join(''):'<div class="hint">当前筛选下没有消息。取消“只看自选相关”或换一个分类即可查看全部内容。</div>';
    document.querySelectorAll('#messageList .message-item[data-url]').forEach(card=>{
      const open=()=>{
        const url=card.dataset.url;
        if(url&&url!=='#')window.open(url,'_blank','noopener');
      };
      card.onclick=event=>{if(!event.target.closest('a,button,input'))open()};
      card.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();open()}};
    });
  }
  function renderSources(){
    const sources=msg.sources||[];
    document.getElementById('messageSources').innerHTML=sources.length?sources.map(source=>`<div class="message-source"><a href="${esc(source.url||'#')}" target="_blank" rel="noopener">${esc(source.name)}</a><div>${esc(source.category||'')} · ${esc(source.type||'公开网页')}</div><div>${esc(source.use||'用于讯息追踪')}</div></div>`).join(''):'<div class="hint">暂无来源目录。</div>';
  }
  function renderTemplate(){
    const rows=(msg.report_templates||[]).map(row=>`<tr><td>${esc(row.module)}</td><td>${esc((row.fields||[]).join(' / '))}</td><td>${esc(row.example||'')}</td></tr>`).join('');
    document.getElementById('messageReportTemplate').innerHTML=`<table><thead><tr><th>类型</th><th>字段</th><th>示例口径</th></tr></thead><tbody>${rows||'<tr><td colspan="3">暂无模板</td></tr>'}</tbody></table>`;
  }
  const schedule=(msg.schedule||['06:20','08:00','10:10','11:40','15:20','16:40','17:00','18:40','19:10']).join('、');
  document.getElementById('messageUpdateNote').innerHTML=`北京时间 ${schedule} 自动更新；最新入库：${cnDate(msg.updated_at)}。已接入近一个月全市场个股资讯库；点击任意讯息卡片可直接打开原文。`;
  try{localStorage.removeItem(legacyStorageKey)}catch(error){}
  watchlist=readLocalWatchlist();
  renderKpis();
  renderWatchlist();
  renderCategories();
  renderSources();
  renderTemplate();
  renderRows();
  await loadSharedWatchlist();
  renderKpis();
  renderWatchlist();
  renderRows();
  const watchlistActionWrap=document.getElementById('watchlistActionWrap');
  const watchlistActionToggle=document.getElementById('watchlistActionToggle');
  const watchlistActionMenu=document.getElementById('watchlistActionMenu');
  const messageCodeInput=document.getElementById('messageCodeInput');
  const watchlistDeleteSelect=document.getElementById('watchlistDeleteSelect');
  watchlistActionToggle.onclick=event=>{
    event.stopPropagation();
    const willOpen=watchlistActionMenu.hidden;
    watchlistActionMenu.hidden=!willOpen;
    watchlistActionToggle.setAttribute('aria-expanded',String(willOpen));
    if(willOpen)setTimeout(()=>messageCodeInput.focus(),0);
  };
  watchlistActionMenu.onclick=event=>event.stopPropagation();
  document.addEventListener('click',event=>{
    if(!watchlistActionWrap.contains(event.target))closeWatchlistMenu();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape')closeWatchlistMenu();
  });
  async function addWatchlistFromInput(){
    const input=document.getElementById('messageCodeInput');
    const code=normalizeStockCode(input.value);
    if(!code){
      setWatchlistStatus('请输入有效的6位A股代码，例如 600353','error');
      input.focus();
      return;
    }
    input.disabled=true;
    try{
      await persistSharedWatchlist('POST',code);
      input.value='';
      renderKpis();
      renderWatchlist();
      renderRows();
      closeWatchlistMenu();
    }catch(error){
      setWatchlistStatus(error?.message||'新增失败，请稍后重试','error');
    }finally{
      input.disabled=false;
    }
  }
  messageCodeInput.onkeydown=event=>{
    if(event.key==='Enter'){
      event.preventDefault();
      addWatchlistFromInput();
    }
  };
  watchlistDeleteSelect.onchange=async event=>{
    const select=event.currentTarget;
    const code=normalizeStockCode(select.value);
    if(!code)return;
    const name=stockNames[code]||'';
    if(!window.confirm(`确认删除 ${code}${name?` · ${name}`:''}？`)){
      select.value='';
      return;
    }
    select.disabled=true;
    try{
      await persistSharedWatchlist('DELETE',code);
      renderKpis();
      renderWatchlist();
      renderRows();
      closeWatchlistMenu();
    }catch(error){
      setWatchlistStatus(error?.message||'删除失败，请稍后重试','error');
    }finally{
      select.value='';
      select.disabled=!watchlist.length;
    }
  };
  document.getElementById('messageOnlyWatch').onchange=renderRows;
  document.getElementById('messageSearchInput').oninput=renderRows;
  document.getElementById('messageClearSearch').onclick=()=>{
    document.getElementById('messageSearchInput').value='';
    renderRows();
  };
}

function renderHealth(){const ds=DATA.status.datasets||{};const labels={macro:'宏观数据',liquidity:'DR/Shibor',market:'全球行情',global_macro:'美债、PCE与美元流动性',market_tracking:'大盘跟踪模型',valuation:'历史估值',sentiment:'A股情绪',crowding:'交易拥挤度',breadth:'涨跌家数',leverage:'两融杠杆',deviation:'指数偏离度',fund_subscription:'基金募集',messages:'讯息推送'};document.getElementById('sourceCards').innerHTML=Object.entries(labels).map(([key,label])=>{const d=ds[key]||{status:'empty'};return `<div class="source-card"><div class="source-status ${d.status}">${label} · ${d.status||'empty'}</div><div class="asset-symbol" style="margin-top:7px">最新日期：${cnDate(d.latest_date)}</div><div class="asset-symbol">本次写入：${d.rows??0} 行；缓存：${d.cached_rows??d.total_cached_rows??0} 行</div>${d.error?`<div class="hint">${String(d.error).slice(0,420)}</div>`:''}</div>`}).join('')}

function safeRender(name,fn){
  try{
    fn();
    return true;
  }catch(error){
    console.error(`Render failed: ${name}`,error);
    return false;
  }
}
const panelRenderers={
  overview:renderOverview,
  tracking:renderTracking,
  macro:renderMacro,
  messages:renderMessages,
  global:renderGlobal,
  ashare:renderAshare,
  valuation:renderValuation,
  fund:renderFund,
  health:renderHealth
};
const renderedPanels=new Set();
function resizePanel(panelId){
  setTimeout(()=>{
    document.querySelectorAll(`#${panelId} .js-plotly-plot`).forEach(element=>{
      try{Plotly.Plots.resize(element)}catch(error){console.warn('Plot resize failed',error)}
    });
  },120);
}
function renderPanel(panelId){
  const renderer=panelRenderers[panelId];
  if(renderer&&!renderedPanels.has(panelId)){
    if(safeRender(panelId,renderer))renderedPanels.add(panelId);
  }
  resizePanel(panelId);
}
document.querySelectorAll('.tab').forEach(btn=>btn.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
  renderPanel(btn.dataset.tab);
});
renderPanel('overview');
</script></body></html>'''


def main() -> None:
    ensure_dirs()
    settings = load_settings()
    paths = {
        "macro": "macro.csv", "liquidity": "liquidity.csv", "market": "market.csv",
        "global_macro": "global_macro.csv", "valuation": "valuation.csv",
        "crowding": "crowding.csv", "breadth": "breadth.csv", "leverage": "leverage.csv",
        "deviation": "deviation.csv", "fund": "fund_subscription.csv",
        "market_tracking_history": "market_tracking.csv",
    }
    data = {key: read_csv_safe(DATA_DIR / filename) for key, filename in paths.items()}
    for key in ["market", "valuation", "crowding", "breadth", "leverage", "liquidity", "global_macro", "deviation"]:
        if not data[key].empty:
            sort_cols = [x for x in ["symbol", "index_code", "series", "trade_date"] if x in data[key].columns]
            data[key] = data[key].sort_values(sort_cols) if sort_cols else data[key]
    messages = read_messages()
    market_items = read_market_messages()
    # Keep the large message feed in its own static asset. This preserves every
    # record while keeping index.html below hosting platforms' per-file limit.
    messages["market_items"] = []
    market_tracking = read_market_tracking()
    payload = {
        "status": read_status(),
        "macro": dataframe_to_records(data["macro"]),
        "liquidity": dataframe_to_records(data["liquidity"], max_rows=5000),
        "market": grouped_tail_records(data["market"], "symbol", rows_per_group=520),
        "market_cards": latest_market_cards(data["market"]),
        "messages": messages,
        "stock_names": read_stock_name_map(),
        "market_tracking": market_tracking,
        "market_tracking_history": dataframe_to_records(data["market_tracking_history"], max_rows=300),
        "global_macro": grouped_tail_records(data["global_macro"], "series", rows_per_group=20000),
        "valuation": grouped_tail_records(data["valuation"], "index_code", rows_per_group=3500),
        "valuation_summary": valuation_summary(data["valuation"]),
        "crowding": dataframe_to_records(data["crowding"], max_rows=3000),
        "breadth": dataframe_to_records(data["breadth"], max_rows=3000),
        "leverage": dataframe_to_records(data["leverage"], max_rows=3000),
        "deviation": dataframe_to_records(data["deviation"], max_rows=5 * 800),
        "fund": dataframe_to_records(data["fund"], max_rows=400),
    }
    seed_warning = any(
        (not frame.empty and 'source' in frame.columns and frame['source'].astype(str).str.contains('演示|示例|seed|demo', case=False, regex=True, na=False).any())
        for frame in data.values()
    )
    html = Template(HTML).render(
        title=f"{settings['app']['name']} · {settings['app']['title_cn']}",
        version=settings["app"]["version"],
        seed_warning=seed_warning,
        disclaimer=settings["app"]["disclaimer"],
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
    )
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "index.html").write_text(html, encoding="utf-8")
    (PUBLIC_DIR / "market-messages-data.js").write_text(
        "window.MACROSCOPE_MARKET_ITEMS="
        + json.dumps(market_items, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        + ";",
        encoding="utf-8",
    )
    (PUBLIC_DIR / ".nojekyll").write_text("", encoding="utf-8")
    nested_public_dir = PUBLIC_DIR / "public"
    nested_public_dir.mkdir(parents=True, exist_ok=True)
    (nested_public_dir / "index.html").write_text(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url=../index.html">
  <title>MacroScope Complete</title>
</head>
<body>
  <p>正在打开 MacroScope 看板，若未自动跳转，<a href="../index.html">点这里进入</a>。</p>
</body>
</html>
""",
        encoding="utf-8",
    )
    status = payload.get("status", {})
    datasets = status.get("datasets", {}) if isinstance(status, dict) else {}
    site_meta = {
        "app_version": status.get("app_version"),
        "updated_at": status.get("updated_at"),
        "overall_status": status.get("overall_status"),
        "last_update_mode": status.get("last_update_mode"),
        "datasets": {
            key: {
                "status": value.get("status"),
                "latest_date": value.get("latest_date"),
                "cached_rows": value.get("cached_rows", value.get("total_cached_rows")),
            }
            for key, value in datasets.items()
            if isinstance(value, dict)
        },
    }
    (PUBLIC_DIR / "site-meta.json").write_text(
        json.dumps(site_meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Built {PUBLIC_DIR / 'index.html'} ({len(html):,} chars)")


if __name__ == "__main__":
    main()
