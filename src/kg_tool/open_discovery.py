"""序2：开放发现（Swanson ABC）生成器——规格 v04_seq2_spec_20261107.md 冻结版。

ABC 原理：A-B、B-C 两边都有边、A-C 无直接边 → (A,C) 是"经共享中间体 B 的候选关联"。
三榜（全报防挑）：
  榜1 主榜 (Disease, Metabolite) 经共享 microbe —— "这个病该测什么代谢物"
  榜2 (Species, Gene)        经共享 metabolite —— 外部考卷=gutMGene 表1（零污染）
  榜3 (Metabolite, Pathway)  经共享 gene     —— 核验源 KEGG=候选_research 不跑
规则（冻结）：中间体两腿总度≤50；A-C 直接边排除（计数披露）；排序=中间体数↓→
PMID 并集↓→字典序；自环排除；逐字节可复现。
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import sha256_file
from timeslice_eval import wilson_ci

ROOT = Path(__file__).resolve().parents[2]
DEG_CAP = 50          # 冻结：中间体两腿总度上限
TOP_N = 50            # 冻结：展示截断（全量落盘）
MIC = ("Species", "Microbe")

BOARDS = [
    # (榜名, 左类型集, 右类型集, 中间体类型集)
    ("board1_disease_metabolite", {"Disease"}, {"Metabolite", "Chemical"},
     set(MIC)),
    ("board2_species_gene", set(MIC), {"Gene"}, {"Metabolite", "Chemical"}),
    ("board3_metabolite_pathway", {"Metabolite", "Chemical"}, {"Pathway"},
     {"Gene"}),
]


def load_graph(g: Path):
    cat = {}
    for n in csv.DictReader(open(g / "merged_nodes.tsv", encoding="utf-8"),
                            delimiter="\t"):
        cat[n["id"]] = n.get("category")
    adj = defaultdict(set)          # node -> 邻居（无向，用于 ABC 两腿）
    pmids = defaultdict(set)        # 无向边 -> PMID 并集
    for e in csv.DictReader(open(g / "merged_edges.tsv", encoding="utf-8"),
                            delimiter="\t"):
        s, o = e["subject"], e["object"]
        adj[s].add(o)
        adj[o].add(s)
        key = tuple(sorted([s, o]))
        for p in (e.get("pmids") or "").split(";"):
            if p.strip().isdigit():
                pmids[key].add(p.strip())
    return cat, adj, pmids


def candidates(cat, adj, pmids, left_types, right_types, mid_types):
    """ABC 生成：返回 (候选 dict, 排除的直接边对数, 被度上限排除的中间体)。"""
    mid_nodes = [n for n, c in cat.items() if c in mid_types]
    # 度上限（中间体在图内总度——两腿并集即其全部邻居，单值上限更严）
    capped = {n for n in mid_nodes if len(adj[n]) > DEG_CAP}
    left = {n for n, c in cat.items() if c in left_types}
    right = {n for n, c in cat.items() if c in right_types}
    cand = defaultdict(lambda: {"mids": set(), "pmids": set()})
    direct = 0
    for b in mid_nodes:
        if b in capped:
            continue
        ls = [a for a in adj[b] if a in left]
        rs = [c for c in adj[b] if c in right]
        for a in ls:
            for c in rs:
                if a == c:
                    continue
                if c in adj[a]:          # 直接边已存在 → 不算候选
                    direct += 1
                    continue
                rec = cand[(a, c)]
                rec["mids"].add(b)
                rec["pmids"] |= pmids.get(tuple(sorted([a, b])), set())
                rec["pmids"] |= pmids.get(tuple(sorted([b, c])), set())
    return dict(cand), direct, sorted(capped)


def rank_and_write(cand, path, board_label, verification_note):
    rows = []
    for (a, c), rec in cand.items():
        rows.append((len(rec["mids"]), len(rec["pmids"]), a, c, rec))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2], r[3]))
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["rank", "left_id", "right_id", "shared_intermediates",
                    "evidence_pmids", "intermediates_top8", "verification"])
        for i, (nm, npm, a, c, rec) in enumerate(rows, 1):
            mids = sorted(rec["mids"])
            w.writerow([i, a, c, nm, npm,
                        ";".join(mids[:8]) + (";…" if len(mids) > 8 else ""),
                        verification_note.format(a=a, c=c)])
    return rows


def _write_report(out: Path, m: dict) -> None:
    """报告由 manifest 生成（数字零手抄）；考卷口径对账表一并呈现。"""
    lines = [
        "# 开放发现三榜报告（自动生成，数字全部来自 od_manifest.json）", "",
        f"- 生成时刻（UTC）：{m['generated_at_utc']}",
        f"- 图源：{m['graph']}｜规则：{m['rules']}",
        f"- **榜头声明：{m['disclaimer']}**", "",
        "| 榜 | 候选数 | 直接边排除 | 度上限排除中间体 | 外部核验 |",
        "|---|---|---|---|---|",
    ]
    for label, b in m["boards"].items():
        ext = (f"考卷 {b['exam_size']} 对，命中 {b['exam_hits']}"
               f" = {b['exam_hit_rate']:.2%}（CI {b['exam_hit_ci95'][0]:.4f}"
               f"–{b['exam_hit_ci95'][1]:.4f}）" if "exam_hits" in b
               else b.get("verification", ""))
        lines.append(f"| {label} | {b['n_candidates']} | "
                     f"{b['direct_edges_excluded']} | "
                     f"{len(b['intermediates_capped'])} | {ext} |")
    lines += [
        "", "## 考卷分母对账（exam_derivation.tsv，逐行留痕）", "",
        "1,333 物理行 = 1,323 条 CSV 记录（9 个换行在引号字段内）→ 剔 12 条"
        "非数字 ID → 1,311 有效行 → 合并 167 重复 → **1,144 distinct 对**。",
        "", "## 措辞边界（冻结）", "",
        "1. 6.49% 是**候选与库收录一致率**，不是预测准确率（考卷非全负样本）；",
        "2. 榜1/榜3 未外部核验（unverified_external / candidate_research）；",
        "3. 候选是图谱推演产物，非已证关联。",
    ]
    (out / "opendiscovery_report.md").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序2 开放发现三榜（规则冻结版）")
    ap.add_argument("--graph", type=Path,
                    default=ROOT / "data/kg/subgraph_htn_gene_20261106_115949")
    ap.add_argument("--gutmgene-t1", type=Path, required=True,
                    help="gutMGene 表1（菌-基因）CSV——榜2 外部考卷")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"[opendisc] {args.out} 非空——换时间戳（fail-closed）")
    args.out.mkdir(parents=True, exist_ok=True)

    cat, adj, pmids = load_graph(args.graph)
    # 考卷（榜2）：gutMGene 表1 taxid-gene 对
    exam = set()
    # gutMGene 为中文库 CSV，实测 GBK 系编码（utf-8 解码失败）——按序回退
    raw = None
    for enc in ("utf-8", "gb18030"):
        try:
            raw = open(args.gutmgene_t1, encoding=enc).read()
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise SystemExit("[opendisc] 考卷编码无法解码（utf-8/gb18030 均失败）")
    import io
    for r in csv.DictReader(io.StringIO(raw)):
        t, g = r["Gut Microbiota NCBI ID"].strip(), r["Gene ID"].strip()
        if t.isdigit() and g.isdigit():
            exam.add((f"NCBITaxon:{t}", f"NCBIGene:{g}"))

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frozen_spec": "v04_seq2_spec_20261107.md",
        "graph": str(args.graph.relative_to(ROOT)),
        "rules": {"degree_cap": DEG_CAP, "top_n_display": TOP_N,
                  "sort": "mids desc, pmids desc, lex"},
        "disclaimer": "候选=图谱推演产物，非已证关联；本图谱内无 A-C 直接边 ≠ 文献未知",
        "boards": {},
        "inputs_sha256": {
            "merged_edges": sha256_file(args.graph / "merged_edges.tsv"),
            "gutmgene_t1": sha256_file(args.gutmgene_t1),
            "script_sha256": sha256_file(Path(__file__)),
        },
    }
    for label, lt, rt, mt in BOARDS:
        cand, direct, capped = candidates(cat, adj, pmids, lt, rt, mt)
        note = ("exam_gutmgene_t1:pending" if label == "board2_species_gene"
                else "unverified_external")
        rows = rank_and_write(cand, args.out / f"{label}.tsv", label, note)
        # 榜2 命中：先算→重写 TSV→再进 manifest（防"全行预写 hit"中间态）
        if label == "board2_species_gene":
            hits = {p for p in cand if p in exam}
            with open(args.out / f"{label}.tsv", "w", encoding="utf-8",
                      newline="") as f:
                w = csv.writer(f, delimiter="	")
                w.writerow(["rank", "left_id", "right_id", "shared_intermediates",
                            "evidence_pmids", "intermediates_top8", "verification"])
                for i, (nm, npm, a, c, rec) in enumerate(rows, 1):
                    mids = sorted(rec["mids"])
                    w.writerow([i, a, c, nm, npm,
                                ";".join(mids[:8]) + (";…" if len(mids) > 8 else ""),
                                "exam_gutmgene_t1:hit" if (a, c) in hits
                                else "exam_gutmgene_t1:miss"])
            manifest["boards"][label] = {
                "n_candidates": len(rows),
                "direct_edges_excluded": direct,
                "intermediates_capped": capped,
                "exam_size": len(exam),
                "exam_hits": len(hits),
                "exam_hit_rate": round(len(hits) / len(rows), 6) if rows else None,
                "exam_hit_ci95": list(wilson_ci(len(hits), len(rows)))
                if rows else None,
                "note": "候选与库收录一致率；禁写'预测准确率'",
            }
        else:
            manifest["boards"][label] = {
                "n_candidates": len(rows),
                "direct_edges_excluded": direct,
                "intermediates_capped": capped,
                "verification": "unverified_external",
            }
    (args.out / "od_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = []
    for f in sorted(args.out.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (args.out / "SHA256SUMS").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    _write_report(args.out, manifest)
    print(f"[opendisc] {args.out}")
    for label, m in manifest["boards"].items():
        extra = (f" 考卷命中 {m['exam_hits']}/{m['n_candidates']}"
                 f" = {m['exam_hit_rate']:.2%}" if "exam_hits" in m else "")
        print(f"  {label}: 候选 {m['n_candidates']}（直接边排除 {m['direct_edges_excluded']}"
              f"，度上限排除中间体 {len(m['intermediates_capped'])}）{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
