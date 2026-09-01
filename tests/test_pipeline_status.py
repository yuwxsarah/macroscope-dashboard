import unittest

import pandas as pd

from src.pipeline import _dataset_status, _merge_history


class PipelineStatusTests(unittest.TestCase):
    def test_nested_failure_makes_dataset_partial(self) -> None:
        metadata = {
            "source_details": {
                "DGS10": {"status": "success"},
                "USD_LIQUIDITY": {"status": "failed", "error": "timeout"},
            }
        }
        self.assertEqual(_dataset_status(50, metadata), "partial")

    def test_all_nested_sources_success(self) -> None:
        metadata = {"indices": {"399001.SZ": {"status": "success"}}}
        self.assertEqual(_dataset_status(50, metadata), "success")

    def test_no_new_rows_is_partial(self) -> None:
        self.assertEqual(_dataset_status(0, {}), "partial")

    def test_partial_refresh_does_not_erase_cached_field(self) -> None:
        old = pd.DataFrame([{"month": "202607", "sf_increment_trillion": 1.4, "sf_stock_trillion": 463.0}])
        new = pd.DataFrame([{"month": "202607", "sf_increment_trillion": 1.41, "sf_stock_trillion": None}])
        merged = _merge_history(old, new, ["month"])
        self.assertEqual(merged.iloc[0]["sf_increment_trillion"], 1.41)
        self.assertEqual(merged.iloc[0]["sf_stock_trillion"], 463.0)


if __name__ == "__main__":
    unittest.main()
