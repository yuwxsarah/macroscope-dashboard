from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils import DATA_DIR, read_csv_safe, write_csv_atomic, write_json  # noqa: E402

try:
    import akshare as ak
except Exception:  # pragma: no cover
    ak = None

MESSAGE_PATH = DATA_DIR / "messages.json"
MARKET_MESSAGES_PATH = DATA_DIR / "market_messages.csv"
UNIVERSE_PATH = DATA_DIR / "a_share_universe.csv"
BEIJING = ZoneInfo("Asia/Shanghai")
MESSAGE_SCHEDULE = ["06:20", "08:00", "10:10", "11:40", "15:20", "16:40", "17:00", "18:40", "19:10"]
COLUMNS = [
    "published_at", "category", "title", "summary", "source", "source_url",
    "symbol", "stock_name", "importance", "status", "source_type", "item_id",
]


def stable_id(*values: object) -> str:
    raw = "|".join(str(value or "") for value in values)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def simple_text(value: object) -> str:
    return " ".join(str(value or "").split())


def date_key(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    timestamp = pd.to_datetime(text, errors="coerce")
    if pd.isna(timestamp):
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[:8] if len(digits) >= 8 else ""
    return timestamp.strftime("%Y%m%d")


def display_date(value: object) -> str:
    key = date_key(value)
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}" if len(key) == 8 else simple_text(value)


def code_symbol(code: object, exchange: object = None) -> str:
    raw = str(code or "").strip().zfill(6)
    if not raw or raw == "000000" or not raw.isdigit():
        return ""
    suffix = str(exchange or "").strip().upper()
    if suffix not in {"SH", "SZ", "BJ"}:
        if raw.startswith(("6", "9")):
            suffix = "SH"
        elif raw.startswith(("4", "8")):
            suffix = "BJ"
        else:
            suffix = "SZ"
    return f"{raw}.{suffix}"


def load_messages() -> dict:
    if not MESSAGE_PATH.exists():
        return {
            "timezone": "Asia/Shanghai",
            "schedule": MESSAGE_SCHEDULE,
            "watchlist_defaults": [],
            "sources": [],
            "items": [],
            "report_templates": [],
        }
    return json.loads(MESSAGE_PATH.read_text(encoding="utf-8"))


def read_universe(limit: int = 0) -> list[dict]:
    frame = read_csv_safe(UNIVERSE_PATH)
    if frame.empty:
        return []
    name_map: dict[str, str] = {}
    market = read_csv_safe(DATA_DIR / "market.csv")
    if not market.empty and {"symbol", "name"}.issubset(market.columns):
        latest_names = market.dropna(subset=["symbol", "name"]).drop_duplicates("symbol", keep="last")
        name_map = {str(row["symbol"]): simple_text(row["name"]) for _, row in latest_names.iterrows()}
    rows = []
    for row in frame.to_dict(orient="records"):
        symbol = code_symbol(row.get("code"), row.get("exchange"))
        if not symbol:
            continue
        rows.append({
            "code": symbol.split(".")[0],
            "exchange": symbol.split(".")[1],
            "symbol": symbol,
            "name": name_map.get(symbol, ""),
        })
    return rows[:limit] if limit and limit > 0 else rows


