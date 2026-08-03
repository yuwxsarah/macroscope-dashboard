from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.extended_providers import (
    ChinaLiquidityProvider,
    ChinaSentimentProvider,
    FredTreasuryProvider,
    FundSubscriptionProvider,
    GlobalMarketProvider,
    UsdLiquidityProvider,
)
from src.providers import ChinaMarketProvider, PublicMacroProvider
from src.utils import DATA_DIR, ensure_dirs, load_settings, read_csv_safe, write_csv_atomic, write_json

MACRO_PATH = DATA_DIR / "macro.csv"
MARKET_PATH = DATA_DIR / "market.csv"
GLOBAL_MACRO_PATH = DATA_DIR / "global_macro.csv"
LIQUIDITY_PATH = DATA_DIR / "liquidity.csv"
VALUATION_PATH = DATA_DIR / "valuation.csv"
CROWDING_PATH = DATA_DIR / "crowding.csv"
BREADTH_PATH = DATA_DIR / "breadth.csv"
LEVERAGE_PATH = DATA_DIR / "leverage.csv"
DEVIATION_PATH = DATA_DIR / "deviation.csv"
FUND_PATH = DATA_DIR / "fund_subscription.csv"
STATUS_PATH = DATA_DIR / "status.json"


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _drop_seed_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove bundled demonstration rows once live public data is available."""
    if frame.empty or "source" not in frame.columns:
        return frame
    source = frame["source"].astype(str)
    return frame[~source.str.contains("演示|示例|seed|demo", case=False, regex=True, na=False)].copy()


def _merge_history(old: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    # The distribution includes clearly labelled demonstration rows so the site is
    # not blank before its first GitHub Action run. As soon as real data arrives,
    # discard those rows before merging.
    if not new.empty:
        live_new = _drop_seed_rows(new)
        if not live_new.empty:
            old = _drop_seed_rows(old)
            new = live_new
    if old.empty:
        merged = new.copy()
    elif new.empty:
        merged = old.copy()
    else:
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(keys, keep="last")
    return merged.sort_values(keys).reset_index(drop=True) if not merged.empty else merged


def _latest_value(df: pd.DataFrame, date_col: str) -> str | None:
    if df.empty or date_col not in df.columns:
        return None
    values = df[date_col].dropna().astype(str)
    return values.max() if not values.empty else None


def _run(fn: Callable[[], tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    started = _now_iso()
    try:
        rows, metadata = fn()
        return {
            "status": "success" if rows > 0 else "partial",
            "rows": rows,
            "started_at": started,
            "finished_at": _now_iso(),
            **metadata,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "rows": 0,
            "started_at": started,
            "finished_at": _now_iso(),
            "error": repr(exc),
        }


def update_macro(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    provider = PublicMacroProvider()
    new, details = provider.fetch(settings["macro_start_month"])
    old = read_csv_safe(MACRO_PATH)
    merged = _merge_history(old, new, ["month"])
    write_csv_atomic(merged, MACRO_PATH)
    return len(new), {
        "latest_date": _latest_value(merged, "month"),
        "total_cached_rows": len(merged),
        "source_details": details,
    }


def update_market(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    cn_provider = ChinaMarketProvider()
    frames: list[pd.DataFrame] = []
    per_symbol: dict[str, Any] = {}
    end_date = datetime.now().strftime("%Y%m%d")

    for item in settings["china_indices"]:
        try:
            frame = cn_provider.fetch_index(item, settings["market_start_date"], end_date)
            frame["currency"] = "点"
            frame["asset_group"] = "中国科技指数"
            frames.append(frame)
            per_symbol[item["symbol"]] = {
                "status": "success", "rows": len(frame),
                "source": frame["source"].iloc[-1] if not frame.empty else None,
            }
        except Exception as exc:
            per_symbol[item["symbol"]] = {"status": "failed", "error": repr(exc)}

    global_items = settings.get("global_assets", [])
    if global_items:
        try:
            global_frame = GlobalMarketProvider().fetch(global_items, settings["market_start_date"])
            frames.append(global_frame)
            for item in global_items:
                ticker = item["ticker"]
                subset = global_frame[global_frame["symbol"] == ticker]
                per_symbol[ticker] = {
                    "status": "success" if not subset.empty else "failed",
                    "rows": len(subset),
                    "source": subset["source"].iloc[-1] if not subset.empty else None,
                }
        except Exception as exc:
            for item in global_items:
                per_symbol[item["ticker"]] = {"status": "failed", "error": repr(exc)}

    if not frames:
        raise RuntimeError("所有全球行情源均失败")
    new = pd.concat(frames, ignore_index=True, sort=False)
    for col, default in [("currency", ""), ("asset_group", "市场行情")]:
        if col not in new.columns:
            new[col] = default
    old = read_csv_safe(MARKET_PATH)
    merged = _merge_history(old, new, ["trade_date", "symbol"])
    write_csv_atomic(merged, MARKET_PATH)
    return len(new), {
        "latest_date": _latest_value(merged, "trade_date"),
        "total_cached_rows": len(merged),
        "symbols": per_symbol,
    }


def update_global_macro(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    treasury, details = FredTreasuryProvider().fetch_with_details(settings.get("fred_start_date", "20000101"))
    frames = [treasury]
    try:
        usd_liquidity, liquidity_details = UsdLiquidityProvider().fetch(settings.get("usd_liquidity_start_date", "20150101"))
        frames.append(usd_liquidity)
        details["USD_LIQUIDITY"] = liquidity_details
    except Exception as exc:
        details["USD_LIQUIDITY"] = {"status": "failed", "error": repr(exc)}
    new = pd.concat(frames, ignore_index=True, sort=False)
    old = read_csv_safe(GLOBAL_MACRO_PATH)
    merged = _merge_history(old, new, ["trade_date", "series"])
    write_csv_atomic(merged, GLOBAL_MACRO_PATH)
    latest_by_series = {}
    for series in ["DGS2", "DGS10", "NET_USD_LIQUIDITY_SOMA", "NET_USD_LIQUIDITY_TOTAL_ASSETS"]:
        part = merged[merged["series"] == series] if "series" in merged.columns else pd.DataFrame()
        latest_by_series[series] = _latest_value(part, "trade_date")
    return len(new), {
        "latest_date": _latest_value(merged, "trade_date"),
        "total_cached_rows": len(merged),
        "source_details": details,
        "latest_by_series": latest_by_series,
        "note": "DGS10独立抓取；美元净流动性使用FRED的SOMA/总资产、TGA、RRP公开序列计算。",
    }


def update_liquidity(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    new, details = ChinaLiquidityProvider().fetch()
    old = read_csv_safe(LIQUIDITY_PATH)
    if old.empty:
        merged = new.copy()
    elif new.empty:
        merged = old.copy()
    else:
        old_i = old.set_index("trade_date")
        new_i = new.set_index("trade_date")
        combined = old_i.copy()
        for col in new_i.columns:
            if col not in combined.columns:
                combined[col] = np.nan
        combined.update(new_i)  # pandas.update ignores NaN in the new frame
        missing_dates = new_i.index.difference(combined.index)
        if len(missing_dates):
            combined = pd.concat([combined, new_i.loc[missing_dates]], axis=0)
        merged = combined.reset_index().sort_values("trade_date")
    value_cols = ["dr001_pct", "dr007_pct", "shibor_on_pct"]
    for col in value_cols:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
    write_csv_atomic(merged, LIQUIDITY_PATH)
    return len(new), {
        "latest_date": _latest_value(merged, "trade_date"),
        "total_cached_rows": len(merged),
        "source_details": details,
        "note": "DR来自中国货币网，严格不以FDR替代。",
    }


def update_valuation(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    provider = ChinaMarketProvider()
    frames: list[pd.DataFrame] = []
    per_index: dict[str, Any] = {}
    end_date = datetime.now().strftime("%Y%m%d")
    for item in settings["valuation_indices"]:
        try:
            frame = provider.fetch_valuation(item, settings["valuation_start_date"], end_date)
            frames.append(frame)
            per_index[item["symbol"]] = {
                "status": "success", "rows": len(frame),
                "source": frame["source"].iloc[-1] if not frame.empty else None,
            }
        except Exception as exc:
            per_index[item["symbol"]] = {"status": "failed", "error": repr(exc)}
    if not frames:
        raise RuntimeError("所有估值源均失败")
    new = pd.concat(frames, ignore_index=True)
    old = read_csv_safe(VALUATION_PATH)
    merged = _merge_history(old, new, ["trade_date", "index_code"])
    write_csv_atomic(merged, VALUATION_PATH)
    return len(new), {
        "latest_date": _latest_value(merged, "trade_date"),
        "total_cached_rows": len(merged),
        "indices": per_index,
    }


def update_sentiment(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Update A-share snapshot.

    During trading hours the values are explicitly marked as an intraday snapshot;
    after 15:20 they are treated as the closing snapshot. Weekends and pre-open runs
    keep the last successful cache instead of replacing it with blanks.
    """
    now = datetime.now(ZoneInfo(settings.get("timezone", "Asia/Shanghai")))
    if now.weekday() >= 5 or (now.hour, now.minute) < (9, 35):
        old = read_csv_safe(CROWDING_PATH)
        return 0, {
            "latest_date": _latest_value(old, "trade_date"),
            "total_cached_rows": len(old),
            "skipped": True,
            "note": "非A股交易时段，继续展示上一次成功快照。",
        }

    provider = ChinaSentimentProvider()
    crowding_row, breadth_row = provider.fetch_snapshot(float(settings["crowding"]["top_fraction"]))
    snapshot_kind = "收盘后快照" if (now.hour, now.minute) >= (15, 20) else "盘中快照"
    crowding_row["snapshot_kind"] = snapshot_kind
    breadth_row["snapshot_kind"] = snapshot_kind

    crowding_old = read_csv_safe(CROWDING_PATH)
    crowding_merged = _merge_history(crowding_old, pd.DataFrame([crowding_row]), ["trade_date"])
    write_csv_atomic(crowding_merged, CROWDING_PATH)

    breadth_old = read_csv_safe(BREADTH_PATH)
    breadth_merged = _merge_history(breadth_old, pd.DataFrame([breadth_row]), ["trade_date"])
    write_csv_atomic(breadth_merged, BREADTH_PATH)

    leverage_meta: dict[str, Any]
    try:
        leverage_row = provider.fetch_margin(breadth_row.get("total_market_cap_trillion"))
        leverage_old = read_csv_safe(LEVERAGE_PATH)
        leverage_merged = _merge_history(leverage_old, pd.DataFrame([leverage_row]), ["trade_date"])
        write_csv_atomic(leverage_merged, LEVERAGE_PATH)
        leverage_meta = {
            "status": "success", "rows": 1,
            "latest_date": leverage_row["trade_date"],
            "source": leverage_row.get("source"),
        }
    except Exception as exc:
        leverage_meta = {"status": "failed", "error": repr(exc)}

    return 2, {
        "latest_date": crowding_row["trade_date"],
        "crowding_cached_rows": len(crowding_merged),
        "breadth_cached_rows": len(breadth_merged),
        "leverage": leverage_meta,
        "source": crowding_row.get("source"),
        "snapshot_kind": snapshot_kind,
    }


