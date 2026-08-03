from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils import DATA_DIR, read_csv_safe, write_csv_atomic, write_json  # noqa: E402

BEIJING = ZoneInfo("Asia/Shanghai")
OUTPUT_JSON = DATA_DIR / "market_tracking.json"
OUTPUT_CSV = DATA_DIR / "market_tracking.csv"
STATUS_PATH = DATA_DIR / "status.json"

HISTORY_COLUMNS = [
    "trade_date",
    "market_score",
    "risk_score",
    "suggested_position_pct",
    "risk_level",
    "market_phase",
    "style_signal",
    "source",
]

FRAMEWORK_SECTIONS = [
    {
        "title": "模型定位",
        "subtitle": "大盘模型不预测指数点位，只回答当前A股是否值得承担权益风险。",
        "items": ["决定总仓位应该是多少", "判断当前进攻、防守还是等待", "识别当前更适合的市场风格", "只决定组合上限，不直接选择股票"],
    },
    {
        "title": "七大机会模块",
        "subtitle": "每个模块先打0-5分，再按权重折算成100分制的大盘机会评分。",
        "items": ["宏观周期 15%", "流动性环境 20%", "政策环境 15%", "盈利周期 15%", "估值水平 15%", "市场情绪 10%", "风格结构 10%"],
    },
    {
        "title": "流动性硬约束",
        "subtitle": "流动性是A股大盘模型的关键闸门。",
        "items": ["流动性原始分 <= 1.5：大盘总分原则上不高于50", "流动性原始分 <= 2.0：不建议重仓进攻", "流动性原始分 >= 4.0 且政策、估值配合：可能进入估值修复"],
    },
    {
        "title": "风险领先预警模型",
        "subtitle": "大盘评分决定应有仓位，风险预警决定仓位是否需要提前打折。",
        "items": ["市场广度恶化 20", "量价质量恶化 20", "主线健康度下降 20", "杠杆与资金拥挤 15", "跨资产压力 10", "估值与盈利背离 10", "政策反应钝化 5"],
    },
    {
        "title": "模型用途",
        "subtitle": "框架更适合仓位管理、风险收益状态识别和高集中度组合风控。",
        "items": ["单独机会评分不用于预测下一周涨跌", "重点看机会和风险的背离", "结构行情中约束追高", "高集中度组合的第一道刹车"],
    },
    {
        "title": "最终决策链",
        "subtitle": "大盘决定仓位，产业决定方向，个股决定标的，交易决定节奏，风控决定生存。",
        "items": ["大盘模型 -> 决定总仓位", "风险领先预警 -> 决定仓位折扣", "产业模型 -> 决定配置方向", "个股模型 -> 决定标的优先级", "交易模型 -> 决定买卖节奏", "组合风控 -> 控制集中度与回撤"],
    },
]

PROCESS_STEPS = ["市场环境判断", "产业方向选择", "个股筛选", "交易执行", "风险控制"]

MARKET_MODULES = [
    ("宏观周期", 15),
    ("流动性环境", 20),
    ("政策环境", 15),
    ("盈利周期", 15),
    ("估值水平", 15),
    ("市场情绪", 10),
    ("风格结构", 10),
]

RISK_MODULES = [
    ("市场广度恶化", 20),
    ("量价质量恶化", 20),
    ("主线健康度下降", 20),
    ("杠杆与资金拥挤", 15),
    ("跨资产压力", 10),
    ("估值盈利背离", 10),
    ("政策反应钝化", 5),
]

MODULE_BLUEPRINT = [
    {"name": "宏观周期", "weight": 15, "subitems": ["经济动能 25%：PMI、工业增加值、社零、投资", "信用周期 25%：社融、信贷、M1、M2", "地产链 20%：销售、投资、新开工、竣工", "外需 15%：出口、海外制造业、汇率", "价格与库存 15%：CPI、PPI、工业品价格、库存"]},
    {"name": "流动性环境", "weight": 20, "subitems": ["货币流动性 30%：央行态度、DR007、MLF、逆回购", "信用流动性 25%：社融、信贷、信用利差", "市场流动性 25%：成交额、换手率、融资余额、ETF", "外部流动性 20%：美债利率、美元、人民币、外资偏好"]},
    {"name": "政策环境", "weight": 15, "subitems": ["宏观政策 30%", "资本市场政策 25%", "产业政策 25%", "监管环境 20%", "同时看方向、力度、落地速度、预期程度和能否转化为盈利"]},
    {"name": "盈利周期", "weight": 15, "subitems": ["全A盈利增速 30%", "盈利预期修正 25%", "利润率趋势 20%", "ROE质量 15%", "盈利扩散度 10%"]},
    {"name": "估值水平", "weight": 15, "subitems": ["宽基指数估值 30%", "全市场估值 20%", "股债性价比 30%", "结构估值 20%"]},
    {"name": "市场情绪", "weight": 10, "subitems": ["成交活跃度 25%", "市场广度 25%", "杠杆资金 15%", "极端指标 20%", "资金行为 15%"]},
    {"name": "风格结构", "weight": 10, "subitems": ["大小盘风格 25%", "成长价值风格 25%", "行业主线 30%", "拥挤度 20%"]},
]

