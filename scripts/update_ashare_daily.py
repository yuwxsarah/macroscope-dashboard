from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"
CROWDING_PATH = DATA_DIR / "crowding.csv"
BREADTH_PATH = DATA_DIR / "breadth.csv"
LEVERAGE_PATH = DATA_DIR / "leverage.csv"
UNIVERSE_PATH = DATA_DIR / "a_share_universe.csv"
STATUS_PATH = DATA_DIR / "status.json"
CHOICE_MARKET_CAP_PATH = DATA_DIR / "两市总市值.xlsx"
CHOICE_MARKET_CAP_SOURCE = "Choice：A股总市值（亿元换算为万亿元）"

A_SHARE_PREFIXES = (
    "000", "001", "002", "003", "300", "301",
    "600", "601", "603", "605", "688", "689",
)

EASTMONEY_UT = "bd1d9ddb04089700cf9c27f6f7426281"
CLIST_FIELDS = "f12,f13,f14,f2,f3,f6,f116"
CLIST_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def date_key(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        text = "".join(ch for ch in str(value) if ch.isdigit())
        return text[:8] if len(text) >= 8 else None
    return parsed.strftime("%Y%m%d")


def read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"trade_date": str, "fund_code": str})
    except Exception:
        return pd.DataFrame()


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(temp, index=False, encoding="utf-8-sig")
    temp.replace(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def curl_json(url: str, tries: int = 10) -> dict[str, Any]:
    """Fetch JSON through curl.

    Eastmoney sometimes closes Python TLS connections from this desktop
    environment, while system curl is noticeably more stable. GitHub Actions also
    provides curl, so this keeps local and scheduled refreshes using one path.
    """
    last_error: Exception | None = None
    for attempt in range(tries):
        try:
            output = subprocess.check_output(
                ["curl", "-fsSL", "--connect-timeout", "8", "--max-time", "25", url],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return json.loads(output)
        except Exception as exc:
            last_error = exc
            time.sleep(min(3.0, 0.45 * (attempt + 1)))
    raise RuntimeError(f"公开接口连续失败: {last_error!r}")


def clist_url(page: int, page_size: int = 100) -> str:
    return (
        "https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn={page}&pz={page_size}&po=1&np=1&ut={EASTMONEY_UT}"
        f"&fltt=2&invt=2&fid=f3&fs={CLIST_FS}&fields={CLIST_FIELDS}"
    )


def fetch_universe() -> list[dict[str, Any]]:
    first = curl_json(clist_url(1), tries=15)
    total = int(first.get("data", {}).get("total") or 0)
    pages = max(1, math.ceil(total / 100))
    raw_rows = list(first.get("data", {}).get("diff") or [])

    for page in range(2, pages + 1):
        payload = curl_json(clist_url(page), tries=15)
        raw_rows.extend(payload.get("data", {}).get("diff") or [])

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        code = str(item.get("f12", "")).strip().zfill(6)
        if not code.startswith(A_SHARE_PREFIXES) or code in seen:
            continue
        try:
            market = int(item.get("f13"))
        except Exception:
            continue
        if market not in (0, 1):
            continue
        seen.add(code)
        rows.append({
            "code": code,
            "market": market,
            "exchange": "SH" if market == 1 else "SZ",
            "name": str(item.get("f14", "")),
            "spot_amount_yuan": pd.to_numeric(item.get("f6"), errors="coerce"),
            "spot_market_cap_yuan": pd.to_numeric(item.get("f116"), errors="coerce"),
            "spot_pct_change": pd.to_numeric(item.get("f3"), errors="coerce"),
        })
    return rows


def fetch_stock_history(item: dict[str, Any], start: str, end: str) -> tuple[str, list[dict[str, Any]], str | None]:
    secid = f"{item['market']}.{item['code']}"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=0&beg={start}&end={end}"
    )
    try:
        payload = curl_json(url, tries=6)
        klines = payload.get("data", {}).get("klines") or []
        rows: list[dict[str, Any]] = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 9:
                continue
            trade_date = parts[0].replace("-", "")
            if trade_date < start or trade_date > end:
                continue
            try:
                amount_yuan = float(parts[6])
                pct_change = float(parts[8])
            except Exception:
                continue
            rows.append({
                "trade_date": trade_date,
                "amount_yuan": amount_yuan,
                "pct_change": pct_change,
            })
        return str(item["code"]), rows, None
    except Exception as exc:
        return str(item["code"]), [], repr(exc)


def fetch_margin_history(page_size: int = 45) -> pd.DataFrame:
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        "?reportName=RPTA_RZRQ_LSHJ&columns=ALL&source=WEB&client=WEB"
        f"&sortColumns=dim_date&sortTypes=-1&pageNumber=1&pageSize={page_size}"
    )
    payload = curl_json(url, tries=10)
    rows = payload.get("result", {}).get("data") or []
    output = []
    for row in rows:
        trade_date = date_key(row.get("DIM_DATE"))
        margin = pd.to_numeric(row.get("RZRQYE"), errors="coerce")
        if not trade_date or pd.isna(margin):
            continue
        output.append({
            "trade_date": str(trade_date),
            "margin_balance_trillion": float(margin) / 1e12,
            "source": "东方财富融资融券历史合计",
            "note": "两融余额来自东方财富融资融券历史合计；总市值优先沿用Choice/日度快照口径。",
        })
    return pd.DataFrame(output).sort_values("trade_date")


