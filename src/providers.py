from __future__ import annotations

import io
import re
from datetime import datetime
from math import ceil
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils import date_key, month_key, numeric, pick_column


class DataFetchError(RuntimeError):
    pass


def _import_akshare():
    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover - environment dependent
        raise DataFetchError(f"AKShare 无法加载: {exc}") from exc
    return ak


def _series(df: pd.DataFrame, candidates: list[str], contains: list[str] | None = None) -> pd.Series:
    col = pick_column(df, candidates, contains)
    if col is None:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return numeric(df[col])


def _release_series(df: pd.DataFrame, value_name: str) -> pd.DataFrame:
    date_col = pick_column(df, ["日期", "date", "Date"])
    value_col = pick_column(df, ["今值", "value", "Value"])
    if date_col is None or value_col is None:
        raise DataFetchError(f"发布型数据缺少日期或今值字段: {list(df.columns)}")
    out = pd.DataFrame({
        "month": df[date_col].map(month_key),
        value_name: numeric(df[value_col]),
    })
    return out.dropna(subset=["month", value_name]).drop_duplicates("month", keep="last")


class PublicMacroProvider:
    """Public macro sources with independent fallbacks for each indicator."""

    def __init__(self) -> None:
        self.ak = _import_akshare()
        self.messages: list[str] = []

    def _try(self, label: str, calls: list[Callable[[], pd.DataFrame]]) -> pd.DataFrame:
        errors: list[str] = []
        for call in calls:
            try:
                df = call()
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df
                errors.append("返回空表")
            except Exception as exc:
                errors.append(repr(exc))
        self.messages.append(f"{label}: " + " | ".join(errors))
        return pd.DataFrame()

    def _fetch_social_stock_official(self, start_month: str) -> pd.DataFrame:
        """Fetch official PBOC AFRE stock and YoY growth tables by year."""
        start_year = max(2015, int(start_month[:4]))
        current_year = datetime.now().year
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MacroScopePublic/4.0; research dashboard)"}

        for year in range(start_year, current_year + 1):
            page_url = f"https://www.pbc.gov.cn/diaochatongjisi/116219/116319/{year}ntjsj/shrzgm/index.html"
            try:
                page = requests.get(page_url, timeout=25, headers=headers)
                page.raise_for_status()
                soup = BeautifulSoup(page.content, "html.parser")
                heading = soup.find(string=lambda value: isinstance(value, str) and "社会融资规模存量统计表" in value)
                if heading is None:
                    raise DataFetchError("页面未找到社融存量标题")
                xls_url = None
                for anchor in heading.find_all_next("a", href=True):
                    href = str(anchor.get("href", ""))
                    if re.search(r"\.xlsx?$", href, flags=re.I):
                        xls_url = urljoin(page_url, href)
                        break
                if not xls_url:
                    raise DataFetchError("页面未找到社融存量 Excel 链接")

                workbook = requests.get(xls_url, timeout=30, headers=headers)
                workbook.raise_for_status()
                raw = pd.read_excel(io.BytesIO(workbook.content), header=None)
                first_col = raw.iloc[:, 0].astype(str).str.replace(r"\s+", "", regex=True)
                matches = raw.index[first_col.str.contains("社会融资规模存量", na=False)].tolist()
                if not matches:
                    raise DataFetchError("Excel 未找到社融存量行")
                numeric_counts = {
                    int(row): int(pd.to_numeric(raw.iloc[int(row), 1:], errors="coerce").notna().sum())
                    for row in matches
                }
                stock_row = max(numeric_counts, key=numeric_counts.get)
                if numeric_counts[stock_row] == 0:
                    raise DataFetchError("Excel 社融存量行没有数值")

                for col in range(raw.shape[1] - 1):
                    month = None
                    for candidate in raw.iloc[:stock_row, col].tolist():
                        text = str(candidate).strip()
                        found = re.search(r"((?:19|20)\d{2})[.年/-](0?[1-9]|1[0-2])", text)
                        if found:
                            month = f"{found.group(1)}{int(found.group(2)):02d}"
                    if not month or month < start_month:
                        continue
                    stock = pd.to_numeric(pd.Series([raw.iat[stock_row, col]]), errors="coerce").iloc[0]
                    growth = pd.to_numeric(pd.Series([raw.iat[stock_row, col + 1]]), errors="coerce").iloc[0]
                    if pd.notna(stock):
                        rows.append({
                            "month": month,
                            "sf_stock_trillion": float(stock),
                            "sf_stock_yoy_pct": float(growth) if pd.notna(growth) else np.nan,
                        })
            except Exception as exc:
                errors.append(f"{year}: {exc!r}")

        if not rows:
            raise DataFetchError("人民银行社融存量表抓取失败: " + " | ".join(errors[-4:]))
        if errors:
            self.messages.append("人民银行社融存量部分年份失败: " + " | ".join(errors[-4:]))
        return pd.DataFrame(rows).drop_duplicates("month", keep="last").sort_values("month")

    def fetch(self, start_month: str = "200801") -> tuple[pd.DataFrame, dict[str, Any]]:
        frames: list[pd.DataFrame] = []
        component_status: dict[str, Any] = {}

        money = self._try("货币供应量", [lambda: self.ak.macro_china_money_supply()])
        if not money.empty:
            date_col = pick_column(money, ["月份", "统计时间", "日期"])
            if date_col:
                out = pd.DataFrame({
                    "month": money[date_col].map(month_key),
                    "m1_trillion": _series(money, ["货币(M1)-数量(亿元)", "货币(M1)-数量", "货币(狭义货币M1)"]) / 10000,
                    "m1_yoy_pct": _series(money, ["货币(M1)-同比增长", "货币(狭义货币M1)同比增长"]),
                    "m2_trillion": _series(money, ["货币和准货币(M2)-数量(亿元)", "货币和准货币(M2)-数量", "货币和准货币（广义货币M2）"]) / 10000,
                    "m2_yoy_pct": _series(money, ["货币和准货币(M2)-同比增长", "货币和准货币（广义货币M2）同比增长"]),
                })
                out = out.dropna(subset=["month"]).drop_duplicates("month", keep="last")
                frames.append(out)
                component_status["money_supply"] = {"status": "success", "rows": len(out), "source": "东方财富 / AKShare"}
            else:
                component_status["money_supply"] = {"status": "failed", "error": "缺少月份字段"}
        else:
            # M2同比 fallback; balance and M1 remain unavailable rather than being fabricated.
            m2_release = self._try("M2同比备用源", [lambda: self.ak.macro_china_m2_yearly()])
            if not m2_release.empty:
                out = _release_series(m2_release, "m2_yoy_pct")
                frames.append(out)
                component_status["money_supply"] = {"status": "partial", "rows": len(out), "source": "金十 / AKShare", "note": "备用源仅含M2同比"}
            else:
                component_status["money_supply"] = {"status": "failed", "error": "所有货币供应量源均失败"}

        social = self._try("社会融资规模", [lambda: self.ak.macro_china_shrzgm()])
        if not social.empty:
            date_col = pick_column(social, ["月份", "日期"])
            value_col = pick_column(social, ["社会融资规模增量"])
            if date_col and value_col:
                out = pd.DataFrame({
                    "month": social[date_col].map(month_key),
                    "sf_increment_trillion": numeric(social[value_col]) / 10000,
                }).dropna(subset=["month"])
                out = out.sort_values("month").drop_duplicates("month", keep="last")
                out["sf_increment_yoy_pct"] = out["sf_increment_trillion"].pct_change(12) * 100
                out["sf_12m_trillion"] = out["sf_increment_trillion"].rolling(12, min_periods=12).sum()
                out["sf_12m_yoy_pct"] = out["sf_12m_trillion"].pct_change(12) * 100
                frames.append(out)
                component_status["social_financing"] = {"status": "success", "rows": len(out), "source": "商务数据中心 / AKShare"}
            else:
                component_status["social_financing"] = {"status": "failed", "error": "缺少社融字段"}
        else:
            component_status["social_financing"] = {"status": "failed", "error": "公开社融源失败"}

        try:
            stock = self._fetch_social_stock_official(start_month)
            frames.append(stock)
            component_status["social_financing_stock"] = {
                "status": "success",
                "rows": len(stock),
                "source": "中国人民银行官方社会融资规模存量统计表",
            }
        except Exception as exc:
            component_status["social_financing_stock"] = {"status": "failed", "error": repr(exc)}
            self.messages.append(f"社融存量官方表: {exc!r}")

        pmi = self._try("PMI", [lambda: self.ak.macro_china_pmi()])
        if not pmi.empty:
            date_col = pick_column(pmi, ["月份", "日期"])
            if date_col:
                out = pd.DataFrame({
                    "month": pmi[date_col].map(month_key),
                    "pmi_manufacturing": _series(pmi, ["制造业-指数", "制造业PMI", "今值"]),
                    "pmi_non_manufacturing": _series(pmi, ["非制造业-指数", "非制造业PMI"]),
                }).dropna(subset=["month"])
                out = out.drop_duplicates("month", keep="last")
                frames.append(out)
                component_status["pmi"] = {"status": "success", "rows": len(out), "source": "东方财富 / AKShare"}
            else:
                component_status["pmi"] = {"status": "failed", "error": "缺少月份字段"}
        else:
            pmi_release = self._try("PMI备用源", [lambda: self.ak.macro_china_pmi_yearly()])
            if not pmi_release.empty:
                out = _release_series(pmi_release, "pmi_manufacturing")
                frames.append(out)
                component_status["pmi"] = {"status": "partial", "rows": len(out), "source": "金十 / AKShare", "note": "备用源仅含官方制造业PMI"}
            else:
                component_status["pmi"] = {"status": "failed", "error": "所有PMI源均失败"}

        cpi = self._try("CPI", [lambda: self.ak.macro_china_cpi()])
        if not cpi.empty:
            date_col = pick_column(cpi, ["月份", "日期"])
            if date_col:
                out = pd.DataFrame({
                    "month": cpi[date_col].map(month_key),
                    "cpi_yoy_pct": _series(cpi, ["全国-同比增长", "全国同比增长"]),
                    "cpi_mom_pct": _series(cpi, ["全国-环比增长", "全国环比增长"]),
                }).dropna(subset=["month"])
                out = out.drop_duplicates("month", keep="last")
                frames.append(out)
                component_status["cpi"] = {"status": "success", "rows": len(out), "source": "东方财富 / AKShare"}
            else:
                component_status["cpi"] = {"status": "failed", "error": "缺少月份字段"}
        else:
            yearly = self._try("CPI同比备用源", [lambda: self.ak.macro_china_cpi_yearly()])
            monthly = self._try("CPI环比备用源", [lambda: self.ak.macro_china_cpi_monthly()])
            fallback_frames: list[pd.DataFrame] = []
            if not yearly.empty:
                fallback_frames.append(_release_series(yearly, "cpi_yoy_pct"))
            if not monthly.empty:
                fallback_frames.append(_release_series(monthly, "cpi_mom_pct"))
            if fallback_frames:
                out = fallback_frames[0]
                for frame in fallback_frames[1:]:
                    out = out.merge(frame, on="month", how="outer")
                frames.append(out)
                component_status["cpi"] = {"status": "partial", "rows": len(out), "source": "金十 / AKShare"}
            else:
                component_status["cpi"] = {"status": "failed", "error": "所有CPI源均失败"}

        if not frames:
            raise DataFetchError("所有宏观公开数据源均失败")

        merged = frames[0]
        for frame in frames[1:]:
            merged = merged.merge(frame, on="month", how="outer")
        merged = merged.dropna(subset=["month"]).sort_values("month")
        merged = merged[merged["month"] >= start_month].copy()
        for col in [
            "m1_trillion", "m2_trillion", "m1_yoy_pct", "m2_yoy_pct",
            "sf_increment_trillion", "sf_increment_yoy_pct", "sf_12m_trillion", "sf_12m_yoy_pct",
            "sf_stock_trillion", "sf_stock_yoy_pct",
            "pmi_manufacturing", "pmi_non_manufacturing", "cpi_yoy_pct", "cpi_mom_pct",
        ]:
            if col not in merged.columns:
                merged[col] = np.nan
        merged["m1_m2_gap_pp"] = merged["m1_yoy_pct"] - merged["m2_yoy_pct"]
        merged["m1_m2_mechanical_sum_trillion"] = merged["m1_trillion"] + merged["m2_trillion"]
        merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return merged.reset_index(drop=True), {"components": component_status, "messages": self.messages}


