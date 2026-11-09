#!/usr/bin/env python3
"""C2 主子图三桶对账（H-B 独立证据交叉）——gutMGene DOID 过滤边集 × 子图边集。

规格=data/logs/interact_deepchain_spec_20261101.md §一（逐字对照；改规格须
同步改代码与测试）。

三桶语义（照 A2 三桶先例的 confirm/extend/conflict 范式）：
- confirm：gutMGene 与子图互相印证同一实体对关联（独立证据互证）；
- extend：gutMGene 支持（且可锚定子图至少一端）而子图缺=候选新知；
- conflict：两侧都有但方向相反=独立证据打架（警示）；
- unmatched：两侧实体都锚定不上子图——披露不入桶（防把子图外实体对
  硬灌进"新知"）。

为什么逐行判定而不是集合运算：对账的最小可审计单元=gutMGene 的一行
（自带 PMID/Index 可溯），集合差丢掉行级溯源——台账必须逐行可复算。

红线：子图/主图零改动——本工具只写子图目录内两个新文件，既有文件
（含 SHA256SUMS 冻结清单）一律不碰；完整性校验见 manifest.integrity。
"""
import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subgraph import GUTMGENE_TABLES, read_tsv  # noqa: E402  复用 B 阶段口径

# 规格锁定常量（spec §一 逐字；改这里=改规格）
PRODUCTION_PREDICATES = {"produces", "biotransforms"}   # 生产向（V01 冻结口径）
CONSUMPTION_PREDICATES = {"consumes"}                    # 消耗向（与生产向相反）

