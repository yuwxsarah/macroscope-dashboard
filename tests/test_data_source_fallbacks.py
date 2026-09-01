import unittest
from unittest.mock import patch

import pandas as pd

from src.extended_providers import ChinaLiquidityProvider, UsdLiquidityProvider
from src.providers import ChinaMarketProvider, PublicMacroProvider


class DataSourceFallbackTests(unittest.TestCase):
    def test_chinamoney_dr_csv_parser(self) -> None:
        text = "2026-08-31,,,,,,1.4219,1.4180,1.4376\n2026-08-28,,,,,,1.3333,1.4444,1.5555\n"
        frame = ChinaLiquidityProvider._parse_dr_csv(text)
        self.assertEqual(frame.iloc[-1]["trade_date"], "20260831")
        self.assertAlmostEqual(frame.iloc[-1]["dr001_pct"], 1.4219)
        self.assertAlmostEqual(frame.iloc[-1]["dr007_pct"], 1.4180)

    def test_fred_series_page_parser(self) -> None:
        html = """
        <input type="hidden" id="coed" value="2026-08-26">
        <span class="series-meta-observation-value">6,462,101</span>
        """
        frame = UsdLiquidityProvider._parse_fred_series_page(html, "WSHOSHO")
        self.assertEqual(frame.iloc[0]["date"].strftime("%Y%m%d"), "20260826")
        self.assertEqual(frame.iloc[0]["WSHOSHO"], 6462101.0)

    def test_fred_page_fallback_can_align_latest_rrp(self) -> None:
        provider = UsdLiquidityProvider()

        def page_value(series):
            dates = {"RRPONTSYD": "2026-08-31"}
            values = {
                "WSHOSHO": 6462101.0,
                "WALCL": 6600000.0,
                "WDTGAL": 800000.0,
                "RRPONTSYD": 40.0,
            }
            return pd.DataFrame({"date": [pd.Timestamp(dates.get(series, "2026-08-26"))], series: [values[series]]})

        with patch.object(provider, "_fred_csv", side_effect=TimeoutError("csv timeout")), patch.object(
            provider, "_fred_series_page", side_effect=page_value
        ):
            frame, details = provider.fetch("20260801")

        self.assertEqual(details["status"], "partial")
        self.assertEqual(details["latest_date"], "20260826")
        self.assertEqual(len(frame), 2)

    def test_pbc_report_parser(self) -> None:
        title = "2026年7月金融统计数据报告"
        text = (
            "2026年7月末社会融资规模存量为463.27万亿元，同比增长7.4%。"
            "前七个月社会融资规模增量累计为22.25万亿元。"
        )
        row = PublicMacroProvider._parse_pbc_social_report(title, text)
        self.assertEqual(row["month"], "202607")
        self.assertEqual(row["sf_stock_trillion"], 463.27)
        self.assertEqual(row["sf_stock_yoy_pct"], 7.4)
        self.assertEqual(row["sf_cumulative_trillion"], 22.25)

    def test_eastmoney_valuation_parser(self) -> None:
        payload = {
            "result": {
                "data": [{
                    "TRADE_MARKET_CODE": "399001",
                    "TRADE_DATE": "2026-08-31 00:00:00",
                    "PE_TTM_AVG": 25.21,
                }]
            }
        }
        item = {"code": "399001", "symbol": "399001.SZ", "name": "深证成指"}
        frame = ChinaMarketProvider._parse_eastmoney_valuation_payload(
            payload, item, "20260101", "20261231"
        )
        self.assertEqual(frame.iloc[0]["trade_date"], "20260831")
        self.assertEqual(frame.iloc[0]["index_code"], "399001.SZ")
        self.assertAlmostEqual(frame.iloc[0]["pe_ttm"], 25.21)


if __name__ == "__main__":
    unittest.main()
