#!/usr/bin/env python3
"""①a dev 三路对照计分（监工 2026-09-30 深夜审 P0-1：结论必须可核验落盘）。

输入（全部已有冻结/落盘文件，无新计算口径）：
  gold     user_blind_labels_50_ORIGINAL.tsv（sha256 已书面确认，dev 身份见 errata）
  pred_D   route_d_role_dev_v1.jsonl（重跑正式版）
  pred_mesh mesh_lookup_150_preds.jsonl 的 dev 子集（冻结 sha b100c670…）
输出：eval_dev_threeway.json —— 逐条 gold/pred_D/pred_mesh、总正确数、混淆矩阵、
Wilson 95% CI、类别 n 与各类命中、输入文件 sha（可复核）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

RE = Path(__file__).resolve().parents[2] / "data/merged/route_eval"


def wilson(p: float, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0, c - h), 4), round(min(1, c + h), 4)]


def main(root: str | None = None):
    global RE
    if root:
        RE = Path(root) / "data/merged/route_eval"
    gold = {int(r["idx"]): int(r["role"])
            for r in csv.DictReader(open(RE / "user_blind_labels_50_ORIGINAL.tsv"), delimiter="\t")}
    pred_d = {p["idx"]: int(p["pred"])
              for p in map(json.loads, open(RE / "route_d_role_dev_v1.jsonl"))}
    pred_m = {int(l["idx"]): int(l["lookup_pred"])
              for l in map(json.loads, open(RE / "mesh_lookup_150_preds.jsonl")) if l["set"] == "dev"}
    assert set(gold) == set(pred_d) == set(range(1, 51))
    assert set(pred_m) >= set(range(1, 51)), "mesh dev 子集未覆盖 1-50"

    def score(pred):
        ok = sum(pred[i] == gold[i] for i in gold)
        return ok, round(ok / len(gold), 4), wilson(ok / len(gold), len(gold))

    d_ok, d_acc, d_ci = score(pred_d)
    m_ok, m_acc, m_ci = score(pred_m)
    per = [{"idx": i, "gold": gold[i], "pred_D": pred_d[i], "pred_mesh": pred_m[i],
            "D_correct": gold[i] == pred_d[i], "mesh_correct": gold[i] == pred_m[i]}
           for i in sorted(gold)]
    cls_n = Counter(gold.values())
    out = {
      "task": "dev 三路对照（gold=用户盲标 dev；D=路线D重跑v1；mesh=冻结规则）",
      "n": 50,
      "D": {"correct": d_ok, "acc": d_acc, "wilson_ci": d_ci,
            "confusion_gold_pred": {f"{g}->{p}": c for (g, p), c in
                                     sorted(Counter((gold[i], pred_d[i]) for i in gold).items())},
            "errors": {i: {"gold": gold[i], "pred": pred_d[i]} for i in sorted(gold)
                       if gold[i] != pred_d[i]}},
      "mesh": {"correct": m_ok, "acc": m_acc, "wilson_ci": m_ci,
               "note": "样本内成绩（词表在 dev 上调整后冻结，sha b100c670…）——"
                       "不得作基线与 D 对等比较（监工二轮审 P0-1/风险预警 1）"},
      "gold_class_n": dict(cls_n),
      "coverage_disclosure": "gold 仅覆盖 2/3/9 三类，background n=0——"
                             "52% 根因（背景病误判）未被 dev 测量；各类 n<10 仅描述性报告",
      "agreement_D_mesh": sum(pred_d[i] == pred_m[i] for i in gold),
      "per_item": per,
      "input_prefix_dup_stats": {"note": "annotation_sheet 原文列自带标题+首句拼接重复",
        "dup_n_of_150": 36, "check": "输入 v1/v2 与源逐条一致（600 字符内断言），非构造引入，对 D 无语义影响"},
      "input_sha256": {
        "gold": "sha256:" + hashlib.sha256((RE / "user_blind_labels_50_ORIGINAL.tsv").read_bytes()).hexdigest(),
        "pred_D": "sha256:" + hashlib.sha256((RE / "route_d_role_dev_v1.jsonl").read_bytes()).hexdigest(),
        "pred_mesh_file": "sha256:" + hashlib.sha256((RE / "mesh_lookup_150_preds.jsonl").read_bytes()).hexdigest()},
    }
    (RE / "eval_dev_threeway.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"D: {d_ok}/50={d_acc:.1%} CI{d_ci} | mesh(样本内): {m_ok}/50={m_acc:.1%} CI{m_ci}"
          f" | D-mesh 一致 {out['agreement_D_mesh']}/50 | gold 类别 n={dict(cls_n)}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None)