def read_choice_market_cap() -> pd.DataFrame:
    """Read the local Choice A-share total-market-cap workbook when present.

    The workbook exported by Choice contains metadata rows before the actual
    daily observations. Real data rows have a numeric serial number in the first
    column, a date in the second column, and A-share total market cap in 亿元 in
    the third column.
    """
    if not CHOICE_MARKET_CAP_PATH.exists():
        return pd.DataFrame(columns=[
            "trade_date", "choice_total_market_cap_trillion", "choice_market_cap_source",
        ])
    try:
        raw = pd.read_excel(CHOICE_MARKET_CAP_PATH, sheet_name="指标", header=None)
    except Exception as exc:
        print(f"读取两市总市值Excel失败：{exc!r}", flush=True)
        return pd.DataFrame(columns=[
            "trade_date", "choice_total_market_cap_trillion", "choice_market_cap_source",
        ])

    if raw.shape[1] < 3:
        return pd.DataFrame(columns=[
            "trade_date", "choice_total_market_cap_trillion", "choice_market_cap_source",
        ])

    serial = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    dates = raw.iloc[:, 1].map(date_key)
    values = pd.to_numeric(raw.iloc[:, 2], errors="coerce")
    output = pd.DataFrame({
        "trade_date": dates,
        "choice_total_market_cap_trillion": values / 10000,
    })
    output = output[serial.notna() & output["trade_date"].notna() & output["choice_total_market_cap_trillion"].notna()].copy()
    output = output.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    output["choice_market_cap_source"] = CHOICE_MARKET_CAP_SOURCE
    return output.reset_index(drop=True)


def fill_choice_market_cap(frame: pd.DataFrame) -> pd.DataFrame:
    """Fill total market cap from the local Choice workbook.

    Choice is used as the preferred historical denominator for both broad
    turnover and margin-to-market-cap ratios. For dates beyond the workbook's
    latest observation, the existing daily snapshot denominator is retained.
    """
    if frame.empty:
        return frame
    cap_ref = read_choice_market_cap()
    if cap_ref.empty:
        return frame

    merged = frame.copy()
    merged["trade_date"] = merged["trade_date"].astype(str)
    if "total_market_cap_trillion" not in merged.columns:
        merged["total_market_cap_trillion"] = np.nan
    if "market_cap_source" not in merged.columns:
        merged["market_cap_source"] = ""

    merged = merged.merge(cap_ref, on="trade_date", how="left")
    choice_cap = pd.to_numeric(merged["choice_total_market_cap_trillion"], errors="coerce")
    has_choice_cap = choice_cap.notna()
    merged.loc[has_choice_cap, "total_market_cap_trillion"] = choice_cap[has_choice_cap]
    merged.loc[has_choice_cap, "market_cap_source"] = merged.loc[has_choice_cap, "choice_market_cap_source"]
    return merged.drop(columns=["choice_total_market_cap_trillion", "choice_market_cap_source"])