# 子图 B 阶段既有文件——完整性校验对象（只读；产物只新增不触碰）
SUBGRAPH_EXISTING = ["merged_edges.tsv", "merged_nodes.tsv",
                     "relation_assertions.tsv", "conflicts.tsv",
                     "corrections.tsv", "unclassified_edges.tsv",
                     "manifest.json", "SHA256SUMS", "subgraph_report.md"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_node_index(nodes: list[dict]) -> dict[str, str]:
    """名字→节点 id 索引（与 B 阶段 gutmgene_cross 同口径：name+aliases
    casefold，先到先占位——同名的按节点表顺序取首个，确定性可复算）。"""
    idx: dict[str, str] = {}
    for n in nodes:
        idx.setdefault(n["name"].strip().casefold(), n["id"])
        for al in (n.get("aliases") or "").split("|"):
            if al.strip():
                idx.setdefault(al.strip().casefold(), n["id"])
    return idx


def build_pair_edges(edges: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """无向节点对→子图连边列表（对账只问"两节点间有无边、什么方向"，
    与边自身 subject/object 方向无关——按 sorted 对做键）。"""
    pair: dict[tuple[str, str], list[dict]] = {}
    for e in edges:
        key = tuple(sorted((e["subject"], e["object"])))
        pair.setdefault(key, []).append(e)
    return pair


def is_opposed(edge: dict) -> bool:
    """反向证据边（spec §一 逐字）：消耗向谓词或 negative 极性。"""
    return edge["predicate"] in CONSUMPTION_PREDICATES \
        or edge.get("polarity") == "negative"


def classify_row(row: dict, node_index: dict[str, str],
                 pair_edges: dict[tuple[str, str], list[dict]]) -> dict:
    """单 gutMGene 行的桶判定（判定序 unmatched→conflict→confirm→extend，
    首中即停——spec §一逐字实现；输出行带方向裁决与子图边引用）。"""
    a = row["entity_a"].strip().casefold()
    b = row["entity_b"].strip().casefold()
    na = node_index.get(a, "")
    nb = node_index.get(b, "")
    out = {**row, "matched_node_a": na, "matched_node_b": nb,
           "subgraph_edge_ids": "", "subgraph_predicates": "",
           "direction_verdict": "", "note": ""}
    # 1. unmatched：两侧都锚定不上——披露不入桶
    if not na and not nb:
        out["bucket"] = "unmatched"
        out["note"] = "两侧实体均未命中子图节点"
        return out
    # 2/3 需要两侧都命中且子图有连边
    if na and nb:
        hit = pair_edges.get(tuple(sorted((na, nb))), [])
        if hit:
            preds = sorted({e["predicate"] for e in hit})
            eids = ";".join(sorted(e["edge_id"] for e in hit))
            out["subgraph_edge_ids"] = eids
            out["subgraph_predicates"] = ";".join(preds)
            n_opp = sum(1 for e in hit if is_opposed(e))
            if n_opp:
                # conflict：任一反向即入警示桶（fail-closed 取向）
                out["bucket"] = "conflict"
                out["direction_verdict"] = ("all_opposed" if n_opp == len(hit)
                                            else "mixed_opposed")
                out["note"] = f"反向证据边 {n_opp}/{len(hit)}"
                return out
            out["bucket"] = "confirm"
            out["direction_verdict"] = (
                "production_agree" if set(preds) & PRODUCTION_PREDICATES
                else "non_directional")
            return out
    # 4. extend：至少一侧命中而子图无该对连边（含单侧命中）=候选新知
    out["bucket"] = "extend"
    out["note"] = ("子图无该对连边" if na and nb else
                   "单侧锚定（另一侧实体不在子图）")
    return out


def load_gutmgene_rows(gutmgene_dir: Path, doid: str) -> list[dict]:
    """DOID 过滤边集（spec §一：token 级精确；HTN 首实例 21 行机器数）。"""
    rows = []
    for fname, (col_a, col_b) in GUTMGENE_TABLES.items():
        fpath = gutmgene_dir / fname
        if not fpath.exists():
            continue
        with open(fpath, encoding="gb18030", newline="") as f:
            for r in csv.DictReader(f):
                tokens = {t.strip() for t in (r.get("DOID") or "").split(";")}
                if doid not in tokens:
                    continue
                rows.append({
                    "table": fname, "row_index": r.get("Index", ""),
                    "pmid": r.get("PMID", ""),
                    "entity_a": (r.get(col_a) or "").strip(),
                    "entity_b": (r.get(col_b) or "").strip(),
                    "doid": r.get("DOID", ""),
                    "associative_mode": r.get("Associative mode", ""),
                })
    rows.sort(key=lambda r: (r["table"], r["row_index"],
                             r["entity_a"], r["entity_b"]))
    return rows


def verify_subgraph_integrity(sg_dir: Path) -> dict:
    """子图完整性校验（spec §三.1）：B 阶段既有文件 sha 与 SHA256SUMS
    冻结清单逐一相等——本工具"只加新文件不改旧件"的机器证。"""
    sums_path = sg_dir / "SHA256SUMS"
    frozen: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, _, name = line.partition("  ")
        frozen[name.strip()] = sha
    checked, mismatches = 0, []
    for name, sha in sorted(frozen.items()):
        p = sg_dir / name
        if not p.exists():
            continue
        checked += 1
        if sha256_file(p) != sha:
            mismatches.append(name)
    return {"files_checked": checked, "mismatches": mismatches,
            "machine_proof": not mismatches,
            "note": "既有文件 sha 与 SHA256SUMS 全等 ⇔ 本工具未改任何旧件"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C2 三桶对账（gutMGene×子图）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--gutmgene-dir", type=Path, default=None,
                    help="gutMGene 目录（缺省=注册表 gutmgene 本机覆盖件）")
    # W2.1 疾病档案：doid 可由 configs/diseases/<key>.json 提供（显式传参优先）
    ap.add_argument("--disease", default=None,
                    help="疾病档案 key（configs/diseases/）")
    ap.add_argument("--doid", default=None, help="gutMGene DOID")
    args = ap.parse_args(argv)
    import disease_profile  # noqa: E402  同目录（上方已挂 sys.path）
    # 原默认=DOID:10763（HTN），一字不改挪入登记表——golden 等价前提
    args = disease_profile.resolve(
        args, {"doid": "DOID:10763"})
    import data_source_profile  # noqa: E402  W2.2 数据腿注册表
    args = data_source_profile.resolve(
        args, {"gutmgene_dir": ("gutmgene", False)},
        {"gutmgene_dir": "<GUTMGENE_LOCAL>"})
    sg_dir: Path = args.subgraph_dir

    # —— 只读子图 + 完整性前证（运行前旧件即与冻结清单一致）
    integrity_before = verify_subgraph_integrity(sg_dir)
    nodes = read_tsv(sg_dir / "merged_nodes.tsv")
    edges = read_tsv(sg_dir / "merged_edges.tsv")
    node_index = build_node_index(nodes)
    pair_edges = build_pair_edges(edges)

    # —— DOID 过滤边集 + 逐行桶判定
    gm_rows = load_gutmgene_rows(args.gutmgene_dir, args.doid)
    out_rows = [classify_row(r, node_index, pair_edges) for r in gm_rows]

    # —— 落盘（新文件；排序键固定→同输入同输出）
    cols = ["bucket", "table", "row_index", "pmid", "entity_a", "entity_b",
            "doid", "associative_mode", "matched_node_a", "matched_node_b",
            "subgraph_edge_ids", "subgraph_predicates", "direction_verdict",
            "note"]
    out_rows.sort(key=lambda r: (r["table"], r["row_index"],
                                 r["entity_a"], r["entity_b"]))
    with open(sg_dir / "interaction_report.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    # —— 计数入 manifest（数字机器输出制：全部程序化转写）
    bucket_counts: dict[str, int] = {}
    table_bucket: dict[str, dict[str, int]] = {}
    verdict_counts: dict[str, int] = {}
    for r in out_rows:
        bucket_counts[r["bucket"]] = bucket_counts.get(r["bucket"], 0) + 1
        table_bucket.setdefault(r["table"], {})
        table_bucket[r["table"]][r["bucket"]] = \
            table_bucket[r["table"]].get(r["bucket"], 0) + 1
        if r["direction_verdict"]:
            verdict_counts[r["direction_verdict"]] = \
                verdict_counts.get(r["direction_verdict"], 0) + 1
    sub_manifest = json.loads((sg_dir / "manifest.json").read_text(
        encoding="utf-8"))
    manifest = {
        "module": "src/06_tool/interact.py",
        "spec": "data/logs/interact_deepchain_spec_20261101.md#一",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "subgraph_dir": str(sg_dir),
            "subgraph_version": sub_manifest.get("subgraph_version"),
            "doid": args.doid,
            "gutmgene_dir": str(args.gutmgene_dir),
            "production_predicates": sorted(PRODUCTION_PREDICATES),
            "consumption_predicates": sorted(CONSUMPTION_PREDICATES),
        },
        "inputs_sha256": {
            fname: sha256_file(args.gutmgene_dir / fname)
            for fname in GUTMGENE_TABLES
            if (args.gutmgene_dir / fname).exists()},
        "counts": {
            "gutmgene_doid_rows": len(gm_rows),
            "buckets": bucket_counts,
            "by_table": table_bucket,
            "direction_verdicts": verdict_counts,
        },
        "integrity_before": integrity_before,
        "determinism": {
            "sort_keys": "table,row_index,entity_a,entity_b",
            "no_run_time_in_output_rows": True,
        },
    }
    (sg_dir / "interaction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # —— 运行后完整性复核（新文件不在冻结清单内→不影响校验；旧件再证一次）
    integrity_after = verify_subgraph_integrity(sg_dir)
    manifest["integrity_after"] = integrity_after
    (sg_dir / "interaction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[interact] 对账行 {len(gm_rows)} → {bucket_counts}")
    print(f"  direction_verdicts={verdict_counts}")
    print(f"  子图完整性：{'通过（旧件 sha 与冻结清单全等）' if integrity_after['machine_proof'] else '失败'}")
    print(f"  台账={sg_dir / 'interaction_report.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
