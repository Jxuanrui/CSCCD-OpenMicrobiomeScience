#!/usr/bin/env python3
"""Phase 2 知识可审计化：provenance 四字段扩列 + 存量回填（幂等）。

背景（2026-09-30）：merged_edges.tsv 的 source_ref 全部为 'unattributed'、
curator 全部为 'automated_pipeline'，不满足消费方（mra prov_standard v0.1
Layer 1）"curated 边 source_ref 须可归因"的验收口径。本脚本用 seed 源文件
+ Source Registry 做确定性回填，让每条边/节点可审计到登记数据源。

回填语义（全部确定性，重跑结果一致）：
  边（merged_edges.tsv，追加 4 列，事实字段零改动）：
    source_id       registry source_key。curated 边按 merge 源优先级回查
                    （seed→bugsigdb→gutmgene→gutmdisorder→kegg，与 merge_qc.py
                    concat+keep='first' 顺序一致，跨源重复边归属先到者）；
                    llm_extracted 边 = glm_extract_v2；回查未命中保留 unattributed
                    并在报告中计数（不猜测归属）。
    retrieved_at    registry last_sync（该源最后一次同步日期）。
    version         registry version（如 2018 / v3 / v2.6）。
    knowledge_layer 五枚举见 knowledge_layer.py；curated→local_kg_curated，
                    llm_extracted→local_kg_llm_extracted。
    （curator 列已存在，保持 automated_pipeline 不动。）
  节点（merged_nodes.tsv，追加 1 列）：
    knowledge_layer id 命中任一 seed *_nodes.tsv → local_kg_curated
                    （LLM 实体闭包补建节点）→ local_kg_llm_extracted。
                    节点跨源共享无单一来源语义，不强标 source_id。

写入须过 write_guard 闸门（P0-6）：execution_id=EX-kg.backfill_provenance-*，
授权键默认 phase-r-remediation（targets 覆盖 data/merged，审计账本留痕）。

用法：
  python3 backfill_provenance.py --verify     # 只读核对报告（不写任何文件）
  python3 backfill_provenance.py              # 备份→闸门→回填→核对
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data/seed"
MERGED = ROOT / "data/merged"
REGISTRY = ROOT / "data/registry/source_registry.tsv"
BACKUP_DIR = MERGED / "provenance_backfill_backup"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from knowledge_layer import KG_ALLOWED_LAYERS, check_layers  # noqa: E402
from merge_qc import SOURCE_MAP  # noqa: E402  输入前缀 -> registry source_key

# 回查源文件按 merge_qc.py concat 顺序排列（先到先得 = drop_duplicates keep='first'）
EDGE_SOURCE_FILES = [
    ("seed_edges.tsv", "seed_"),
    ("bugsigdb_edges.tsv", "bugsigdb_"),
    ("gutmgene_edges.tsv", "gutmgene_"),
    ("gutmdisorder_edges.tsv", "gutmdisorder_"),
    ("kegg_pathway_edges.tsv", "kegg_pathway_"),
]
NODE_SOURCE_FILES = [f.replace("edges", "nodes") for f, _ in EDGE_SOURCE_FILES]
LLM_SOURCE_KEY = SOURCE_MAP["llm_relations"]
NEW_EDGE_COLS = ["source_id", "retrieved_at", "version", "knowledge_layer"]


def load_registry() -> pd.DataFrame:
    reg = pd.read_csv(REGISTRY, sep="\t", dtype=str).fillna("")
    if reg.duplicated("source_key").any():
        raise SystemExit("[registry] source_key 重复，拒绝回填")
    return reg.set_index("source_key")


def build_edge_lookup() -> dict[tuple[str, str, str], str]:
    """(subject, predicate, object) -> source_key，按源优先级先到先得。"""
    lookup: dict[tuple[str, str, str], str] = {}
    for fname, prefix in EDGE_SOURCE_FILES:
        path = SEED / fname
        if not path.exists():
            continue
        df = pd.read_csv(path, sep="\t", dtype=str).fillna("")
        source_key = SOURCE_MAP[prefix]
        for key in zip(df["subject"], df["predicate"], df["object"]):
            lookup.setdefault(key, source_key)
    return lookup


def build_node_lookup() -> set[str]:
    seen: set[str] = set()
    for fname in NODE_SOURCE_FILES:
        path = SEED / fname
        if not path.exists():
            continue
        df = pd.read_csv(path, sep="\t", dtype=str).fillna("")
        seen.update(df["id"])
    return seen


def enrich_edges(edges: pd.DataFrame, reg: pd.DataFrame,
                 lookup: dict[tuple[str, str, str], str]) -> pd.DataFrame:
    out = edges.copy()
    src_ids, retrieved, versions, layers = [], [], [], []
    for row in edges.itertuples(index=False):
        if row.source_type == "llm_extracted":
            key = LLM_SOURCE_KEY
        else:
            key = lookup.get((row.subject, row.predicate, row.object), "unattributed")
        src_ids.append(key)
        retrieved.append(reg.at[key, "last_sync"] if key in reg.index else "")
        versions.append(reg.at[key, "version"] if key in reg.index else "")
        layer = ("local_kg_llm_extracted" if row.source_type == "llm_extracted"
                 else "local_kg_curated")
        layers.append(layer)
    out["source_id"] = src_ids
    out["retrieved_at"] = retrieved
    out["version"] = versions
    out["knowledge_layer"] = layers
    return out


def enrich_nodes(nodes: pd.DataFrame, curated_ids: set[str]) -> pd.DataFrame:
    out = nodes.copy()
    out["knowledge_layer"] = [
        "local_kg_curated" if i in curated_ids else "local_kg_llm_extracted"
        for i in nodes["id"]]
    return out


def verify(edges_old: pd.DataFrame, edges_new: pd.DataFrame,
           nodes_old: pd.DataFrame, nodes_new: pd.DataFrame,
           reg: pd.DataFrame) -> int:
    """核对报告；返回违规计数（0 = 全部通过）。"""
    problems: list[str] = []
    # 1) 事实字段零改动：旧列在前后两版完全一致
    for name, old, new in [("edges", edges_old, edges_new), ("nodes", nodes_old, nodes_new)]:
        if len(old) != len(new):
            problems.append(f"{name} 行数变化 {len(old)} -> {len(new)}")
            continue
        for col in old.columns:
            if not (old[col].fillna("") == new[col].fillna("")).all():
                problems.append(f"{name} 事实列 {col!r} 被改动（违反零改动约束）")
    # 2) 枚举合法且不越层
    problems += check_layers(edges_new["knowledge_layer"], context="edges.knowledge_layer")
    problems += check_layers(nodes_new["knowledge_layer"], context="nodes.knowledge_layer")
    # 3) curated 边可归因率（unattributed 残留）
    curated = edges_new[edges_new.source_type != "llm_extracted"]
    unattr = (curated["source_id"] == "unattributed").sum()
    # 4) 回填值与 registry 一致性：source_id 在册 且 retrieved_at/version 匹配
    for row in edges_new.drop_duplicates("source_id").itertuples(index=False):
        if row.source_id == "unattributed":
            continue
        if row.source_id not in reg.index:
            problems.append(f"source_id {row.source_id!r} 不在 registry")
            continue
        if row.retrieved_at != reg.at[row.source_id, "last_sync"]:
            problems.append(f"{row.source_id} retrieved_at 与 registry 不符")
        if row.version != reg.at[row.source_id, "version"]:
            problems.append(f"{row.source_id} version 与 registry 不符")

    print("== 回填核对报告 ==")
    print(f"edges 总数 {len(edges_new)}；来源分布：")
    for k, v in edges_new["source_id"].value_counts().items():
        print(f"  {k}: {v}")
    print(f"nodes 总数 {len(nodes_new)}；层分布：")
    for k, v in nodes_new["knowledge_layer"].value_counts().items():
        print(f"  {k}: {v}")
    print(f"curated 未归因（unattributed）: {unattr}")
    print(f"枚举+一致性检查: {'PASS' if not problems else 'FAIL'}")
    for p in problems:
        print(f"  [问题] {p}")
    if unattr:
        print("  [提示] 未归因边保留 unattributed（源文件与 merge 产物存在差集，需人工核对）")
    return len(problems)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true", help="只读核对，不写文件")
    args = ap.parse_args()

    edges_path, nodes_path = MERGED / "merged_edges.tsv", MERGED / "merged_nodes.tsv"
    edges_old = pd.read_csv(edges_path, sep="\t", dtype=str).fillna("")
    nodes_old = pd.read_csv(nodes_path, sep="\t", dtype=str).fillna("")
    reg = load_registry()

    edges_new = enrich_edges(edges_old, reg, build_edge_lookup())
    nodes_new = enrich_nodes(nodes_old, build_node_lookup())
    fails = verify(edges_old, edges_new, nodes_old, nodes_new, reg)
    if args.verify:
        sys.exit(1 if fails else 0)

    if fails:
        raise SystemExit("[backfill] 核对未通过，拒绝写入（fail-closed）")

    # 备份（首次执行时留底；幂等重跑不覆盖原始备份）
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for src in (edges_path, nodes_path):
        dst = BACKUP_DIR / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            print(f"[backup] {src.name} -> {dst}")

    # write_guard 闸门（P0-6）：data/merged 写入须授权键 + 审计留痕
    sys.path.insert(0, str(ROOT / "src/07_capability"))
    from write_guard import guard_write, new_execution_id  # noqa: E402
    exec_id = os.environ.get("KG_EXECUTION_ID") or new_execution_id("kg.backfill_provenance")
    guard_write("data/merged", exec_id, os.environ.get("KG_WRITE_AUTH", "phase-r-remediation"))
    print(f"[write_guard] merged 写入放行 exec={exec_id}")

    # 新列追加在尾部（旧列顺序与内容保持字节级不变由 verify 兜底）
    for df, path, old_cols in ((edges_new, edges_path, edges_old.columns),
                               (nodes_new, nodes_path, nodes_old.columns)):
        df.to_csv(path, sep="\t", index=False)
        added = [c for c in df.columns if c not in old_cols]
        print(f"[write] {path.name}: 新增列 {added}，共 {len(df)} 行")
    print("[done] provenance 回填完成")


if __name__ == "__main__":
    main()