def fetch_exchange_market_cap(trade_date: str) -> tuple[float | None, str]:
    try:
        from src.extended_providers import ChinaSentimentProvider

        cap_yuan, source = ChinaSentimentProvider()._official_market_cap(trade_date)
        if cap_yuan is None or not np.isfinite(cap_yuan) or cap_yuan <= 1e12:
            return None, source
        return float(cap_yuan) / 1e12, source
    except Exception as exc:
        return None, f"交易所市场总貌失败：{exc!r}"


def fill_latest_exchange_market_cap(frame: pd.DataFrame) -> pd.DataFrame:
    """Use exchange market-summary tables only for the newest date still blank."""
    if frame.empty or "trade_date" not in frame.columns:
        return frame
    merged = frame.copy()
    if "total_market_cap_trillion" not in merged.columns:
        merged["total_market_cap_trillion"] = np.nan
    if "market_cap_source" not in merged.columns:
        merged["market_cap_source"] = ""

    latest_date = str(merged["trade_date"].astype(str).max())
    latest_mask = merged["trade_date"].astype(str) == latest_date
    needs_cap = latest_mask & pd.to_numeric(merged["total_market_cap_trillion"], errors="coerce").isna()
    if not needs_cap.any():
        return merged

    cap, source = fetch_exchange_market_cap(latest_date)
    if cap is not None:
        merged.loc[needs_cap, "total_market_cap_trillion"] = cap
        merged.loc[needs_cap, "market_cap_source"] = source
    else:
        print(source, flush=True)
    return merged


def current_trade_window(existing: pd.DataFrame, days: int) -> tuple[str, str, pd.Timestamp]:
    end = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None)
    if not existing.empty and "trade_date" in existing.columns:
        dates = pd.to_datetime(existing["trade_date"], format="%Y%m%d", errors="coerce").dropna()
        if not dates.empty and dates.max() > end:
            end = dates.max()
    start = end - pd.Timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), start


def summarize_histories(
    universe: list[dict[str, Any]],
    start: str,
    end: str,
    max_workers: int,
    top_fraction: float,
) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    amounts_by_date: dict[str, list[float]] = defaultdict(list)
    failures: list[tuple[str, str]] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_stock_history, item, start, end) for item in universe]
        for future in as_completed(futures):
            code, rows, error = future.result()
            completed += 1
            if error:
                failures.append((code, error))
            for row in rows:
                trade_date = str(row["trade_date"])
                pct = float(row["pct_change"])
                amount_yuan = float(row["amount_yuan"])
                counts[trade_date]["total_count"] += 1
                counts[trade_date]["amount_yuan"] += amount_yuan
                if amount_yuan > 0:
                    amounts_by_date[trade_date].append(amount_yuan)
                if pct > 0:
                    counts[trade_date]["up_count"] += 1
                elif pct < 0:
                    counts[trade_date]["down_count"] += 1
                else:
                    counts[trade_date]["flat_count"] += 1
            if completed % 500 == 0:
                print(f"已处理 {completed}/{len(universe)}，失败 {len(failures)}", flush=True)

    rows = []
    for trade_date, counter in sorted(counts.items()):
        if counter["total_count"] <= 0:
            continue
        daily_amounts = amounts_by_date.get(trade_date, [])
        amount_count = len(daily_amounts)
        top_count = max(1, math.ceil(amount_count * top_fraction)) if amount_count else 0
        top_amount = sum(sorted(daily_amounts, reverse=True)[:top_count]) if top_count else np.nan
        total_amount = float(counter["amount_yuan"])
        rows.append({
            "trade_date": trade_date,
            "up_count_new": int(counter["up_count"]),
            "down_count_new": int(counter["down_count"]),
            "flat_count_new": int(counter["flat_count"]),
            "total_count_new": int(counter["total_count"]),
            "stock_amount_trillion_new": total_amount / 1e12,
            "crowding_stock_count_new": int(amount_count),
            "crowding_top_count_new": int(top_count),
            "crowding_top_amount_trillion_new": float(top_amount) / 1e12 if np.isfinite(top_amount) else np.nan,
            "crowding_pct_new": float(top_amount) / total_amount * 100 if np.isfinite(top_amount) and total_amount > 0 else np.nan,
        })
    return pd.DataFrame(rows), failures


