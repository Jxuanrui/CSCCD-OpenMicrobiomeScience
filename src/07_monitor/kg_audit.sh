#!/bin/bash
# KG 定时审计（只读巡检）；API Key 从项目根 .env 读取（已 gitignore）
KG_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$KG_ROOT/.env" ] && set -a && source "$KG_ROOT/.env" && set +a
LOG="$KG_ROOT/data/logs/kg_audit_$(date +%Y%m%d).log"
STAGING="$KG_ROOT/data/staging/llm_relations.jsonl"
echo "===== KG 审计 $(date '+%F %T') =====" >> "$LOG"
CLS=$(pgrep -f "classif[y]_relations" 2>/dev/null)
[ -n "$CLS" ] && echo "[S1-classify] PID=$CLS $(ps -o etime= -p $CLS) ✅" >> "$LOG" || echo "[S1-classify] 无" >> "$LOG"
[ -f "$STAGING" ] && echo "[S2-staging] $(( $(date +%s) - $(stat -c %Y "$STAGING") ))s" >> "$LOG"
echo "[S3-neo4j] $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:17474)" >> "$LOG"
echo "[S4-corpus] $(wc -l < "$KG_ROOT/data/pubtator/articles.jsonl" 2>/dev/null) 篇" >> "$LOG"
API_R=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -x http://127.0.0.1:7890 https://open.bigmodel.cn/api/paas/v4/models -H "Authorization: Bearer ${BIGMODEL_KEY:-none}" 2>/dev/null)
echo "[S5-api] HTTP$API_R" >> "$LOG"
echo "[S6-streamlit] $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8765)" >> "$LOG"
python3 -c "
import json
from collections import Counter
try:
    rows=[json.loads(l) for l in open('$STAGING',encoding='utf-8')]
    ok=sum(1 for r in rows if r['status']=='ok')
    tb=Counter((r['subject']['id'],r['predicate'],r['object']['id']) for r in rows if r['status']=='ok')
    print(f'[summary] ok={ok} TierB={sum(1 for v in tb.values() if v>=2)} err={sum(1 for r in rows if r[\"status\"]==\"error\")}')
except: print('[summary] 读取失败')
" >> "$LOG" 2>/dev/null
echo "===== 审计结束 =====" >> "$LOG"
date +%s > "$KG_ROOT/data/logs/kg_audit.heartbeat"
