#!/bin/bash
KG_ROOT="/data/LYteamwork/JiXuanRui/Project/Knowledge_Graph"
LOG="/tmp/kg_audit_$(date +%Y%m%d).log"
STAGING="$KG_ROOT/data/staging/llm_relations.jsonl"
ARTICLES="$KG_ROOT/data/pubtator/articles.jsonl"
HEARTBEAT="/tmp/kg_audit.heartbeat"

echo "===== KG 审计 $(date '+%F %T') =====" >> "$LOG"
CLS_PIDS=$(pgrep -f "classif[y]_relations" 2>/dev/null)
if [ -n "$CLS_PIDS" ]; then
  for p in $CLS_PIDS; do
    ET=$(ps -o etime= -p $p 2>/dev/null | tr -d ' ')
    echo "[S1-classify] PID=$p 运行=$ET ✅" >> "$LOG"
  done
else
  echo "[S1-classify] 无运行进程" >> "$LOG"
fi
if [ -f "$STAGING" ]; then
  STALE=$(( $(date +%s) - $(stat -c %Y "$STAGING") ))
  if [ $STALE -gt 1800 ]; then
    echo "[S2-staging] ${STALE}s 陈旧（S2/S3 正常）" >> "$LOG"
  else
    echo "[S2-staging] ${STALE}s 新鲜 ✅" >> "$LOG"
  fi
fi
NEO4J_OK=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:17474 2>/dev/null)
echo "[S3-neo4j] HTTP$NEO4J_OK" >> "$LOG"
if [ -f "$ARTICLES" ]; then
  echo "[S4-corpus] $(wc -l < "$ARTICLES") 篇" >> "$LOG"
fi
API_R=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -x http://127.0.0.1:7890 https://ai.shimiaocheng.top/v1/models -H "Authorization: Bearer ${SHIMIAO_KEY}" 2>/dev/null)
echo "[S5-api] HTTP$API_R" >> "$LOG"
ST_OK=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8765 2>/dev/null)
echo "[S6-streamlit] HTTP$ST_OK" >> "$LOG"
python3 -c "
import json
from collections import Counter
try:
    rows=[json.loads(l) for l in open('$STAGING',encoding='utf-8')]
    ok=sum(1 for r in rows if r['status']=='ok')
    food=sum(1 for r in rows if r['status']=='ok' and r['subject'].get('category')=='Food')
    err=sum(1 for r in rows if r['status']=='error')
    tb=Counter((r['subject']['id'],r['predicate'],r['object']['id']) for r in rows if r['status']=='ok')
    print(f'[summary] ok={ok} Food={food} TierB={sum(1 for v in tb.values() if v>=2)} error={err}')
except: print('[summary] staging 读取失败')
" >> "$LOG" 2>/dev/null
echo "===== 审计结束 =====" >> "$LOG"
date +%s > "$HEARTBEAT"
