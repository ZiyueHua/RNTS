#!/bin/bash
# ============================================================
# RNTS 一键环境初始化（Linux / macOS）
# 作用：建虚拟环境 + 装依赖 + 初始化数据库
# 只需运行一次：bash setup_env.sh
# ============================================================
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] 未检测到 python3，请先安装 Python 3.12+"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[1/3] 创建虚拟环境 .venv ..."
    python3 -m venv .venv
else
    echo "[1/3] .venv 已存在，跳过创建。"
fi

source .venv/bin/activate

echo "[2/3] 安装依赖（可能需要几分钟）..."
pip install --upgrade pip
pip install -r requirements.txt

echo "[3/3] 初始化数据库..."
python -c "from app.database import init_db; init_db(); print('DB initialized OK')"

echo
echo "[OK] 环境就绪。"
echo "  - 启动网页：bash run.sh，然后打开 http://localhost:8000"
echo "  - 每日任务：参考《操作说明_每日任务.md》配置 cron"
