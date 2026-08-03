# 部署为所有人可访问的公开网页

这套方案不要求同事安装任何程序。部署一次后，所有人直接打开网址。

## 准备

需要一个 GitHub 账号。使用公开仓库最简单，网站和源代码都会公开。项目中不包含 Token、密码或个人账户信息。

## 第1步：创建仓库

1. 登录 GitHub。
2. 点击右上角 `+` → `New repository`。
3. Repository name 填写，例如：`macroscope-dashboard`。
4. Visibility 选择 `Public`。
5. 不勾选额外生成 README、.gitignore 或 License，避免与压缩包中的文件冲突。
6. 点击 `Create repository`。

## 第2步：上传项目

### 推荐：GitHub Desktop

1. 安装并登录 GitHub Desktop。
2. 选择 `File` → `Add local repository`，选择解压后的项目文件夹。
3. 仓库还未初始化时，按提示创建仓库。
4. Commit message 填写 `Initial MacroScope public dashboard`。
5. 点击 `Commit to main`。
6. 点击 `Publish repository`，选择刚才的公开仓库。

### 也可以使用网页上传

在空仓库页面点击 `uploading an existing file`，上传项目内容。必须保留隐藏目录：

```text
.github/workflows/update-and-deploy.yml
```

网页上传大量子目录时不够稳定，因此更推荐 GitHub Desktop。

## 第3步：启用 GitHub Pages

1. 打开仓库。
2. 点击 `Settings`。
3. 左侧点击 `Pages`。
4. `Build and deployment` 下的 `Source` 选择 `GitHub Actions`。

不要选择从某个分支直接发布，因为本项目需要先抓取数据并生成静态网页。

## 第4步：首次运行

1. 回到仓库顶部，点击 `Actions`。
2. 左侧选择 `Update data and deploy public dashboard`。
3. 点击右侧 `Run workflow`。
4. Branch 选择 `main`。
5. 再点击绿色 `Run workflow`。
6. 等待工作流完成。绿色对勾表示部署成功。

首次运行会下载依赖、抓取历史数据并构建网页，通常比后续更新慢。

## 第5步：打开网址

网址通常是：

```text
https://你的GitHub用户名.github.io/macroscope-dashboard/
```

也可以在：

```text
Settings → Pages
```

查看GitHub显示的正式网址。

## 第6步：发给同事

把上述网址直接发给同事。访问者不需要：

- GitHub账号
- Python
- Docker
- Tushare Token
- 下载压缩包

## 自动更新时间

工作流文件：

```text
.github/workflows/update-and-deploy.yml
```

默认使用北京时间：

```yaml
- 每天 08:15
- 工作日 16:40
```

GitHub的定时任务可能因平台负载延迟几分钟。需要立即刷新时，在Actions页面手动运行工作流。

## 自定义域名

部署稳定后，可以在：

```text
Settings → Pages → Custom domain
```

填写自己的域名。随后按GitHub提示在域名服务商处设置DNS。

## 排查“没有数据”

### 1. 查看Actions日志

进入：

```text
Actions → Update data and deploy public dashboard → 最近一次运行
```

依次展开：

- `Fetch and cache public data`
- `Build static website`
- `Deploy GitHub Pages`

### 2. 查看网站状态页

网站顶部进入：

```text
数据口径与运行状态
```

每个模块会显示：

- `success`：本次成功更新
- `partial`：部分来源成功
- `failed`：本次失败且没有可写入新数据
- `stale` / `serving_cached_data`：本次失败，网页继续展示旧缓存

### 3. 手动重跑

公开网站临时限频时，等待30分钟后在Actions中再次点击 `Run workflow`。

### 4. 长时间失败

公开数据网页可能改变字段或URL。根据日志中失败的数据集修改 `src/providers.py`，不要用0或演示数据伪装真实值。

## 重要说明

GitHub Pages是公开静态网站，不适合保存私密资料。不要向仓库提交密码、账户Cookie、API密钥或公司内部文件。