def quiet_akshare_call(func, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        return func(*args, **kwargs)


def fetch_notice_for_date(day: datetime) -> tuple[list[dict], str | None]:
    if ak is None:
        return [], "akshare is not available"
    key = day.strftime("%Y%m%d")
    try:
        frame = quiet_akshare_call(ak.stock_notice_report, symbol="全部", date=key)
    except Exception as exc:
        return [], f"{key}: {exc!r}"
    if frame is None or frame.empty:
        return [], None
    rows: list[dict] = []
    for raw in frame.to_dict(orient="records"):
        symbol = code_symbol(raw.get("代码"))
        title = simple_text(raw.get("公告标题"))
        url = simple_text(raw.get("网址"))
        pub = display_date(raw.get("公告日期") or key)
        if not title or not symbol:
            continue
        notice_type = simple_text(raw.get("公告类型")) or "公告"
        rows.append({
            "published_at": pub,
            "category": "公告",
            "title": title,
            "summary": f"{simple_text(raw.get('名称'))}披露{notice_type}，点击可查看公告全文。",
            "source": "东方财富公告大全",
            "source_url": url,
            "symbol": symbol,
            "stock_name": simple_text(raw.get("名称")),
            "importance": "high" if any(key in title for key in ["重大", "回购", "重组", "停牌", "风险", "业绩", "半年度报告", "年度报告"]) else "normal",
            "status": "全市场近一个月",
            "source_type": notice_type,
            "item_id": f"notice-{stable_id(symbol, title, url, pub)}",
        })
    return rows, None


def fetch_notices(days: int) -> tuple[list[dict], list[str]]:
    today = datetime.now(BEIJING)
    rows: list[dict] = []
    errors: list[str] = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        day_rows, error = fetch_notice_for_date(day)
        rows.extend(day_rows)
        if error:
            errors.append(error)
    return rows, errors


def fetch_stock_news(item: dict, start_key: str, end_key: str) -> tuple[list[dict], str | None]:
    if ak is None:
        return [], "akshare is not available"
    code = item["code"]
    symbol = item["symbol"]
    stock_name = simple_text(item.get("name"))
    try:
        frame = quiet_akshare_call(ak.stock_news_em, symbol=code)
    except Exception as exc:
        return [], f"{symbol}: {exc!r}"
    if frame is None or frame.empty:
        return [], None
    rows: list[dict] = []
    for raw in frame.to_dict(orient="records"):
        pub_key = date_key(raw.get("发布时间"))
        if not pub_key or pub_key < start_key or pub_key > end_key:
            continue
        title = simple_text(raw.get("新闻标题"))
        url = simple_text(raw.get("新闻链接"))
        if not title or not url:
            continue
        rows.append({
            "published_at": display_date(raw.get("发布时间")),
            "category": "个股资讯",
            "title": title,
            "summary": simple_text(raw.get("新闻内容"))[:220],
            "source": simple_text(raw.get("文章来源")) or "东方财富个股新闻",
            "source_url": url,
            "symbol": symbol,
            "stock_name": stock_name,
            "importance": "medium" if any(key in title for key in ["涨停", "跌停", "回购", "业绩", "重组", "风险", "重大"]) else "normal",
            "status": "全市场近一个月",
            "source_type": "东方财富个股新闻",
            "item_id": f"news-{stable_id(symbol, title, url)}",
        })
    return rows, None


def fetch_market_news(days: int, max_workers: int, news_limit: int) -> tuple[list[dict], list[str], int]:
    today = datetime.now(BEIJING)
    start_key = (today - timedelta(days=days - 1)).strftime("%Y%m%d")
    end_key = today.strftime("%Y%m%d")
    universe = read_universe(news_limit)
    rows: list[dict] = []
    errors: list[str] = []
    if not universe:
        return rows, ["a_share_universe.csv is empty; run update_ashare_daily.py first"], 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_stock_news, item, start_key, end_key) for item in universe]
        for future in as_completed(futures):
            item_rows, error = future.result()
            rows.extend(item_rows)
            if error:
                errors.append(error)
    return rows, errors, len(universe)


