"""交付A1：假设包交付件生成器——把 43,773 张卡片浓缩成实验室可直接开工的交付包。

为什么这么做（设计约束，均出自 20261102 一次性方案 W-交付A1 + 监工 P0-2）：
1. 只做"机器字段转印"——详卡上的每一个字都来自既有 TSV 的既有列，
   禁止引入任何未经核验的外部事实（检测法建议等一律不写）；
2. 前 100 简卡沿用 hypothesize.rank_key 同一排序键，且以
   "top10 复现校验"fail-closed——排序若与既有 TOP 榜不一致直接报错退出，
   防止交付件与已交付卡片出现两套排序；
3. 每张详卡强制携带三列披露（kg_version/证据粒度/门禁状态）与票①混杂警示句。
"""
import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def rank_key(c: dict) -> tuple:
    """与 hypothesize.rank_key 完全同式（复制而非 import，防阶段 2 改造期被牵连）。

    读 TSV 回读值全为字符串——显式转型同源逻辑。
    """
    return (-int(c["votes_total"]), -int(c["borda_score"]),
            -float(c["kg_coi_rate"]), float(c["penalty"]), c["chain_id"])


def read_tsv(p: Path) -> list:
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def parse_step1_support(s: str) -> tuple:
    """解析 step1_support（形如 subgraph_edge:<sha16>|pmids:<p1;p2>）。

    返回 (edge_id16, pmids)；解析失败返回 ("", [])——hop1 证据则标"无"。
    """
    edge, pmids = "", []
    for part in (s or "").split("|"):
        if part.startswith("subgraph_edge:"):
            edge = part.split(":", 1)[1]
        elif part.startswith("pmids:"):
            pmids = [x for x in part.split(":", 1)[1].split(";") if x]
    return edge, pmids


