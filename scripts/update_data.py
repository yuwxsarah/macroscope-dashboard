from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline import update_selected  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update MacroScope public datasets")
    parser.add_argument(
        "--mode",
        choices=["all", "macro", "global", "close", "deviation", "fund", "evening"],
        default="all",
        help="all=全部；macro=中国宏观与资金利率；global=全球行情与美债；close=A股收盘情绪/估值；deviation=仅指数偏离度；fund=基金募集；evening=央行宏观+资金利率+基金募集",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(json.dumps(update_selected(args.mode), ensure_ascii=False, indent=2))