OPPORTUNITY_BANDS = [
    {"range": "80-100", "stage": "进攻期", "position": "80%-95%", "single_stock": "20%", "industry": "50%", "theme": "40%"},
    {"range": "65-80", "stage": "修复期", "position": "60%-80%", "single_stock": "18%", "industry": "45%", "theme": "35%"},
    {"range": "50-65", "stage": "震荡期", "position": "40%-60%", "single_stock": "15%", "industry": "35%", "theme": "25%"},
    {"range": "35-50", "stage": "防御期", "position": "20%-40%", "single_stock": "10%", "industry": "25%", "theme": "15%"},
    {"range": "0-35", "stage": "风险期", "position": "0%-20%", "single_stock": "5%", "industry": "15%", "theme": "10%"},
]

RISK_BANDS = [
    {"range": "0-20", "status": "绿色", "action": "正常持仓", "discount": "100%"},
    {"range": "20-35", "status": "黄色", "action": "停止追高", "discount": "90%"},
    {"range": "35-50", "status": "橙色", "action": "提前降风险", "discount": "75%-85%"},
    {"range": "50-70", "status": "红色", "action": "系统性降仓", "discount": "50%-70%"},
    {"range": "70以上", "status": "深红", "action": "防御优先", "discount": "30%-50%"},
]

SIGNAL_GROUPS = [
    {"name": "领先信号", "action": "停止加仓，降低后排，减少高波动和纯情绪仓。", "items": ["指数未跌但上涨家数减少", "主线仍涨但龙头滞涨", "成交很大但指数不创新高", "融资继续增加但指数涨不动", "利好出现但市场反应越来越弱"]},
    {"name": "同步信号", "action": "系统性降仓。", "items": ["科技成长高位放量下跌", "指数跌破短期趋势", "成交明显萎缩", "强势板块开始补跌"]},
    {"name": "滞后信号", "action": "只适合确认风险，不适合成为首次降仓依据。", "items": ["指数已大幅回撤", "主线已破位", "融资资金出现踩踏", "市场由普涨转为普跌"]},
]

STATE_MATRIX = [
    {"opportunity": "高", "risk": "低", "meaning": "最佳进攻窗口", "action": "提高仓位"},
    {"opportunity": "高", "risk": "高", "meaning": "机会仍在但风险上升", "action": "持核心，减后排"},
    {"opportunity": "中", "risk": "高", "meaning": "风险收益比下降", "action": "降仓"},
    {"opportunity": "低", "risk": "高", "meaning": "防御状态", "action": "系统性降风险"},
    {"opportunity": "中", "risk": "低", "meaning": "等待机会", "action": "保持中性"},
    {"opportunity": "低", "risk": "低", "meaning": "风险释放但机会未确认", "action": "等待右侧信号"},
]

CHOICE_DAILY_MODEL = [
    {"dataset": "indices_daily", "fields": ["四大指数：上证指数、深成指、创业板指、科创50"]},
    {"dataset": "market_breadth_daily", "fields": ["全市场成交额", "上涨/下跌/平盘家数", "涨停/跌停家数", "20日新高/新低家数", "中位数涨跌幅", "等权指数", "加权指数"]},
    {"dataset": "margin_daily", "fields": ["融资余额", "融资买入额", "融资偿还额", "融资净买入", "两融成交占比", "电子/通信/计算机融资净买入"]},
    {"dataset": "valuation_daily", "fields": ["PE_TTM", "PB", "PE三年分位", "PB三年分位"]},
    {"dataset": "validation", "fields": ["未来5/10/20日收益", "未来10日最大回撤", "风险分桶收益", "不同模型信号胜率"]},
]