def dedupe_rows(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[COLUMNS].fillna("")
    frame = frame.drop_duplicates(subset=["item_id"])
    frame = frame.sort_values(["published_at", "category", "symbol"], ascending=[False, True, True])
    return frame


def fetch_first_finance() -> list[dict]:
    url = "https://www.yicai.com/news/gushi/"
    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results: list[dict] = []
    for link in soup.select("a[href]"):
        title = simple_text(link.get_text(" "))
        href = link.get("href", "")
        if len(title) < 8 or "/news/" not in href:
            continue
        if href.startswith("/"):
            href = "https://www.yicai.com" + href
        if not href.startswith("http"):
            continue
        results.append({
            "id": f"auto-yicai-{stable_id(href)}",
            "category": "重大新闻",
            "title": title[:80],
            "summary": "第一财经A股频道公开网页更新，需结合正文进一步提取涉及股票代码、板块和影响方向。",
            "source": "第一财经",
            "source_url": href,
            "published_at": datetime.now(BEIJING).strftime("%Y-%m-%d"),
            "symbols": ["A股"],
            "importance": "normal",
            "status": "自动抓取",
        })
        if len(results) >= 5:
            break
    return results


def fetch_catl_news() -> list[dict]:
    url = "https://www.catl.com/news/"
    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text("\n")
    results: list[dict] = []
    for line in text.splitlines():
        line = simple_text(line)
        if not line or "2026-" not in line or "宁德时代" not in line:
            continue
        parts = line.rsplit(" ", 1)
        title = parts[0]
        date = parts[1] if len(parts) == 2 and parts[1].startswith("2026-") else datetime.now(BEIJING).strftime("%Y-%m-%d")
        results.append({
            "id": f"auto-catl-{stable_id(line)}",
            "category": "行业重要事项",
            "title": title[:80],
            "summary": "宁德时代官网新闻中心更新，适合跟踪动力电池、储能、海外合作与产业链订单变化。",
            "source": "宁德时代官网",
            "source_url": url,
            "published_at": date,
            "symbols": ["300750.SZ", "新能源", "储能"],
            "importance": "normal",
            "status": "自动抓取",
        })
        if len(results) >= 3:
            break
    return results


def dedupe_manual_items(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    output: list[dict] = []
    for item in items:
        key = simple_text(f"{item.get('title')}|{item.get('source_url')}").lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return sorted(output, key=lambda row: str(row.get("published_at", "")), reverse=True)


def update_status(row_count: int, covered_stocks: int, errors: list[str]) -> None:
    status_path = DATA_DIR / "status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status = {"overall_status": "partial", "datasets": {}}
    else:
        status = {"overall_status": "partial", "datasets": {}}
    status.setdefault("datasets", {})["messages"] = {
        "status": "partial" if errors else "success",
        "latest_date": datetime.now(BEIJING).strftime("%Y%m%d"),
        "rows": row_count,
        "cached_rows": row_count,
        "covered_stocks": covered_stocks,
        "source": "东方财富公告大全 + 东方财富个股新闻 + 财经媒体公开网页",
        "error": "；".join(errors[-3:]) if errors else None,
    }
    write_json(status_path, status)


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新讯息页：近一个月全市场公告与个股资讯")
    parser.add_argument("--days", type=int, default=31, help="回看天数，默认31天")
    parser.add_argument("--max-workers", type=int, default=12, help="个股新闻并发数")
    parser.add_argument("--news-limit", type=int, default=0, help="限制抓取前N只股票；0表示读取全A股票池")
    parser.add_argument("--skip-stock-news", action="store_true", help="只更新全市场公告，不抓逐只个股新闻")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = load_messages()
    payload.setdefault("timezone", "Asia/Shanghai")
    payload["schedule"] = MESSAGE_SCHEDULE
    payload.setdefault("items", [])
    existing_market = read_csv_safe(MARKET_MESSAGES_PATH, columns=COLUMNS)

    errors: list[str] = []
    notice_rows, notice_errors = fetch_notices(args.days)
    errors.extend(notice_errors)
    stock_rows: list[dict] = []
    covered_stocks = 0
    if not args.skip_stock_news:
        stock_rows, stock_errors, covered_stocks = fetch_market_news(args.days, args.max_workers, args.news_limit)
        errors.extend(stock_errors[:50])
    if not notice_rows and not existing_market.empty:
        cached = existing_market[existing_market["category"].astype(str) == "公告"]
        if not cached.empty:
            notice_rows = cached.to_dict(orient="records")
            errors.append("本轮公告接口未返回，已保留上一轮公告缓存")
    if not stock_rows and not existing_market.empty:
        cached = existing_market[existing_market["category"].astype(str) == "个股资讯"]
        if not cached.empty:
            stock_rows = cached.to_dict(orient="records")
            errors.append("本轮个股新闻接口未返回，已保留上一轮个股资讯缓存")

    market_frame = dedupe_rows([*notice_rows, *stock_rows])
    write_csv_atomic(market_frame, MARKET_MESSAGES_PATH)

    fetched_manual: list[dict] = []
    for fetcher in [fetch_first_finance, fetch_catl_news]:
        try:
            fetched_manual.extend(fetcher())
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {exc}")

    payload["items"] = dedupe_manual_items([*fetched_manual, *payload.get("items", [])])[:120]
    payload["last_checked_at"] = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S+08:00")
    payload["updated_at"] = payload["last_checked_at"]
    actual_covered = int(market_frame["symbol"].nunique()) if not market_frame.empty else 0
    payload["market_feed"] = {
        "days": args.days,
        "rows": int(len(market_frame)),
        "covered_stocks": int(max(covered_stocks, actual_covered)),
        "notice_rows": int(len(notice_rows)),
        "stock_news_rows": int(len(stock_rows)),
        "source": "东方财富公告大全、东方财富个股新闻",
    }
    write_json(MESSAGE_PATH, payload)
    update_status(len(market_frame), int(payload["market_feed"]["covered_stocks"]), errors)
    print(json.dumps({
        "market_rows": len(market_frame),
        "notice_rows": len(notice_rows),
        "stock_news_rows": len(stock_rows),
        "covered_stocks": payload["market_feed"]["covered_stocks"],
        "errors": errors[:10],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
