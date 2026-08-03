#!/bin/bash
set -u
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3。请先安装 Python 3.11 或更高版本。"
  read -r -p "按回车退出..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python scripts/update_data.py || true
python scripts/update_ashare_daily.py --days 95 || true
if [ ! -s data/macro.csv ] && [ ! -s data/market.csv ] && [ ! -s data/valuation.csv ]; then
  echo "公开源暂时不可用，生成明确标注的演示数据供界面检查。"
  python scripts/generate_demo_data.py
fi
python scripts/update_market_tracking.py || true
python scripts/build_site.py

(open http://localhost:8000 >/dev/null 2>&1 &) || true
echo "网站已启动：http://localhost:8000"
echo "关闭本窗口或按 Control+C 可停止。"
python -m http.server 8000 -d public
