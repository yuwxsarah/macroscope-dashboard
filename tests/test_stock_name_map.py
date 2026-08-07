import unittest

from scripts.build_site import read_stock_name_map


class StockNameMapTests(unittest.TestCase):
    def test_full_message_history_supplies_watchlist_names(self) -> None:
        names = read_stock_name_map()

        self.assertEqual(names["600519.SH"], "贵州茅台")
        self.assertEqual(names["300750.SZ"], "宁德时代")


if __name__ == "__main__":
    unittest.main()
