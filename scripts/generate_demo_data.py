from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils import DATA_DIR, ensure_dirs, write_csv_atomic, write_json  # noqa: E402

MACRO_PATH = DATA_DIR / "macro.csv"
MARKET_PATH = DATA_DIR / "market.csv"
GLOBAL_MACRO_PATH = DATA_DIR / "global_macro.csv"
LIQUIDITY_PATH = DATA_DIR / "liquidity.csv"
VALUATION_PATH = DATA_DIR / "valuation.csv"
CROWDING_PATH = DATA_DIR / "crowding.csv"
BREADTH_PATH = DATA_DIR / "breadth.csv"
LEVERAGE_PATH = DATA_DIR / "leverage.csv"
DEVIATION_PATH = DATA_DIR / "deviation.csv"
FUND_PATH = DATA_DIR / "fund_subscription.csv"
STATUS_PATH = DATA_DIR / "status.json"

rng = np.random.default_rng(42)


def dates(start: str, periods: int, freq: str = "B") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq=freq)


def main() -> None:
    ensure_dirs()
    months = pd.date_range("2020-01-01", periods=78, freq="MS")
    t = np.arange(len(months))
    m1_yoy = 4 + 2.2 * np.sin(t / 7) + rng.normal(0, .35, len(t))
    m2_yoy = 8 + 1.4 * np.sin(t / 11) + rng.normal(0, .25, len(t))
    macro = pd.DataFrame({
        "month": months.strftime("%Y%m"), "m1_trillion": 55 + t * .45,
        "m1_yoy_pct": m1_yoy, "m2_trillion": 210 + t * 1.8,
        "m2_yoy_pct": m2_yoy, "sf_increment_trillion": 2.2 + rng.normal(0,.6,len(t)),
        "sf_stock_trillion": 280 + t * 1.8, "sf_stock_yoy_pct": 9 + np.sin(t/8),
        "pmi_manufacturing": 50 + 1.2*np.sin(t/5), "pmi_non_manufacturing": 51 + 1.4*np.sin(t/6),
        "cpi_yoy_pct": 1.2 + 1.1*np.sin(t/9), "cpi_mom_pct": rng.normal(.1,.2,len(t)),
    })
    macro["m1_m2_gap_pp"] = macro["m1_yoy_pct"] - macro["m2_yoy_pct"]
    macro["m1_m2_mechanical_sum_trillion"] = macro["m1_trillion"] + macro["m2_trillion"]
    macro["sf_increment_yoy_pct"] = macro["sf_increment_trillion"].pct_change(12)*100
    macro["sf_12m_trillion"] = macro["sf_increment_trillion"].rolling(12).sum()
    macro["sf_12m_yoy_pct"] = macro["sf_12m_trillion"].pct_change(12)*100
    macro["source"] = "内置演示数据（首次在线更新后自动替换）"
    write_csv_atomic(macro, MACRO_PATH)

    d = dates("2024-01-02", 520)
    liquidity = pd.DataFrame({"trade_date": d.strftime("%Y%m%d"),
        "dr001_pct": 1.7 + .25*np.sin(np.arange(len(d))/18)+rng.normal(0,.04,len(d)),
        "dr007_pct": 1.9 + .28*np.sin(np.arange(len(d))/21)+rng.normal(0,.04,len(d)),
        "shibor_on_pct": 1.75 + .24*np.sin(np.arange(len(d))/19)+rng.normal(0,.04,len(d)),
        "source":"内置演示数据（首次在线更新后自动替换）"})
    write_csv_atomic(liquidity, LIQUIDITY_PATH)

    items = [
        ("000688.SH","科创50","CN_INDEX","点","中国科技指数",1000),
        ("931087.CSI","中国科技龙头","CN_INDEX","点","中国科技指数",1200),
        ("^IXIC","纳斯达克综合指数","US_INDEX","点","美国主要指数",15000),
        ("^GSPC","标普500指数","US_INDEX","点","美国主要指数",5000),
        ("^DJI","道琼斯工业指数","US_INDEX","点","美国主要指数",38000),
        ("DX-Y.NYB","美元指数","FX_INDEX","点","美元与利率",104),
        ("AAPL","苹果","US_EQUITY","USD","科技龙头",180),("MSFT","微软","US_EQUITY","USD","科技龙头",400),
        ("NVDA","英伟达","US_EQUITY","USD","科技龙头",120),("AMZN","亚马逊","US_EQUITY","USD","科技龙头",180),
        ("GOOGL","谷歌母公司","US_EQUITY","USD","科技龙头",170),("META","Meta","US_EQUITY","USD","科技龙头",500),
        ("TSLA","特斯拉","US_EQUITY","USD","科技龙头",220),("MU","美光科技","US_EQUITY","USD","科技龙头",110),
        ("005930.KS","三星电子","KR_EQUITY","KRW","科技龙头",70000),("000660.KS","SK海力士","KR_EQUITY","KRW","科技龙头",180000),
    ]
    frames=[]
    for i,(sym,name,market,currency,group,base) in enumerate(items):
        rets=rng.normal(.00035,.015,len(d)); close=base*np.exp(np.cumsum(rets))
        frames.append(pd.DataFrame({"trade_date":d.strftime("%Y%m%d"),"symbol":sym,"name":name,"market":market,"currency":currency,"asset_group":group,"close":close,"pct_change":pd.Series(close).pct_change()*100,"volume":rng.integers(1e6,2e8,len(d)),"amount":np.nan,"source":"内置演示数据（首次在线更新后自动替换）"}))
    market=pd.concat(frames,ignore_index=True);write_csv_atomic(market,MARKET_PATH)

    gm=[]
    for series,name,base in [("DGS2","美国2年期国债收益率",4.3),("DGS10","美国10年期国债收益率",4.4)]:
        gm.append(pd.DataFrame({"trade_date":d.strftime("%Y%m%d"),"series":series,"name":name,"value_pct":base+.35*np.sin(np.arange(len(d))/45)+rng.normal(0,.03,len(d)),"unit":"%","source":"内置演示数据（首次在线更新后自动替换）"}))
    write_csv_atomic(pd.concat(gm,ignore_index=True),GLOBAL_MACRO_PATH)

    val=[]
    for j,(code,name) in enumerate([("000001.SH","上证综指"),("399001.SZ","深证成指"),("399006.SZ","创业板指"),("000688.SH","科创50"),("000016.SH","上证50"),("000300.SH","沪深300"),("000905.SH","中证500")]):
        v=14+j+2*np.sin(np.arange(len(d))/80)+rng.normal(0,.3,len(d))
        val.append(pd.DataFrame({"trade_date":d.strftime("%Y%m%d"),"index_code":code,"index_name":name,"pe_ttm":v,"pb":1.4+j*.12,"source":"内置演示数据（首次在线更新后自动替换）"}))
    write_csv_atomic(pd.concat(val,ignore_index=True),VALUATION_PATH)

    total=1.0+.5*np.sin(np.arange(len(d))/30)+rng.normal(0,.08,len(d));total=np.clip(total,.35,None)
    crowd=38+8*np.sin(np.arange(len(d))/26)+rng.normal(0,2,len(d))
    write_csv_atomic(pd.DataFrame({"trade_date":d.strftime("%Y%m%d"),"top_fraction":.05,"stock_count":5200,"top_count":260,"top_amount_trillion":total*crowd/100,"total_amount_trillion":total,"crowding_pct":crowd,"source":"内置演示数据（首次在线更新后自动替换）","snapshot_kind":"演示快照"}),CROWDING_PATH)
    up=np.clip((2600+900*np.sin(np.arange(len(d))/17)+rng.normal(0,220,len(d))).astype(int),100,5000);down=np.clip(5200-up-rng.integers(40,150,len(d)),50,5000)
    cap=90+np.arange(len(d))*.02
    write_csv_atomic(pd.DataFrame({"trade_date":d.strftime("%Y%m%d"),"up_count":up,"down_count":down,"flat_count":5200-up-down,"total_count":5200,"total_amount_trillion":total,"total_market_cap_trillion":cap,"broad_turnover_pct":total/cap*100,"source":"内置演示数据（首次在线更新后自动替换）","snapshot_kind":"演示快照"}),BREADTH_PATH)
    margin=1.55+.18*np.sin(np.arange(len(d))/50)
    write_csv_atomic(pd.DataFrame({"trade_date":d.strftime("%Y%m%d"),"margin_balance_trillion":margin,"total_market_cap_trillion":cap,"margin_to_market_cap_pct":margin/cap*100,"source":"内置演示数据（首次在线更新后自动替换）","note":"演示"}),LEVERAGE_PATH)

    dev=[]
    for sym,name in [("000001.SH","上证综指"),("399001.SZ","深证成指"),("399006.SZ","创业板指"),("000688.SH","科创50"),("000300.SH","沪深300")]:
        g=market[market.symbol==sym].copy();g["date"]=pd.to_datetime(g.trade_date)
        for period,label,freq in [("week","周K","W-FRI"),("month","月K","M")]:
            x=g.set_index("date").close.resample(freq).last().dropna().to_frame("close")
            x["ma20"]=x.close.rolling(20).mean();x["ma30"]=x.close.rolling(30).mean()
            x["dev20_pct"]=(x.close/x.ma20-1)*100;x["dev30_pct"]=(x.close/x.ma30-1)*100
            x=x.reset_index();x["trade_date"]=x.date.dt.strftime("%Y%m%d");x["symbol"]=sym;x["name"]=name
            x["period"]=period;x["period_label"]=label;x["source"]="演示数据"
            dev.append(x[["trade_date","symbol","name","period","period_label","close","ma20","ma30","dev20_pct","dev30_pct","source"]])
    write_csv_atomic(pd.concat(dev,ignore_index=True),DEVIATION_PATH)

    fdates=pd.date_range("2025-01-03",periods=80,freq="5D")
    funds=pd.DataFrame({"founded_date":fdates.strftime("%Y%m%d"),"fund_code":[f"0{i:05d}" for i in range(80)],"fund_name":[f"示例成长基金{i+1}" for i in range(80)],"fund_type":rng.choice(["混合型","股票型","指数型"],80),"fund_company":rng.choice(["甲基金","乙基金","丙基金"],80),"raised_shares_100m":rng.uniform(2,80,80)})
    funds["estimated_raised_amount_100m"]=funds.raised_shares_100m;funds["source"]="演示数据";funds["method_note"]="演示";write_csv_atomic(funds,FUND_PATH)

    datasets={k:{"status":"demo","rows":1,"cached_rows":1,"latest_date":d[-1].strftime("%Y%m%d")} for k in ["macro","liquidity","market","global_macro","valuation","crowding","breadth","leverage","deviation","fund_subscription"]}
    write_json(STATUS_PATH,{"app_version":"6.0.0","updated_at":pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),"overall_status":"demo","last_update_mode":"demo","datasets":datasets})
    print("Generated labelled demonstration data. The first successful GitHub update replaces it with live public data.")


if __name__ == "__main__":
    main()
