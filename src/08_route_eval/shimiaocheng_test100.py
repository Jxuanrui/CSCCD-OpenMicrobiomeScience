#!/usr/bin/env python3
"""shimiaocheng GLM-5.3 在 test_100 分类任务上的评估（决策测试）.

目的：确定 GLM-5.3 @ shimiaocheng 能否替代 ARK/DeepSeek 完成 S1+S2+S3 全链。
≥ 80% → 全 GLM 方案可行；< 80% → 回到监工混合方案。
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path
from collections import Counter

RE = Path(__file__).resolve().parents[2] / "data/merged/route_eval"
BASE = os.getenv("JUDGE_BASE_URL", "https://ai.shimiaocheng.top/v1")
KEY = os.getenv("JUDGE_KEY", "")
MODEL = "GLM-5.3"

SYSTEM = (
    "疾病语义角色九分类：1 background（背景已有疾病）/ 2 target_disease（谓词直接宾语或断言直接关于的疾病）/ "
    "3 endpoint_related（inflammation/damage/barrier/tumorigenesis 等过程指标）/ 4 subgroup / "
    "5 comorbidity / 6 exclusion / 7 treatment_context / 8 not_disease / 9 uncertain。"
    "互斥：疾病名+谓词直接宾语→2；过程指标词→3。"
)

def call(items):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "对下列每条判定角色，只输出数字(1-9)每行一个按顺序，禁止分析。\n" +
             "\n".join(f"{r['idx']} | {r['subject']} | {r['disease']} | {r['sentence'][:250]}" for r in items)}
        ],
        "max_tokens": 2048,
        "temperature": 0,
    }
    req = urllib.request.Request(
        BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                return None

def parse(text, idxs):
    if not text:
        return {}
    out = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # 优先：带ID格式 "51=ANSWER: 2" / "51: 2"
    for line in lines:
        m = re.search(r'(\d+)\s*[:=]\s*ANSWER\s*:?\s*(\d)', line, re.I) or \
              re.search(r'^(\d+)\s*[:=]\s*([1-9])\b', line)
        if m and int(m.group(1)) in idxs:
            out[int(m.group(1))] = int(m.group(2))

    # 兜底：位置解析（第N行对应第N条）
    if len(out) < len(idxs):
        vals = []
        for line in lines:
            m = re.search(r'^([1-9])\s*$', line)  # 纯数字行
            if m:
                vals.append(int(m.group(1)))
            else:
                m2 = re.search(r'ANSWER\s*:?\s*([1-9])', line, re.I)
                if m2:
                    vals.append(int(m2.group(1)))
        if len(vals) == len(idxs):
            out = {idxs[i]: v for i, v in enumerate(vals)}

    return out

def main():
    rows = [json.loads(l) for l in open(RE / "route_d_role_test_input_v2.jsonl")]
    gold = {int(l.split("\t")[0]): int(l.split("\t")[1])
            for l in open(RE / "gold_test_100_user_direct.tsv").readlines()[1:]}

    preds = {}
    for b in range(0, len(rows), 5):
        items = rows[b:b + 5]
        idxs = [r["idx"] for r in items]
        text = call(items)
        got = parse(text, idxs)
        preds.update(got)
        print(f"[{b//5+1}/{(len(rows)+4)//5}] {len(preds)}/{len(rows)}", flush=True)
        time.sleep(0.3)

    ok = sum(preds.get(i) == gold[i] for i in gold if i in preds)
    covered = len(preds)
    acc = ok / covered if covered else 0

    print(f"\n{'='*60}")
    print(f"===== shimiaocheng GLM-5.3 test_100 结果 =====")
    print(f"覆盖: {covered}/100")
    print(f"正确: {ok}/{covered}")
    print(f"准确率: {acc:.1%}")
    print(f"{'='*60}")

    if covered >= 95:
        # 混淆矩阵
        conf = Counter((gold[i], preds.get(i)) for i in gold if i in preds)
        print(f"混淆: {dict(sorted(conf.items()))}")

        # 对比表
        print(f"\n与其他端点对比：")
        print(f"  shimiaocheng GLM-5.3:  {acc:.1%} @ {covered}/100")
        print(f"  ark/DeepSeek:          90.0% @ 90/100")
        print(f"  ark/GLM-5.3-flash:     68.6% @ 35/100")
        print(f"  coding plan GLM:       54.0% @ 100/100")
        print(f"  MCP glm_flash(原):     84.0% @ 100/100")
        print(f"\n结论: {'✅ ≥80% 全 GLM 方案可行' if acc >= 0.80 else '❌ <80% 需要混合方案'}")

        # 保存结果
        json.dump({
            "endpoint": BASE, "model": MODEL,
            "covered": covered, "correct": ok, "accuracy": round(acc, 4),
            "verdict": "PASS" if acc >= 0.80 else "FAIL",
            "threshold": 0.80,
            "preds": preds,
        }, open(RE / "shimiaocheng_glm_test100.json", "w"), indent=1)
        print(f"结果已保存: {RE / 'shimiaocheng_glm_test100.json'}")
    else:
        print(f"⚠️ 覆盖不足 ({covered}/100)，解析可能有问题")

if __name__ == "__main__":
    main()