def refresh_crowding(
    existing: pd.DataFrame,
    history: pd.DataFrame,
    window_start: pd.Timestamp,
    top_fraction: float,
) -> pd.DataFrame:
    if not existing.empty:
        existing["trade_date"] = existing["trade_date"].astype(str)
    merged = existing.merge(history, on="trade_date", how="outer") if not existing.empty else history.copy()

    combine_map = {
        "stock_count": "crowding_stock_count_new",
        "top_count": "crowding_top_count_new",
        "top_amount_trillion": "crowding_top_amount_trillion_new",
        "total_amount_trillion": "stock_amount_trillion_new",
        "crowding_pct": "crowding_pct_new",
    }
    for col, new_col in combine_map.items():
        if col not in merged.columns:
            merged[col] = np.nan
        if new_col in merged.columns:
            merged[col] = merged[new_col].combine_first(merged[col])

    merged["top_fraction"] = pd.to_numeric(merged.get("top_fraction"), errors="coerce").fillna(top_fraction)
    source_prefix = "东方财富历史日线成交额统计交易拥挤度"
    if "source" not in merged.columns:
        merged["source"] = source_prefix
    merged["source"] = merged["source"].fillna("").astype(str).map(
        lambda value: source_prefix if not value else (value if source_prefix in value else f"{source_prefix}；{value}")
    )
    if "snapshot_kind" not in merged.columns:
        merged["snapshot_kind"] = ""
    merged["snapshot_kind"] = merged["snapshot_kind"].fillna("").replace("", "日度滚动窗口")

    cutoff = window_start.strftime("%Y%m%d")
    merged = merged[merged["trade_date"].astype(str) >= cutoff].copy()
    for column in ["stock_count", "top_count"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce").round().astype("Int64")
    keep = [
        "trade_date", "top_fraction", "stock_count", "top_count",
        "top_amount_trillion", "total_amount_trillion", "crowding_pct",
        "source", "snapshot_kind",
    ]
    for column in keep:
        if column not in merged.columns:
            merged[column] = np.nan
    return merged[keep].sort_values("trade_date").reset_index(drop=True)


def refresh_breadth(
    existing: pd.DataFrame,
    history: pd.DataFrame,
    universe: list[dict[str, Any]],
    window_start: pd.Timestamp,
) -> pd.DataFrame:
    merged = existing.merge(history, on="trade_date", how="outer") if not existing.empty else history.copy()

    for col in ["up_count", "down_count", "flat_count", "total_count"]:
        new_col = f"{col}_new"
        if col not in merged.columns:
            merged[col] = np.nan
        if new_col in merged.columns:
            merged[col] = merged[new_col].combine_first(merged[col])

    if "total_amount_trillion" not in merged.columns:
        merged["total_amount_trillion"] = np.nan
    if "stock_amount_trillion_new" in merged.columns:
        needs_amount = merged["total_amount_trillion"].isna()
        # Keep Choice exchange-level turnover where present; use stock-history
        # aggregation for new daily rows or old rows that were blank.
        merged.loc[needs_amount, "total_amount_trillion"] = merged.loc[needs_amount, "stock_amount_trillion_new"]

    merged = fill_choice_market_cap(merged)

    latest_history_date = str(history["trade_date"].max()) if not history.empty else None
    spot_cap = pd.to_numeric(pd.Series([item.get("spot_market_cap_yuan") for item in universe]), errors="coerce").dropna().sum()
    if latest_history_date and np.isfinite(spot_cap) and spot_cap > 1e12:
        if "total_market_cap_trillion" not in merged.columns:
            merged["total_market_cap_trillion"] = np.nan
        latest_mask = merged["trade_date"].astype(str) == latest_history_date
        needs_cap = latest_mask & merged["total_market_cap_trillion"].isna()
        merged.loc[needs_cap, "total_market_cap_trillion"] = float(spot_cap) / 1e12
        if "market_cap_source" not in merged.columns:
            merged["market_cap_source"] = ""
        merged.loc[needs_cap, "market_cap_source"] = "东方财富实时行情总市值汇总"

    merged = fill_latest_exchange_market_cap(merged)

    if "broad_turnover_pct" not in merged.columns:
        merged["broad_turnover_pct"] = np.nan
    amount = pd.to_numeric(merged["total_amount_trillion"], errors="coerce")
    cap = pd.to_numeric(merged.get("total_market_cap_trillion"), errors="coerce")
    ratio = amount / cap * 100
    merged["broad_turnover_pct"] = ratio.combine_first(pd.to_numeric(merged["broad_turnover_pct"], errors="coerce"))

    source_prefix = "东方财富历史日线统计涨跌家数"
    if "source" not in merged.columns:
        merged["source"] = source_prefix
    merged["source"] = merged["source"].fillna("").astype(str).map(
        lambda value: source_prefix if not value else (value if source_prefix in value else f"{source_prefix}；{value}")
    )
    if "snapshot_kind" not in merged.columns:
        merged["snapshot_kind"] = ""
    merged["snapshot_kind"] = merged["snapshot_kind"].fillna("").replace("", "日度滚动窗口")

    cutoff = window_start.strftime("%Y%m%d")
    merged = merged[merged["trade_date"].astype(str) >= cutoff].copy()
    for column in ["up_count", "down_count", "flat_count", "total_count"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce").round().astype("Int64")
    keep = [
        "trade_date", "up_count", "down_count", "flat_count", "total_count",
        "total_amount_trillion", "total_market_cap_trillion", "broad_turnover_pct",
        "source", "market_cap_source", "snapshot_kind",
    ]
    for column in keep:
        if column not in merged.columns:
            merged[column] = np.nan
    return merged[keep].sort_values("trade_date").reset_index(drop=True)


def refresh_leverage(breadth: pd.DataFrame, margin: pd.DataFrame, window_start: pd.Timestamp) -> pd.DataFrame:
    old = read_csv_safe(LEVERAGE_PATH)
    if old.empty:
        old = pd.DataFrame(columns=[
            "trade_date", "margin_balance_trillion", "total_market_cap_trillion",
            "margin_to_market_cap_pct", "source", "note",
        ])
    old["trade_date"] = old["trade_date"].astype(str)

    cap = breadth[["trade_date", "total_market_cap_trillion"]].copy()
    margin_new = margin.merge(cap, on="trade_date", how="left") if not margin.empty else pd.DataFrame()
    if not margin_new.empty:
        margin_new["margin_to_market_cap_pct"] = (
            pd.to_numeric(margin_new["margin_balance_trillion"], errors="coerce")
            / pd.to_numeric(margin_new["total_market_cap_trillion"], errors="coerce") * 100
        )
        margin_new["note"] = (
            "两融余额/市场总市值 = 两融余额 ÷ A股总市值；"
            "两融来自东方财富历史合计，总市值优先沿用Choice或日度快照。"
        )

    combined = pd.concat([old, margin_new], ignore_index=True, sort=False)
    combined = combined.drop_duplicates("trade_date", keep="last")
    cutoff = window_start.strftime("%Y%m%d")
    combined = combined[combined["trade_date"].astype(str) >= cutoff].copy()
    combined = combined.sort_values("trade_date").reset_index(drop=True)
    return combined[[
        "trade_date", "margin_balance_trillion", "total_market_cap_trillion",
        "margin_to_market_cap_pct", "source", "note",
    ]]


def fill_existing_market_cap_series() -> dict[str, Any]:
    """Repair local CSVs from the Choice market-cap workbook without network calls."""
    breadth = read_csv_safe(BREADTH_PATH)
    if not breadth.empty:
        breadth["trade_date"] = breadth["trade_date"].astype(str)
        breadth = fill_choice_market_cap(breadth)
        breadth = fill_latest_exchange_market_cap(breadth)
        amount = pd.to_numeric(breadth["total_amount_trillion"], errors="coerce")
        cap = pd.to_numeric(breadth["total_market_cap_trillion"], errors="coerce")
        breadth["broad_turnover_pct"] = amount / cap * 100
        keep = [
            "trade_date", "up_count", "down_count", "flat_count", "total_count",
            "total_amount_trillion", "total_market_cap_trillion", "broad_turnover_pct",
            "source", "market_cap_source", "snapshot_kind",
        ]
        for column in keep:
            if column not in breadth.columns:
                breadth[column] = np.nan
        write_csv_atomic(breadth[keep].sort_values("trade_date"), BREADTH_PATH)

    leverage = read_csv_safe(LEVERAGE_PATH)
    if not leverage.empty and not breadth.empty:
        leverage["trade_date"] = leverage["trade_date"].astype(str)
        cap = breadth[["trade_date", "total_market_cap_trillion"]].copy()
        leverage = leverage.drop(columns=["total_market_cap_trillion"], errors="ignore").merge(cap, on="trade_date", how="left")
        margin = pd.to_numeric(leverage["margin_balance_trillion"], errors="coerce")
        market_cap = pd.to_numeric(leverage["total_market_cap_trillion"], errors="coerce")
        leverage["margin_to_market_cap_pct"] = margin / market_cap * 100
        keep = [
            "trade_date", "margin_balance_trillion", "total_market_cap_trillion",
            "margin_to_market_cap_pct", "source", "note",
        ]
        for column in keep:
            if column not in leverage.columns:
                leverage[column] = np.nan
        write_csv_atomic(leverage[keep].sort_values("trade_date"), LEVERAGE_PATH)

    breadth_after = read_csv_safe(BREADTH_PATH)
    leverage_after = read_csv_safe(LEVERAGE_PATH)
    return {
        "breadth_rows": len(breadth_after),
        "breadth_ratio_rows": int(pd.to_numeric(breadth_after.get("broad_turnover_pct"), errors="coerce").notna().sum()) if not breadth_after.empty else 0,
        "leverage_rows": len(leverage_after),
        "leverage_ratio_rows": int(pd.to_numeric(leverage_after.get("margin_to_market_cap_pct"), errors="coerce").notna().sum()) if not leverage_after.empty else 0,
    }


def update_status(
    breadth: pd.DataFrame,
    leverage: pd.DataFrame,
    crowding: pd.DataFrame,
    universe_size: int,
    history: pd.DataFrame,
    failures: list[tuple[str, str]],
) -> None:
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            status = {}
    except Exception:
        status = {}
    status.setdefault("datasets", {})
    updated_at = now_iso()
    status["updated_at"] = updated_at
    status["datasets"]["a_share_universe"] = {
        "status": "success",
        "rows": universe_size,
        "cached_rows": universe_size,
        "latest_date": str(breadth["trade_date"].max()) if not breadth.empty else None,
        "source": "东方财富行情中心代码列表",
    }
    status["datasets"]["breadth"] = {
        "status": "success",
        "rows": len(breadth),
        "cached_rows": len(breadth),
        "total_cached_rows": len(breadth),
        "latest_date": str(breadth["trade_date"].max()) if not breadth.empty else None,
        "source_details": {
            "breadth_counts": {
                "status": "success",
                "rows": len(history),
                "date_range": (
                    f"{history['trade_date'].min()}-{history['trade_date'].max()}"
                    if not history.empty else None
                ),
                "universe_size": universe_size,
                "failed_symbols": len(failures),
                "source": "东方财富历史日线接口 push2his.eastmoney.com",
                "method": "逐只股票取日线涨跌幅；>0上涨，<0下跌，=0平盘；停牌无日线者不计入当日总数。",
            },
            "turnover": {
                "status": "success",
                "source": "Choice交易所口径优先；缺口用东方财富个股历史成交额汇总补齐。",
            },
        },
    }
    status["datasets"]["crowding"] = {
        "status": "success" if not crowding.empty else "partial",
        "rows": len(crowding),
        "cached_rows": len(crowding),
        "total_cached_rows": len(crowding),
        "latest_date": str(crowding["trade_date"].max()) if not crowding.empty else None,
        "source_details": {
            "crowding_history": {
                "status": "success",
                "rows": len(history),
                "date_range": (
                    f"{history['trade_date'].min()}-{history['trade_date'].max()}"
                    if not history.empty else None
                ),
                "universe_size": universe_size,
                "failed_symbols": len(failures),
                "source": "东方财富历史日线接口 push2his.eastmoney.com",
                "method": "每日成交额前5%股票成交额合计 ÷ 当日A股全部股票成交额。",
            },
        },
    }
    status["datasets"]["leverage"] = {
        "status": "success" if not leverage.empty else "partial",
        "rows": len(leverage),
        "cached_rows": len(leverage),
        "total_cached_rows": len(leverage),
        "latest_date": str(leverage["trade_date"].max()) if not leverage.empty else None,
        "source_details": {
            "margin_balance": {
                "status": "success",
                "source": "东方财富融资融券历史合计 RPTA_RZRQ_LSHJ",
            },
            "market_cap": {
                "status": "cached",
                "source": "Choice A股总市值或东方财富当日总市值汇总",
            },
        },
    }
    if "sentiment" in status["datasets"] and isinstance(status["datasets"]["sentiment"], dict):
        status["datasets"]["sentiment"]["crowding_cached_rows"] = len(crowding)
        status["datasets"]["sentiment"]["latest_date"] = str(crowding["trade_date"].max()) if not crowding.empty else status["datasets"]["sentiment"].get("latest_date")
        status["datasets"]["sentiment"]["breadth_cached_rows"] = len(breadth)
        status["datasets"]["sentiment"]["breadth_latest_date"] = str(breadth["trade_date"].max()) if not breadth.empty else None
        status["datasets"]["sentiment"]["leverage_cached_rows"] = len(leverage)
        status["datasets"]["sentiment"]["leverage_latest_date"] = str(leverage["trade_date"].max()) if not leverage.empty else None

    statuses = [
        str(value.get("status", "empty"))
        for value in status.get("datasets", {}).values()
        if isinstance(value, dict)
    ]
    status["overall_status"] = (
        "failed" if statuses and all(item == "failed" for item in statuses)
        else "partial" if "failed" in statuses
        else "success"
    )
    write_json(STATUS_PATH, status)


def refresh_ashare_daily(days: int, max_workers: int) -> dict[str, Any]:
    existing = read_csv_safe(BREADTH_PATH)
    if not existing.empty:
        existing["trade_date"] = existing["trade_date"].astype(str)
    start, end, window_start = current_trade_window(existing, days)

    universe = fetch_universe()
    if len(universe) < 4500:
        raise RuntimeError(f"A股代码池过小：{len(universe)}")

    print(f"代码池：{len(universe)} 只；刷新区间：{start}-{end}", flush=True)
    pd.DataFrame({
        "code": [item["code"] for item in universe],
        "exchange": [item["exchange"] for item in universe],
        "source": "东方财富行情中心代码列表",
        "updated_at": now_iso(),
    }).to_csv(UNIVERSE_PATH, index=False, encoding="utf-8-sig")

    top_fraction = 0.05
    history, failures = summarize_histories(universe, start, end, max_workers, top_fraction)
    if history.empty:
        raise RuntimeError("没有成功统计出任何涨跌家数")
    if float(history["total_count_new"].median()) < 4500:
        raise RuntimeError(f"历史统计有效股票数偏低：中位数 {history['total_count_new'].median()}")

    breadth = refresh_breadth(existing, history, universe, window_start)
    write_csv_atomic(breadth, BREADTH_PATH)

    crowding_existing = read_csv_safe(CROWDING_PATH)
    crowding = refresh_crowding(crowding_existing, history, window_start, top_fraction)
    write_csv_atomic(crowding, CROWDING_PATH)

    margin = fetch_margin_history(page_size=max(45, days + 10))
    leverage = refresh_leverage(breadth, margin, window_start)
    write_csv_atomic(leverage, LEVERAGE_PATH)

    update_status(breadth, leverage, crowding, len(universe), history, failures)
    return {
        "crowding_rows": len(crowding),
        "breadth_rows": len(breadth),
        "leverage_rows": len(leverage),
        "history_rows": len(history),
        "universe_size": len(universe),
        "failed_symbols": len(failures),
        "date_range": [str(breadth["trade_date"].min()), str(breadth["trade_date"].max())],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh rolling A-share breadth, crowding, turnover, and leverage series")
    parser.add_argument("--days", type=int, default=95, help="滚动窗口自然日数，默认95天")
    parser.add_argument("--max-workers", type=int, default=16, help="东方财富日线并发数，默认16")
    parser.add_argument(
        "--fill-local-market-cap-only",
        action="store_true",
        help="补齐本地CSV中的总市值比例线；优先用Choice Excel，最新缺口尝试交易所市场总貌，不重新刷新股票日线",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.fill_local_market_cap_only:
        result = fill_existing_market_cap_series()
    else:
        result = refresh_ashare_daily(days=args.days, max_workers=args.max_workers)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
