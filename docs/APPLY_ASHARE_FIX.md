# MacroScope v5.1 A股数据修复：GitHub 网页操作

本修复解决：

1. 上涨家数、下跌家数、两市成交额为空；
2. 成交额/总市值为空；
3. 两融余额/总市值为空；
4. 上证综指、深证成指、创业板指的10周/20周均线偏离度缺失；
5. 周线偏离度日期被标到未来周五；
6. GitHub Actions 在15:20前手动运行时直接跳过A股情绪数据。

## 需要覆盖的文件

- `config/settings.yaml`
- `src/providers.py`
- `src/extended_providers.py`
- `src/pipeline.py`

## 覆盖后运行

进入：

`Actions → Update data and deploy public dashboard → Run workflow`

选择：

`mode: all`

交易日上午 09:35 以后运行，会展示盘中快照；15:20以后运行，会展示收盘后快照。非交易时段继续展示最近一次成功缓存。