def _period_deviation(frame: pd.DataFrame, symbol: str, name: str, period: str, label: str) -> pd.DataFrame:
    if period == "week":
        frame = frame.copy()
        frame["_bucket"] = frame["date"].dt.to_period("W-FRI")
        sampled = frame.groupby("_bucket", as_index=False).tail(1).copy()
    elif period == "month":
        frame = frame.copy()
        frame["_bucket"] = frame["date"].dt.to_period("M")
        sampled = frame.groupby("_bucket", as_index=False).tail(1).copy()
    else:
        raise ValueError(f"Unsupported deviation period: {period}")

    sampled = sampled[["date", "close"]].sort_values("date")
    sampled["ma20"] = sampled["close"].rolling(20, min_periods=20).mean()
    sampled["ma30"] = sampled["close"].rolling(30, min_periods=30).mean()
    sampled["dev20_pct"] = (sampled["close"] / sampled["ma20"] - 1) * 100
    sampled["dev30_pct"] = (sampled["close"] / sampled["ma30"] - 1) * 100
    sampled["trade_date"] = sampled["date"].dt.strftime("%Y%m%d")
    sampled["symbol"] = symbol
    sampled["name"] = name
    sampled["period"] = period
    sampled["period_label"] = label
    sampled["source"] = f"公开指数日线计算（{label}最后实际交易日）"
    return sampled[[
        "trade_date", "symbol", "name", "period", "period_label", "close",
        "ma20", "ma30", "dev20_pct", "dev30_pct", "source",
    ]]