def clean_number(value: Any) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return float(number)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return float(max(low, min(high, value)))


def opportunity_raw(score: float) -> float:
    return round(clamp(score) / 20, 1)


def weighted_score(score: float, weight: float) -> float:
    return round(opportunity_raw(score) / 5 * weight, 1)


def score_between(value: float | None, low: float, high: float) -> float:
    if value is None or high == low:
        return 50.0
    return clamp((value - low) / (high - low) * 100)


def inverse_score_between(value: float | None, low: float, high: float) -> float:
    return 100.0 - score_between(value, low, high)


def average(values: list[float | None], default: float = 50.0) -> float:
    clean = [float(x) for x in values if x is not None and np.isfinite(float(x))]
    return float(np.mean(clean)) if clean else default


def pct_rank(values: pd.Series, current: float | None) -> float:
    series = pd.to_numeric(values, errors="coerce").dropna()
    if current is None or series.empty:
        return 50.0
    return float((series <= current).mean() * 100)


def latest_row(frame: pd.DataFrame, date_col: str, as_of: str | None = None) -> pd.Series | None:
    if frame.empty or date_col not in frame.columns:
        return None
    current = frame.copy()
    current[date_col] = current[date_col].astype(str)
    if as_of:
        key = as_of[:6] if date_col == "month" else as_of
        current = current[current[date_col] <= key]
    current = current.sort_values(date_col)
    if current.empty:
        return None
    return current.iloc[-1]


def latest_value(frame: pd.DataFrame, column: str, date_col: str, as_of: str | None = None) -> float | None:
    row = latest_row(frame, date_col, as_of)
    return clean_number(row.get(column)) if row is not None and column in row else None


def series_until(frame: pd.DataFrame, date_col: str, as_of: str | None = None) -> pd.DataFrame:
    if frame.empty or date_col not in frame.columns:
        return frame.copy()
    current = frame.copy()
    current[date_col] = current[date_col].astype(str)
    if as_of:
        key = as_of[:6] if date_col == "month" else as_of
        current = current[current[date_col] <= key]
    return current.sort_values(date_col)


def delta(frame: pd.DataFrame, column: str, date_col: str, periods: int = 1, as_of: str | None = None) -> float | None:
    current = series_until(frame, date_col, as_of)
    values = pd.to_numeric(current[column], errors="coerce").dropna() if column in current else pd.Series(dtype=float)
    if len(values) <= periods:
        return None
    return float(values.iloc[-1] - values.iloc[-1 - periods])


def momentum(market: pd.DataFrame, symbol: str, days: int, as_of: str | None = None) -> float | None:
    if market.empty:
        return None
    rows = market[market["symbol"].astype(str) == symbol].copy()
    rows = series_until(rows, "trade_date", as_of)
    rows["close"] = pd.to_numeric(rows.get("close"), errors="coerce")
    rows = rows.dropna(subset=["close"])
    if len(rows) <= days:
        return None
    return float((rows["close"].iloc[-1] / rows["close"].iloc[-1 - days] - 1) * 100)


def ma_gap(market: pd.DataFrame, symbol: str, days: int, as_of: str | None = None) -> float | None:
    rows = market[market["symbol"].astype(str) == symbol].copy()
    rows = series_until(rows, "trade_date", as_of)
    rows["close"] = pd.to_numeric(rows.get("close"), errors="coerce")
    rows = rows.dropna(subset=["close"])
    if len(rows) < days:
        return None
    close = float(rows["close"].iloc[-1])
    ma = float(rows["close"].tail(days).mean())
    if ma == 0:
        return None
    return (close / ma - 1) * 100


