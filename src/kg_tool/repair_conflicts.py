"""序4：矛盾边机械修复（规则 R 已在 v04_seq0_seq4_spec_20261107.md §二冻结）。

规则 R（冻结，禁逐对看内容后定）：
1. 冲突对 = 同 (taxid, mesh) 同时存在 aggravates 与 alleviates；
2. 按谓词分侧数 **distinct PMID**（独立研究数，一篇文章多边只计一次）；
3. 一侧严格更多 → 胜出；败侧全部边 predicate → affects（中性保留，禁删边）；
4. 平票（含各 1 篇）→ 两侧全部 predicate → affects，对标 unresolved_tie；
5. 台账逐对登记：两侧 PMID 数、胜方、动作。重跑逐字节同输出。

产物（评审三锁②：只增不改）：
  data/kg/k5_full_<ts>/  —— merged_edges 改写版 + 其余文件逐字节复制
                           + conflict_resolution_ledger.tsv（新）
                           + disease_class_annotation.tsv（新，只加注记不删节点）
                           + repair_manifest.json（新）
旧图 k4_full_20261028_155020/ 逐字节不动。
"""
import argparse
import csv
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bridge_probe import sha256_file

ROOT = Path(__file__).resolve().parents[2]
COPY_VERBATIM = ("merged_nodes.tsv", "relation_assertions.tsv", "conflicts.tsv",
                 "corrections.tsv", "unclassified_edges.tsv", "manifest.json")
POLAR = ("aggravates", "alleviates")


def disease_class_annotation(nodes_tsv: Path, tree_numbers: Path) -> list:
    """Disease 节点 → 分类注记（只对树号快照内可判；快照外如实标 outside）。"""
    tn = json.loads(tree_numbers.read_text(encoding="utf-8"))
    rows = []
    for n in csv.DictReader(open(nodes_tsv, encoding="utf-8"), delimiter="\t"):
        if n.get("category") != "Disease":
            continue
        acc = n["id"].split(":", 1)[1] if ":" in n["id"] else n["id"]
        acc = acc.upper() if re.match(r"^[DC]\d{6,}$", acc, re.I) else None
        if acc is None:
            cls = "malformed_id"
        elif acc not in tn:
            # 不在 760 UID 快照内的：可能是快照外我方节点，也可能是库侧独有——
            # 按节点表逐个判即可，无法归类的一律 outside_snapshot，不猜
            cls = "outside_snapshot"
        elif any(t.startswith("C23") for t in tn[acc]):
            cls = "signs_symptoms_C23"
        else:
            cls = "disease_other"
        rows.append([n["id"], cls])
    return rows


