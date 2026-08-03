# 从 v5.1 升级到 v5.2（仅用 GitHub 网页）

需要覆盖 4 个文件：

1. `src/extended_providers.py`
2. `src/pipeline.py`
3. `scripts/build_site.py`
4. `config/settings.yaml`

同时可覆盖 `README.md`、`CHANGELOG.md` 和 `VERSION`。

覆盖后进入 **Actions → Update data and deploy public dashboard → Run workflow**，第一次选择 `mode: all`。

检查：

- `data/liquidity.csv` 中存在 `dr007_pct`；
- `data/global_macro.csv` 中存在 `series=DGS10`；
- 网站“宏观与资金”出现 DR007 独立卡片；
- 网站“全球市场”出现美国10年期国债收益率独立卡片。
