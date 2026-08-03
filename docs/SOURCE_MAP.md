# 数据来源

| 模块 | 主要来源 | 程序入口 |
|---|---|---|
| M1/M2、PMI、CPI、社融 | 人民银行、国家统计局及 AKShare 公开适配器 | `PublicMacroProvider` |
| DR001、DR007 | 中国货币网首页公开行情 | `ChinaLiquidityProvider.fetch_dr_current` |
| 隔夜Shibor | 中国货币网 / AKShare `rate_interbank` | `ChinaLiquidityProvider.fetch_shibor_overnight` |
| 美债2年/10年 | 美联储 H.15，经 FRED CSV | `FredTreasuryProvider` |
| 美元净流动性 | FRED：WSHOSHO、WALCL、WDTGAL、RRPONTSYD | `UsdLiquidityProvider` |
| 美股、韩股、美国指数、美元指数 | Yahoo Finance / yfinance | `GlobalMarketProvider` |
| A股指数与估值 | 东方财富、中证指数、乐咕乐股 / AKShare | `ChinaMarketProvider` |
| A股成交额、市值、涨跌家数 | 东方财富公开行情 / AKShare | `ChinaSentimentProvider` |
| 两融余额 | 上交所、深交所 / AKShare | `ChinaSentimentProvider.fetch_margin` |
| 新成立基金募集份额 | 天天基金 / AKShare `fund_new_found_em` | `FundSubscriptionProvider` |
| 拥挤度历史 | 东方财富历史行情 / efinance | `scripts/backfill_crowding.py` |

DR 与 FDR 是不同口径，本项目严格不以 FDR001/FDR007 替代 DR001/DR007。某一公开接口失败时，网站保留上一次成功缓存并在状态页显示错误。
