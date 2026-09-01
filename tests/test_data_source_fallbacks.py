import unittest
from unittest.mock import patch

import pandas as pd

from src.extended_providers import ChinaLiquidityProvider, FredTreasuryProvider, UsdLiquidityProvider
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

    def test_official_fed_fallback_can_align_rrp(self) -> None:
        provider = UsdLiquidityProvider()
        official = {
            "WSHOSHO": pd.DataFrame({"date": [pd.Timestamp("2026-08-26")], "WSHOSHO": [6462101.0]}),
            "WALCL": pd.DataFrame({"date": [pd.Timestamp("2026-08-26")], "WALCL": [6730912.0]}),
            "WDTGAL": pd.DataFrame({"date": [pd.Timestamp("2026-08-26")], "WDTGAL": [959435.0]}),
            "RRPONTSYD": pd.DataFrame({
                "date": [pd.Timestamp("2026-08-25"), pd.Timestamp("2026-08-26")],
                "RRPONTSYD": [0.15, 0.20],
            }),
        }

        with patch.object(provider, "_fred_csv", side_effect=TimeoutError("csv timeout")), patch.object(
            provider, "_federal_reserve_fallback", return_value=official
        ), patch.object(
            provider, "_fred_series_page", side_effect=AssertionError("FRED page should not run")
        ):
            frame, details = provider.fetch("20260801")

        self.assertEqual(details["status"], "partial")
        self.assertEqual(details["latest_date"], "20260826")
        self.assertEqual(details["source"], "美联储H.4.1 / 纽约联储逆回购API（FRED备用）")
        self.assertTrue((frame["source"] == details["source"]).all())
        self.assertEqual(len(frame), 2)

    def test_h41_and_nyfed_parsers(self) -> None:
        html = """
        <table><thead><tr>
          <th>Assets, liabilities, and capital</th>
          <th>Eliminations from consolidation</th>
          <th>Wednesday Aug 26, 2026</th>
          <th>Change</th>
        </tr></thead><tbody>
          <tr><td>Securities held outright1</td><td></td><td>6462101</td><td>-13202</td></tr>
          <tr><td>Total assets</td><td>0</td><td>6730912</td><td>-14787</td></tr>
          <tr><td>U.S. Treasury, General Account</td><td></td><td>959435</td><td>23029</td></tr>
        </tbody></table>
        """
        h41 = UsdLiquidityProvider._parse_h41_latest(html)
        self.assertEqual(h41["WSHOSHO"].iloc[0]["WSHOSHO"], 6462101.0)
        self.assertEqual(h41["WALCL"].iloc[0]["WALCL"], 6730912.0)
        self.assertEqual(h41["WDTGAL"].iloc[0]["WDTGAL"], 959435.0)

        payload = {"repo": {"operations": [
            {"operationDate": "2026-08-26", "operationType": "Reverse Repo", "totalAmtAccepted": 175000000},
            {"operationDate": "2026-08-26", "operationType": "Reverse Repo", "totalAmtAccepted": 25000000},
        ]}}
        rrp = UsdLiquidityProvider._parse_nyfed_rrp(payload)
        self.assertAlmostEqual(rrp.iloc[0]["RRPONTSYD"], 0.2)

    def test_treasury_uses_h15_before_fred_text(self) -> None:
        provider = FredTreasuryProvider()
        latest = pd.DataFrame([{"trade_date": "20260828", "value_pct": 4.0}])
        with patch.object(provider, "_fred_csv", side_effect=TimeoutError("csv timeout")), patch.object(
            provider, "_h15_latest", return_value=latest
        ), patch.object(provider, "_fred_txt", side_effect=AssertionError("TXT should not run")):
            frame, details = provider.fetch_with_details("20260801")

        self.assertEqual(len(frame), 2)
        self.assertEqual(details["DGS2"]["source"], "美联储H.15当前发布页")
        self.assertEqual(details["DGS10"]["source"], "美联储H.15当前发布页")

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