def format_pct(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "暂无"
    return f"{value:.{digits}f}%"


def format_num(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "暂无"
    return f"{value:.{digits}f}"


def status_label(score: float) -> str:
    raw = opportunity_raw(score)
    if raw >= 4:
        return "明显改善"
    if raw >= 3:
        return "中性偏强"
    if raw >= 2:
        return "中性偏弱"
    if raw >= 1:
        return "偏弱"
    return "明显恶化"


def risk_level(score: float) -> str:
    if score <= 20:
        return "绿色"
    if score <= 35:
        return "黄色"
    if score <= 50:
        return "橙色"
    if score <= 70:
        return "红色"
    return "深红"


def risk_label(score: float) -> str:
    if score <= 20:
        return "低"
    if score <= 35:
        return "偏低"
    if score <= 50:
        return "中"
    if score <= 70:
        return "偏高"
    return "高"


def opportunity_band(score: float) -> dict[str, str]:
    if score >= 80:
        return OPPORTUNITY_BANDS[0]
    if score >= 65:
        return OPPORTUNITY_BANDS[1]
    if score >= 50:
        return OPPORTUNITY_BANDS[2]
    if score >= 35:
        return OPPORTUNITY_BANDS[3]
    return OPPORTUNITY_BANDS[4]


def risk_band(score: float) -> dict[str, str]:
    if score <= 20:
        return RISK_BANDS[0]
    if score <= 35:
        return RISK_BANDS[1]
    if score <= 50:
        return RISK_BANDS[2]
    if score <= 70:
        return RISK_BANDS[3]
    return RISK_BANDS[4]


def parse_range(text: str) -> tuple[float, float]:
    normalized = text.replace("%", "").replace("以上", "-100")
    numbers = [float(part) for part in normalized.split("-") if part.strip()]
    if not numbers:
        return 0.0, 0.0
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def format_range(low: float, high: float) -> str:
    return f"{low:.0f}%-{high:.0f}%"


def suggested_position(score: float, risk: float) -> dict[str, Any]:
    opportunity = opportunity_band(score)
    warning = risk_band(risk)
    base_low, base_high = parse_range(opportunity["position"])
    discount_low, discount_high = parse_range(warning["discount"])
    adjusted_low = base_low * discount_low / 100
    adjusted_high = base_high * discount_high / 100
    return {
        "base_position_pct": round((base_low + base_high) / 2, 1),
        "base_position_range": opportunity["position"],
        "risk_discount": round((discount_low + discount_high) / 200, 2),
        "risk_discount_range": warning["discount"],
        "suggested_position_pct": round((adjusted_low + adjusted_high) / 2, 1),
        "suggested_position_range": format_range(adjusted_low, adjusted_high),
        "single_stock_limit": opportunity["single_stock"],
        "industry_limit": opportunity["industry"],
        "theme_limit": opportunity["theme"],
        "risk_action": warning["action"],
    }


def market_phase(score: float, risk: float) -> str:
    stage = opportunity_band(score)["stage"]
    if risk > 70:
        return f"{stage} / 深红防御优先"
    if risk > 50:
        return f"{stage} / 红色预警"
    if risk > 35:
        return f"{stage} / 橙色降风险"
    return stage


def concentration_note(score: float) -> str:
    band = opportunity_band(score)
    if score >= 65:
        return f"单股上限{band['single_stock']}，常规核心仓不宜直接打满；单行业上限{band['industry']}，单主题上限{band['theme']}。"
    if score >= 50:
        return f"单股上限{band['single_stock']}，单行业上限{band['industry']}，单主题上限{band['theme']}，以结构性机会为主。"
    return f"防御或风险期，单股上限{band['single_stock']}，单行业上限{band['industry']}，单主题上限{band['theme']}。"


def style_signal(market: pd.DataFrame, as_of: str | None = None) -> tuple[str, float, list[str]]:
    growth_symbols = ["399006.SZ", "000688.SH", "931087.CSI"]
    broad_symbols = ["000300.SH", "000001.SH"]
    growth_values = [momentum(market, symbol, 20, as_of) for symbol in growth_symbols]
    broad_values = [momentum(market, symbol, 20, as_of) for symbol in broad_symbols]
    growth = average(growth_values, default=0.0)
    broad = average(broad_values, default=0.0)
    spread = growth - broad
    if spread >= 3:
        label = "成长科技占优"
    elif spread <= -3:
        label = "大盘价值占优"
    else:
        label = "均衡轮动"
    score = clamp(50 + abs(spread) * 5)
    basis = [
        f"成长/科技20日动量均值 {format_pct(growth)}",
        f"宽基20日动量均值 {format_pct(broad)}",
        f"风格差 {format_pct(spread)}",
    ]
    return label, score, basis


def valuation_percentile(valuation: pd.DataFrame, as_of: str | None = None) -> tuple[float, list[str]]:
    if valuation.empty or "pe_ttm" not in valuation.columns:
        return 50.0, ["估值数据暂缺，按中性处理"]
    current = series_until(valuation, "trade_date", as_of)
    percentiles: list[float] = []
    notes: list[str] = []
    for code, group in current.groupby("index_code"):
        values = pd.to_numeric(group["pe_ttm"], errors="coerce").dropna()
        if values.empty:
            continue
        current_value = float(values.iloc[-1])
        rank = float((values <= current_value).mean() * 100)
        percentiles.append(rank)
        name = str(group.iloc[-1].get("index_name", code))
        notes.append(f"{name} PE历史分位 {rank:.0f}%")
    if not percentiles:
        return 50.0, ["估值数据暂缺，按中性处理"]
    return float(np.mean(percentiles)), notes[:4]


def evaluate(as_of: str | None, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    macro = data["macro"]
    liquidity = data["liquidity"]
    market = data["market"]
    global_macro = data["global_macro"]
    valuation = data["valuation"]
    crowding = data["crowding"]
    breadth = data["breadth"]
    leverage = data["leverage"]

    latest_breadth = latest_row(breadth, "trade_date", as_of)
    up_count = clean_number(latest_breadth.get("up_count")) if latest_breadth is not None else None
    total_count = clean_number(latest_breadth.get("total_count")) if latest_breadth is not None else None
    up_ratio = up_count / total_count * 100 if up_count is not None and total_count else None
    broad_turnover = clean_number(latest_breadth.get("broad_turnover_pct")) if latest_breadth is not None else None
    amount = clean_number(latest_breadth.get("total_amount_trillion")) if latest_breadth is not None else None

    pmi = latest_value(macro, "pmi_manufacturing", "month", as_of)
    m1_m2_gap = latest_value(macro, "m1_m2_gap_pp", "month", as_of)
    sf_yoy = latest_value(macro, "sf_stock_yoy_pct", "month", as_of)
    cpi = latest_value(macro, "cpi_yoy_pct", "month", as_of)
    dr007 = latest_value(liquidity, "dr007_pct", "trade_date", as_of)
    dr007_rank = pct_rank(series_until(liquidity, "trade_date", as_of).get("dr007_pct", pd.Series(dtype=float)), dr007)
    dr007_delta_5 = delta(liquidity, "dr007_pct", "trade_date", 5, as_of)
    crowd = latest_value(crowding, "crowding_pct", "trade_date", as_of)
    crowd_rank = pct_rank(series_until(crowding, "trade_date", as_of).get("crowding_pct", pd.Series(dtype=float)), crowd)
    margin_ratio = latest_value(leverage, "margin_to_market_cap_pct", "trade_date", as_of)
    margin_rank = pct_rank(series_until(leverage, "trade_date", as_of).get("margin_to_market_cap_pct", pd.Series(dtype=float)), margin_ratio)
    val_rank, val_notes = valuation_percentile(valuation, as_of)

    dgs10 = latest_value(global_macro[global_macro["series"].astype(str) == "DGS10"], "value_pct", "trade_date", as_of)
    dgs10_delta_20 = delta(global_macro[global_macro["series"].astype(str) == "DGS10"], "value_pct", "trade_date", 20, as_of)
    usd_liq = latest_value(global_macro[global_macro["series"].astype(str) == "NET_USD_LIQUIDITY_SOMA"], "value_pct", "trade_date", as_of)
    usd_liq_delta = delta(global_macro[global_macro["series"].astype(str) == "NET_USD_LIQUIDITY_SOMA"], "value_pct", "trade_date", 4, as_of)

    sh_comp_20 = momentum(market, "000001.SH", 20, as_of)
    csi300_20 = momentum(market, "000300.SH", 20, as_of)
    chinext_20 = momentum(market, "399006.SZ", 20, as_of)
    star50_20 = momentum(market, "000688.SH", 20, as_of)
    nasdaq_20 = momentum(market, "^IXIC", 20, as_of)
    ma_gaps = [ma_gap(market, symbol, 20, as_of) for symbol in ["000001.SH", "000300.SH", "399006.SZ", "000688.SH"]]
    index_momentum = average([sh_comp_20, csi300_20, chinext_20, star50_20], default=0.0)
    style, style_score, style_basis = style_signal(market, as_of)

    macro_score = average([
        score_between(pmi, 47, 52),
        score_between(m1_m2_gap, -8, 4),
        score_between(sf_yoy, 7, 12),
    ])
    liquidity_score = average([
        100 - dr007_rank,
        60 if dr007_delta_5 is not None and dr007_delta_5 <= 0 else 42,
        score_between(usd_liq_delta, -0.15, 0.15),
    ])
    policy_score = average([
        58 if dr007_delta_5 is not None and dr007_delta_5 <= 0 else 48,
        score_between(sf_yoy, 7, 12),
        55 if pmi is not None and pmi < 50 else 50,
    ])
    earnings_score = average([
        score_between(pmi, 47, 52),
        score_between(sf_yoy, 7, 12),
        score_between(index_momentum, -8, 8),
    ])
    valuation_score = 100 - val_rank
    sentiment_score = average([
        score_between(up_ratio, 35, 65),
        score_between(index_momentum, -8, 8),
        inverse_score_between(abs((crowd or 40) - 42), 0, 22),
    ])

    blueprint = {item["name"]: item for item in MODULE_BLUEPRINT}
    module_inputs = [
        ("宏观周期", 15, macro_score, [f"制造业PMI {format_num(pmi)}", f"M1-M2剪刀差 {format_num(m1_m2_gap)}个百分点", f"社融存量同比 {format_pct(sf_yoy)}"]),
        ("流动性环境", 20, liquidity_score, [f"DR007 {format_pct(dr007)}，历史分位 {dr007_rank:.0f}%", f"DR007近5期变化 {format_num(dr007_delta_5)}个百分点", f"美元净流动性近4期变化 {format_num(usd_liq_delta)}万亿美元"]),
        ("政策环境", 15, policy_score, ["公开高频政策指标暂未接入，当前用流动性、信用扩张和经济动能做代理", f"社融存量同比 {format_pct(sf_yoy)}", f"DR007近5期变化 {format_num(dr007_delta_5)}个百分点"]),
        ("盈利周期", 15, earnings_score, [f"制造业PMI {format_num(pmi)}", f"社融存量同比 {format_pct(sf_yoy)}", f"主要A股指数20日动量均值 {format_pct(index_momentum)}"]),
        ("估值水平", 15, valuation_score, [f"指数PE历史分位均值 {val_rank:.0f}%", *val_notes[:3]]),
        ("市场情绪", 10, sentiment_score, [f"上涨家数占比 {format_pct(up_ratio)}", f"A股成交额/总市值 {format_pct(broad_turnover)}", f"交易拥挤度 {format_pct(crowd)}"]),
        ("风格结构", 10, style_score, style_basis),
    ]
    market_modules = []
    for name, weight, score, basis in module_inputs:
        raw = opportunity_raw(score)
        market_modules.append({
            "name": name,
            "weight": weight,
            "score": round(score, 1),
            "raw_score": raw,
            "weighted_score": weighted_score(score, weight),
            "status": status_label(score),
            "basis": basis,
            "blueprint": blueprint.get(name, {}).get("subitems", []),
        })

    market_score_before_gate = round(sum(item["weighted_score"] for item in market_modules), 1)
    liquidity_raw = next((item["raw_score"] for item in market_modules if item["name"] == "流动性环境"), 2.5)
    liquidity_gate = ""
    market_score = market_score_before_gate
    if liquidity_raw <= 1.5 and market_score > 50:
        market_score = 50.0
        liquidity_gate = "流动性原始分<=1.5，按框架规则将大盘总分封顶为50。"
    elif liquidity_raw <= 2.0:
        liquidity_gate = "流动性原始分<=2.0，按框架规则不建议重仓进攻。"
    elif liquidity_raw >= 4.0 and valuation_score >= 55 and policy_score >= 55:
        liquidity_gate = "流动性原始分>=4.0且政策、估值配合，关注估值修复窗口。"

    breadth_health = average([score_between(up_ratio, 35, 65), score_between(delta(breadth, "up_count", "trade_date", 5, as_of), -800, 800)])
    ma_health = average([score_between(value, -6, 6) for value in ma_gaps])
    price_quality_health = average([ma_health, score_between(index_momentum, -8, 8), score_between(delta(breadth, "total_amount_trillion", "trade_date", 5, as_of), -0.5, 0.5)])
    mainline_health = average([score_between(chinext_20, -10, 10), score_between(star50_20, -10, 10), inverse_score_between(abs((crowd or 42) - 42), 0, 25)])
    leverage_risk = average([crowd_rank, margin_rank])
    external_pressure = average([
        score_between(dgs10, 3.3, 5.0),
        score_between(dgs10_delta_20, -0.25, 0.25),
        inverse_score_between(nasdaq_20, -8, 8),
        inverse_score_between(usd_liq_delta, -0.15, 0.15),
    ])
    val_earnings_risk = average([val_rank, 100 - earnings_score if val_rank >= 65 else 45])
    policy_dull_risk = average([55 if policy_score < 50 else 40, 60 if pmi is not None and pmi < 50 and index_momentum < 0 else 40])

    raw_risk_inputs = [
        ("市场广度恶化", 20, 100 - breadth_health, [f"上涨家数占比 {format_pct(up_ratio)}", f"近5期上涨家数变化 {format_num(delta(breadth, 'up_count', 'trade_date', 5, as_of), 0)}只"]),
        ("量价质量恶化", 20, 100 - price_quality_health, [f"主要指数20日动量均值 {format_pct(index_momentum)}", f"主要指数相对20日均线均值 {format_pct(average(ma_gaps, default=0.0))}", f"成交额近5期变化 {format_num(delta(breadth, 'total_amount_trillion', 'trade_date', 5, as_of))}万亿元"]),
        ("主线健康度下降", 20, 100 - mainline_health, [f"创业板20日动量 {format_pct(chinext_20)}", f"科创50 20日动量 {format_pct(star50_20)}", f"交易拥挤度 {format_pct(crowd)}"]),
        ("杠杆与资金拥挤", 15, leverage_risk, [f"两融/总市值 {format_pct(margin_ratio)}，历史分位 {margin_rank:.0f}%", f"交易拥挤度历史分位 {crowd_rank:.0f}%"]),
        ("跨资产压力", 10, external_pressure, [f"美国10年期国债 {format_pct(dgs10)}", f"纳斯达克20日动量 {format_pct(nasdaq_20)}", f"美元净流动性 {format_num(usd_liq)}万亿美元"]),
        ("估值盈利背离", 10, val_earnings_risk, [f"指数PE历史分位均值 {val_rank:.0f}%", f"盈利周期原始分 {opportunity_raw(earnings_score):.1f}/5"]),
        ("政策反应钝化", 5, policy_dull_risk, ["当前尚未接入政策文本事件库，先用政策代理指标与市场反应判断", f"政策环境原始分 {opportunity_raw(policy_score):.1f}/5"]),
    ]
    risk_items = []
    for name, weight, raw_risk, basis in raw_risk_inputs:
        raw_risk = clamp(raw_risk)
        contribution = round(raw_risk / 100 * weight, 1)
        risk_items.append({
            "name": name,
            "weight": weight,
            "risk": contribution,
            "raw_risk": round(raw_risk, 1),
            "level": risk_label(raw_risk),
            "basis": basis,
        })
    risk_score = round(sum(item["risk"] for item in risk_items), 1)
    position = suggested_position(market_score, risk_score)
    latest_dates = [
        str(value)
        for value in [
            latest_breadth.get("trade_date") if latest_breadth is not None else None,
            latest_row(market, "trade_date", as_of).get("trade_date") if latest_row(market, "trade_date", as_of) is not None else None,
            latest_row(liquidity, "trade_date", as_of).get("trade_date") if latest_row(liquidity, "trade_date", as_of) is not None else None,
        ]
        if value is not None and str(value) != "nan"
    ]
    latest_date = max(latest_dates) if latest_dates else as_of

    return {
        "trade_date": latest_date,
        "summary": {
            "market_score": round(market_score, 1),
            "market_score_before_gate": round(market_score_before_gate, 1),
            "risk_score": round(risk_score, 1),
            "risk_level": risk_level(risk_score),
            "market_phase": market_phase(market_score, risk_score),
            "base_position_pct": position["base_position_pct"],
            "base_position_range": position["base_position_range"],
            "risk_discount": position["risk_discount"],
            "risk_discount_range": position["risk_discount_range"],
            "suggested_position_pct": position["suggested_position_pct"],
            "suggested_position_range": position["suggested_position_range"],
            "single_stock_limit": position["single_stock_limit"],
            "industry_limit": position["industry_limit"],
            "theme_limit": position["theme_limit"],
            "risk_action": position["risk_action"],
            "liquidity_gate": liquidity_gate,
            "concentration_note": concentration_note(market_score),
            "style_signal": style,
            "style_reason": "；".join(style_basis),
            "formula": "实际仓位 = 大盘建议仓位 × 风险折扣",
        },
        "modules": market_modules,
        "risk_modules": risk_items,
        "signals": {
            "up_ratio_pct": round(up_ratio, 2) if up_ratio is not None else None,
            "total_amount_trillion": round(amount, 3) if amount is not None else None,
            "broad_turnover_pct": round(broad_turnover, 3) if broad_turnover is not None else None,
            "crowding_pct": round(crowd, 2) if crowd is not None else None,
            "margin_to_market_cap_pct": round(margin_ratio, 3) if margin_ratio is not None else None,
            "index_momentum_20d_pct": round(index_momentum, 2),
            "dgs10_pct": round(dgs10, 3) if dgs10 is not None else None,
        },
    }


def load_data() -> dict[str, pd.DataFrame]:
    return {
        "macro": read_csv_safe(DATA_DIR / "macro.csv"),
        "liquidity": read_csv_safe(DATA_DIR / "liquidity.csv"),
        "market": read_csv_safe(DATA_DIR / "market.csv"),
        "global_macro": read_csv_safe(DATA_DIR / "global_macro.csv"),
        "valuation": read_csv_safe(DATA_DIR / "valuation.csv"),
        "crowding": read_csv_safe(DATA_DIR / "crowding.csv"),
        "breadth": read_csv_safe(DATA_DIR / "breadth.csv"),
        "leverage": read_csv_safe(DATA_DIR / "leverage.csv"),
    }


def history_dates(data: dict[str, pd.DataFrame], lookback: int = 120) -> list[str]:
    breadth = data["breadth"]
    if not breadth.empty and "trade_date" in breadth.columns:
        dates = breadth["trade_date"].dropna().astype(str).sort_values().unique().tolist()
        return dates[-lookback:]
    market = data["market"]
    if not market.empty and "trade_date" in market.columns:
        dates = market["trade_date"].dropna().astype(str).sort_values().unique().tolist()
        return dates[-lookback:]
    return [datetime.now(BEIJING).strftime("%Y%m%d")]


def update_status(row_count: int, latest_date: str | None) -> None:
    if STATUS_PATH.exists():
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            status = {"overall_status": "partial", "datasets": {}}
    else:
        status = {"overall_status": "partial", "datasets": {}}
    status.setdefault("datasets", {})["market_tracking"] = {
        "status": "success" if row_count else "partial",
        "latest_date": latest_date,
        "rows": row_count,
        "cached_rows": row_count,
        "source": "由宏观、流动性、估值、A股情绪、两融、全球市场公开数据自动评分",
        "note": "每日随网站数据更新自动重算；政策环境当前为代理指标，后续可接政策事件库。",
    }
    status["updated_at"] = datetime.now(BEIJING).isoformat(timespec="seconds")
    write_json(STATUS_PATH, status)


def main() -> None:
    data = load_data()
    dates = history_dates(data)
    latest = evaluate(dates[-1] if dates else None, data)
    history_rows = []
    for trade_date in dates:
        result = evaluate(trade_date, data)
        history_rows.append({
            "trade_date": trade_date,
            "market_score": result["summary"]["market_score"],
            "risk_score": result["summary"]["risk_score"],
            "suggested_position_pct": result["summary"]["suggested_position_pct"],
            "risk_level": result["summary"]["risk_level"],
            "market_phase": result["summary"]["market_phase"],
            "style_signal": result["summary"]["style_signal"],
            "source": "二级市场投研框架自动评分",
        })
    history = pd.DataFrame(history_rows, columns=HISTORY_COLUMNS)
    write_csv_atomic(history, OUTPUT_CSV)
    payload = {
        "updated_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S+08:00"),
        "timezone": "Asia/Shanghai",
        "schedule": ["每日随公开数据刷新", "交易日收盘后重点更新A股情绪与仓位信号"],
        "framework_source": "二级市场投研框架：完整对话记录.pdf",
        "framework_sections": FRAMEWORK_SECTIONS,
        "module_blueprint": MODULE_BLUEPRINT,
        "opportunity_bands": OPPORTUNITY_BANDS,
        "risk_bands": RISK_BANDS,
        "signal_groups": SIGNAL_GROUPS,
        "state_matrix": STATE_MATRIX,
        "choice_daily_model": CHOICE_DAILY_MODEL,
        "process_steps": PROCESS_STEPS,
        "tracking": latest,
        "method_note": "大盘机会评分按七大模块0-5原始分折算为100分；风险领先预警分越高越需要降仓。该模型用于仓位管理与风控，不直接预测指数点位，不构成投资建议。",
    }
    write_json(OUTPUT_JSON, payload)
    update_status(len(history), latest.get("trade_date"))
    print(json.dumps({"latest": latest["summary"], "history_rows": len(history)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
