from __future__ import annotations

import io
import math
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.providers import DataFetchError, _import_akshare
from src.utils import date_key, numeric, pick_column

A_SHARE_PREFIXES = (
    "000", "001", "002", "003", "300", "301",
    "600", "601", "603", "605", "688", "689",
)


def _latest_trade_date(ak: Any) -> str:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    try:
        calendar = ak.tool_trade_date_hist_sina()
        date_col = pick_column(calendar, ["trade_date", "日期", "date"])
        if date_col:
            dates = calendar[date_col].map(date_key).dropna()
            eligible = dates[dates <= today]
            if not eligible.empty:
                return str(eligible.max())
    except Exception:
        pass
    return today


def _clean_a_spot(spot: pd.DataFrame) -> pd.DataFrame:
    code_col = pick_column(spot, ["代码", "symbol", "股票代码"])
    amount_col = pick_column(spot, ["成交额", "amount", "成交金额"])
    pct_col = pick_column(spot, ["涨跌幅", "pct_change", "涨跌幅%"])
    cap_col = pick_column(spot, ["总市值", "market_cap", "总市值(元)"])
    if code_col is None or amount_col is None:
        raise DataFetchError(f"A股实时行情缺少代码或成交额字段: {list(spot.columns)}")
    clean = pd.DataFrame({
        "code": spot[code_col].astype(str).str.extract(r"(\d{6})", expand=False).str.zfill(6),
        "amount": numeric(spot[amount_col]),
        "pct_change": numeric(spot[pct_col]) if pct_col else np.nan,
        "market_cap": numeric(spot[cap_col]) if cap_col else np.nan,
    })
    clean = clean[clean["code"].str.startswith(A_SHARE_PREFIXES, na=False)]
    clean = clean.drop_duplicates("code", keep="last")
    return clean