def update_deviation(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    market = read_csv_safe(MARKET_PATH)
    if market.empty:
        raise RuntimeError("没有市场行情，无法计算指数偏离度")
    names = {x["symbol"]: x["name"] for x in settings.get("deviation_indices", [])}
    rows: list[pd.DataFrame] = []
    per_index: dict[str, Any] = {}
    for symbol, name in names.items():
        frame = market[market["symbol"] == symbol][["trade_date", "close"]].copy()
        frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
        if frame.empty:
            per_index[symbol] = {"status": "failed", "error": "market.csv中没有该指数行情"}
            continue

        weekly = _period_deviation(frame, symbol, name, "week", "周K")
        monthly = _period_deviation(frame, symbol, name, "month", "月K")
        rows.extend([weekly, monthly])
        per_index[symbol] = {
            "status": "success",
            "rows": len(weekly) + len(monthly),
            "latest_week_date": str(weekly["trade_date"].max()),
            "latest_month_date": str(monthly["trade_date"].max()),
        }
    if not rows:
        raise RuntimeError("没有足够指数数据计算偏离度")
    new = pd.concat(rows, ignore_index=True)
    write_csv_atomic(new.sort_values(["symbol", "trade_date"]), DEVIATION_PATH)
    return len(new), {
        "latest_date": _latest_value(new, "trade_date"),
        "total_cached_rows": len(new),
        "method": "(指数收盘/20或30周期均线-1)×100%；周期包括周K和月K",
        "indices": per_index,
    }


def update_fund(settings: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    max_rows = int(settings.get("fund_subscription", {}).get("lookback_rows", 300))
    new = FundSubscriptionProvider().fetch(max_rows=max_rows)
    old = read_csv_safe(FUND_PATH)
    merged = _merge_history(old, new, ["founded_date", "fund_code"])
    merged = merged.tail(max_rows).reset_index(drop=True)
    write_csv_atomic(merged, FUND_PATH)
    return len(new), {
        "latest_date": _latest_value(merged.rename(columns={"founded_date": "trade_date"}), "trade_date"),
        "total_cached_rows": len(merged),
        "note": settings.get("fund_subscription", {}).get("amount_note"),
    }


def _load_status(settings: dict[str, Any]) -> dict[str, Any]:
    status: dict[str, Any] = {"app_version": settings["app"]["version"], "datasets": {}}
    if STATUS_PATH.exists():
        try:
            import json
            loaded = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                status.update(loaded)
                status.setdefault("datasets", {})
        except Exception:
            pass
    status["app_version"] = settings["app"]["version"]
    status["updated_at"] = _now_iso()
    return status


def update_selected(mode: str = "all") -> dict[str, Any]:
    ensure_dirs()
    settings = load_settings()
    status = _load_status(settings)
    status["last_update_mode"] = mode

    tasks: dict[str, list[tuple[str, Callable[[], tuple[int, dict[str, Any]]]]]] = {
        "macro": [
            ("macro", lambda: update_macro(settings)),
            ("liquidity", lambda: update_liquidity(settings)),
        ],
        "global": [
            ("market", lambda: update_market(settings)),
            ("global_macro", lambda: update_global_macro(settings)),
            ("deviation", lambda: update_deviation(settings)),
        ],
        "close": [
            ("market", lambda: update_market(settings)),
            ("valuation", lambda: update_valuation(settings)),
            ("sentiment", lambda: update_sentiment(settings)),
            ("deviation", lambda: update_deviation(settings)),
        ],
        "snapshot": [
            ("sentiment", lambda: update_sentiment(settings)),
        ],
        "deviation": [("deviation", lambda: update_deviation(settings))],
        "fund": [("fund_subscription", lambda: update_fund(settings))],
        "evening": [
            ("macro", lambda: update_macro(settings)),
            ("liquidity", lambda: update_liquidity(settings)),
            ("fund_subscription", lambda: update_fund(settings)),
        ],
        "all": [
            ("macro", lambda: update_macro(settings)),
            ("liquidity", lambda: update_liquidity(settings)),
            ("market", lambda: update_market(settings)),
            ("global_macro", lambda: update_global_macro(settings)),
            ("valuation", lambda: update_valuation(settings)),
            ("sentiment", lambda: update_sentiment(settings)),
            ("deviation", lambda: update_deviation(settings)),
            ("fund_subscription", lambda: update_fund(settings)),
        ],
    }
    if mode not in tasks:
        raise ValueError(f"Unsupported update mode: {mode}")

    for name, task in tasks[mode]:
        status["datasets"][name] = _run(task)

    cache_map = {
        "macro": (MACRO_PATH, "month"),
        "liquidity": (LIQUIDITY_PATH, "trade_date"),
        "market": (MARKET_PATH, "trade_date"),
        "global_macro": (GLOBAL_MACRO_PATH, "trade_date"),
        "valuation": (VALUATION_PATH, "trade_date"),
        "crowding": (CROWDING_PATH, "trade_date"),
        "breadth": (BREADTH_PATH, "trade_date"),
        "leverage": (LEVERAGE_PATH, "trade_date"),
        "deviation": (DEVIATION_PATH, "trade_date"),
        "fund_subscription": (FUND_PATH, "founded_date"),
    }
    available = 0
    for dataset, (path, date_col) in cache_map.items():
        cached = read_csv_safe(path)
        if not cached.empty:
            available += 1
            info = status["datasets"].setdefault(dataset, {"status": "cached", "rows": 0})
            latest_cached_date = _latest_value(cached, date_col)
            info["cached_rows"] = len(cached)
            info["latest_date"] = latest_cached_date or info.get("latest_date")
            if info.get("status") == "failed":
                info["serving_cached_data"] = True

    ran = [status["datasets"].get(name, {}) for name, _ in tasks[mode]]
    success_count = sum(1 for x in ran if x.get("status") in {"success", "partial"})
    if success_count == len(ran):
        status["overall_status"] = "success"
    elif success_count > 0 or available > 0:
        status["overall_status"] = "partial"
    else:
        status["overall_status"] = "failed"

    write_json(STATUS_PATH, status)
    if success_count == 0 and available == 0:
        raise RuntimeError(f"更新失败且无历史缓存: {status['datasets']}")
    return status


def update_all() -> dict[str, Any]:
    return update_selected("all")


if __name__ == "__main__":
    print(update_all())
