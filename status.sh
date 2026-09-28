#!/usr/bin/env bash
# 汇报前必跑的执行健康核验：输出事实证据（进程/时间戳/git/CI），供进度汇报引用。
# 用法：bash status.sh
set -u
W=/data/LYteamwork/JiXuanRui/Project/Knowledge_Graph-mra
echo "==[1] 我方后台进程（ps 事实）=="
FOUND=0
for PAT in 'full_sca[n]' 'run_batc[h]' 'mra.researc[h]' 'temporal_runne[r]' 'vecstor[e]' 'litrea[d]'; do
  ps aux | grep "$PAT" | grep -v grep | grep -v Dietary_cohort && FOUND=1
done
[ "$FOUND" = "0" ] && echo "（无）"
echo; echo "==[2] 雷达 cron 新鲜度 =="
tail -1 "$W/radar/logs/cron.log" 2>/dev/null || echo "⚠ 无日志"
echo; echo "==[3] git 状态 =="
cd "$W" && git log --oneline -1; DIRTY=$(git status --short | grep -v '^??' | wc -l); echo "未提交修改: $DIRTY"
echo; echo "==[4] 运行态目录最近写入 =="
for D in mra/var/research mra/var/vecstore mra/var/exec_registry artifact_engine/data; do
  [ -d "$D" ] && ls -lt --time-style='+%m-%d %H:%M' "$D" 2>/dev/null | sed -n 2p | awk -v d="$D" '{print d": "$6, $7}'
done
echo; echo "==[5] 上次本地测试 =="
grep -c 'def test' mra/tests -r 2>/dev/null | awk -F: '{s+=$2} END {print "测试函数总数: "s}'