class GlobalMarketProvider:
    """Public end-of-day market data via Yahoo Finance/yfinance."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), reraise=True)
    def fetch(self, items: list[dict[str, Any]], start_date: str) -> pd.DataFrame:
        try:
            import yfinance as yf
        except Exception as exc:  # pragma: no cover
            raise DataFetchError(f"yfinance 无法加载: {exc}") from exc

        tickers = [str(item["ticker"]) for item in items]
        metadata = {str(item["ticker"]): item for item in items}
        raw = yf.download(
            tickers=tickers,
            start=pd.to_datetime(start_date).strftime("%Y-%m-%d"),
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=False,
            timeout=45,
        )
        if raw is None or raw.empty:
            raise DataFetchError("Yahoo Finance 返回空行情")

        rows: list[pd.DataFrame] = []
        if isinstance(raw.columns, pd.MultiIndex):
            available = set(str(x) for x in raw.columns.get_level_values(0))
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

        if not rows:
            raise DataFetchError("Yahoo Finance 没有返回任何指定资产")

        out = pd.concat(rows, ignore_index=True).rename(
            columns={"Date": "trade_date", "Close": "close", "Volume": "volume"}
        )
        out["trade_date"] = out["trade_date"].map(date_key)
        out["close"] = numeric(out.get("close", pd.Series(index=out.index, dtype=float)))
        out["volume"] = numeric(out.get("volume", pd.Series(index=out.index, dtype=float)))
        out["name"] = out["symbol"].map(lambda x: metadata.get(str(x), {}).get("chinese_name") or metadata.get(str(x), {}).get("name"))
        out["market"] = out["symbol"].map(lambda x: metadata.get(str(x), {}).get("market", "GLOBAL"))
        out["currency"] = out["symbol"].map(lambda x: metadata.get(str(x), {}).get("currency", ""))
        out["asset_group"] = out["symbol"].map(lambda x: metadata.get(str(x), {}).get("group", "全球市场"))
        out = out.dropna(subset=["trade_date", "close"]).sort_values(["symbol", "trade_date"])
        out["pct_change"] = out.groupby("symbol")["close"].pct_change() * 100
        out["amount"] = np.nan
        out["source"] = "Yahoo Finance / yfinance"
        return out[[
            "trade_date", "symbol", "name", "market", "currency", "asset_group",
            "close", "pct_change", "volume", "amount", "source",
        ]].reset_index(drop=True)


class FredTreasuryProvider:
    """Official U.S. Treasury yields with independent per-series fallbacks.

    Primary source: FRED CSV (Federal Reserve H.15 series).
    Fallbacks: the Federal Reserve H.15 current-release HTML table, then FRED
    plain text. DGS10 is fetched
    independently from DGS2 so one missing series can never suppress the other.
    """

    SERIES = {
        "DGS2": "美国2年期国债收益率",
        "DGS10": "美国10年期国债收益率",
    }
    FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
    FRED_TXT = "https://fred.stlouisfed.org/data/{series}.txt"
    H15_URL = "https://www.federalreserve.gov/releases/h15/"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (compatible; MacroScopePublic/5.2; research dashboard)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/csv;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        }

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def _fred_csv(self, series: str, start_date: str) -> pd.DataFrame:
        url = self.FRED_CSV.format(series=series, start=pd.to_datetime(start_date).strftime("%Y-%m-%d"))
        response = requests.get(url, timeout=20, headers=self._headers())
        response.raise_for_status()
        raw = pd.read_csv(io.StringIO(response.text))
        date_col = pick_column(raw, ["DATE", "observation_date", "date"])
        if date_col is None or series not in raw.columns:
            raise DataFetchError(f"FRED CSV缺少{series}: {list(raw.columns)}")
        out = pd.DataFrame({
            "trade_date": raw[date_col].map(date_key),
            "value_pct": numeric(raw[series]),
        }).dropna(subset=["trade_date", "value_pct"])
        out = out[out["trade_date"] >= start_date]
        if out.empty:
            raise DataFetchError(f"FRED CSV中{series}为空")
        return out

    @retry(stop=stop_after_attempt(1), reraise=True)
    def _fred_txt(self, series: str, start_date: str) -> pd.DataFrame:
        response = requests.get(self.FRED_TXT.format(series=series), timeout=20, headers=self._headers())
        response.raise_for_status()
        lines = response.text.splitlines()
        header_idx = next((i for i, line in enumerate(lines) if re.match(r"^\s*DATE\s+VALUE\s*$", line)), None)
        if header_idx is None:
            raise DataFetchError(f"FRED TXT未找到{series}数据头")
        raw = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), sep=r"\s+", engine="python")
        if "DATE" not in raw.columns or "VALUE" not in raw.columns:
            raise DataFetchError(f"FRED TXT缺少{series}字段")
        out = pd.DataFrame({
            "trade_date": raw["DATE"].map(date_key),
            "value_pct": numeric(raw["VALUE"]),
        }).dropna(subset=["trade_date", "value_pct"])
        out = out[out["trade_date"] >= start_date]
        if out.empty:
            raise DataFetchError(f"FRED TXT中{series}为空")
        return out

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def _h15_latest(self, series: str) -> pd.DataFrame:
        response = requests.get(self.H15_URL, timeout=20, headers=self._headers())
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        target = "2-year" if series == "DGS2" else "10-year"
        for table in tables:
            flat = table.copy()
            flat.columns = [" ".join(str(x) for x in col if str(x) != "nan") if isinstance(col, tuple) else str(col) for col in flat.columns]
            for _, row in flat.iterrows():
                first = str(row.iloc[0]).strip().lower()
                if target not in first:
                    continue
                values: list[tuple[str, float]] = []
                for col, cell in row.iloc[1:].items():
                    val = pd.to_numeric(pd.Series([cell]), errors="coerce").iloc[0]
                    if pd.isna(val):
                        continue
                    # Try a date from the column header; if not available, use release date.
                    parsed = pd.to_datetime(str(col), errors="coerce")
                    date = parsed.strftime("%Y%m%d") if pd.notna(parsed) else None
                    values.append((date or "", float(val)))
                if not values:
                    continue
                trade_date, value = values[-1]
                if not trade_date:
                    soup = BeautifulSoup(response.text, "html.parser")
                    page_text = " ".join(soup.stripped_strings)
                    match = re.search(r"Release date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", page_text)
                    parsed = pd.to_datetime(match.group(1), errors="coerce") if match else pd.NaT
                    trade_date = (parsed - pd.tseries.offsets.BDay(1)).strftime("%Y%m%d") if pd.notna(parsed) else datetime.now().strftime("%Y%m%d")
                return pd.DataFrame([{"trade_date": trade_date, "value_pct": value}])
        raise DataFetchError(f"H.15当前发布页未解析到{series}")

    def fetch_with_details(self, start_date: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        frames: list[pd.DataFrame] = []
        details: dict[str, Any] = {}
        for series, name in self.SERIES.items():
            errors: list[str] = []
            frame = pd.DataFrame()
            source = ""
            for label, fn in [
                ("FRED CSV", self._fred_csv),
                ("H15 HTML", lambda current_series, _start: self._h15_latest(current_series)),
                ("FRED TXT", self._fred_txt),
            ]:
                try:
                    frame = fn(series, start_date)
                    source = "美联储H.15当前发布页" if label == "H15 HTML" else f"美联储H.15 / {label}"
                    break
                except Exception as exc:
                    errors.append(f"{label}: {exc!r}")
            if frame.empty:
                details[series] = {"status": "failed", "errors": errors}
                continue
            frame = frame.copy()
            frame["series"] = series
            frame["name"] = name
            frame["unit"] = "%"
            frame["source"] = source
            frames.append(frame[["trade_date", "series", "name", "value_pct", "unit", "source"]])
            details[series] = {
                "status": "success",
                "rows": len(frame),
                "latest_date": str(frame["trade_date"].max()),
                "source": source,
                "fallback_errors": errors,
            }
        if not frames:
            raise DataFetchError(f"DGS2与DGS10均抓取失败: {details}")
        out = pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "series"], keep="last")
        return out.sort_values(["series", "trade_date"]), details

    def fetch(self, start_date: str) -> pd.DataFrame:
        return self.fetch_with_details(start_date)[0]


class UsdLiquidityProvider:
    """Net USD liquidity from FRED balance-sheet and RRP series."""

    FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
    FRED_SERIES_PAGE = "https://fred.stlouisfed.org/series/{series}"
    H41_URL = "https://www.federalreserve.gov/releases/h41/current/"
    NYFED_RRP_URL = "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json"
    SOURCE = "FRED：WSHOSHO/WALCL/WDTGAL/RRPONTSYD"

    @staticmethod
    def _headers() -> dict[str, str]:
        return FredTreasuryProvider._headers()

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def _fred_csv(self, series: str, start_date: str) -> pd.DataFrame:
        start = pd.to_datetime(start_date).strftime("%Y-%m-%d")
        url = self.FRED_CSV.format(series=series, start=start)
        response = requests.get(url, timeout=20, headers=self._headers())
        response.raise_for_status()
        raw = pd.read_csv(io.StringIO(response.text))
        date_col = pick_column(raw, ["DATE", "observation_date", "date"])
        if date_col is None or series not in raw.columns:
            raise DataFetchError(f"FRED CSV缺少{series}: {list(raw.columns)}")
        out = pd.DataFrame({
            "date": pd.to_datetime(raw[date_col], errors="coerce"),
            series: numeric(raw[series]),
        }).dropna(subset=["date", series])
        if out.empty:
            raise DataFetchError(f"FRED CSV中{series}为空")
        return out.sort_values("date").reset_index(drop=True)

    @staticmethod
    def _parse_fred_series_page(html: str, series: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "html.parser")
        date_node = soup.select_one("input#coed")
        value_node = soup.select_one(".series-meta-observation-value")
        raw_date = date_node.get("value") if date_node else None
        raw_value = value_node.get_text(" ", strip=True) if value_node else None
        parsed_date = pd.to_datetime(raw_date, errors="coerce")
        parsed_value = pd.to_numeric(str(raw_value or "").replace(",", ""), errors="coerce")
        if pd.isna(parsed_date) or pd.isna(parsed_value):
            raise DataFetchError(f"FRED序列页面未解析到{series}最新值")
        return pd.DataFrame({"date": [parsed_date], series: [float(parsed_value)]})

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def _fred_series_page(self, series: str) -> pd.DataFrame:
        response = requests.get(
            self.FRED_SERIES_PAGE.format(series=series),
            timeout=20,
            headers=self._headers(),
        )
        response.raise_for_status()
        return self._parse_fred_series_page(response.text, series)

    @staticmethod
    def _parse_h41_latest(html: str) -> dict[str, pd.DataFrame]:
        tables = pd.read_html(io.StringIO(html))
        targets = {
            "WSHOSHO": "Securities held outright",
            "WALCL": "Total assets",
            "WDTGAL": "U.S. Treasury, General Account",
        }
        values: dict[str, float] = {}
        observation_date: pd.Timestamp | None = None
        for table in tables:
            columns = [
                " ".join(str(part) for part in column if str(part) != "nan")
                if isinstance(column, tuple) else str(column)
                for column in table.columns
            ]
            if not any("Eliminations from consolidation" in column for column in columns):
                continue
            if observation_date is None and len(columns) > 2:
                match = re.search(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", columns[2])
                if match:
                    observation_date = pd.to_datetime(match.group(1), errors="coerce")
            labels = table.iloc[:, 0].astype(str).str.replace(r"\d+$", "", regex=True).str.strip()
            for series, label in targets.items():
                matches = table.index[labels == label].tolist()
                if not matches or table.shape[1] < 3:
                    continue
                value = pd.to_numeric(pd.Series([table.iloc[matches[0], 2]]), errors="coerce").iloc[0]
                if pd.notna(value):
                    values[series] = float(value)
        if observation_date is None or pd.isna(observation_date) or any(series not in values for series in targets):
            raise DataFetchError(f"美联储H.4.1未解析完整美元流动性输入: {values}")
        return {
            series: pd.DataFrame({"date": [observation_date], series: [value]})
            for series, value in values.items()
        }

    @staticmethod
    def _parse_nyfed_rrp(payload: dict[str, Any]) -> pd.DataFrame:
        repo = payload.get("repo") if isinstance(payload, dict) else None
        operations = repo.get("operations", []) if isinstance(repo, dict) else []
        rows: list[dict[str, Any]] = []
        for operation in operations if isinstance(operations, list) else []:
            if str(operation.get("operationType", "")).lower() != "reverse repo":
                continue
            date = pd.to_datetime(operation.get("operationDate"), errors="coerce")
            amount = pd.to_numeric(pd.Series([operation.get("totalAmtAccepted")]), errors="coerce").iloc[0]
            if pd.notna(date) and pd.notna(amount):
                rows.append({"date": date, "RRPONTSYD": float(amount) / 1_000_000_000})
        if not rows:
            raise DataFetchError("纽约联储逆回购API没有有效操作记录")
        out = pd.DataFrame(rows).groupby("date", as_index=False)["RRPONTSYD"].sum()
        return out.sort_values("date").reset_index(drop=True)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
    def _federal_reserve_fallback(self) -> dict[str, pd.DataFrame]:
        response = requests.get(self.H41_URL, timeout=25, headers=self._headers())
        response.raise_for_status()
        inputs = self._parse_h41_latest(response.text)
        h41_date = inputs["WALCL"]["date"].max()
        rrp_response = requests.get(
            self.NYFED_RRP_URL,
            params={
                "startDate": (h41_date - timedelta(days=14)).strftime("%Y-%m-%d"),
                "endDate": datetime.now().strftime("%Y-%m-%d"),
            },
            timeout=25,
            headers={**self._headers(), "Accept": "application/json"},
        )
        rrp_response.raise_for_status()
        inputs["RRPONTSYD"] = self._parse_nyfed_rrp(rrp_response.json())
        return inputs

    def fetch(self, start_date: str = "20150101") -> tuple[pd.DataFrame, dict[str, Any]]:
        start_key = date_key(start_date) or "20150101"
        raw: dict[str, pd.DataFrame] = {}
        input_sources: dict[str, str] = {}
        input_errors: dict[str, list[str]] = {}
        for series in ["WSHOSHO", "WALCL", "WDTGAL", "RRPONTSYD"]:
            errors: list[str] = []
            try:
                raw[series] = self._fred_csv(series, start_key)
                input_sources[series] = "FRED CSV"
            except Exception as exc:
                errors.append(repr(exc))
            if errors:
                input_errors[series] = errors
        missing = [series for series in ["WSHOSHO", "WALCL", "WDTGAL", "RRPONTSYD"] if series not in raw]
        if missing:
            try:
                official = self._federal_reserve_fallback()
                for series in missing:
                    if series in official:
                        raw[series] = official[series]
                        input_sources[series] = (
                            "New York Fed reverse-repo API fallback"
                            if series == "RRPONTSYD" else "Federal Reserve H.4.1 fallback"
                        )
            except Exception as exc:
                input_errors["official_fallback"] = [repr(exc)]
        missing = [series for series in ["WSHOSHO", "WALCL", "WDTGAL", "RRPONTSYD"] if series not in raw]
        for series in missing:
            try:
                raw[series] = self._fred_series_page(series)
                input_sources[series] = "FRED series page fallback"
            except Exception as exc:
                input_errors.setdefault(series, []).append(repr(exc))
        missing = [series for series in ["WSHOSHO", "WALCL", "WDTGAL", "RRPONTSYD"] if series not in raw]
        if missing:
            raise DataFetchError(f"FRED美元流动性输入失败: {missing}; {input_errors}")

        weekly = raw["WSHOSHO"].merge(raw["WALCL"], on="date", how="inner").merge(raw["WDTGAL"], on="date", how="inner")
        rrp = raw["RRPONTSYD"].rename(columns={"RRPONTSYD": "rrp_busd"})
        rrp_is_latest_only = len(rrp) == 1
        aligned = pd.merge_asof(
            weekly.sort_values("date"),
            rrp[["date", "rrp_busd"]].sort_values("date"),
            on="date",
            direction="nearest" if rrp_is_latest_only else "backward",
            tolerance=timedelta(days=7) if rrp_is_latest_only else None,
        ).dropna(subset=["WSHOSHO", "WALCL", "WDTGAL", "rrp_busd"])
        if aligned.empty:
            raise DataFetchError("美元净流动性序列对齐后为空")

        values = pd.DataFrame({
            "trade_date": aligned["date"].dt.strftime("%Y%m%d"),
            "net_liquidity_soma_trn": aligned["WSHOSHO"] / 1_000_000 - aligned["WDTGAL"] / 1_000_000 - aligned["rrp_busd"] / 1_000,
            "net_liquidity_total_assets_trn": aligned["WALCL"] / 1_000_000 - aligned["WDTGAL"] / 1_000_000 - aligned["rrp_busd"] / 1_000,
        })

        outputs = [
            ("net_liquidity_soma_trn", "NET_USD_LIQUIDITY_SOMA", "美元净流动性（SOMA-TGA-RRP）"),
            ("net_liquidity_total_assets_trn", "NET_USD_LIQUIDITY_TOTAL_ASSETS", "美元净流动性（总资产-TGA-RRP）"),
        ]
        output_source = (
            self.SOURCE
            if all(source == "FRED CSV" for source in input_sources.values())
            else "美联储H.4.1 / 纽约联储逆回购API（FRED备用）"
        )
        frames: list[pd.DataFrame] = []
        for column, series, name in outputs:
            frames.append(pd.DataFrame({
                "trade_date": values["trade_date"],
                "series": series,
                "name": name,
                "value_pct": values[column].round(4),
                "unit": "万亿美元",
                "source": output_source,
            }))

        out = pd.concat(frames, ignore_index=True).drop_duplicates(["trade_date", "series"], keep="last")
        details = {
            "status": "partial" if any("fallback" in source for source in input_sources.values()) else "success",
            "rows": len(out),
            "latest_date": str(out["trade_date"].max()),
            "source": output_source,
            "formula": "SOMA口径=WSHOSHO-WDTGAL-RRPONTSYD；总资产口径=WALCL-WDTGAL-RRPONTSYD；统一换算为万亿美元",
            "input_latest_dates": {series: date_key(frame["date"].max()) for series, frame in raw.items()},
            "input_sources": input_sources,
            "fallback_errors": input_errors,
            "fallback_alignment_note": (
                "RRP仅有页面最新值时，按7天内最近观测与周度资产负债表对齐。"
                if rrp_is_latest_only else None
            ),
        }
        return out.sort_values(["series", "trade_date"]).reset_index(drop=True), details


class ChinaLiquidityProvider:
    """Official/official-adapter money-market rates. DR is never replaced with FDR."""

    DR_URLS = [
        "https://www.chinamoney.com.cn/chinese/mkdatapm/?tab=2",
        "https://www.chinamoney.com.cn/english/mdtqapprp/",
        "https://www.chinamoney.com.cn/chinese/mkdatapm/",
    ]
    DR_CSV_URL = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/prr-chrt.csv"

    def __init__(self) -> None:
        self.ak = _import_akshare()

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (compatible; MacroScopePublic/5.2; research dashboard)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Referer": "https://www.chinamoney.com.cn/",
            "Cache-Control": "no-cache",
        }

    def fetch_shibor_overnight(self) -> pd.DataFrame:
        frame = self.ak.rate_interbank(
            market="上海银行同业拆借市场",
            symbol="Shibor人民币",
            indicator="隔夜",
        )
        if frame is None or frame.empty:
            raise DataFetchError("隔夜Shibor返回空表")
        date_col = pick_column(frame, ["报告日", "日期", "date"])
        value_col = pick_column(frame, ["利率", "今值", "value"])
        if date_col is None or value_col is None:
            raise DataFetchError(f"隔夜Shibor缺少字段: {list(frame.columns)}")
        out = pd.DataFrame({
            "trade_date": frame[date_col].map(date_key),
            "shibor_on_pct": numeric(frame[value_col]),
        })
        out = out.dropna(subset=["trade_date", "shibor_on_pct"]).drop_duplicates("trade_date", keep="last")
        return out.sort_values("trade_date")

    @staticmethod
    def _extract_rate(text: str, code: str) -> float | None:
        # Prefer a decimal after the exact code; this prevents capturing the 007 in DR007.
        cleaned = re.sub(r"\s+", " ", text)
        patterns = [
            rf"\b{re.escape(code)}\b[^0-9]{{0,120}}?([0-9]{{1,2}}\.[0-9]{{2,6}})",
            rf"\b{re.escape(code)}\b[^0-9]{{0,120}}?([0-9]{{1,2}}(?:\.[0-9]+)?)\s*%",
        ]
        for pattern in patterns:
            match = re.search(pattern, cleaned, flags=re.I | re.S)
            if match:
                value = float(match.group(1))
                if 0 <= value <= 30:
                    return value
        return None

    @staticmethod
    def _table_rate(table: pd.DataFrame, code: str) -> float | None:
        frame = table.copy()
        frame.columns = [" ".join(str(x) for x in col if str(x) != "nan") if isinstance(col, tuple) else str(col) for col in frame.columns]
        for _, row in frame.iterrows():
            row_text = " ".join(str(v) for v in row.tolist())
            if not re.search(rf"\b{re.escape(code)}\b", row_text, flags=re.I):
                continue
            preferred = [c for c in frame.columns if any(k in str(c).lower() for k in ["加权利率", "weighted rate", "weighted avg", "weighted average"])]
            for col in preferred:
                value = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
                if pd.notna(value) and 0 <= float(value) <= 30:
                    return float(value)
            # Conservative fallback: use decimal-like values only and ignore codes/terms/counts.
            candidates = re.findall(r"(?<!\d)([0-9]{1,2}\.[0-9]{2,6})(?!\d)", row_text)
            for raw in candidates:
                value = float(raw)
                if 0 <= value <= 30:
                    return value
        return None

    @staticmethod
    def _parse_dr_csv(text: str) -> pd.DataFrame:
        raw = pd.read_csv(io.StringIO(text), header=None)
        if raw.shape[1] < 4:
            raise DataFetchError(f"中国货币网DR CSV列数异常: {raw.shape[1]}")
        values = raw.iloc[:, -3:]
        out = pd.DataFrame({
            "trade_date": raw.iloc[:, 0].map(date_key),
            "dr001_pct": numeric(values.iloc[:, 0]),
            "dr007_pct": numeric(values.iloc[:, 1]),
        })
        out = out.dropna(subset=["trade_date"])
        out = out[out[["dr001_pct", "dr007_pct"]].notna().any(axis=1)]
        if out.empty:
            raise DataFetchError("中国货币网DR CSV没有有效DR001/DR007")
        out["source"] = "中国货币网 / 全国银行间同业拆借中心"
        return out.drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def _fetch_dr_csv(self) -> pd.DataFrame:
        response = requests.get(self.DR_CSV_URL, timeout=30, headers=self._headers())
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return self._parse_dr_csv(response.text)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=16), reraise=True)
    def _fetch_dr_page(self, url: str) -> tuple[float | None, float | None, str]:
        response = requests.get(url, timeout=45, headers=self._headers())
        response.raise_for_status()
        dr001 = dr007 = None
        try:
            for table in pd.read_html(io.StringIO(response.text)):
                dr001 = dr001 if dr001 is not None else self._table_rate(table, "DR001")
                dr007 = dr007 if dr007 is not None else self._table_rate(table, "DR007")
                if dr001 is not None and dr007 is not None:
                    break
        except Exception:
            pass
        soup = BeautifulSoup(response.text, "html.parser")
        text = " ".join(soup.stripped_strings)
        dr001 = dr001 if dr001 is not None else self._extract_rate(text, "DR001")
        dr007 = dr007 if dr007 is not None else self._extract_rate(text, "DR007")
        return dr001, dr007, url

    def fetch_dr_current(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        csv_errors: list[str] = []
        try:
            frame = self._fetch_dr_csv()
            latest = frame.iloc[-1]
            return frame, {
                "status": "success",
                "rows": len(frame),
                "latest_date": str(latest["trade_date"]),
                "dr001_pct": float(latest["dr001_pct"]) if pd.notna(latest["dr001_pct"]) else None,
                "dr007_pct": float(latest["dr007_pct"]) if pd.notna(latest["dr007_pct"]) else None,
                "urls_used": [self.DR_CSV_URL],
                "errors": [],
                "note": "DR001/DR007来自中国货币网官方历史CSV；未使用FDR替代。",
            }
        except Exception as exc:
            csv_errors.append(f"{self.DR_CSV_URL}: {exc!r}")

        dr001 = dr007 = None
        errors: list[str] = list(csv_errors)
        used: list[str] = []
        for url in self.DR_URLS:
            try:
                x1, x7, source = self._fetch_dr_page(url)
                if x1 is not None:
                    dr001 = x1
                if x7 is not None:
                    dr007 = x7
                used.append(source)
                if dr001 is not None and dr007 is not None:
                    break
            except Exception as exc:
                errors.append(f"{url}: {exc!r}")
        if dr001 is None and dr007 is None:
            raise DataFetchError("中国货币网未解析到DR001/DR007；严格不以FDR替代。" + " | ".join(errors))
        trade_date = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        frame = pd.DataFrame([{
            "trade_date": trade_date,
            "dr001_pct": dr001,
            "dr007_pct": dr007,
            "source": "中国货币网 / 全国银行间同业拆借中心",
        }])
        details = {
            "status": "success" if dr007 is not None else "partial",
            "dr001_pct": dr001,
            "dr007_pct": dr007,
            "urls_used": used,
            "errors": errors,
            "note": "DR007为存款类机构7天质押式回购加权利率；未使用FDR007替代。",
        }
        return frame, details

    def fetch(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        details: dict[str, Any] = {}
        shibor = pd.DataFrame()
        dr = pd.DataFrame()
        try:
            shibor = self.fetch_shibor_overnight()
            details["shibor_on"] = {"status": "success", "rows": len(shibor), "source": "中国货币网 / AKShare"}
        except Exception as exc:
            details["shibor_on"] = {"status": "failed", "error": repr(exc)}
        try:
            dr, dr_details = self.fetch_dr_current()
            details["dr"] = dr_details
        except Exception as exc:
            details["dr"] = {"status": "failed", "error": repr(exc), "note": "严格不使用FDR替代DR"}
        if shibor.empty and dr.empty:
            raise DataFetchError("DR与Shibor数据源均失败")
        if shibor.empty:
            out = dr.copy()
            out["shibor_on_pct"] = np.nan
        elif dr.empty:
            out = shibor.copy()
            out["dr001_pct"] = np.nan
            out["dr007_pct"] = np.nan
            out["source"] = "中国货币网 / AKShare"
        else:
            out = shibor.merge(dr, on="trade_date", how="outer", suffixes=("_shibor", "_dr"))
            source_dr = out["source_dr"] if "source_dr" in out.columns else pd.Series(index=out.index, dtype=object)
            source_shibor = out["source_shibor"] if "source_shibor" in out.columns else pd.Series(index=out.index, dtype=object)
            out["source"] = source_dr.fillna(source_shibor)
            out = out.drop(columns=[x for x in ["source_dr", "source_shibor"] if x in out.columns])
        return out.sort_values("trade_date"), details


class ChinaSentimentProvider:
    """A-share breadth, crowding, broad turnover, and margin leverage."""

    def __init__(self) -> None:
        self.ak = _import_akshare()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=12), reraise=True)
    def _call_frame(self, fn: Any, **kwargs: Any) -> pd.DataFrame:
        frame = fn(**kwargs)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise DataFetchError("数据源返回空表")
        return frame

    def _split_spot_em(self) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for fn_name in ["stock_sh_a_spot_em", "stock_sz_a_spot_em"]:
            fn = getattr(self.ak, fn_name, None)
            if not callable(fn):
                continue
            try:
                frame = self._call_frame(fn)
                frames.append(frame)
            except Exception:
                continue
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _spot(self) -> tuple[pd.DataFrame, str]:
        """Return one all-A-share snapshot with Eastmoney, Sina and split-market fallbacks."""
        errors: list[str] = []
        candidates: list[tuple[str, Any]] = [
            ("东方财富沪深京A股实时行情", getattr(self.ak, "stock_zh_a_spot_em", None)),
            ("新浪沪深京A股实时行情", getattr(self.ak, "stock_zh_a_spot", None)),
            ("东方财富沪深A股分市场行情", self._split_spot_em),
        ]
        for source, fn in candidates:
            if not callable(fn):
                errors.append(f"{source}: 当前AKShare版本无此接口")
                continue
            try:
                frame = self._call_frame(fn)
                clean = _clean_a_spot(frame)
                valid = clean[clean["amount"].notna() & (clean["amount"] > 0)]
                if len(valid) < 1000:
                    errors.append(f"{source}: 有效A股仅{len(valid)}只")
                    continue
                return clean, source + " / AKShare"
            except Exception as exc:
                errors.append(f"{source}: {exc!r}")
        raise DataFetchError("A股实时行情失败: " + " | ".join(errors))

    @staticmethod
    def _to_yuan(value: float | int | None, *, default_unit: str) -> float | None:
        if value is None or not np.isfinite(float(value)):
            return None
        number = float(value)
        if default_unit == "100m":
            return number * 1e8
        return number

    def _official_market_cap(self, trade_date: str) -> tuple[float | None, str]:
        """Get沪深股票总市值 from exchange market-summary tables as fallback."""
        errors: list[str] = []
        sse_cap_yuan: float | None = None
        szse_cap_yuan: float | None = None

        sse_fn = getattr(self.ak, "stock_sse_summary", None)
        if callable(sse_fn):
            try:
                sse = self._call_frame(sse_fn)
                item_col = pick_column(sse, ["项目", "item"])
                stock_col = pick_column(sse, ["股票", "stock"])
                if item_col and stock_col:
                    row = sse[sse[item_col].astype(str).str.replace(r"\s+", "", regex=True) == "总市值"]
                    if not row.empty:
                        raw = numeric(row[stock_col]).dropna()
                        if not raw.empty:
                            # 上交所股票数据总貌的总市值字段为亿元。
                            sse_cap_yuan = float(raw.iloc[-1]) * 1e8
            except Exception as exc:
                errors.append(f"上交所市场总貌: {exc!r}")

        sz_fn = getattr(self.ak, "stock_szse_summary", None)
        if callable(sz_fn):
            candidate_dates = pd.bdate_range(
                end=pd.to_datetime(trade_date), periods=8
            ).strftime("%Y%m%d").tolist()[::-1]
            for candidate in candidate_dates:
                try:
                    sz = self._call_frame(sz_fn, date=candidate)
                    category_col = pick_column(sz, ["证券类别", "类别"])
                    cap_col = pick_column(sz, ["总市值", "market_cap"])
                    if category_col and cap_col:
                        categories = sz[category_col].astype(str)
                        a_rows = sz[categories.str.contains("A股", na=False)]
                        target = a_rows if not a_rows.empty else sz[categories == "股票"]
                        values = numeric(target[cap_col]).dropna()
                        if not values.empty:
                            # 深交所市场总貌总市值字段为元。
                            szse_cap_yuan = float(values.sum())
                            break
                except Exception as exc:
                    errors.append(f"深交所市场总貌{candidate}: {exc!r}")

        parts = [x for x in [sse_cap_yuan, szse_cap_yuan] if x is not None and np.isfinite(x)]
        if not parts:
            return None, "；".join(errors[-4:])
        return float(sum(parts)), "上交所、深交所市场总貌 / AKShare"

    def fetch_snapshot(self, top_fraction: float) -> tuple[dict[str, Any], dict[str, Any]]:
        if not 0 < top_fraction <= 1:
            raise ValueError("top_fraction必须在(0,1]内")
        clean, source = self._spot()
        amount_clean = clean[clean["amount"].notna() & (clean["amount"] > 0)].copy()
        if amount_clean.empty:
            raise DataFetchError("没有有效A股成交额")
        total_amount = float(amount_clean["amount"].sum())
        top_count = max(1, math.ceil(len(amount_clean) * top_fraction))
        top_amount = float(amount_clean.nlargest(top_count, "amount")["amount"].sum())
        valid_pct = clean[clean["pct_change"].notna()].copy()
        up_count = int((valid_pct["pct_change"] > 0).sum())
        down_count = int((valid_pct["pct_change"] < 0).sum())
        flat_count = int((valid_pct["pct_change"] == 0).sum())
        trade_date = _latest_trade_date(self.ak)

        if clean["market_cap"].notna().any():
            total_market_cap = float(clean["market_cap"].dropna().sum())
            cap_source = source
        else:
            total_market_cap, cap_source = self._official_market_cap(trade_date)
            total_market_cap = float(total_market_cap) if total_market_cap is not None else np.nan

        crowding = {
            "trade_date": trade_date,
            "top_fraction": top_fraction,
            "stock_count": int(len(amount_clean)),
            "top_count": int(top_count),
            "top_amount_trillion": top_amount / 1e12,
            "total_amount_trillion": total_amount / 1e12,
            "crowding_pct": top_amount / total_amount * 100 if total_amount else np.nan,
            "source": source,
        }
        breadth = {
            "trade_date": trade_date,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "total_count": int(len(valid_pct)),
            "total_amount_trillion": total_amount / 1e12,
            "total_market_cap_trillion": total_market_cap / 1e12 if np.isfinite(total_market_cap) else np.nan,
            "broad_turnover_pct": total_amount / total_market_cap * 100 if np.isfinite(total_market_cap) and total_market_cap > 0 else np.nan,
            "source": source,
            "market_cap_source": cap_source,
        }
        return crowding, breadth

    @staticmethod
    def _extract_margin_total(frame: pd.DataFrame) -> float | None:
        if frame is None or frame.empty:
            return None
        col = pick_column(frame, ["融资融券余额", "融资余额合计", "融资融券余额(元)"], contains=["融资", "融券", "余额"])
        if col is None:
            return None
        values = numeric(frame[col]).dropna()
        if values.empty:
            return None
        text = frame.astype(str).agg(" ".join, axis=1)
        total_mask = text.str.contains("合计|总计", regex=True, na=False)
        if total_mask.any():
            totals = numeric(frame.loc[total_mask, col]).dropna()
            if not totals.empty:
                return float(totals.iloc[-1])
        if len(values) == 1:
            return float(values.iloc[0])
        return float(values.sum())

    @staticmethod
    def _latest_margin_from_history(frame: pd.DataFrame) -> tuple[str, float] | None:
        if frame is None or frame.empty:
            return None
        date_col = pick_column(frame, ["日期", "信用交易日期", "trade_date"])
        value_col = pick_column(frame, ["融资融券余额", "融资融券余额(元)"], contains=["融资", "融券", "余额"])
        if date_col is None or value_col is None:
            return None
        clean = pd.DataFrame({
            "trade_date": frame[date_col].map(date_key),
            "value": numeric(frame[value_col]),
        }).dropna(subset=["trade_date", "value"]).sort_values("trade_date")
        if clean.empty:
            return None
        row = clean.iloc[-1]
        return str(row["trade_date"]), float(row["value"])

    def fetch_margin(self, total_market_cap_trillion: float | None) -> dict[str, Any]:
        """Fetch沪深两融余额 with correct exchange-specific units and historical fallbacks."""
        target_date = _latest_trade_date(self.ak)
        start = (datetime.strptime(target_date, "%Y%m%d") - timedelta(days=25)).strftime("%Y%m%d")
        errors: list[str] = []
        parts_yuan: list[tuple[str, float, str]] = []

        # SSE official endpoint: RMB yuan.
        try:
            sse = self.ak.stock_margin_sse(start_date=start, end_date=target_date)
            date_col = pick_column(sse, ["信用交易日期", "日期", "trade_date"])
            if date_col:
                sse = sse.assign(_date=sse[date_col].map(date_key)).dropna(subset=["_date"]).sort_values("_date")
                latest_date = str(sse["_date"].max())
                sse = sse[sse["_date"] == latest_date]
            else:
                latest_date = target_date
            value = self._extract_margin_total(sse)
            if value is not None:
                parts_yuan.append((latest_date, float(value), "上交所融资融券汇总"))
        except Exception as exc:
            errors.append(f"上交所官方: {exc!r}")

        # SZSE official endpoint: 100 million RMB.
        candidate_dates = pd.bdate_range(end=pd.to_datetime(target_date), periods=10).strftime("%Y%m%d").tolist()[::-1]
        for candidate in candidate_dates:
            try:
                szse = self.ak.stock_margin_szse(date=candidate)
                value = self._extract_margin_total(szse)
                if value is not None:
                    parts_yuan.append((candidate, float(value) * 1e8, "深交所融资融券汇总"))
                    break
            except Exception as exc:
                errors.append(f"深交所官方{candidate}: {exc!r}")

        # Historical public adapters are used only when an exchange part is missing.
        have_sse = any("上交所" in x[2] for x in parts_yuan)
        have_szse = any("深交所" in x[2] for x in parts_yuan)
        if not have_sse:
            try:
                result = self._latest_margin_from_history(self.ak.macro_china_market_margin_sh())
                if result:
                    date_, value = result
                    parts_yuan.append((date_, value, "上海两融历史公开表"))
            except Exception as exc:
                errors.append(f"上海两融备用: {exc!r}")
        if not have_szse:
            try:
                result = self._latest_margin_from_history(self.ak.macro_china_market_margin_sz())
                if result:
                    date_, value = result
                    parts_yuan.append((date_, value, "深圳两融历史公开表"))
            except Exception as exc:
                errors.append(f"深圳两融备用: {exc!r}")

        if not parts_yuan:
            raise DataFetchError("沪深两融余额失败: " + " | ".join(errors[-8:]))

        total_yuan = float(sum(x[1] for x in parts_yuan))
        trade_date = min(x[0] for x in parts_yuan)
        cap = float(total_market_cap_trillion) if total_market_cap_trillion is not None else np.nan
        return {
            "trade_date": trade_date,
            "margin_balance_trillion": total_yuan / 1e12,
            "total_market_cap_trillion": cap,
            "margin_to_market_cap_pct": (total_yuan / 1e12) / cap * 100 if np.isfinite(cap) and cap > 0 else np.nan,
            "source": " + ".join(x[2] for x in parts_yuan),
            "note": "上交所按元；深交所汇总接口按亿元转换；备用历史表按元。",
        }


class FundSubscriptionProvider:
    """Newly established public-fund raised shares; no fake daily net subscription amount."""

    def __init__(self) -> None:
        self.ak = _import_akshare()

    def fetch(self, max_rows: int = 300) -> pd.DataFrame:
        frame = self.ak.fund_new_found_em()
        if frame is None or frame.empty:
            raise DataFetchError("新成立基金返回空表")
        code_col = pick_column(frame, ["基金代码"])
        name_col = pick_column(frame, ["基金简称"])
        type_col = pick_column(frame, ["基金类型"])
        shares_col = pick_column(frame, ["募集份额"])
        date_col = pick_column(frame, ["成立日期"])
        company_col = pick_column(frame, ["发行公司"])
        if code_col is None or name_col is None or shares_col is None or date_col is None:
            raise DataFetchError(f"新成立基金缺少字段: {list(frame.columns)}")
        out = pd.DataFrame({
            "founded_date": frame[date_col].map(date_key),
            "fund_code": frame[code_col].astype(str),
            "fund_name": frame[name_col].astype(str),
            "fund_type": frame[type_col].astype(str) if type_col else "",
            "fund_company": frame[company_col].astype(str) if company_col else "",
            "raised_shares_100m": numeric(frame[shares_col]),
        })
        out["estimated_raised_amount_100m"] = out["raised_shares_100m"]
        out["source"] = "天天基金新成立基金 / AKShare"
        out["method_note"] = "募集份额单位为亿份；按常见初始面值1元/份近似估算募集规模，非存续期每日净申购额"
        return out.dropna(subset=["founded_date", "raised_shares_100m"]).sort_values("founded_date").tail(max_rows).reset_index(drop=True)
