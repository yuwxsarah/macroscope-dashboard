from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils import DATA_DIR, ensure_dirs, write_csv_atomic, write_json

SCHEMAS = {
    "macro.csv": ["month","m1_trillion","m1_yoy_pct","m2_trillion","m2_yoy_pct","m1_m2_gap_pp","m1_m2_mechanical_sum_trillion","sf_increment_trillion","sf_increment_yoy_pct","sf_12m_trillion","sf_12m_yoy_pct","sf_stock_trillion","sf_stock_yoy_pct","pmi_manufacturing","pmi_non_manufacturing","cpi_yoy_pct","cpi_mom_pct","source"],
    "liquidity.csv": ["trade_date","dr001_pct","dr007_pct","shibor_on_pct","source"],
    "market.csv": ["trade_date","symbol","name","market","currency","asset_group","close","pct_change","volume","amount","source"],
    "global_macro.csv": ["trade_date","series","name","value_pct","unit","source"],
    "valuation.csv": ["trade_date","index_code","index_name","pe_ttm","pb","source"],
    "crowding.csv": ["trade_date","top_fraction","stock_count","top_count","top_amount_trillion","total_amount_trillion","crowding_pct","source","snapshot_kind"],
    "breadth.csv": ["trade_date","up_count","down_count","flat_count","total_count","total_amount_trillion","total_market_cap_trillion","broad_turnover_pct","source","snapshot_kind"],
    "leverage.csv": ["trade_date","margin_balance_trillion","total_market_cap_trillion","margin_to_market_cap_pct","source","note"],
    "deviation.csv": ["trade_date","symbol","name","period","period_label","close","ma20","ma30","dev20_pct","dev30_pct","source"],
    "bull_deviation_reference.csv": ["symbol","index_name","bull_round","high","week20_ratio","week30_ratio","month20_ratio","month30_ratio","week20_dev_pct","week30_dev_pct","month20_dev_pct","month30_dev_pct"],
    "fund_subscription.csv": ["founded_date","fund_code","fund_name","fund_type","fund_company","raised_shares_100m","estimated_raised_amount_100m","source","method_note"],
    "a_share_universe.csv": ["code","exchange","name","source","updated_at"],
    "market_messages.csv": ["published_at","category","title","summary","source","source_url","symbol","stock_name","importance","status","source_type","item_id"],
    "market_tracking.csv": ["trade_date","market_score","risk_score","suggested_position_pct","risk_level","market_phase","style_signal","source"],
}


def main() -> None:
    ensure_dirs()
    created = []
    for filename, columns in SCHEMAS.items():
        path = DATA_DIR / filename
        if not path.exists() or path.stat().st_size == 0:
            write_csv_atomic(pd.DataFrame(columns=columns), path)
            created.append(filename)
    status = DATA_DIR / "status.json"
    if not status.exists():
        write_json(status, {"app_version":"6.0.0","overall_status":"empty","updated_at":None,"datasets":{}})
    print(json.dumps({"created": created, "checked": list(SCHEMAS)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
