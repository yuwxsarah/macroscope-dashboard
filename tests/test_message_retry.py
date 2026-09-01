import unittest
from unittest.mock import patch

from scripts.update_messages import fetch_market_news


class MessageRetryTests(unittest.TestCase):
    def test_failed_symbol_is_retried_once(self) -> None:
        universe = [{"code": "300398", "symbol": "300398.SZ", "name": "飞凯材料"}]
        attempts = {"300398.SZ": 0}

        def flaky_fetcher(item, start_key, end_key):
            attempts[item["symbol"]] += 1
            if attempts[item["symbol"]] == 1:
                return [], f"{item['symbol']}: timeout"
            return [{"item_id": "ok"}], None

        with patch("scripts.update_messages.read_universe", return_value=universe):
            rows, errors, covered, failed = fetch_market_news(31, 2, 0, fetcher=flaky_fetcher)

        self.assertEqual(attempts["300398.SZ"], 2)
        self.assertEqual(rows, [{"item_id": "ok"}])
        self.assertEqual(errors, [])
        self.assertEqual(covered, 1)
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
