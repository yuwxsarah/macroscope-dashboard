from __future__ import annotations

import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "global_macro.csv"
STATUS_PATH = ROOT / "data" / "status.json"

SERIES = {
    "DGS2": "美国2年期国债收益率",
    "DGS10": "美国10年期国债收益率",
}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
START_DATES = {"DGS2": "1976-06-01", "DGS10": "1962-01-02"}


def headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (compatible; MacroScopePublic/7.1; treasury history backfill)",
        "Accept": "text/csv,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def five_year_windows(start: str, end: str) -> list[tuple[str, str]]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    windows: list[tuple[str, str]] = []
    cursor = start_ts
    while cursor <= end_ts:
        window_end = min(cursor + pd.DateOffset(years=5) - pd.Timedelta(days=1), end_ts)
        windows.append((cursor.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d")))
        cursor = window_end + pd.Timedelta(days=1)
    return windows


def fetch_chunk(session: requests.Session, series: str, start: str, end: str) -> pd.DataFrame:
    response = session.get(
        FRED_CSV,
        params={"id": series, "cosd": start, "coed": end},
        headers=headers(),
        timeout=60,
    )
    response.raise_for_status()
    raw = pd.read_csv(io.StringIO(response.text))
    date_col = "observation_date" if "observation_date" in raw.columns else "DATE" if "DATE" in raw.columns else None
    if date_col is None or series not in raw.columns:
        raise RuntimeError(f"FRED返回字段异常: {list(raw.columns)}")
    out = pd.DataFrame({
        "trade_date": pd.to_datetime(raw[date_col], errors="coerce").dt.strftime("%Y%m%d"),
        "value_pct": pd.to_numeric(raw[series], errors="coerce"),
    }).dropna(subset=["trade_date", "value_pct"])
    return out


def fetch_series(series: str, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
    session = requests.Session()
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    windows = five_year_windows(start, end)
    for index, (window_start, window_end) in enumerate(windows, start=1):
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                frame = fetch_chunk(session, series, window_start, window_end)
                frames.append(frame)
                print(f"{series} [{index}/{len(windows)}] {window_start}~{window_end}: {len(frame)} rows")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                wait = min(2 ** attempt, 20)
                print(f"{series} {window_start}~{window_end} attempt {attempt} failed: {exc!r}; sleep {wait}s")
                time.sleep(wait)
        if last_error is not None:
            errors.append(f"{window_start}~{window_end}: {last_error!r}")
    if not frames:
        raise RuntimeError(f"{series}所有历史区间均抓取失败: {errors}")
    out = pd.concat(frames, ignore_index=True).drop_duplicates("trade_date", keep="last")
    out["series"] = series
    out["name"] = SERIES[series]
    out["unit"] = "%"
    out["source"] = "美联储H.15 / FRED历史数据"
    out = out[["trade_date", "series", "name", "value_pct", "unit", "source"]]
    return out.sort_values("trade_date"), errors


def read_existing() -> pd.DataFrame:
    if not DATA_PATH.exists():
        return pd.DataFrame(columns=["trade_date", "series", "name", "value_pct", "unit", "source"])
    try:
        return pd.read_csv(DATA_PATH, dtype={"trade_date": str, "series": str})
    except Exception:
        return pd.DataFrame(columns=["trade_date", "series", "name", "value_pct", "unit", "source"])


def update_status(total_rows: int, latest_date: str, details: dict[str, Any]) -> None:
    payload: dict[str, Any] = {}
    if STATUS_PATH.exists():
        try:
            payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    payload.setdefault("datasets", {})
    payload["datasets"]["global_macro"] = {
        "status": "success",
        "rows": total_rows,
        "cached_rows": total_rows,
        "latest_date": latest_date,
        "source_details": details,
        "note": "美国2年和10年期国债收益率历史已回填；日常工作流继续追加最新值。",
    }
    payload["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    statuses = [str(v.get("status", "empty")) for v in payload.get("datasets", {}).values() if isinstance(v, dict)]
    payload["overall_status"] = "failed" if statuses and all(x == "failed" for x in statuses) else "partial" if "failed" in statuses else "success"
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    end = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    fetched: list[pd.DataFrame] = []
    details: dict[str, Any] = {}
    for series in ["DGS2", "DGS10"]:
        frame, errors = fetch_series(series, START_DATES[series], end)
        fetched.append(frame)
        details[series] = {
            "status": "success",
            "rows": len(frame),
            "start_date": str(frame["trade_date"].min()),
            "latest_date": str(frame["trade_date"].max()),
            "partial_errors": errors,
        }

    new = pd.concat(fetched, ignore_index=True)
    old = read_existing()
    merged = pd.concat([old, new], ignore_index=True)
    merged["trade_date"] = merged["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(8)
    merged["value_pct"] = pd.to_numeric(merged["value_pct"], errors="coerce")
    merged = merged.dropna(subset=["trade_date", "series", "value_pct"])
    merged = merged.drop_duplicates(["trade_date", "series"], keep="last")
    merged = merged.sort_values(["series", "trade_date"]).reset_index(drop=True)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(DATA_PATH, index=False, encoding="utf-8-sig")
    latest_date = str(merged["trade_date"].max()) if not merged.empty else ""
    update_status(len(merged), latest_date, details)
    print(f"Saved {len(merged)} rows to {DATA_PATH}")


if __name__ == "__main__":
    main()
