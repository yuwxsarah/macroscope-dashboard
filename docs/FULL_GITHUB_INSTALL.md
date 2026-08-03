# GitHub 网站完整安装步骤（只用浏览器）

## 1. 创建仓库

- GitHub 右上角 `+` → `New repository`
- 名称：`macroscope-dashboard-v6`
- Visibility：`Public`
- 不创建 README、.gitignore 或 License

## 2. 上传

- 解压 ZIP
- 进入 `macroscope_public_web_v6_complete` 文件夹内部
- Finder 按 `Command + Shift + .` 显示隐藏文件
- 选择所有内容，包括 `.github`
- 拖到 GitHub 的 `Upload files`
- 提交到 `main`

仓库首页必须直接看到：`.github`、`config`、`data`、`docs`、`public`、`scripts`、`src`。

## 3. 权限

- Settings → Actions → General
- Workflow permissions → `Read and write permissions`
- Save

## 4. Pages

- Settings → Pages
- Source → `GitHub Actions`

## 5. 第一次真实更新

- Actions → `Update data and deploy public dashboard`
- Run workflow
- mode 选择 `all`
- 只点击一次

网页初始会显示演示数据警告。工作流成功后，真实数据会自动替换成功抓取到的演示数据。

## 6. 交易拥挤度历史

- Actions → `Backfill A-share crowding history`
- 第一次测试：start_date=`20250601`，end_date=`20250630`，batch_size=`50`
- 成功后再运行：start_date=`20250101`，end_date留空，batch_size=`50`

## 7. 自动更新（北京时间）

- 每天 06:20：美股、韩股、美国指数、美元指数、美债收益率、美元净流动性
- 每天 10:10：中国宏观、DR001、DR007、Shibor
- 工作日 16:40：A股收盘、估值、情绪、拥挤度、两融、偏离度
- 每天 18:40：宏观、利率、基金募集复查
- 工作日 19:10：全量刷新所有模块并重建网页

GitHub cron 文件使用 UTC 等价时间，避免时区字段兼容问题。

## 8. 分享

Pages 地址格式：`https://你的用户名.github.io/macroscope-dashboard-v6/`
同事无需 GitHub 账号、Token、Python 或 Docker。