def repair_edges(edges_tsv: Path):
    """按规则 R 处理 → (新行列表, 台账行列表)。不删边、不改胜侧。"""
    rows = list(csv.DictReader(open(edges_tsv, encoding="utf-8"),
                               delimiter="\t"))
    nodes = {}  # 运行时重建 pair→edges 索引（依赖 category，见下）
    # 先拿 category 集合
    kg_dir = edges_tsv.parent
    cat = {}
    for n in csv.DictReader(open(kg_dir / "merged_nodes.tsv", encoding="utf-8"),
                            delimiter="\t"):
        cat[n["id"]] = n.get("category")
    UID = re.compile(r"^[DC]\d{6,}$", re.I)
    pair_edges = defaultdict(list)
    for i, r in enumerate(rows):
        s, o = r["subject"], r["object"]
        if not (((cat.get(s) in ("Species", "Microbe")) and cat.get(o) == "Disease")
                or ((cat.get(o) in ("Species", "Microbe")) and cat.get(s) == "Disease")):
            continue
        mi = s if cat.get(s) in ("Species", "Microbe") else o
        di = o if cat.get(s) in ("Species", "Microbe") else s
        acc = di.split(":", 1)[1] if ":" in di else di
        if not UID.match(acc):
            continue
        pair_edges[(mi.split(":", 1)[1], acc.upper())].append(i)

    ledger = []
    conflicts = []
    for k, idxs in pair_edges.items():
        preds = {rows[i]["predicate"] for i in idxs}
        if not {"aggravates", "alleviates"} <= preds:
            continue
        conflicts.append(k)
        side_pmids = {}
        for p in POLAR:
            s = set()
            for i in idxs:
                if rows[i]["predicate"] != p:
                    continue
                s |= {x.strip() for x in (rows[i].get("pmids") or "").split(";")
                      if x.strip().isdigit()}
            side_pmids[p] = s
        na, nl = len(side_pmids["aggravates"]), len(side_pmids["alleviates"])
        if na > nl:
            winner, action = "aggravates", "loser_relabel"
        elif nl > na:
            winner, action = "alleviates", "loser_relabel"
        else:
            winner, action = None, "tie_both_neutral"
        changed = 0
        for i in idxs:
            if rows[i]["predicate"] not in POLAR:
                continue  # affects 边不涉争端，零触碰
            if action == "tie_both_neutral" or rows[i]["predicate"] != winner:
                rows[i]["predicate"] = "affects"
                changed += 1
        ledger.append({
            "microbe_taxid": k[0], "disease_mesh": k[1],
            "pmid_aggravates": na, "pmid_alleviates": nl,
            "winner": winner or "unresolved_tie", "action": action,
            "edges_relabeled": changed,
        })
    return rows, sorted(ledger, key=lambda d: (d["microbe_taxid"],
                                               d["disease_mesh"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序4 矛盾边机械修复（规则 R 冻结版）")
    ap.add_argument("--src", type=Path,
                    default=ROOT / "data/kg/k4_full_20261028_155020")
    ap.add_argument("--tree-numbers", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"[repair] {args.out} 非空——换时间戳（fail-closed）")
    args.out.mkdir(parents=True, exist_ok=True)

    new_rows, ledger = repair_edges(args.src / "merged_edges.tsv")
    # 断言：边数不变（只改标签不增删）
    with open(args.src / "merged_edges.tsv", encoding="utf-8") as f:
        n_old = sum(1 for _ in f) - 1
    assert len(new_rows) == n_old, f"边数变了 {n_old}->{len(new_rows)}"
    assert len(ledger) > 0

    # 写新图：merged_edges 改写，其余逐字节复制
    cols = list(csv.DictReader(open(args.src / "merged_edges.tsv",
                                    encoding="utf-8"),
                               delimiter="\t").fieldnames)
    with open(args.out / "merged_edges.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(new_rows)
    for name in COPY_VERBATIM:
        src = args.src / name
        if src.exists():
            shutil.copy2(src, args.out / name)

    # 两个新 sidecar
    with open(args.out / "conflict_resolution_ledger.tsv", "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["microbe_taxid", "disease_mesh",
                                          "pmid_aggravates", "pmid_alleviates",
                                          "winner", "action", "edges_relabeled"],
                           delimiter="\t")
        w.writeheader()
        w.writerows(ledger)
    ann = disease_class_annotation(args.src / "merged_nodes.tsv",
                                   args.tree_numbers)
    with open(args.out / "disease_class_annotation.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["node_id", "disease_class"])
        w.writerows(ann)

    # 修复 manifest（机器真源）
    relabel_total = sum(r["edges_relabeled"] for r in ledger)
    tie = sum(1 for r in ledger if r["action"] == "tie_both_neutral")
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule": "R (v04_seq0_seq4_spec_20261107.md §二 冻结)",
        "src_graph": str(args.src.relative_to(ROOT)),
        "conflict_pairs": len(ledger),
        "edges_relabeled_total": relabel_total,
        "tie_pairs": tie,
        "edges_total_before": n_old,
        "edges_total_after": len(new_rows),
        "disease_class_counts": {c: sum(1 for _, x in ann if x == c)
                                 for c in {x for _, x in ann}},
        "inputs_sha256": {
            "merged_edges_src": sha256_file(args.src / "merged_edges.tsv"),
            "tree_numbers": sha256_file(args.tree_numbers),
            "script_sha256": sha256_file(Path(__file__)),
        },
        "discipline": [
            "禁删边；败侧仅 predicate→affects；polarity 列零改动",
            "旧图逐字节不动；本目录为唯一修复版",
            "台账重跑逐字节可复现",
        ],
    }
    (args.out / "repair_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[repair] {args.out}")
    print(f"  冲突对 {len(ledger)}（tie {tie}）→ 改写 {relabel_total} 条边"
          f"（边数 {n_old}→{len(new_rows)} 不变）")
    print(f"  疾病注记：{manifest['disease_class_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