class ChinaMarketProvider:
    def __init__(self) -> None:
        self.ak = _import_akshare()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def fetch_index(self, item: dict[str, Any], start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch Chinese index history with independent Sina/Tencent/Eastmoney/CSIndex fallbacks."""
        code = str(item["code"])
        symbol = str(item["symbol"])
        if symbol.endswith(".SZ") or code.startswith("399"):
            vendor_symbol = f"sz{code}"
        elif symbol.endswith(".CSI") or code.startswith("93"):
            vendor_symbol = f"csi{code}"
        else:
            vendor_symbol = f"sh{code}"

        frames: list[tuple[str, Callable[[], pd.DataFrame]]] = []
        preference = str(item.get("source_preference", "")).lower()

        def add_standard_sources() -> None:
            sina = getattr(self.ak, "stock_zh_index_daily", None)
            if callable(sina) and not vendor_symbol.startswith("csi"):
                frames.append(("新浪财经 / AKShare", lambda: sina(symbol=vendor_symbol)))
            tx = getattr(self.ak, "stock_zh_index_daily_tx", None)
            if callable(tx) and not vendor_symbol.startswith("csi"):
                frames.append(("腾讯财经 / AKShare", lambda: tx(symbol=vendor_symbol)))
            em_daily = getattr(self.ak, "stock_zh_index_daily_em", None)
            if callable(em_daily):
                frames.append((
                    "东方财富指数日线 / AKShare",
                    lambda: em_daily(symbol=vendor_symbol, start_date=start_date, end_date=end_date),
                ))
            index_hist = getattr(self.ak, "index_zh_a_hist", None)
            if callable(index_hist):
                frames.append((
                    "东方财富指数历史 / AKShare",
                    lambda: index_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date),
                ))

        csindex = getattr(self.ak, "stock_zh_index_hist_csindex", None)
        if preference == "csindex" or code.startswith("93"):
            if callable(csindex):
                frames.append((
                    "中证指数官网 / AKShare",
                    lambda: csindex(symbol=code, start_date=start_date, end_date=end_date),
                ))
            add_standard_sources()
        else:
            add_standard_sources()
            if callable(csindex):
                frames.append((
                    "中证指数官网 / AKShare",
                    lambda: csindex(symbol=code, start_date=start_date, end_date=end_date),
                ))

        errors: list[str] = []
        for source, call in frames:
            try:
                df = call()
                if df is None or df.empty:
                    errors.append(f"{source}: 空表")
                    continue
                date_col = pick_column(df, ["日期", "date", "Date"])
                close_col = pick_column(df, ["收盘", "close", "Close"])
                if date_col is None or close_col is None:
                    errors.append(f"{source}: 缺少日期或收盘字段 {list(df.columns)}")
                    continue
                pct_col = pick_column(df, ["涨跌幅", "pct_change", "pct_chg"])
                volume_col = pick_column(df, ["成交量", "volume", "vol"])
                amount_col = pick_column(df, ["成交额", "成交金额", "amount"])
                out = pd.DataFrame({
                    "trade_date": df[date_col].map(date_key),
                    "symbol": symbol,
                    "name": item.get("short_name", item["name"]),
                    "market": "CN_INDEX",
                    "close": numeric(df[close_col]),
                    "pct_change": numeric(df[pct_col]) if pct_col else np.nan,
                    "volume": numeric(df[volume_col]) if volume_col else np.nan,
                    "amount": numeric(df[amount_col]) if amount_col else np.nan,
                    "source": source,
                })
                out = out.dropna(subset=["trade_date", "close"])
                out = out[(out["trade_date"] >= start_date) & (out["trade_date"] <= end_date)]
                out = out.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
                if out.empty:
                    errors.append(f"{source}: 日期过滤后为空")
                    continue
                if out["pct_change"].isna().all():
                    out["pct_change"] = out["close"].pct_change() * 100
                return out.reset_index(drop=True)
            except Exception as exc:
                errors.append(f"{source}: {exc!r}")
        raise DataFetchError("指数行情失败: " + " | ".join(errors))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def fetch_valuation(self, item: dict[str, Any], start_date: str, end_date: str) -> pd.DataFrame:
        code = str(item["code"])
        errors: list[str] = []

        # First choice: CSIndex historical data, which includes rolling PE for many major indexes.
        try:
            df = self.ak.stock_zh_index_hist_csindex(symbol=code, start_date=start_date, end_date=end_date)
            if not df.empty:
                date_col = pick_column(df, ["日期", "date"])
                pe_col = pick_column(df, ["滚动市盈率", "市盈率1", "市盈率2"])
                pb_col = pick_column(df, ["市净率", "市净率1", "市净率2"])
                if date_col and pe_col:
                    out = pd.DataFrame({
                        "trade_date": df[date_col].map(date_key),
                        "index_code": item["symbol"],
                        "index_name": item["name"],
                        "pe_ttm": numeric(df[pe_col]),
                        "pb": numeric(df[pb_col]) if pb_col else np.nan,
                        "source": "中证指数官网 / AKShare",
                    })
                    out = out.dropna(subset=["trade_date"]).sort_values("trade_date")
                    if out["pe_ttm"].notna().sum() >= 20:
                        return out.reset_index(drop=True)
        except Exception as exc:
            errors.append(f"中证指数: {exc!r}")

        # Second choice: Legulegu adapters for PE/PB histories.
        try:
            name = item.get("legulegu_name", item["name"])
            pe = self.ak.stock_index_pe_lg(symbol=name)
            pb = self.ak.stock_index_pb_lg(symbol=name)
            pe_date = pick_column(pe, ["日期", "date"])
            pb_date = pick_column(pb, ["日期", "date"])
            pe_col = pick_column(pe, ["滚动市盈率", "等权滚动市盈率", "静态市盈率", "市盈率"])
            pb_col = pick_column(pb, ["市净率", "等权市净率"])
            if pe_date and pe_col:
                pe_out = pd.DataFrame({"trade_date": pe[pe_date].map(date_key), "pe_ttm": numeric(pe[pe_col])})
                if pb_date and pb_col:
                    pb_out = pd.DataFrame({"trade_date": pb[pb_date].map(date_key), "pb": numeric(pb[pb_col])})
                    out = pe_out.merge(pb_out, on="trade_date", how="outer")
                else:
                    out = pe_out.assign(pb=np.nan)
                out["index_code"] = item["symbol"]
                out["index_name"] = item["name"]
                out["source"] = "乐咕乐股 / AKShare"
                out = out.dropna(subset=["trade_date"]).sort_values("trade_date")
                if not out.empty:
                    return out.reset_index(drop=True)
        except Exception as exc:
            errors.append(f"乐咕乐股: {exc!r}")

        raise DataFetchError("估值数据失败: " + " | ".join(errors))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def fetch_crowding(self, top_fraction: float) -> dict[str, Any]:
        if not 0 < top_fraction <= 1:
            raise ValueError("top_fraction must be in (0, 1]")
        candidates: list[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("沪深京A股实时行情", lambda: self.ak.stock_zh_a_spot_em()),
            ("沪深A股分市场实时行情", self._fetch_split_spot),
        ]
        errors: list[str] = []
        for source, call in candidates:
            try:
                spot = call()
                if spot.empty:
                    errors.append(f"{source}: 空表")
                    continue
                code_col = pick_column(spot, ["代码", "symbol", "股票代码"])
                amount_col = pick_column(spot, ["成交额", "amount"])
                if code_col is None or amount_col is None:
                    errors.append(f"{source}: 缺少代码或成交额")
                    continue
                clean = pd.DataFrame({"code": spot[code_col].astype(str).str.zfill(6), "amount": numeric(spot[amount_col])})
                clean = clean[clean["code"].str.startswith(("0", "3", "6"))]
                clean = clean[clean["amount"].notna() & (clean["amount"] > 0)].drop_duplicates("code", keep="last")
                if clean.empty:
                    errors.append(f"{source}: 无有效A股成交额")
                    continue
                top_count = max(1, ceil(len(clean) * top_fraction))
                ranked = clean.sort_values("amount", ascending=False)
                top_amount = float(ranked.head(top_count)["amount"].sum())
                total_amount = float(ranked["amount"].sum())
                trade_date = datetime.now().strftime("%Y%m%d")
                try:
                    calendar = self.ak.tool_trade_date_hist_sina()
                    calendar_col = pick_column(calendar, ["trade_date", "日期", "date"])
                    if calendar_col:
                        dates = calendar[calendar_col].map(date_key).dropna()
                        eligible = dates[dates <= trade_date]
                        if not eligible.empty:
                            trade_date = str(eligible.max())
                except Exception:
                    pass
                return {
                    "trade_date": trade_date,
                    "top_fraction": top_fraction,
                    "stock_count": int(len(clean)),
                    "top_count": int(top_count),
                    "top_amount_trillion": top_amount / 1e12,
                    "total_amount_trillion": total_amount / 1e12,
                    "crowding_pct": top_amount / total_amount * 100 if total_amount else np.nan,
                    "source": source + " / AKShare",
                }
            except Exception as exc:
                errors.append(f"{source}: {exc!r}")
        raise DataFetchError("交易拥挤度失败: " + " | ".join(errors))

    def _fetch_split_spot(self) -> pd.DataFrame:
        frames = []
        for fn_name in ["stock_sh_a_spot_em", "stock_sz_a_spot_em"]:
            fn = getattr(self.ak, fn_name, None)
            if callable(fn):
                df = fn()
                if isinstance(df, pd.DataFrame) and not df.empty:
                    frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


class USMarketProvider:
    def fetch(self, tickers: list[str], names: dict[str, str], start_date: str) -> pd.DataFrame:
        errors: list[str] = []
        try:
            import yfinance as yf

            raw = yf.download(
                tickers=tickers,
                start=pd.to_datetime(start_date).strftime("%Y-%m-%d"),
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                threads=False,
                timeout=30,
            )
            if not raw.empty:
                rows: list[pd.DataFrame] = []
                if isinstance(raw.columns, pd.MultiIndex):
                    available = set(raw.columns.get_level_values(0))
                    for ticker in tickers:
                        if ticker not in available:
                            continue
                        frame = raw[ticker].reset_index().copy()
                        frame["symbol"] = ticker
                        rows.append(frame)
                else:
                    frame = raw.reset_index().copy()
                    frame["symbol"] = tickers[0]
                    rows.append(frame)
                if rows:
                    out = pd.concat(rows, ignore_index=True).rename(columns={"Date": "trade_date", "Close": "close", "Volume": "volume"})
                    out["trade_date"] = out["trade_date"].map(date_key)
                    out["name"] = out["symbol"].map(names)
                    out["market"] = "US_EQUITY"
                    out["close"] = numeric(out["close"])
                    out = out.sort_values(["symbol", "trade_date"])
                    out["pct_change"] = out.groupby("symbol")["close"].pct_change() * 100
                    out["amount"] = np.nan
                    out["source"] = "Yahoo Finance / yfinance"
                    return out[["trade_date", "symbol", "name", "market", "close", "pct_change", "volume", "amount", "source"]].dropna(subset=["trade_date", "close"])
        except Exception as exc:
            errors.append(f"yfinance: {exc!r}")

        # Fallback: Stooq daily CSV, one ticker at a time.
        rows = []
        end_date = datetime.now().strftime("%Y%m%d")
        for ticker in tickers:
            try:
                symbol = ticker.lower() + ".us"
                url = f"https://stooq.com/q/d/l/?s={symbol}&d1={start_date}&d2={end_date}&i=d"
                response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                frame = pd.read_csv(io.StringIO(response.text))
                if frame.empty or "Close" not in frame.columns:
                    continue
                frame = frame.rename(columns={"Date": "trade_date", "Close": "close", "Volume": "volume"})
                frame["trade_date"] = frame["trade_date"].map(date_key)
                frame["symbol"] = ticker
                frame["name"] = names[ticker]
                frame["market"] = "US_EQUITY"
                frame["close"] = numeric(frame["close"])
                frame = frame.sort_values("trade_date")
                frame["pct_change"] = frame["close"].pct_change() * 100
                frame["amount"] = np.nan
                frame["source"] = "Stooq"
                rows.append(frame[["trade_date", "symbol", "name", "market", "close", "pct_change", "volume", "amount", "source"]])
            except Exception as exc:
                errors.append(f"Stooq {ticker}: {exc!r}")
        if rows:
            return pd.concat(rows, ignore_index=True)
        raise DataFetchError("美股行情失败: " + " | ".join(errors))
