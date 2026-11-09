"""卡片讲人话层（阶段 3 轨A2）——LLM 只许翻译，不许发明。

物理隔离设计（B-1 批准位 + 评审护栏）：
1. 本模块住 src/kg_tool/（NOT 06_tool/）——hypothesize 主模块的
   禁 LLM 红线 grep（llm/http/requests 全族）零命中不受影响；
2. 输入白名单=top_hypotheses.tsv+hypothesis_manifest.json，
   只读固化层产物，零新结论零重排序；
3. **强制披露标签**：每段解释必须携带 kg_version/证据粒度/门禁状态
   三列（manifest 缺任一=SystemExit fail-closed）——LLM 输出只挂在
   标签块之后，标签本身由机械代码生成不经过 LLM；
4. --no-llm（默认）：纯模板人话（确定性，离线可用）；--llm 需环境变量
   KG_LLM_BASE_URL/KG_LLM_API_KEY/KG_LLM_MODEL（OpenAI 兼容口），
   prompt 明令"只翻译给定事实，禁止新增任何生物学论断"。
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_LABELS = ("kg_version", "evidence_granularity", "k5_gate_status")


def load_disclosure_labels(sg_dir: Path) -> dict:
    """从卡片首行抽三列披露标签（三列披露的法定位置=每张卡行内）。

    缺列/空值即 fail-closed——无标签不出解释。
    """
    import csv
    with open(sg_dir / "top_hypotheses.tsv", encoding="utf-8",
              newline="") as f:
        row = next(iter(csv.DictReader(f, delimiter="\t")), None)
    if row is None:
        sys.exit("[explain] top_hypotheses.tsv 空——无卡片无解释")
    labels = {k: row.get(k) for k in REQUIRED_LABELS}
    missing = [k for k, v in labels.items() if v in (None, "")]
    if missing:
        sys.exit(f"[explain] 卡片缺披露列 {missing}——"
                 f"无标签不出解释（fail-closed）")
    return labels


def build_context(sg_dir: Path, top_n: int) -> str:
    """机械上下文：TOP 卡逐张转人话模板（零 LLM 参与）。"""
    import csv
    rows = list(csv.DictReader(
        open(sg_dir / "top_hypotheses.tsv", encoding="utf-8"), delimiter="\t"))
    lines = []
    for i, c in enumerate(rows[:top_n], 1):
        lines.append(
            f"{i}. {c['microbe_name']} → {c['metabolite_name']} → "
            f"{c['hop2_gene_name']} → {c['hop3_pathway']}：四票合计 "
            f"{c['votes_total']}（佐证{c['vote1_ext_cohort']}/链深"
            f"{c['vote2_kg_depth']}/交叉{c['vote3_gutmgene_cross']}/可测"
            f"{c['vote4_measurability']}），步进验证率 "
            f"{c['kg_coi_rate']}；证据：hop1 {c['step1_support']}")
    return "\n".join(lines)


def label_block(labels: dict) -> str:
    return (f"【披露】kg_version={labels['kg_version']}｜证据粒度="
            f"{labels['evidence_granularity']}｜k5_gate_status="
            f"{labels['k5_gate_status']}")


def llm_narrate(context: str, labels: dict) -> str:
    """LLM 翻译层：OpenAI 兼容口；prompt 禁新增论断；异常即退出不降级。"""
    import requests
    base = os.environ.get("KG_LLM_BASE_URL", "")
    key = os.environ.get("KG_LLM_API_KEY", "")
    model = os.environ.get("KG_LLM_MODEL", "")
    if not (base and key and model):
        sys.exit("[explain] --llm 需要 KG_LLM_BASE_URL/KG_LLM_API_KEY/"
                 "KG_LLM_MODEL 三个环境变量（fail-closed，不出无配置解释）")
    prompt = (
        "你是科研工具的讲解员。以下事实清单由知识图谱机械生成，"
        "你的任务：只用大白话向湿实验室研究者解释每条机制链的含义与"
        "可实验性。铁律：只翻译给定事实，禁止新增任何生物学论断、"
        "禁止给任何新结论。\n\n" + context)
    r = requests.post(f"{base.rstrip('/')}/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "messages":
                            [{"role": "user", "content": prompt}]},
                      timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="假设卡片讲人话（只读解释层）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--llm", action="store_true",
                    help="启用 LLM 翻译（需三环境变量；默认纯模板）")
    args = ap.parse_args(argv)

    labels = load_disclosure_labels(args.subgraph_dir)
    context = build_context(args.subgraph_dir, args.top_n)
    out = [label_block(labels), "",
           "以下解释由机械模板生成（--llm 可换成 LLM 翻译版）：", "", context]
    if args.llm:
        out += ["", "—— LLM 大白话版 ——", "", llm_narrate(context, labels),
                "", label_block(labels)]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
