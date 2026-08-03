# 仅使用 GitHub 网站升级现有公开看板

## 1. 上传 v5 文件

1. 下载并解压 v5 压缩包。
2. 打开现有 GitHub 仓库的 `Code` 页面。
3. 点击 `Add file` → `Upload files`。
4. 将 v5 文件夹内部的可见文件和文件夹拖入上传区。
5. 同名文件选择覆盖，提交到 `main`。

重点确认仓库根目录直接存在：

- `config/`
- `data/`
- `docs/`
- `public/`
- `scripts/`
- `src/`
- `requirements.txt`

## 2. 替换主工作流

打开：

`.github/workflows/update-and-deploy.yml`

点击铅笔，把本压缩包同路径文件的内容完整复制进去并提交。

## 3. 新建历史回填工作流

在仓库点击 `Add file` → `Create new file`，文件名：

`.github/workflows/backfill-crowding.yml`

复制本压缩包同路径文件内容并提交。

## 4. Pages 设置

`Settings` → `Pages` → `Source` 选择 `GitHub Actions`。

## 5. 第一次完整更新

`Actions` → `Update data and deploy public dashboard` → `Run workflow` → 选择 `all`。

## 6. 回填拥挤度历史

`Actions` → `Backfill A-share crowding history` → `Run workflow`：

- start_date: `20250101`
- end_date: 留空
- batch_size: `100`

成功后，`data/crowding.csv` 和 `data/breadth.csv` 会写入历史数据并自动重新发布网站。
