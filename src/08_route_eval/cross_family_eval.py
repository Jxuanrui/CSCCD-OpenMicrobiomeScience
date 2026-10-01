#!/usr/bin/env python3
"""跨家族复核（监工 testscore 审拍板#3）：DeepSeek 全量 100 条 test_100 盲评。

家族边界：D=glm-5.3-flash（GLM 家族）→ candidate；本复核=deepseek-v4-1-flash
（火山方舟 Coding plan，与 GLM 不同家族），同输入/同 prompt 语义/temperature=0，
gold 已定版（C1 闭环后计分合法——gold 于复核前已对执行方可见，但 D 预测先于
gold 锁定且本复核与 D 无关，其意义是独立第二意见而非盲评）。

用法：python3 cross_family_eval.py --root 主项目根
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RE = ROOT / "data/merged/route_eval"
MODEL = "deepseek-v4-1-flash-260910"
SYSTEM = ("疾病语义角色九分类：1 background（背景已有疾病）/ 2 target_disease（谓词直接宾语或断言直接关于的疾病）/ "
          "3 endpoint_related（inflammation/damage/barrier/tumorigenesis/carcinogenesis 等过程指标）/ 4 subgroup / "
          "5 comorbidity / 6 exclusion / 7 treatment_context / 8 not_disease（仅修饰）/ 9 uncertain。"
          "互斥：疾病名+谓词直接宾语→2；过程指标词→3。")

def call_api(base: str, key: str, items: list[dict]) -> str:
    body = {"model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "只输出答案行\"idx=ANSWER: 数字\"（每条一行），禁止分析。\n" +
                          "\n".join(f"{r['idx']} | {r['subject']} | {r['disease']} | {r['sentence']}" for r in items)}],
            "max_tokens": 1024, "temperature": 0, "thinking": {"type": "disabled"}}
    req = urllib.request.Request(base.rstrip('/') + "/chat/completions",
        data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {key}",
                                                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["choices"][0]["message"]["content"]

def parse_answers(text: str, idxs: list[int]) -> dict[int, int]:
    out = {}
    for line in text.splitlines():
        m = re.search(r'(\d+)\D*[:=]\D*ANSWER\D*:?\s*(\d)|ANSWER\D*:\s*(\d)', line, re.I)
        if m:
            idx = int(m.group(1)) if m.group(1) in idxs else None
            val = int(m.group(2) or m.group(3))
            if idx: out[idx] = val
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args()
    global RE
    RE = Path(args.root) / "data/merged/route_eval"
    base, key = os.getenv("ARK_BASE_URL"), os.getenv("ARK_KEY")
    assert base and key, "需 ARK_BASE_URL/ARK_KEY（项目根 .env）"

    rows = [json.loads(l) for l in open(RE / "route_d_role_test_input_v2.jsonl")]
    assert len(rows) == 100
    preds: dict[int, int] = {}
    raw_log = []
    batch, B = 0, 3
    while batch * B < len(rows):
        items = rows[batch * B:(batch + 1) * B]
        idxs = [r["idx"] for r in items]
        if all(i in preds for i in idxs):
            batch += 1; continue
        text, err = None, None
        for attempt in range(3):
            try:
                text = call_api(base, key, items); break
            except Exception as e:
                err = str(e)[:120]; time.sleep(1.5 * (attempt + 1))
        raw_log.append({"batch": batch, "idx": idxs,
                        "raw_output": (text or "")[:500], "error": err,
                        "retries": attempt, "ts": datetime.now(timezone.utc).isoformat()})
        if text:
            preds.update(parse_answers(text, idxs))
        if (batch + 1) % 5 == 0:
            print(f"[{batch+1}/{(len(rows)+B-1)//B}] 已得 {len(preds)}/100", flush=True)
        batch += 1
        time.sleep(0.3)

    missing = [r["idx"] for r in rows if r["idx"] not in preds]
    if missing:
        print(f"[warn] 未解析到 {len(missing)} 条：{missing}（见 raw_log 人工核对）")

    out = RE / "cross_family_ds_preds.jsonl"
    out.write_text("\n".join(json.dumps({"idx": i, "pred": preds[i]}) for i in sorted(preds)) + "\n")
    (RE / "cross_family_ds_raw.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in raw_log) + "\n")
    meta = {"file": out.name, "n": len(preds), "missing": missing,
            "model": MODEL, "family": "DeepSeek（非 GLM）", "channel": "火山方舟 Coding plan（用户提供 key，仅存 .env）",
            "temperature": 0, "thinking": "disabled（reasoning_tokens=0 实测）",
            "input_file": "route_d_role_test_input_v2.jsonl",
            "input_sha256": "sha256:56d6d4d048ee28db85909002eeecb678fbdf3b6cb8d876104a9694313abc682",
            "system_prompt_sha256": "sha256:" + hashlib.sha256(SYSTEM.encode()).hexdigest(),
            "note": "gold 于复核前已定版可见——本复核性质=独立第二意见（非盲评）；D 预测先于 gold 锁定不受影响",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "output_sha256": "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest()}
    (RE / "cross_family_ds_preds.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps({k: meta[k] for k in ["n", "missing", "output_sha256"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
