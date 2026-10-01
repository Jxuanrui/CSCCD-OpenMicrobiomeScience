#!/usr/bin/env python3
"""补充集预测 runner（监工 cf2 确认路线：D 补齐→锁 sha→DS（JSON mode）→才交用户盲标）。

引擎：
  glm-ark  = glm-5-3-flash-260828（火山方舟，temperature=0，不传 thinking——该型号
             不接受 disabled 且内建推理需自流；通道变更自 MCP glm_flash，用户 2026-10-01
             指示"用官方的"，原 MCP 通道 4 条作废，50 条全量同通道）
  ds-ark   = deepseek-v4-1-flash-260910（temperature=0 + thinking disabled + JSON mode
             ——与 test_100 的 DS 配置不同，成绩不与之合并，单独记录）

输入 supp_bg_50_blind.tsv（sha 99cc125c…）；在线只落 raw（断点续跑）；解析与计分
走 --offline（复现模式，与 cross_family_eval.py 同原则）。
"""
from __future__ import annotations

import argparse
import csv
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
ENGINES = {
  "glm-ark": {"model": "glm-5-3-flash-260828", "temperature": 0, "thinking": None,
              "raw": "supp_bg_d_raw.jsonl", "preds": "supp_bg_d_preds.jsonl",
              "prompt": "只输出答案行\"{idx}=ANSWER: 数字\"（每条一行），禁止分析。"},
  "ds-ark": {"model": "deepseek-v4-1-flash-260910", "temperature": 0,
              "thinking": {"type": "disabled"}, "raw": "supp_bg_ds_raw.jsonl",
              "preds": "supp_bg_ds_preds.jsonl", "json_mode": True,
              "prompt": "输出一个 JSON 数组，每项 {{\"idx\": \"S001\", \"pred\": 1-9}}，无其他文字。"},
}
SYSTEM = ("疾病语义角色九分类：1 background（背景已有疾病，如 patients with X 队列）/ 2 target_disease"
          "（谓词直接宾语或断言直接关于的疾病）/ 3 endpoint_related（inflammation/damage/barrier/"
          "tumorigenesis 等过程指标）/ 4 subgroup / 5 comorbidity / 6 exclusion / 7 treatment_context / "
          "8 not_disease（仅修饰）/ 9 uncertain。互斥：疾病名+谓词直接宾语→2；过程指标词→3。")


def load_items() -> list[dict]:
    rows = list(csv.DictReader(open(RE / "supp_bg_50_blind.tsv"), delimiter="\t"))
    return [{"idx": r["序号"], "subject": r["主体（菌/食物）"],
             "disease": r["客体（疾病名）"], "sentence": r["证据句子（原文）"]} for r in rows]


def call(base, key, eng, items):
    body = {"model": eng["model"], "temperature": eng["temperature"],
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": eng["prompt"] + "\n" +
                          "\n".join(f"{r['idx']} | {r['subject']} | {r['disease']} | {r['sentence']}" for r in items)}],
            "max_tokens": 8192}
    if eng.get("thinking"): body["thinking"] = eng["thinking"]
    if eng.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
        body["messages"][1]["content"] = ("输出 JSON 对象 {\"answers\":[{\"idx\":\"S001\",\"pred\":1-9},...]}。设 involved 为空数组。\n"
                                          + body["messages"][1]["content"])
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {key}",
                                                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)["choices"][0]["message"]["content"]


def parse(text: str, idxs: list[str]) -> dict[str, int]:
    out = {}
    for m in re.finditer(r'(S\d{3})\D{0,12}?([1-9])\b', text):
        if m.group(1) in idxs: out[m.group(1)] = int(m.group(2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--engine", choices=list(ENGINES), required=True)
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="0=全部（断点续跑跳过已得）")
    args = ap.parse_args()
    global RE
    RE = Path(args.root) / "data/merged/route_eval"
    base, key = os.getenv("ARK_BASE_URL"), os.getenv("ARK_KEY")
    assert base and key, "需 ARK_BASE_URL/ARK_KEY（.env）"
    eng = ENGINES[args.engine]
    items = load_items()
    raw_path, preds_path = RE / eng["raw"], RE / eng["preds"]
    preds = {}
    if preds_path.exists():
        preds = {r["idx"]: r["pred"] for r in map(json.loads, open(preds_path))}
    raw_f = open(raw_path, "a", encoding="utf-8")
    todo = [r for r in items if r["idx"] not in preds][: args.limit or None]
    for b in range(0, len(todo), args.batch_size):
        batch = todo[b:b + args.batch_size]
        text, err = None, None
        for attempt in range(3):
            try:
                text = call(base, key, eng, batch); break
            except Exception as e:
                err = str(e)[:150]; time.sleep(2 * (attempt + 1))
        got = parse(text or "", [r["idx"] for r in batch]) if text else {}
        raw_f.write(json.dumps({"batch": b // args.batch_size, "idx": [r["idx"] for r in batch],
                                "raw_output": (text or "")[:600], "error": err, "got": len(got)},
                               ensure_ascii=False) + "\n")
        raw_f.flush()
        preds.update(got)
        preds_path.write_text("\n".join(json.dumps({"idx": i, "pred": preds[i]}) for i in sorted(preds)) + "\n")
        print(f"[{b//args.batch_size+1}] 累计 {len(preds)}/{len(items)}（本轮 +{len(got)}）", flush=True)
        time.sleep(0.4)
    meta = {"engine": args.engine, "model": eng["model"], "temperature": 0,
            "thinking": "disabled" if eng.get("thinking") else "未传（glm 型不接受 disabled，内建推理自流）",
            "json_mode": bool(eng.get("json_mode")),
            "input": "supp_bg_50_blind.tsv",
            "input_sha256": "sha256:" + hashlib.sha256((RE / "supp_bg_50_blind.tsv").read_bytes()).hexdigest(),
            "channel_change_note": "glm-ark：MCP glm_flash→火山方舟官方直连（用户 2026-10-01 指示；原 MCP 4 条作废，50 条同通道全量）" if args.engine == "glm-ark" else "ds-ark：与 test_100 DS 配置不同（JSON mode），成绩不合并",
            "blinding": "预测分布不公开；gold 交回前仅公开 preds sha",
            "n": len(preds), "complete": len(preds) == len(items),
            "output_sha256": "sha256:" + hashlib.sha256(preds_path.read_bytes()).hexdigest(),
            "generated_at": datetime.now(timezone.utc).isoformat()}
    (RE / (eng["preds"].replace(".jsonl", ".meta.json"))).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps({k: meta[k] for k in ["n", "complete", "output_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