def build_hop1_evidence(assertions: list) -> dict:
    """把子图断言表按 (edge_id, pmid) 建索引——hop1 原句查表用（零新事实）。"""
    idx: dict = {}
    for a in assertions:
        idx.setdefault(a["edge_id"], []).append(a)
    return idx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="假设包交付件生成（交付A1）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--top-n", type=int, default=100,
                    help="简卡行数（监工 D 审建议：首轮反馈载体=前 100）")
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args(argv)

    sg = args.subgraph_dir
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=False)

    cards = read_tsv(sg / "hypothesis_cards.tsv")
    tops = read_tsv(sg / "top_hypotheses.tsv")
    assertions = read_tsv(sg / "relation_assertions.tsv")
    hop1_idx = build_hop1_evidence(assertions)

    # —— 排序复现校验（fail-closed）：本脚本排出的前 10 必须与既有 TOP 榜同序
    ranked = sorted(cards, key=rank_key)
    mine10 = [c["chain_id"] for c in ranked[:10]]
    theirs10 = [c["chain_id"] for c in tops]
    if mine10 != theirs10:
        print(f"[FAIL] top10 复现校验不等：mine={mine10[:3]} theirs={theirs10[:3]}…")
        return 1

    # —— ①前 N 简卡（关键列子集，全部既有列转印）
    brief_cols = ["chain_id", "microbe_name", "metabolite_name", "hop2_gene_name",
                  "hop3_pathway", "depth", "hop_terminus", "votes_total",
                  "borda_score", "kg_coi_rate", "strain_available",
                  "endpoint_meas_ko", "confound_warning", "kg_version",
                  "k5_gate_status"]
    brief_path = out / f"top{args.top_n}_brief_cards.tsv"
    with open(brief_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=brief_cols, delimiter="\t",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(ranked[:args.top_n])

    # —— ②TOP10 详卡（markdown，每卡=链条+四票+步进验证+每跳证据+实验骨架+披露）
    lines = ["# TOP10 假设详卡（交付A1 自动转印）", ""]
    for i, c in enumerate(ranked[:10], 1):
        e16, pmids = parse_step1_support(c.get("step1_support", ""))
        lines.append(f"## {i}. {c['microbe_name']} → {c['metabolite_name']} → "
                     f"{c['hop2_gene_name']} → {c['hop3_pathway']}")
        lines.append(f"- chain_id：`{c['chain_id']}`｜深度 {c['depth']}"
                     f"（terminus={c['hop_terminus']}，pruned={c['pruned']}，"
                     f"penalty={c['penalty']}）")
        lines.append(f"- 四票：票①外部队列佐证={c['vote1_ext_cohort']}｜票②KG 链深="
                     f"{c['vote2_kg_depth']}｜票③gutMGene 交叉={c['vote3_gutmgene_cross']}"
                     f"｜票④可测性={c['vote4_measurability']} → "
                     f"**总票 {c['votes_total']}**（Borda {c['borda_score']}）")
        lines.append(f"- KG 步进验证率：{c['kg_coi_verified_steps']}/"
                     f"{c['kg_coi_total_steps']} = {c['kg_coi_rate']}"
                     f"（step1/2/3 = {c['step1_verified']}/{c['step2_verified']}/"
                     f"{c['step3_verified']}）")
        lines.append("- 每跳证据：")
        lines.append(f"  - hop1 菌→代谢物：`{c['step1_support']}`")
        for a in hop1_idx.get(e16, [])[:3]:  # 最多印 3 条原句，防长卡
            lines.append(f"    - PMID {a['pmid']}：{a['sentence']}")
        if not hop1_idx.get(e16):
            lines.append("    - （断言表无该边行——见 step1_support 溯源串）")
        lines.append(f"  - hop2 代谢物→基因：`{c['step2_support']}`（gutMGene 溯源）")
        lines.append(f"  - hop3 基因→通路：`{c['step3_support']}`（V01 KEGG 快照溯源）")
        lines.append("- 实验骨架（纯机器字段转印，检测方法由实验室自定）：")
        lines.append(f"  - 菌株：{c['microbe_name']}（{c['microbe_id']}，"
                     f"strain_available={c['strain_available']}）")
        lines.append(f"  - 代谢物：{c['metabolite_name']}（{c['metabolite_id']}）")
        lines.append(f"  - 端点基因：{c['hop2_gene_name']}（{c['hop2_gene']}，"
                     f"endpoint_meas_ko={c['endpoint_meas_ko']}）")
        lines.append(f"  - 通路锚：{c['hop3_pathway']}")
        lines.append(f"- 披露：kg_version={c['kg_version']}｜证据粒度="
                     f"{c['evidence_granularity']}｜k5_gate_status="
                     f"{c['k5_gate_status']}")
        lines.append(f"- ⚠️ 票①警示：{c['confound_warning']}")
        lines.append("")
    (out / "top10_detail_cards.md").write_text("\n".join(lines), encoding="utf-8")

    # —— ③交付说明一页（防误读两条+文件清单+披露）
    n_all = len(cards)
    readme = f"""# 假设包交付说明（交付A1）——{datetime.now().strftime('%Y-%m-%d')}

给实验室的一页话：这包里是什么、怎么用、哪几处不要误读。

## 文件清单
| 文件 | 内容 |
|---|---|
| top10_detail_cards.md | 前 10 名假设详卡：机制链+四票+每跳证据原文+实验骨架 |
| top{args.top_n}_brief_cards.tsv | 前 {args.top_n} 名简卡（首轮反馈建议载体） |
| hypothesis_cards.tsv / top_hypotheses.tsv | 全量卡与 TOP 榜**不在本包**——源目录路径见 delivery_manifest.json（由移交方随附） |
| delivery_manifest.json | 本交付包指纹（来源文件 sha256 全录） |

## 两处防误读（监工 D 审裁定原文要点）
1. **k5_gate_status=False 不是卡片缺陷**：这是主图谱质量门禁里"disgenet 冲突核对 0/28"一项未过线的如实披露——它约束的是**图谱发布层**，不影响本包卡片的四票计算与证据溯源。
2. **"7 票"≠7 项独立验证**：总票=四票之和（TOP10 全部为 1+3+1+2：票①佐证+票②链深 3+票③交叉+票④可测性 2 的组合），且 **7 票头名全部包含票①**——票①是外部队列（21612，n=102）的**描述性旁证**，带 35% 用药+零个体协变量混杂（每卡 confound_warning 列印警示句）。空证对照臂已验证：掏空 102 条列表后 7 票全消。

## 证据粒度
全部边的证据粒度=**名称级**（文献句级留档在主图谱断言表，可逐条回溯 PMID+原句）。

## 怎么读详卡
每张卡=一条"菌 → 代谢物 → 基因 → 通路"机制链：四票是**机械打分**（无 AI 裁量），步进验证率是逐跳查图谱"这步有边撑着吗"的比例；实验骨架只列菌/代谢物/端点基因/通路的**标准编号与可测性标记**——具体检测方法请实验室按自有管线自定。

## 版本指纹
子图 version={tops[0].get('kg_version', '见 manifest')}；全部来源文件 sha256 见 delivery_manifest.json。
"""
    (out / "DELIVERY_PACK_README.md").write_text(readme, encoding="utf-8")

    # —— ④交付 manifest（数字机器输出制：来源 sha 全录）
    manifest = {
        "batch": "delivery_A1_htn",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "subgraph_dir": str(sg),
        "n_cards_total": n_all,
        "top_n_brief": args.top_n,
        "top10_reproduced": True,
        "source_files_sha256": {
            f.name: sha256_file(sg / f.name)
            for f in [sg / "hypothesis_cards.tsv", sg / "top_hypotheses.tsv",
                      sg / "chains.tsv", sg / "chain_annotations.tsv",
                      sg / "relation_assertions.tsv"]},
        "outputs_sha256": {
            p.name: sha256_file(p) for p in
            [brief_path, out / "top10_detail_cards.md",
             out / "DELIVERY_PACK_README.md"]},
    }
    (out / "delivery_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[delivery_pack] 交付包={out}")
    print(f"  全量 {n_all} 卡 → TOP10 详卡 + 前 {args.top_n} 简卡 + 说明一页 + manifest")
    print(f"  top10 复现校验：通过（与 top_hypotheses.tsv 同序）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
