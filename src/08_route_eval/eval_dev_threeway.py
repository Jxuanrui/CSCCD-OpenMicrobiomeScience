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


# ---- v3 审 P0-1：test / supp_bg 计分正式化（并入本脚本，不新建文件；wilson 复用）----

def score_test(RE):
    """test_100 三路计分（gold 直付版）→ eval_test_score.json 复现。"""
    gold = {int(l.split("\t")[0]): int(l.split("\t")[1])
            for l in (RE / "gold_test_100_user_direct.tsv").read_text().splitlines()[1:]}
    pred_d = {r["idx"]: r["pred"] for r in map(json.loads, open(RE / "route_d_role_test_preds_full.jsonl"))}
    pred_m = {int(l["idx"]): int(l["lookup_pred"]) for l in
              map(json.loads, open(RE / "mesh_lookup_150_preds.jsonl")) if l["set"] == "test"}
    assert set(gold) == set(pred_d) == set(range(51, 151))
    out = json.loads((RE / "eval_test_score.json").read_text())  # 原有披露字段保留
    d_ok = sum(pred_d[i] == gold[i] for i in gold)
    m_ok = sum(pred_m[i] == gold[i] for i in gold)
    out["reproduced_by"] = "eval_dev_threeway.py --split test"
    assert out["D"]["correct"] == d_ok and out["mesh"]["correct"] == m_ok, "复现数字与落盘不一致"
    print(f"test 复现：D {d_ok}/100、mesh {m_ok}/100（与落盘一致）")


def score_supp(RE):
    """补充集三路双口径计分 → eval_supp_score.json 复现（v3 审裁：full50 主口径）。"""
    gold = {l.split("\t")[0]: int(l.split("\t")[1])
            for l in (RE / "supp_bg_50_gold.tsv").read_text().splitlines()[1:]}
    pd_ = {r["idx"]: r["pred"] for r in map(json.loads, open(RE / "supp_bg_d_preds.jsonl"))}
    ps_ = {r["idx"]: r["pred"] for r in map(json.loads, open(RE / "supp_bg_ds_preds.jsonl"))}
    pm_ = {r["idx"]: int(r["lookup_pred"]) for r in map(json.loads, open(RE / "supp_bg_mesh_preds.jsonl"))}
    assert len(gold) == len(pd_) == len(ps_) == len(pm_) == 50
    out = json.loads((RE / "eval_supp_score.json").read_text())
    out["primary_calibration"] = "full50（v3 审终裁：预注册停止规则触发后 full50 为最终分析集；first30=交付批敏感性）"
    out["reserve20_disclosure"] = {"background_n": 0, "target_n": 14, "endpoint_n": 6,
        "reserve20_scores": {"mesh": 20, "D": 17, "DS": 16},
        "note": "两口径差异完全来自 reserve20 段 mesh 全对（监工 v3 E6）——两口径并列呈现"}
    for name, pr in [("D_glm_ark", pd_), ("DS_ark", ps_), ("mesh_frozen", pm_)]:
        ok30 = sum(pr[i] == gold[i] for i in gold if int(i[1:]) <= 30)
        ok50 = sum(pr[i] == gold[i] for i in gold)
        assert out["scores"][name]["first30"]["correct"] == ok30
        assert out["scores"][name]["full50"]["correct"] == ok50
    # 背景类 CI（v3 审 P0-3 允许的表述数据）
    bg = [i for i in gold if gold[i] == 1]
    out["background_class_disclosure"] = {
        "n": 3, "items": {i: {"D": pd_[i], "DS": ps_[i], "mesh": pm_[i],
                              "wilson_D": wilson(1.0, 3), "wilson_DS": wilson(2/3, 3)} for i in bg},
        "mesh_note": "0/3 为设计决定（predict 只输出 2/3/9），非测量结果",
        "statement_rule": "描述性，不支持推断；不得作为根因证据（监工 v3 P0-3）"}
    out["reproduced_by"] = "eval_dev_threeway.py --split supp_bg"
    (RE / "eval_supp_score.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"supp 复现：D {out['scores']['D_glm_ark']['full50']['correct']}/50、"
          f"DS {out['scores']['DS_ark']['full50']['correct']}/50、"
          f"mesh {out['scores']['mesh_frozen']['full50']['correct']}/50；主口径已改 full50+披露已补")


if __name__ == "__main__":
    import sys
    root, split = None, "dev"
    for a in sys.argv[1:]:
        if a.startswith("--split="):
            split = a.split("=", 1)[1]
        elif not a.startswith("--"):
            root = a
    if split == "dev":
        main(root)
    else:
        RE_ = (Path(root) if root else Path(__file__).resolve().parents[2]) / "data/merged/route_eval"
        (score_test if split == "test" else score_supp)(RE_)
