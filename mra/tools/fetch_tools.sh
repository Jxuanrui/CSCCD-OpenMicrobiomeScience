#!/usr/bin/env bash
# tools/fetch_tools.sh — 可复现地下载本平台外部二进制（PLANNING.md 第 9 章 Q10）
# 规则：固定版本 + SHA256 校验；二进制本体不入 Git（tools/* 已 gitignore）。
set -euo pipefail
cd "$(dirname "$0")"

# OPA（策略决策点 PDP，PLANNING.md 5.2）
OPA_VERSION="1.20.2"
OPA_SHA256="69da5179ee403d10fa11bab6cfb4ffb0d23dba5f9b682fa977db772a1da5670f"
OPA_URL="https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_amd64_static"

fetch() { # $1=url $2=dest $3=sha256
    if [ -f "$2" ] && echo "$3  $2" | sha256sum -c --status 2>/dev/null; then
        echo "[ok] $2 已存在且哈希匹配，跳过"
        return
    fi
    echo "[fetch] $1"
    curl -sSL --fail -o "$2" "$1"
    chmod +x "$2"
    echo "$3  $2" | sha256sum -c
}

fetch "$OPA_URL" opa "$OPA_SHA256"
echo "完成。注意：OCI→SIF 等其他制品的外部存储路径需另行审批（PLANNING.md 5.14-Q2），不在本脚本范围。"
