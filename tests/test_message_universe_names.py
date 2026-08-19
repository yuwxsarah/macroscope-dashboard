import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.update_messages import ensure_universe_names


class MessageUniverseNameTests(unittest.TestCase):
    def test_legacy_universe_without_names_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            universe_path = Path(temp_dir) / "a_share_universe.csv"
            pd.DataFrame([{"code": "300398", "exchange": "SZ"}]).to_csv(
                universe_path, index=False, encoding="utf-8-sig"
            )

            def fake_fetcher():
                return [{"code": "300398", "exchange": "SZ", "name": "飞凯材料"}]

            with patch("scripts.update_messages.UNIVERSE_PATH", universe_path):
                count, error = ensure_universe_names(fake_fetcher, min_rows=1)

            repaired = pd.read_csv(universe_path, dtype={"code": str})
            self.assertIsNone(error)
            self.assertEqual(count, 1)
            self.assertEqual(repaired.loc[0, "code"], "300398")
            self.assertEqual(repaired.loc[0, "name"], "飞凯材料")

    def test_complete_universe_does_not_refetch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            universe_path = Path(temp_dir) / "a_share_universe.csv"
            pd.DataFrame([
                {"code": "300398", "exchange": "SZ", "name": "飞凯材料"},
            ]).to_csv(universe_path, index=False, encoding="utf-8-sig")

            def unexpected_fetcher():
                raise AssertionError("complete cache should not be fetched again")

            with patch("scripts.update_messages.UNIVERSE_PATH", universe_path):
                count, error = ensure_universe_names(unexpected_fetcher, min_rows=1)

            self.assertIsNone(error)
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
