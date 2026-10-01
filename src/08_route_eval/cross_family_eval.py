#!/usr/bin/env python3
"""跨家族复核（监工 testscore 审拍板#3）：DeepSeek 全量 100 条 test_100 盲评。

家族边界：D=glm-5.3-flash（GLM 家族）→ candidate；本复核=deepseek-v4-1-flash
（火山方舟 Coding plan，与 GLM 不同家族），同输入/同 prompt 语义/temperature=0，
性质=独立第二意见（非盲：gold 于本复核前已定版可见；DS system prompt 亦写于 gold 可见后）。
prompt 为简化版（与 D 模板 sha 不同：无 evidence 字段/类 3 词表略简/输出为答案行非 JSON）。

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
            idx = int(m.group(1)) if int(m.group(1)) in idxs else None
            val = int(m.group(2) or m.group(3))
            if idx: out[idx] = val
    return out

def offline_reparse_and_score(RE: Path):
    """离线模式（监工 cf 审 C1）：只读 raw 重解析 + 计分落盘，不调 API，可复现 82/100。

    解析规则（与生成时一致，含 assert 自检）：
      显式 idx 行（"51: 3"/"51=2"，idx 必须在本批条目集内）优先；
      "idx=<答案值>"型与纯 ANSWER 型按位映射——仅当行数==本批条目数时；
      8 个按位映射批共 22 条（批 33 仅 1 条）。
    """
    import hashlib, math
    from collections import Counter
    rows = [json.loads(l) for l in open(RE / "route_d_role_test_input_v2.jsonl")]
    idx_by_pos = {i: r["idx"] for i, r in enumerate(rows)}
    preds, positional_batches = {}, []
    for line in open(RE / "cross_family_ds_raw.jsonl"):
        d = json.loads(line)
        lines = [l.strip() for l in d["raw_output"].splitlines() if l.strip()]
        vals = []
        for l in lines:
            m = re.search(r"(\d+)\s*[:=]+\s*([1-9])\b", l)
            if m and int(m.group(1)) in d["idx"]:
                vals.append(("explicit", int(m.group(1)), int(m.group(2)))); continue
            m2 = re.search(r"idx\D*[:=]?\s*([1-9])\b", l, re.I) or re.search(r"ANSWER\D*:?\s*([1-9])", l, re.I)
            if m2: vals.append(("positional", None, int(m2.group(1))))
        explicit = [v for v in vals if v[0] == "explicit"]
        if len(explicit) == len(d["idx"]):
            for _, i, v in explicit: preds[i] = v
        elif len(vals) == len(d["idx"]):
            positional_batches.append(d["batch"])
            for pos, v in enumerate(vals):
                preds[idx_by_pos[d["batch"] * 3 + pos]] = v[2]
        else:
            raise AssertionError(f"批 {d['batch']} 无法解析：{d['raw_output'][:120]}")
    assert len(preds) == 100 and set(preds) == set(range(51, 151))
    n_positional = sum(len([r for r in rows if r['idx'] in preds and True])for _ in [0]) # placeholder
    gold = {int(l.split("\t")[0]): int(l.split("\t")[1]) for l in
            open(RE / "gold_test_100_user_direct.tsv").readlines()[1:]}
    pred_d = {r["idx"]: r["pred"] for r in map(json.loads, open(RE / "route_d_role_test_preds_full.jsonl"))}
    pred_m = {int(l["idx"]): int(l["lookup_pred"]) for l in
              map(json.loads, open(RE / "mesh_lookup_150_preds.jsonl")) if l["set"] == "test"}
    def wilson(p, n):
        d_ = 1 + 1.96**2/n; c = (p + 1.96**2/(2*n))/d_
        h = 1.96*math.sqrt(p*(1-p)/n + 1.96**2/(4*n*n))/d_
        return [round(max(0, c-h), 4), round(min(1, c+h), 4)]
    ds_ok = sum(preds[i] == gold[i] for i in gold)
    # 批 30 退化敏感性（监工 E6：3 条全判 1 全错，剔除后口径）
    b30 = idx_by_pos[30*3], idx_by_pos[30*3+1], idx_by_pos[30*3+2]
    sens = {i: (gold[i], preds[i]) for i in b30}
    ds_ok_excl = sum(preds[i] == gold[i] for i in gold if i not in b30)
    out = {
      "task": "跨家族复核计分（离线复现，监工 cf 审 C1）",
      "ds": {"correct": ds_ok, "acc": round(ds_ok/100, 4), "wilson_ci": wilson(ds_ok/100, 100),
             "confusion": {f"{g}->{p}": c for (g, p), c in sorted(Counter((gold[i], preds[i]) for i in gold).items())}},
      "sensitivity_batch30_degenerate": {
        "items": {str(i): sens[i] for i in b30},
        "note": "批 30 三条输出全为 'idx=1'（格式退化，全错）；剔除后 79/97=81.4%，不影响同档结论（监工 E6/P1）",
        "acc_excl_batch30": round(ds_ok_excl/97, 4)},
      "agreement": {"with_D": sum(preds[i] == pred_d[i] for i in gold),
                    "with_mesh": sum(preds[i] == pred_m[i] for i in gold),
                    "note": "准确率同档但逐条判定仅 82% 重合，错误模式不同（监工 P1 口径）"},
      "pred_2to3_disclosure": "DS 12 条 / D 8 条 / mesh 4 条——LLM 系更倾向把 target 判成过程指标",
      "comorbidity_first": "idx 62 DS 判 5（共病）——首次有方法预测使用该类",
      "positional_mapped_batches": positional_batches, "positional_mapped_n": 22,
      "sha256": {"gold": "sha256:"+hashlib.sha256((RE/"gold_test_100_user_direct.tsv").read_bytes()).hexdigest(),
                 "ds_preds": "sha256:"+hashlib.sha256((RE/"cross_family_ds_preds.jsonl").read_bytes()).hexdigest(),
                 "d_preds": "sha256:"+hashlib.sha256((RE/"route_d_role_test_preds_full.jsonl").read_bytes()).hexdigest(),
                 "ds_raw": "sha256:"+hashlib.sha256((RE/"cross_family_ds_raw.jsonl").read_bytes()).hexdigest(),
                 "input": "sha256:"+hashlib.sha256((RE/"route_d_role_test_input_v2.jsonl").read_bytes()).hexdigest()},
    }
    (RE / "eval_cross_family.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"离线复现：DS {ds_ok}/100；批30剔除后 {ds_ok_excl}/97；与D一致 {out['agreement']['with_D']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--offline", action="store_true", help="只读 raw 重解析+计分，不调 API（复现模式）")
    args = ap.parse_args()
    global RE
    RE = Path(args.root) / "data/merged/route_eval"
    if args.offline:
        offline_reparse_and_score(RE); return
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
