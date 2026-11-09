"""L2-D 谓词-语言忠实度审计（全量·零外部依赖·机械可算）。

为什么需要它：L2 银标准（对独立策展库）只能评估 1.8% 的边——任何"标准库"
对"文献抽取图谱"都存在实体体系错配（症状/表型词 vs 标准化临床术语），换库
换对象都绕不开。本审计换一个轴：**原文句子是客观事实**（有无因果动词可机械
判定、任何人可逐句复核），**谓词是我们的判定**——两者不一致即解读偏差。

<<<<<<< HEAD
**定位（评审 2026-11-06 裁定）**：本审计度量的是**忠实度 faithfulness**（内部
=======
**定位（监工 2026-11-06 裁定）**：本审计度量的是**忠实度 faithfulness**（内部
>>>>>>> origin/main
一致性），不是**准确率 accuracy**（对外部金标准）。全文禁以 accuracy/精确率指称。
与 A（外部银标准）、L1（结构校验）、L3（judge/MedNLI 旗标）**并列互补**。

六格归类（冻结，禁报告时挑格）：
  因果型谓词 ∩ causal 原文 = **计对（371）**
  因果型谓词 ∩ correlational 原文 = **计错（180，过度解读）**
  中性型谓词 ∩ causal 原文 = **保守（80，谓词比原文弱，不计错）**
  中性型谓词 ∩ correlational 原文 = **一致保守（50，同样不计错）**
  two × none = **无判定力（2,405 + 870=3,275）**

词表**import 复用** causal_marks 冻结常量（改词表=过审，可审计性链条不断）。
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "06_tool"))

ROOT = Path(__file__).resolve().parents[2]

<<<<<<< HEAD
# 词表引用不复制（评审 P0-1）：从 B1 冻结模块 import
=======
# 词表引用不复制（监工 P0-1）：从 B1 冻结模块 import
>>>>>>> origin/main
from causal_marks import CAUSAL_TRIGGERS, CORRELATIONAL_TRIGGERS  # noqa: E402

# 谓词方向归类（因果型断言方向/机制；中性型不主张方向）——冻结
CAUSAL_PREDICATES = ("produces", "biotransforms", "aggravates", "alleviates")
NEUTRAL_PREDICATES = ("affects", "consumes")

SIDECAR = "causal_language_marks.tsv"
ASSERTIONS = "relation_assertions.tsv"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def classify(predicate: str, mark: str) -> str:
    """六格归类（返回格名，冻结语义）。"""
    p = (predicate or "").strip().lower()
    if p in CAUSAL_PREDICATES:
        if mark == "causal":
            return "correct"
        if mark == "correlational":
            return "overread"
        return "no_judgement_causal"
    if p in NEUTRAL_PREDICATES:
        if mark == "causal":
            return "conservative"
        if mark == "correlational":
            return "consistent_conservative"
        return "no_judgement_neutral"
    return "unclassified_predicate"


def audit(sg_dir: Path, out_dir: Path) -> dict:
    sidecar_p = sg_dir / SIDECAR
    asserts_p = sg_dir / ASSERTIONS
    for p in (sidecar_p, asserts_p):
        if not p.exists():
            raise SystemExit(f"[faithfulness] 缺输入 {p}——fail-closed")

    marks = {}
    with open(sidecar_p, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            marks[r["assertion_id"]] = r["mark"]

    cells = {"correct": [], "overread": [], "conservative": [],
             "consistent_conservative": [], "no_judgement_causal": [],
             "no_judgement_neutral": [], "unclassified_predicate": []}
    n = 0
    with open(asserts_p, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            n += 1
            aid = r["assertion_id"]
            mk = marks.get(aid)
            if mk is None:
                raise SystemExit(f"[faithfulness] 断言 {aid} 无语言标注——"
                                 "sidecar 与断言不同源，fail-closed")
            cells[classify(r.get("predicate", ""), mk)].append({
                "assertion_id": aid, "predicate": r.get("predicate", ""),
                "mark": mk, "pmid": r.get("pmid", ""),
                "subject": r.get("subject_name", ""),
                "object": r.get("object_name", "")})

    c = {k: len(v) for k, v in cells.items()}
    # 分母必须按谓词类型各归各（首版把中性型 none 混入因果分母→4.70% 假低）
    causal_total = c["correct"] + c["overread"] + c["no_judgement_causal"]
    neutral_total = (c["conservative"] + c["consistent_conservative"]
                     + c["no_judgement_neutral"])
    judged = c["correct"] + c["overread"]
    with_power = c["correct"] + c["overread"] + c["conservative"] \
        + c["consistent_conservative"]

    res = {
        "n_assertions": n,
        "cells": c,
        "metrics": {
            # ①主指标：因果型谓词中被原文明确说成"相关"的比例（越低越好）
            "overread_rate": (c["overread"] / causal_total) if causal_total else None,
            # 反向读法（**上界**，词表偏差方向：corr 词表偏窄会低估 overread）
            "faithfulness_upper_bound": (1 - c["overread"] / causal_total)
            if causal_total else None,
            # ②剔除 none 空白后的方向一致率
            "direction_agreement": (c["correct"] / judged) if judged else None,
            # ③覆盖措辞分母：有语言判定力的条数/占比
            "n_with_language_power": with_power,
            "with_language_power_rate": (with_power / n) if n else None,
        },
        "denominators": {
            "causal_predicate_total": causal_total,
            "neutral_predicate_total": neutral_total,
            "no_judgement_causal": c["no_judgement_causal"],
            "no_judgement_neutral": c["no_judgement_neutral"],
            "no_judgement_total": c["no_judgement_causal"] + c["no_judgement_neutral"],
        },
        "inputs_sha256": {
            SIDECAR: sha256_file(sidecar_p),
            ASSERTIONS: sha256_file(asserts_p),
        },
        "trigger_vocab": {
            "source": "causal_marks.py (B1 冻结常量，import 复用未复制)",
            "n_causal_triggers": len(CAUSAL_TRIGGERS),
            "n_correlational_triggers": len(CORRELATIONAL_TRIGGERS),
        },
        "predicate_groups": {"causal": list(CAUSAL_PREDICATES),
                             "neutral": list(NEUTRAL_PREDICATES)},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    # ① 机器档（json，数字唯一真源）
    (out_dir / "faithfulness_audit.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    # ② 180 条过度解读清单（逐句可复核承诺的兑现载体）
    with open(out_dir / "faithfulness_overread_ids.tsv", "w",
              encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["assertion_id", "predicate", "lang_mark", "pmid",
                    "subject", "object"])
        for row in cells["overread"]:
            w.writerow([row["assertion_id"], row["predicate"], row["mark"],
                        row["pmid"], row["subject"], row["object"]])
    # ③ 摘要档
    m = res["metrics"]
    lines = [
        "# L2-D 谓词-语言忠实度审计（机器生成，数字唯一真源=faithfulness_audit.json）",
        "",
        f"- 断言总数：**{n}**（全量覆盖 100%）",
        f"- 输入锚：{SIDECAR} sha256={res['inputs_sha256'][SIDECAR][:16]}…｜"
        f"{ASSERTIONS} sha256={res['inputs_sha256'][ASSERTIONS][:16]}…",
        f"- 词表：{res['trigger_vocab']['source']}"
        f"（causal {res['trigger_vocab']['n_causal_triggers']} 词/"
        f"corr {res['trigger_vocab']['n_correlational_triggers']} 词）",
        "",
        "## 六格交叉表（冻结归类，禁挑格报告）",
        "",
        "| 格 | 语义 | 条数 |",
        "|---|---|---|",
        f"| correct | 因果型谓词 ∩ 原文 causal = **计对** | {c['correct']} |",
        f"| overread | 因果型谓词 ∩ 原文 correlational = **计错（过度解读）** | {c['overread']} |",
        f"| conservative | 中性型谓词 ∩ 原文 causal = 保守（不计错） | {c['conservative']} |",
        f"| consistent_conservative | 中性型谓词 ∩ 原文 correlational = 一致保守（不计错） | {c['consistent_conservative']} |",
        f"| no_judgement_causal | 因果型谓词 ∩ 原文 none = 无判定力（**计入因果分母**） | {c['no_judgement_causal']} |",
        f"| no_judgement_neutral | 中性型谓词 ∩ 原文 none = 无判定力（计入中性分母） | {c['no_judgement_neutral']} |",
        f"| unclassified_predicate | 谓词不在两组内 | {c['unclassified_predicate']} |",
        "",
        "## 三指标（**忠实度 faithfulness，禁称 accuracy/精确率**）",
        "",
        f"1. **过度解读率 = {m['overread_rate']:.2%}**"
        f"（{c['overread']}/{res['denominators']['causal_predicate_total']} 因果型谓词断言中原文明确只是相关话术）",
        f"2. **忠实度上界 = {m['faithfulness_upper_bound']:.2%}**"
        "（**上界**——corr 词表偏窄会低估过度解读，故此值为乐观界，须入 limitation）",
        f"3. **方向一致率 = {m['direction_agreement']:.2%}**"
        f"（{c['correct']}/{judged}，剔除 none 空白后）",
        "",
        f"**覆盖措辞（冻结）**：全量审计 {n} 条，其中 "
        f"{m['n_with_language_power']} 条（{m['with_language_power_rate']:.1%}）"
        "有语言判定力；其余 "
        f"{res['denominators']['no_judgement_total']} 条原文为中性叙述（不计错也不计对）。",
        "",
        "## 逐句可复核",
        "",
        "180 条过度解读断言清单：`faithfulness_overread_ids.tsv`"
        "（含 assertion_id/predicate/mark/pmid/subject/object，任何人可回原文核对）。",
    ]
    (out_dir / "faithfulness_audit.md").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="kg faithfulness-audit",
        description="L2-D 谓词-语言忠实度审计（全量·零外部依赖·机械）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="输出目录（默认 data/logs/l2d_faithfulness_<ts>/）")
    args = ap.parse_args(argv)
    from datetime import datetime
    out = args.out_dir or (ROOT / "data/logs" /
                           f"l2d_faithfulness_{datetime.now():%Y%m%d_%H%M%S}")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"[faithfulness] {out} 已存在且非空——"
                         "两次运行不混放，换时间戳目录（fail-closed）")
    res = audit(args.subgraph_dir, out)
    m = res["metrics"]
    print(f"[faithfulness] {out}")
    print(f"  断言 {res['n_assertions']} | 过度解读 {res['cells']['overread']}"
          f" | 过度解读率 {m['overread_rate']:.2%}"
          f" | 忠实度上界 {m['faithfulness_upper_bound']:.2%}"
          f" | 有判定力 {m['n_with_language_power']}"
          f"（{m['with_language_power_rate']:.1%}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
