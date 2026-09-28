#!/usr/bin/env bash
# 每日文献雷达：纯 PubMed 抓取（零 LLM API）。摘要生成待 M2 接 GLM 后启用。
set -euo pipefail
cd "$(dirname "$0")"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7890}"
export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7890}"
mkdir -p logs data/daily
python3 metaweb.py fetch --days 1
