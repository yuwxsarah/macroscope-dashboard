from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.update_ashare_daily import (  # noqa: E402
    BREADTH_PATH,
    CROWDING_PATH,
    LEVERAGE_PATH,
    fetch_margin_history,
    fetch_universe,
    read_csv_safe,
    refresh_breadth,
    refresh_crowding,
    refresh_leverage,
    summarize_histories,
    update_status,
    write_csv_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill A-share crowding and breadth history")
    parser.add_argument("--start", required=True, help="开始日期 YYYYMMDD")
    parser.add_argument("--end", default="", help="结束日期 YYYYMMDD；留空为今天")
    parser.add_argument("--batch-size", type=int, default=100, help="兼容GitHub工作流输入；本地用于限制最大并发")
    parser.add_argument("--max-workers", type=int, default=16, help="东方财富日线并发数")
    parser.add_argument("--top-fraction", type=float, default=0.05, help="拥挤度成交额头部比例，默认5%")
    return parser.parse_args()


def normalize_date(value: str, fallback_today: bool = False) -> str:
    if not value and fallback_today:
        return pd.Timestamp.now(tz=ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"日期格式不正确: {value}")
    return parsed.strftime("%Y%m%d")


def main() -> None:
    args = parse_args()
    start = normalize_date(args.start)
    end = normalize_date(args.end, fallback_today=True)
    if start > end:
        raise ValueError(f"开始日期不能晚于结束日期: {start} > {end}")
    if not 0 < args.top_fraction <= 1:
        raise ValueError("--top-fraction 必须在 (0, 1] 内")

    workers = max(1, min(int(args.max_workers), int(args.batch_size)))
    window_start = pd.to_datetime(start, format="%Y%m%d")

    universe = fetch_universe()
    if len(universe) < 4500:
        raise RuntimeError(f"A股代码池过小：{len(universe)}")

    print(f"代码池：{len(universe)} 只；回填区间：{start}-{end}；并发：{workers}", flush=True)
    history, failures = summarize_histories(universe, start, end, workers, args.top_fraction)
    if history.empty:
        raise RuntimeError("没有成功统计出任何历史交易拥挤度")

    breadth = refresh_breadth(read_csv_safe(BREADTH_PATH), history, universe, window_start)
    write_csv_atomic(breadth, BREADTH_PATH)

    crowding = refresh_crowding(read_csv_safe(CROWDING_PATH), history, window_start, args.top_fraction)
    write_csv_atomic(crowding, CROWDING_PATH)

    days = max(45, (pd.to_datetime(end, format="%Y%m%d") - window_start).days + 15)
    margin = fetch_margin_history(page_size=int(days))
    leverage = refresh_leverage(breadth, margin, window_start)
    write_csv_atomic(leverage, LEVERAGE_PATH)

    update_status(breadth, leverage, crowding, len(universe), history, failures)
    print(json.dumps({
        "crowding_rows": len(crowding),
        "breadth_rows": len(breadth),
        "leverage_rows": len(leverage),
        "history_rows": len(history),
        "universe_size": len(universe),
        "failed_symbols": len(failures),
        "date_range": [str(crowding["trade_date"].min()), str(crowding["trade_date"].max())],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
