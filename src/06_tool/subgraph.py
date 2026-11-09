#!/usr/bin/env python3
"""疾病子图构建器（重排路线图 B 阶段：Tier-0 存量切出 / Tier-1 差集计数 /
Tier-2 增量判定 + THGAgents 因果列）。

规格=data/logs/subgraph_spec_20261101.md（逐字对照；改规格须同步改代码）。

三档制为什么这么分：
- Tier-0 存量切出零 LLM：全语料 K3 已跑完（98,448 对 error=0），库内疾病
  文献的判定结果本就躺在主图断言库里——秒级切出，不花钱；
- Tier-1 差集计数零 LLM：一次 esearch 回答"最新时间点还差多少文献"，
  数字机器输出制——先探查后报预算；
- Tier-2 增量判定才花 LLM：帽 1200（用户预批"≤帽直接跑、超帽单报"），
  增量只进子图不回流主图（主图=指纹冻结的发布资产，两者解耦）。

红线：主图零改动（运行前后五件 sha256 对照=机器证）；零关联分析；
因果列零 LLM（三信号机械推导，词表正则逐字锁定于规格 §四）。
"""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
# 同目录模块导入惯例（与 interact.py:29 同式）——CLI 按路径加载本模块时
# 不会自动把本目录挂上 sys.path，必须显式插入
sys.path.insert(0, str(Path(__file__).resolve().parent))
import disease_profile  # noqa: E402  W2.1 疾病档案（--disease 解析）
import data_source_profile  # noqa: E402  W2.2 数据腿注册表
sys.path.insert(0, str(ROOT / "src" / "01_literature"))

# ============================================================
# 规格锁定常量（subgraph_spec §四 逐字；改这里=改规格）
# ============================================================
ACTION_PREDICATES = {"aggravates", "alleviates", "produces", "biotransforms",
                     "consumes", "promotes_growth", "inhibits_growth"}
CAUSAL_REGEX = re.compile(
    r"induce|inhibit|prevent|attenuat|aggravat|alleviat|promot|"
    r"caus(?:e|ed|ing)|reduc|improv|protect|disturb|impair|generat|"
    r"produc|convert|synthes|inactivat|metaboliz", re.IGNORECASE)
CORRELATION_REGEX = re.compile(
    r"correlat|associat|enrich|deplet|increas|decreas|elevat|lower|higher|"
    r"abundan|prevalen|alter|shift|chang", re.IGNORECASE)

# K1 全领域四词检索式（fetch_literature.py:61-72 冻结原文——Tier-1 的基底）
SEARCH_QUERY = (
    '"Gastrointestinal Microbiome"[MeSH] '
    'OR "gut microbiota"[TIAB] '
    'OR "intestinal microbiome"[TIAB] '
    'OR "gut microbiome"[TIAB]'
)
YEAR_RANGE = "2010:3000[PDAT]"
DIFF_CAP = 1200          # Tier-2 预算帽（用户预批机制）
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
SLEEP_S = 0.4            # NCBI 限速（无 key 3/s，保守 0.4s）

# 主图五件（零改动机器证的对照清单）
MASTER_FIVE = ["merged_edges.tsv", "merged_nodes.tsv",
               "relation_assertions.tsv", "conflicts.tsv", "corrections.tsv"]

# gutMGene 三表（gb18030/DictReader 逻辑记录口径——INHERITANCE 登记约束）
GUTMGENE_TABLES = {
    "Gut Microbe-Host Gene.csv": ("Gut Microbiota", "Gene"),
    "Gut Microbe-Microbial metabolite.csv": ("Gut Microbiota", "Metabolite"),
    "Microbial metabolite-Host Gene.csv": ("Metabolite", "Gene"),
}


# ============================================================
# 基础 IO（全仓同口径：TSV DictReader/Writer、分块 sha256）
# ============================================================
def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def tsv_header(path: Path) -> list[str]:
    """读表头列（与行数据解耦——空表也要有列结构，写子集文件用）。"""
    with open(path, encoding="utf-8", newline="") as f:
        return next(csv.reader(f, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_id(node_id: str) -> str:
    """ID 大小写规范（规格 §一）：主图存在 MESH:/MeSH: 双前缀变体，
    凡按 ID 匹配一律 upper——否则族过滤会漏一半。"""
    return node_id.strip().upper()


# ============================================================
# votes/judge 回连（规格 §四：K3 批次 relations.jsonl → assertion_id 重算）
# ============================================================
def rebuild_assertion_id(run_batch: str, pmid: str, subj: str, pred: str,
                         obj: str, sentence: str, evidence: str) -> str:
    """K4 的 assertion_id 公式（merge_relations 冻结算法）逐字复刻：
    RA-sha256(run_batch|pmid|subject_id|predicate|object_id|sentence|evidence)[:16]"""
    raw = f"{run_batch}|{pmid}|{subj}|{pred}|{obj}|{sentence}|{evidence}"
    return "RA-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_votes_index(k3_batches: list[Path]) -> dict[str, dict]:
    """扫 K3 批次 relations.jsonl，重算 assertion_id 建 votes/judge 索引。

    为什么重算而不是直接带：断言表没有 votes 列（K4 聚合时未保留），唯一
    机械回连键=按同一公式重算——2026-11-01 实测回连覆盖 5063/5063=100%。
    """
    idx: dict[str, dict] = {}
    for bdir in k3_batches:
        batch_id = bdir.name
        rpath = bdir / "relations.jsonl"
        if not rpath.exists():
            continue
        with open(rpath, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                s, o = d.get("subject") or {}, d.get("object") or {}
                aid = rebuild_assertion_id(batch_id, str(d.get("pmid", "")),
                                           str(s.get("id", "")),
                                           str(d.get("predicate", "")),
                                           str(o.get("id", "")),
                                           str(d.get("sentence", "")),
                                           str(d.get("evidence", "")))
                votes = d.get("votes") or []
                judge = d.get("judge") or {}
                # 主导谓词票数 ≥2/3（三票自一致性投票的机械信号）
                dom = max(votes, key=votes.count) if votes else ""
                dom_n = votes.count(dom) if votes else 0
                idx[aid] = {
                    "votes": votes,
                    "dominant_votes": dom_n,
                    "judge_verdict": str(judge.get("verdict", "")),
                }
    return idx


# ============================================================
# 因果列派生（规格 §四，三信号→三档，零 LLM）
# ============================================================
def derive_causality(edge: dict, asserts: list[dict],
                     votes_idx: dict) -> dict:
    """单边三信号计算与分档（规则逐字=spec §四判定序）。

    返回 {causality, causality_basis, causality_diag, n_asserts,
    n_votes_missing}——diag 四信号串可独立复算分档（可审计性）。
    """
    pred = edge["predicate"]
    sig_action = 1 if pred in ACTION_PREDICATES else 0
    sig_vote = 0
    sig_causal_ev = 0
    sig_corr_ev = 0
    n_missing = 0
    for a in asserts:
        v = votes_idx.get(a["assertion_id"])
        if v is None:
            n_missing += 1
        elif v["dominant_votes"] >= 2 or v["judge_verdict"] == "SUPPORTED":
            sig_vote = 1
        text = f"{a.get('evidence', '')} {a.get('sentence', '')}"
        if CAUSAL_REGEX.search(text):
            sig_causal_ev = 1
        if CORRELATION_REGEX.search(text):
            sig_corr_ev = 1
    diag = f"action={sig_action},vote={sig_vote},causal_ev={sig_causal_ev}," \
           f"corr_ev={sig_corr_ev}"
    if sig_action and sig_vote and sig_causal_ev:
        causality, basis = "causal", "rule1:action+vote+causal_ev"
    elif pred == "affects" or sig_corr_ev:
        causality = "correlated"
        basis = "rule2:" + ("predicate_affects" if pred == "affects" else "")
        if sig_corr_ev:
            basis += ("+" if basis != "rule2:" else "") + "corr_ev"
    else:
        causality = "unclassified"
        missing = [n for n, s in (("action", sig_action), ("vote", sig_vote),
                                  ("causal_ev", sig_causal_ev)) if not s]
        basis = "rule3:missing=" + "|".join(missing)
    return {"causality": causality, "causality_basis": basis,
            "causality_diag": diag,
            "n_asserts": len(asserts), "n_votes_missing": n_missing}


# ============================================================
# Tier-0：疾病族 → 疾病文献集 → 种子 → BFS → 诱导子图 → 因果列 → 落盘
# ============================================================
def build_disease_family(nodes: list[dict], mesh_ids: set[str],
                         name_root: str) -> tuple[set[str], list[dict]]:
    """疾病节点族（spec §二机械定义）：Disease 类 AND (ID∈输入集 OR 名含词根)。

    返回 (norm_id 族集, 逐节点披露行)。邻接排除（如 D003668 "pressure"）
    由调用方另行 census 披露——保守排除可查可翻案。
    """
    family_norm, disclose = set(), []
    for n in nodes:
        if n["category"] != "Disease":
            continue
        nid, name = norm_id(n["id"]), n["name"]
        in_family = nid in mesh_ids or (name_root and name_root in name.lower())
        if in_family:
            family_norm.add(nid)
            disclose.append({"id": n["id"], "name": name,
                             "via": "mesh_input" if nid in mesh_ids
                             else "name_root"})
    return family_norm, disclose


def disease_literature_set(library: Path, family_norm: set[str]) -> set[str]:
    """疾病文献集：任一 Disease 标注 id.upper() ∈ 族的 PMID 集。"""
    lit = set()
    with open(library, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            for a in d.get("annotations") or []:
                if a.get("type") == "Disease" and \
                        norm_id(str(a.get("id", ""))) in family_norm:
                    lit.add(str(d["pmid"]))
                    break
    return lit


def bfs_node_set(adj: dict[str, set[str]], seeds: set[str], k: int) -> set[str]:
    """k 层 BFS（无向邻接）。k=0 → 只有种子。诱导子图口径见 spec §二。"""
    nodes, frontier = set(seeds), set(seeds)
    for _ in range(k):
        nxt = set()
        for n in frontier:
            for m in adj.get(n, ()):
                if m not in nodes:
                    nxt.add(m)
        nodes |= nxt
        frontier = nxt
        if not frontier:
            break
    return nodes


def gutmgene_cross(gutmgene_dir: Path, doid: str,
                   node_index: dict[str, str]) -> list[dict]:
    """gutMGene DOID 列交叉（spec §三）：token 级精确命中 + 名字 casefold 匹配。"""
    rows = []
    if not gutmgene_dir or not doid:
        return rows
    for fname, (col_a, col_b) in GUTMGENE_TABLES.items():
        fpath = gutmgene_dir / fname
        if not fpath.exists():
            continue
        with open(fpath, encoding="gb18030", newline="") as f:
            for r in csv.DictReader(f):
                tokens = {t.strip() for t in (r.get("DOID") or "").split(";")}
                if doid not in tokens:
                    continue
                a, b = (r.get(col_a) or "").strip(), (r.get(col_b) or "").strip()
                hits = []
                for label, nm in (("a", a), ("b", b)):
                    nid = node_index.get(nm.casefold())
                    hits.append(nid if nid else "")
                status = ("both" if hits[0] and hits[1]
                          else "a_side" if hits[0] else "b_side" if hits[1]
                          else "none")
                rows.append({
                    "table": fname, "row_index": r.get("Index", ""),
                    "pmid": r.get("PMID", ""), "entity_a": a, "entity_b": b,
                    "col_a": col_a, "col_b": col_b, "doid": r.get("DOID", ""),
                    "associative_mode": r.get("Associative mode", ""),
                    "matched_node_a": hits[0], "matched_node_b": hits[1],
                    "match_status": status,
                })
    rows.sort(key=lambda r: (r["table"], r["row_index"]))
    return rows


def cmd_tier0(args) -> int:
    kg_dir: Path = args.kg_dir
    out_dir: Path = args.out
    t_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    if out_dir is None:
        out_dir = ROOT / "data" / "kg" / f"subgraph_{args.key}_{t_start}"

    # —— 红线机器证（前半）：主图五件 sha 运行前快照
    master_before = {f: sha256_file(kg_dir / f) for f in MASTER_FIVE}

    # —— 读主图（只读）；列结构从表头取（空表也有列）
    edges = read_tsv(kg_dir / "merged_edges.tsv")
    nodes = read_tsv(kg_dir / "merged_nodes.tsv")
    asserts = read_tsv(kg_dir / "relation_assertions.tsv")
    conflicts = read_tsv(kg_dir / "conflicts.tsv")
    corrections = read_tsv(kg_dir / "corrections.tsv")
    ecols_src = tsv_header(kg_dir / "merged_edges.tsv")
    ncols_src = tsv_header(kg_dir / "merged_nodes.tsv")
    acols_src = tsv_header(kg_dir / "relation_assertions.tsv")
    ccols_src = tsv_header(kg_dir / "conflicts.tsv")
    rcols_src = tsv_header(kg_dir / "corrections.tsv")

    # —— 疾病节点族 + 邻接排除披露
    mesh_ids = {norm_id(m) for m in args.mesh.split(",") if m.strip()}
    family, family_disclose = build_disease_family(nodes, mesh_ids,
                                                   args.name_root)
    if not family:
        raise SystemExit(f"[tier0] 疾病节点族为空：mesh={mesh_ids} "
                         f"name_root={args.name_root!r}——检查参数")
    adjacent_excluded = [
        {"id": n["id"], "name": n["name"]}
        for n in nodes if n["category"] == "Disease"
        and norm_id(n["id"]) not in family
        and any(w in n["name"].lower() for w in ("pressure", "blood pressure"))
    ]

    # —— 疾病文献集 + 种子断言
    lit = disease_literature_set(args.library, family)
    seed_edges: dict[str, set[str]] = defaultdict(set)   # edge_id → 过滤原因集
    seed_nodes = set()
    for a in asserts:
        reasons = []
        if a["pmid"] in lit:
            reasons.append("pmid_in_disease_lit")
        if norm_id(a["object_id"]) in family:
            reasons.append("object_in_family")
        if norm_id(a["subject_id"]) in family:
            reasons.append("subject_in_family")
        if reasons:
            seed_edges[a["edge_id"]] |= set(reasons)
            seed_nodes.add(a["subject_id"])
            seed_nodes.add(a["object_id"])

    # —— BFS k-hop + 诱导子图
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e["subject"]].add(e["object"])
        adj[e["object"]].add(e["subject"])
    node_set = bfs_node_set(adj, seed_nodes, args.khop)
    sub_edges = [e for e in edges
                 if e["subject"] in node_set and e["object"] in node_set]
    sub_edge_ids = {e["edge_id"] for e in sub_edges}

    # —— 断言子集（edge_id ∈ 子图边）+ 因果列（votes 回连）
    sub_asserts = [a for a in asserts if a["edge_id"] in sub_edge_ids]
    votes_idx = load_votes_index([Path(p) for p in args.k3_batch])
    asserts_by_edge: dict[str, list[dict]] = defaultdict(list)
    for a in sub_asserts:
        asserts_by_edge[a["edge_id"]].append(a)
    causality_counts = {"causal": 0, "correlated": 0, "unclassified": 0}
    votes_missing_total = 0
    out_edges, unclassified_rows = [], []
    for e in sub_edges:
        alist = asserts_by_edge.get(e["edge_id"], [])
        c = derive_causality(e, alist, votes_idx)
        votes_missing_total += c["n_votes_missing"]
        causality_counts[c["causality"]] += 1
        prov = ("seed_assertion:" + "+".join(sorted(seed_edges[e["edge_id"]]))
                if e["edge_id"] in seed_edges else "khop_expand")
        row = {**e, "provenance": prov, "causality": c["causality"],
               "causality_basis": c["causality_basis"],
               "causality_diag": c["causality_diag"]}
        out_edges.append(row)
        if c["causality"] == "unclassified":
            unclassified_rows.append(row)
    # 断言表 seed_reason 列：种子边下的断言=该边的过滤原因（边级口径）；
    # khop 扩边下的断言=khop_expand——每行断言可独立溯源进子图的理由
    edge_seed_reason = {eid: "+".join(sorted(rs))
                        for eid, rs in seed_edges.items()}
    out_asserts = [{**a, "seed_reason": (edge_seed_reason.get(a["edge_id"])
                                         or "khop_expand")}
                   for a in sub_asserts]

    # —— 节点/冲突/修正 子集
    sub_nodes = [n for n in nodes if n["id"] in node_set]
    sub_conflicts = [c for c in conflicts
                     if norm_id(c["subject"]) in {norm_id(x) for x in node_set}
                     and norm_id(c["object"]) in {norm_id(x) for x in node_set}]
    sub_corrections = [c for c in corrections
                       if c["edge_id_new"] in sub_edge_ids]

    # —— gutMGene DOID 交叉（名字→节点索引：name 与 aliases 各段）
    node_index: dict[str, str] = {}
    for n in sub_nodes:
        node_index.setdefault(n["name"].casefold(), n["id"])
        for al in (n.get("aliases") or "").split("|"):
            if al.strip():
                node_index.setdefault(al.strip().casefold(), n["id"])
    gm_rows = gutmgene_cross(args.gutmgene_dir, args.doid, node_index)

    # —— 落盘（五件套 + 派生件；排序键与主图一致→确定性）
    out_dir.mkdir(parents=True, exist_ok=True)
    ecols = ecols_src + ["provenance", "causality",
                         "causality_basis", "causality_diag"]
    out_edges.sort(key=lambda r: (r["subject"], r["predicate"], r["object"]))
    write_tsv(out_dir / "merged_edges.tsv", out_edges, ecols)
    sub_nodes.sort(key=lambda r: r["id"])
    write_tsv(out_dir / "merged_nodes.tsv", sub_nodes, ncols_src)
    out_asserts.sort(key=lambda r: (r["subject_id"], r["predicate"],
                                    r["object_id"], r["pmid"],
                                    r["assertion_id"]))
    write_tsv(out_dir / "relation_assertions.tsv", out_asserts,
              acols_src + ["seed_reason"])
    write_tsv(out_dir / "conflicts.tsv", sub_conflicts, ccols_src)
    write_tsv(out_dir / "corrections.tsv", sub_corrections, rcols_src)
    if unclassified_rows:
        write_tsv(out_dir / "unclassified_edges.tsv", unclassified_rows, ecols)
    if gm_rows:
        write_tsv(out_dir / "gutmgene_cross.tsv", gm_rows, list(gm_rows[0].keys()))

    # —— 红线机器证（后半）：主图五件 sha 运行后对照（diff=空 ⇔ sha 全等）
    master_after = {f: sha256_file(kg_dir / f) for f in MASTER_FIVE}
    unchanged = all(master_before[f] == master_after[f] for f in MASTER_FIVE)
    if not unchanged:
        raise SystemExit("[红线] 主图 sha 运行前后不等——立即停止，不得产出子图")

    # —— subgraph_version 指纹（内容变则变；主图变也变）
    version_seed = "|".join([
        "subgraph_v1", args.key, sha256_file(kg_dir / "manifest.json"),
        ",".join(sorted(sub_edge_ids)), ",".join(sorted(node_set))])
    subgraph_version = hashlib.sha256(version_seed.encode()).hexdigest()[:16]

    prov_counts = {"seed_assertion": sum(1 for e in out_edges
                                         if e["provenance"].startswith("seed")),
                   "khop_expand": sum(1 for e in out_edges
                                      if e["provenance"] == "khop_expand")}
    gm_status = {}
    for r in gm_rows:
        gm_status[r["match_status"]] = gm_status.get(r["match_status"], 0) + 1
    manifest = {
        "subgraph_id": out_dir.name,
        "subgraph_version": subgraph_version,
        "spec": "data/logs/subgraph_spec_20261101.md",
        "module": "src/06_tool/subgraph.py",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tier": "tier0",
        "params": {
            "kg_dir": str(kg_dir), "kg_manifest_sha256":
                sha256_file(kg_dir / "manifest.json"),
            "library": str(args.library), "k3_batches": list(args.k3_batch),
            "mesh": sorted(mesh_ids), "name_root": args.name_root,
            "doid": args.doid, "khop": args.khop,
            "gutmgene_dir": str(args.gutmgene_dir or ""),
        },
        "disease_family": family_disclose,
        "adjacent_excluded": adjacent_excluded,
        "master_unchanged_proof": {
            "files": MASTER_FIVE, "before": master_before,
            "after": master_after, "machine_proof": unchanged,
            "note": "运行前后逐件 sha256 全等 ⇔ diff=空（零改动机器证）",
        },
        "counts": {
            "n_edges": len(out_edges), "n_nodes": len(sub_nodes),
            "n_assertions": len(out_asserts),
            "n_conflicts": len(sub_conflicts),
            "n_corrections": len(sub_corrections),
            "disease_lit_pmids": len(lit),
            "seed_edges": len(seed_edges),
            "seed_nodes": len(seed_nodes),
            "provenance": prov_counts,
            "causality": causality_counts,
            "unclassified_edges": len(unclassified_rows),
            "votes_missing_assertions": votes_missing_total,
            "gutmgene_cross_rows": len(gm_rows),
            "gutmgene_match_status": gm_status,
        },
        "determinism": {
            "sort_keys": {"edges": "subject,predicate,object",
                          "nodes": "id",
                          "assertions": "subject_id,predicate,object_id,pmid,assertion_id"},
            "no_run_time_in_output_rows": True,
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # —— SHA256SUMS（清单不含自身——release_v1 自指条目教训的装配规则）
    sums = []
    for p in sorted(out_dir.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS":
            sums.append(f"{sha256_file(p)}  {p.name}")
    (out_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    # —— 机器报告（数字程序化转写自 manifest——零手抄）
    c = manifest["counts"]
    report = (
        f"# 疾病子图报告（{out_dir.name}）\n\n"
        f"- subgraph_version=`{subgraph_version}`（tier0）｜规格=subgraph_spec_20261101.md\n"
        f"- 主图零改动机器证："
        f"{'**通过**（五件 sha 运行前后全等 ⇔ diff=空）' if unchanged else '**失败**'}\n"
        f"- 边 **{c['n_edges']}**（种子 {prov_counts['seed_assertion']} + "
        f"khop{args.khop} {prov_counts['khop_expand']}）｜节点 **{c['n_nodes']}**｜"
        f"断言 {c['n_assertions']}｜冲突 {c['n_conflicts']}｜修正 {c['n_corrections']}\n"
        f"- 疾病文献集 {c['disease_lit_pmids']} 篇｜疾病节点族 "
        f"{len(family_disclose)} 个描述符（邻接排除 "
        f"{len(adjacent_excluded)}：{', '.join(a['name'] for a in adjacent_excluded)}）\n"
        f"- 因果列分布：causal **{causality_counts['causal']}** / correlated "
        f"**{causality_counts['correlated']}** / unclassified "
        f"**{causality_counts['unclassified']}**（名单在 unclassified_edges.tsv，"
        f"不静默扔）｜votes 回连缺失断言 {votes_missing_total}\n"
        f"- gutMGene DOID 交叉：{c['gutmgene_cross_rows']} 行，命中 "
        f"{gm_status}\n"
    )
    (out_dir / "subgraph_report.md").write_text(report, encoding="utf-8")
    # 报告后补 sha（报告也是产物——统一进 SHA256SUMS）
    sums.append(f"{sha256_file(out_dir / 'subgraph_report.md')}  subgraph_report.md")
    (out_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    print(f"[tier0] 子图={out_dir}")
    print(f"  边 {len(out_edges)}（种子 {prov_counts['seed_assertion']} + "
          f"khop {prov_counts['khop_expand']}）| 节点 {len(sub_nodes)} | "
          f"断言 {len(out_asserts)}")
    print(f"  因果列：{causality_counts} | 未桥 votes 断言 "
          f"{votes_missing_total} | gutMGene 交叉 {len(gm_rows)} 行 {gm_status}")
    print(f"  主图零改动机器证：{'通过（diff=空）' if unchanged else '失败'}"
          f" | subgraph_version={subgraph_version}")
    return 0


# ============================================================
# Tier-1：esearch 定向检索差集计数（零 LLM）
# ============================================================
def _esearch(params: dict) -> dict:
    """esearch 一跳：先走环境代理，连接失败降级直连。

    为什么降级：本机常驻代理 127.0.0.1:7890 长期失效（SSL unexpected eof，
    2026-11-01 实测），requests 默认 trust_env 会被坏代理拖死——先按环境走
    （尊重可用代理的环境），失败再 trust_env=False 直连（NCBI 学术端点
    可直达）。两种环境都不崩。
    """
    try:
        r = requests.get(ESEARCH_URL, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        s = requests.Session()
        s.trust_env = False
        r = s.get(ESEARCH_URL, params=params, timeout=30)
        r.raise_for_status()
        return r.json()


def _load_api_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("NCBI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def fetch_query_pmids(query: str) -> tuple[list[str], int]:
    """按查询取全量 PMID：≤10k 一次拿完；>10k 按年切片（防静默截断）。"""
    key = _load_api_key()
    base = {"db": "pubmed", "retmode": "json", "term": query,
            "api_key": key}
    total = int(_esearch({**base, "retmax": 0})
                .get("esearchresult", {}).get("count", 0))
    if total == 0:
        return [], 0
    if total <= 10000:
        time.sleep(SLEEP_S)
        ids = _esearch({**base, "retmax": 10000, "retstart": 0}) \
            .get("esearchresult", {}).get("idlist", [])
        return ids, total
    all_ids: list[str] = []
    end_year = datetime.now().year
    for year in range(2010, end_year + 1):
        yq = f"({query}) AND {year}[PDAT]"
        yb = {"db": "pubmed", "retmode": "json", "term": yq, "api_key": key}
        ytotal = int(_esearch({**yb, "retmax": 0})
                     .get("esearchresult", {}).get("count", 0))
        time.sleep(SLEEP_S)
        if ytotal == 0:
            continue
        ids = _esearch({**yb, "retmax": 10000, "retstart": 0}) \
            .get("esearchresult", {}).get("idlist", [])
        all_ids.extend(ids)
        time.sleep(SLEEP_S)
    return all_ids, total


def cmd_tier1(args) -> int:
    query = (f"({SEARCH_QUERY}) AND ({args.disease_query}) "
             f"AND {YEAR_RANGE}")
    print(f"[tier1] 检索式：{query}")
    pmids, total = fetch_query_pmids(query)
    lib_ids = set()
    with open(args.library, encoding="utf-8") as f:
        for line in f:
            lib_ids.add(str(json.loads(line)["pmid"]))
    qset = set(pmids)
    diff = sorted(qset - lib_ids, key=int)
    result = {
        "tier": "tier1",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "esearch_count": total,
        "esearch_returned": len(pmids),
        "library_total": len(lib_ids),
        "overlap": len(qset & lib_ids),
        "n_diff": len(diff),
        "diff_pmids": diff,
        "cap": DIFF_CAP,
        "tier2_eligible": len(diff) <= DIFF_CAP,
        "note": "差集=定向检索集−库内集；≤cap 可直接进 Tier-2（用户预批机制），"
                "超帽停下报数字不跑",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"[tier1] esearch={total} 库内={len(lib_ids)} 重叠={len(qset & lib_ids)} "
          f"差集={len(diff)}（帽 {DIFF_CAP} → "
          f"{'可进 Tier-2' if result['tier2_eligible'] else '超帽，停下报数字'}）")
    print(f"  json={out}")
    return 0


# ============================================================
# Tier-2：K1 取文 → K3 批次判定 → 只并子图（LLM，仅差集 ≤ 帽）
# ============================================================
def cmd_tier2(args) -> int:
    t1 = json.loads(Path(args.tier1_json).read_text(encoding="utf-8"))
    if not t1.get("tier2_eligible"):
        print(f"[tier2] 未获准：差集 {t1.get('n_diff')} > 帽 {t1.get('cap')}"
              f"——停下报数字不跑（用户预批机制）")
        return 1
    diff = t1["diff_pmids"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    lit_dir = ROOT / "data" / "literature" / f"{ts}_tier2_{args.key}"
    batch_dir = ROOT / "data" / "kg" / f"{ts}_tier2_{args.key}"
    lit_dir.mkdir(parents=True, exist_ok=True)
    batch_dir.mkdir(parents=True, exist_ok=True)

    # 1) K1 取文（PubTator3，50/批——复用 K1 的批量函数，不重造）。
    #    pmid 必须手动补进 parsed（K1 主流程同款：parse_article 不带 id，
    #    缺了会让 K3 产出空 pmid 行——assertion_id 全串位，第一次实现踩过）
    from fetch_literature import BATCH_SIZE, fetch_batch, parse_article
    session = requests.Session()
    session.trust_env = False  # 坏代理环境实测需直连（同 tier1 降级口径）
    arts, failed = [], []
    for i in range(0, len(diff), BATCH_SIZE):
        raw = fetch_batch(diff[i:i + BATCH_SIZE], session)
        returned = {str(a.get("id", "")) for a in raw}
        for pmid in diff[i:i + BATCH_SIZE]:
            if pmid not in returned:
                failed.append(pmid)   # 未返回=太新/已删，与 K1 同披露口径
        for a in raw:
            p = parse_article(a)
            if p:
                p["pmid"] = str(a.get("id", ""))
                arts.append(p)
            else:
                failed.append(str(a.get("id", "")))  # 空解析
        print(f"  K1: {min(i + BATCH_SIZE, len(diff))}/{len(diff)}"
              f"（成功 {len(arts)} 未得 {len(failed)}）", flush=True)
    arts_file = lit_dir / "articles.jsonl"
    with open(arts_file, "w", encoding="utf-8") as f:
        for a in arts:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    (lit_dir / "failed_pmids.json").write_text(
        json.dumps(failed, ensure_ascii=False), encoding="utf-8")
    if not arts:
        print("[tier2] K1 取文全部失败——停止（不硬灌空批次）")
        return 1

    # 2) K3 判定（subprocess；--batch-dir 锁定输出；K3 输入=最新 literature
    #    目录，tier2 时间戳目录天然成为最新且与历史隔离；预算熔断显式设定
    #    ——K3 自家口径"全量跑前必须显式设定"，帽=差集×20 的工程冗余）
    budget = args.max_api_calls or max(500, len(diff) * 20)
    k3 = subprocess.run(
        [sys.executable, str(ROOT / "src" / "03_relation" /
                             "classify_relations.py"),
         "--batch-dir", str(batch_dir),
         "--max-api-calls", str(budget)],
        cwd=ROOT, env={**os.environ})
    if k3.returncode != 0:
        print("[tier2] K3 非零退出——停止（批次目录保留供 --resume）")
        return 1
    print(f"[tier2] K3 批次={batch_dir}（预算帽 {budget} 次调用）")

    # 3) 只并子图：把 ok 行并入子图目录（tier2_increment provenance）
    #    合并口径=K4 聚合简化式（同 subject|predicate|object 合并；run_batch
    #    记新批次号；不写主图任何文件——红线）
    sg_dir = Path(args.subgraph_dir)
    ok_rows = []
    with open(batch_dir / "relations.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("status") == "ok":
                s, o = d["subject"], d["object"]
                ok_rows.append({
                    "subject": s["id"], "predicate": d["predicate"],
                    "object": o["id"], "pmid": str(d["pmid"]),
                    "sentence": d.get("sentence", ""),
                    "evidence": d.get("evidence", ""),
                    "confidence": d.get("confidence", ""),
                    "run_batch": batch_dir.name,
                })
    print(f"[tier2] K3 ok 行 {len(ok_rows)}（增量明细清单落 {batch_dir}；"
          f"并入子图由后续合并步执行——见 manifest.tier2）")
    (batch_dir / "tier2_summary.json").write_text(json.dumps({
        "lit_dir": str(lit_dir), "n_diff": len(diff),
        "n_articles": len(arts), "n_failed": len(failed),
        "n_ok_rows": len(ok_rows), "subgraph_dir": str(sg_dir),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[tier2] 完成。增量并入子图命令见规格 §六（独立步：防半途崩溃"
          f"把半批增量灌进子图——批次与合并分两步走）")
    return 0


# ============================================================
# merge：Tier-2 增量并入子图（新时间戳目录；不写主图——红线）
# ============================================================
def _edge_id(subject: str, predicate: str, object_: str,
             run_batch: str) -> str:
    """K4 edge_id 公式逐字复刻：sha256(subject|predicate|object|run_batch)[:16]"""
    return hashlib.sha256(
        f"{subject}|{predicate}|{object_}|{run_batch}".encode()
    ).hexdigest()[:16]


def cmd_merge(args) -> int:
    sg_dir: Path = Path(args.subgraph_dir)
    batch_dir: Path = Path(args.tier2_batch)
    kg_dir: Path = args.kg_dir
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 输出目录可显式指定（测试/指定盘位用）；缺省=仓内 data/kg 时间戳目录
    out_dir = Path(args.out) if args.out else (
        ROOT / "data" / "kg" / f"subgraph_{args.key}_{ts}_t2merge")

    master_before = {f: sha256_file(kg_dir / f) for f in MASTER_FIVE}

    # —— 读 tier0 子图 + 主图节点表（新实体兜底查 K2 节点）+ tier2 批次
    edges = read_tsv(sg_dir / "merged_edges.tsv")
    ecols_src = tsv_header(sg_dir / "merged_edges.tsv")
    nodes = read_tsv(sg_dir / "merged_nodes.tsv")
    asserts = read_tsv(sg_dir / "relation_assertions.tsv")
    conflicts = read_tsv(sg_dir / "conflicts.tsv")
    corrections = read_tsv(sg_dir / "corrections.tsv")
    main_nodes = {n["id"]: n for n in read_tsv(kg_dir / "merged_nodes.tsv")}
    batch_id = batch_dir.name
    batch_date = datetime.fromtimestamp(
        (batch_dir / "relations.jsonl").stat().st_mtime
    ).strftime("%Y-%m-%d")

    ok_rows = []
    with open(batch_dir / "relations.jsonl", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("status") == "ok":
                s, o = d["subject"], d["object"]
                ok_rows.append({
                    "subject": s, "object": o, "predicate": d["predicate"],
                    "pmid": str(d["pmid"]), "sentence": d.get("sentence", ""),
                    "evidence": d.get("evidence", ""),
                    "confidence": d.get("confidence", ""),
                    "polarity": d.get("polarity", "positive"),
                    "model": d.get("model", ""), "stage": str(d.get("stage", "")),
                    "subject_mention": d.get("subject_mention", ""),
                })
    if not ok_rows:
        print("[merge] tier2 批次零 ok 行——不产出合并目录（空合并没有意义）")
        return 1

    # —— 按 (subject|predicate|object) 聚合 ok 行（K4 聚合的简化式：
    # 同三元组合并 support 与 pmids；conflict 检测不在本步——单批单票无对冲，
    # 限制披露进 manifest）
    agg: dict[tuple, list[dict]] = defaultdict(list)
    for r in ok_rows:
        agg[(r["subject"]["id"], r["predicate"], r["object"]["id"])].append(r)

    edge_key = lambda e: (e["subject"], e["predicate"], e["object"])  # noqa: E731
    existing = {edge_key(e): e for e in edges}
    new_asserts = []
    n_appended_edges = n_new_edges = 0
    for key, rows in agg.items():
        subj, pred, obj = key
        pmids = sorted({r["pmid"] for r in rows})
        e = existing.get(key)
        # 断言挂靠边 id 的口径：追加进既有边 → 用既有边的 edge_id（断言的
        # 聚合宿主是子图里的那条边）；全新三元组 → K4 公式新算
        eid = e["edge_id"] if e else _edge_id(subj, pred, obj, batch_id)
        for r in rows:
            new_asserts.append({
                "assertion_id": rebuild_assertion_id(
                    batch_id, r["pmid"], subj, pred, obj,
                    r["sentence"], r["evidence"]),
                "edge_id": eid, "run_batch": batch_id, "pmid": r["pmid"],
                "subject_id": subj, "subject_name": r["subject"].get("name", ""),
                "subject_mention": r["subject_mention"],
                "predicate": pred, "object_id": obj,
                "object_name": r["object"].get("name", ""),
                "sentence": r["sentence"], "evidence": r["evidence"],
                "confidence": r["confidence"], "polarity": r["polarity"],
                "evidence_granularity": "名称级",
                "granularity_source": "default_missing",
                "model": r["model"], "stage": r["stage"], "execution_id": "",
                "seed_reason": "tier2_increment",
            })
        if e:
            # 已有边：pmids 追加去重排序；support_count 累加；增量 pmid 记档
            # （.get 缺省——兼容列不全的边行，真实产物恒有全列）
            old = {p for p in (e.get("pmids") or "").split(";") if p}
            merged = sorted(old | set(pmids))
            e["pmids"] = ";".join(merged)
            e["support_count"] = str(int(e.get("support_count") or 0)
                                     + len(rows))
            e["tier2_increment_pmids"] = ";".join(pmids)
            n_appended_edges += 1
        else:
            # 新边：22 列全机械填充（C 档 llm_extracted 边的冻结模板值）
            conf = max((float(r["confidence"]) for r in rows
                        if r["confidence"]), default=0.0)
            pol = ("positive" if sum(1 for r in rows
                                     if r["polarity"] == "positive")
                   >= len(rows) / 2 else "negative")
            existing[key] = {
                "subject": subj, "predicate": pred, "object": obj,
                "source_type": "llm_extracted", "evidence_tier": "C",
                "pmids": ";".join(pmids), "years": "",
                "support_count": str(len(rows)),
                "confidence": f"{round(conf, 4):.4f}", "polarity": pol,
                "last_updated": batch_date,
                "relation_status": "context_supported",
                "canonical_view": "derived_summary",
                "curator": "unattributed", "source_ref": "automated_pipeline",
                "source_id": "llm_extract_v2", "retrieved_at": batch_date,
                "version": "pipeline v2",
                "knowledge_layer": "local_kg_llm_extracted",
                "edge_id": eid, "run_batch": batch_id,
                "evidence_granularity": "名称级",
                "provenance": "tier2_increment", "causality": "",
                "causality_basis": "", "causality_diag": "",
                "tier2_increment_pmids": ";".join(pmids),
            }
            n_new_edges += 1

    # —— 节点闭包：新端点优先查主图节点，查不到按 K3 行类别构造（披露计数）
    out_edges = list(existing.values())
    have_nodes = {n["id"] for n in nodes}
    closure_nodes, n_constructed = [], 0
    for e in out_edges:
        for nid in (e["subject"], e["object"]):
            if nid in have_nodes:
                continue
            have_nodes.add(nid)
            if nid in main_nodes:
                closure_nodes.append(main_nodes[nid])
            else:
                # K3 行自带 category/name——兜底构造（无法从 K2 判 rank 的诚实缺省）
                src = next((r for r in ok_rows
                            if r["subject"]["id"] == nid
                            or r["object"]["id"] == nid), None)
                cat = (src["subject"] if src and src["subject"]["id"] == nid
                       else src["object"]) if src else {}
                closure_nodes.append({
                    "id": nid, "name": cat.get("name", ""),
                    "category": cat.get("category", ""),
                    "aliases": "", "xrefs": "", "tax_rank": "",
                    "knowledge_layer": "local_kg_llm_extracted"})
                n_constructed += 1
    out_nodes = nodes + closure_nodes

    # —— 因果列重算（受影响边=全部边——重算便宜且保证口径一致）
    votes_idx = load_votes_index(
        [Path(p) for p in args.k3_batch] + [batch_dir])
    asserts_by_edge: dict[str, list[dict]] = defaultdict(list)
    for a in asserts + new_asserts:
        asserts_by_edge[a["edge_id"]].append(a)
    out_asserts = ([{**a, "seed_reason": a.get("seed_reason", "khop_expand")}
                    for a in asserts] + new_asserts)
    causality_counts = {"causal": 0, "correlated": 0, "unclassified": 0}
    unclassified_rows = []
    votes_missing_total = 0
    for e in out_edges:
        alist = asserts_by_edge.get(e["edge_id"], [])
        c = derive_causality(e, alist, votes_idx)
        votes_missing_total += c["n_votes_missing"]
        causality_counts[c["causality"]] += 1
        e["causality"] = c["causality"]
        e["causality_basis"] = c["causality_basis"]
        e["causality_diag"] = c["causality_diag"]
        if c["causality"] == "unclassified":
            unclassified_rows.append(e)

    # —— 落盘（新时间戳目录——与 tier0 产物不混放）
    # 列集=规范 22 列 ∪ 子图表头 ∪ 派生列：真实子图表头本就是规范 22 列
    # （并集不变形）；退化输入（测试迷你图）不会把新边模板列截丢
    out_dir.mkdir(parents=True, exist_ok=True)
    CANON = ["subject", "predicate", "object", "source_type", "evidence_tier",
             "pmids", "years", "support_count", "confidence", "polarity",
             "last_updated", "relation_status", "canonical_view",
             "source_ref", "curator", "source_id", "retrieved_at", "version",
             "knowledge_layer", "edge_id", "run_batch", "evidence_granularity"]
    extra_src = [c for c in ecols_src if c not in CANON]
    derived = ["provenance", "causality", "causality_basis",
               "causality_diag", "tier2_increment_pmids"]
    ecols = (CANON + extra_src +
             [c for c in derived if c not in CANON + extra_src])
    out_edges.sort(key=lambda r: (r["subject"], r["predicate"], r["object"]))
    write_tsv(out_dir / "merged_edges.tsv", out_edges, ecols)
    out_nodes.sort(key=lambda r: r["id"])
    write_tsv(out_dir / "merged_nodes.tsv", out_nodes,
              tsv_header(sg_dir / "merged_nodes.tsv"))
    out_asserts.sort(key=lambda r: (r["subject_id"], r["predicate"],
                                    r["object_id"], r["pmid"],
                                    r["assertion_id"]))
    write_tsv(out_dir / "relation_assertions.tsv", out_asserts,
              tsv_header(sg_dir / "relation_assertions.tsv"))
    write_tsv(out_dir / "conflicts.tsv", conflicts,
              tsv_header(sg_dir / "conflicts.tsv"))
    write_tsv(out_dir / "corrections.tsv", corrections,
              tsv_header(sg_dir / "corrections.tsv"))
    if unclassified_rows:
        write_tsv(out_dir / "unclassified_edges.tsv", unclassified_rows, ecols)

    master_after = {f: sha256_file(kg_dir / f) for f in MASTER_FIVE}
    unchanged = all(master_before[f] == master_after[f] for f in MASTER_FIVE)
    if not unchanged:
        raise SystemExit("[红线] 主图 sha 运行前后不等——立即停止")

    node_ids = {n["id"] for n in out_nodes}
    version_seed = "|".join([
        "subgraph_v1+tier2", args.key, sha256_file(kg_dir / "manifest.json"),
        ",".join(sorted(e["edge_id"] for e in out_edges)),
        ",".join(sorted(node_ids)), batch_id])
    subgraph_version = hashlib.sha256(version_seed.encode()).hexdigest()[:16]
    prov_counts = {}
    for e in out_edges:
        p = e["provenance"].split(":")[0]
        prov_counts[p] = prov_counts.get(p, 0) + 1
    manifest = {
        "subgraph_id": out_dir.name,
        "subgraph_version": subgraph_version,
        "spec": "data/logs/subgraph_spec_20261101.md",
        "module": "src/06_tool/subgraph.py",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tier": "tier2_merge",
        "lineage": {
            "merged_from_subgraph": str(sg_dir),
            "merged_from_version": json.loads(
                (sg_dir / "manifest.json").read_text(encoding="utf-8")
            ).get("subgraph_version"),
            "tier2_batch": str(batch_dir),
            "tier1_json": str(args.tier1_json),
        },
        "master_unchanged_proof": {
            "files": MASTER_FIVE, "before": master_before,
            "after": master_after, "machine_proof": unchanged,
        },
        "counts": {
            "n_edges": len(out_edges), "n_nodes": len(out_nodes),
            "n_assertions": len(out_asserts),
            "n_conflicts": len(conflicts), "n_corrections": len(corrections),
            "provenance": prov_counts, "causality": causality_counts,
            "unclassified_edges": len(unclassified_rows),
            "votes_missing_assertions": votes_missing_total,
            "tier2": {
                "n_ok_rows": len(ok_rows),
                "n_distinct_triples": len(agg),
                "n_edges_appended": n_appended_edges,
                "n_edges_new": n_new_edges,
                "n_closure_nodes_from_main": len(closure_nodes) - n_constructed,
                "n_closure_nodes_constructed": n_constructed,
            },
        },
        "disclosure": [
            "tier2 增量未做 K4 式冲突检测（单批单票无对冲）——若后续再批增量批次，须引入跨批 conflict 复核",
            "构造节点（K2 不可达的新实体）category 取 K3 行原值，rank 缺省空",
        ],
        "determinism": {
            "sort_keys": {"edges": "subject,predicate,object", "nodes": "id",
                          "assertions": "subject_id,predicate,object_id,pmid,assertion_id"},
            "no_run_time_in_output_rows": True,
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    c = manifest["counts"]
    report = (
        f"# 疾病子图报告（tier2 合并版 {out_dir.name}）\n\n"
        f"- subgraph_version=`{subgraph_version}`｜前身={manifest['lineage']['merged_from_version']}\n"
        f"- 主图零改动机器证：{'**通过**' if unchanged else '**失败**'}（五件 sha 前后全等）\n"
        f"- 边 **{c['n_edges']}**（provenance {prov_counts}）｜节点 **{c['n_nodes']}**｜断言 {c['n_assertions']}\n"
        f"- tier2 增量：ok 行 {c['tier2']['n_ok_rows']} → 三元组 "
        f"{c['tier2']['n_distinct_triples']}（新边 {c['tier2']['n_edges_new']} + "
        f"追加既有边 {c['tier2']['n_edges_appended']}）｜闭包节点 "
        f"{c['tier2']['n_closure_nodes_from_main']}（主图）+ "
        f"{c['tier2']['n_closure_nodes_constructed']}（构造）\n"
        f"- 因果列：{causality_counts}｜unclassified 名单在 "
        f"unclassified_edges.tsv\n"
    )
    (out_dir / "subgraph_report.md").write_text(report, encoding="utf-8")
    sums = []
    for p in sorted(out_dir.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS":
            sums.append(f"{sha256_file(p)}  {p.name}")
    (out_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    print(f"[merge] 合并子图={out_dir}")
    print(f"  tier2 ok 行 {len(ok_rows)} → 新边 {n_new_edges} + 追加 "
          f"{n_appended_edges} | 总边 {len(out_edges)} 节点 {len(out_nodes)}")
    print(f"  因果列 {causality_counts} | 主图零改动 "
          f"{'通过' if unchanged else '失败'} | version={subgraph_version}")
    return 0


# ============================================================
# CLI
# ============================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="疾病子图构建器（三档制+因果列）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    # W2.2 数据腿：kg/library/k3-batch/gutmgene 由注册表供应（显式>注册表>原默认）
    common.add_argument("--kg-dir", type=Path, default=None,
                        help="主图目录（缺省=注册表 kg_v2）")
    common.add_argument("--library", type=Path, default=None,
                        help="文献库 jsonl（缺省=注册表 library）")
    # W2.1 疾病档案：--disease htn 时身份字段从 configs/diseases/htn.json 解析；
    # 不传 --disease 时行为与改造前逐字节一致（golden 等价前提）
    common.add_argument("--disease", default=None,
                        help="疾病档案 key（configs/diseases/；不传=用下方模块默认）")

    p0 = sub.add_parser("tier0", parents=[common],
                        help="存量切出（零 LLM）")
    # 以下四个疾病身份参数默认值改为 None 哨兵——原默认值挪入 _ORIG_DEFAULTS
    # （值一字不改），由 disease_profile.resolve 按"显式>档案>原默认"回填
    p0.add_argument("--mesh", default=None,
                    help="疾病 MeSH 号集（逗号分隔）")
    p0.add_argument("--name-root", default=None,
                    help="疾病名词根（lowercase contains）")
    p0.add_argument("--doid", default=None, help="gutMGene DOID")
    p0.add_argument("--khop", type=int, default=1)
    p0.add_argument("--key", default=None)
    p0.add_argument("--k3-batch", action="append", default=None,
                    help="K3 批次目录（缺省=注册表 k3_batch）")
    p0.add_argument("--gutmgene-dir", type=Path, default=None,
                    help="gutMGene 目录（缺省=注册表 gutmgene 本机覆盖件）")
    p0.add_argument("--out", type=Path, default=None)
    p0.set_defaults(func=cmd_tier0)

    p1 = sub.add_parser("tier1", parents=[common],
                        help="定向检索差集计数（零 LLM，一次 esearch）")
    p1.add_argument("--disease-query", default=None,
                    help="tier1 定向检索式（不传且无 --disease=用原默认 HTN 式）")
    p1.add_argument("--out", type=Path,
                    default=ROOT / "data/logs/tier1_diff_count_20261101.json")
    p1.set_defaults(func=cmd_tier1)

    p2 = sub.add_parser("tier2", parents=[common],
                        help="增量判定（仅差集 ≤1200；K1→K3→批次落盘）")
    p2.add_argument("--tier1-json", required=True)
    p2.add_argument("--subgraph-dir", required=True)
    p2.add_argument("--key", default=None)
    p2.add_argument("--max-api-calls", type=int, default=0,
                    help="K3 预算熔断（0=自动：max(500, 差集×20)）")
    p2.set_defaults(func=cmd_tier2)

    p3 = sub.add_parser("merge", parents=[common],
                        help="tier2 批次并入子图（新时间戳目录；不写主图）")
    p3.add_argument("--subgraph-dir", required=True)
    p3.add_argument("--tier2-batch", required=True)
    p3.add_argument("--tier1-json", default="")
    p3.add_argument("--key", default=None)
    p3.add_argument("--k3-batch", action="append",
                    default=[str(ROOT / "data/kg/20261016_165931")])
    p3.add_argument("--out", type=Path, default=None)
    p3.set_defaults(func=cmd_merge)

    # 改造前默认值原样登记（golden 等价前提：无 --disease/注册表缺失时回填这些值，
    # 行为与改造前逐字节一致）
    _ORIG_DEFAULTS = {
        "mesh": "D006973,D006976",
        "name_root": "hypertens",
        "doid": "DOID:10763",
        "key": "htn",
        "disease_query": '"Hypertension"[MeSH] OR "hypertension"[TIAB]',
        "kg_dir": str(ROOT / "data/kg/k4_full_20261028_155020"),
        "library": str(ROOT / "data/literature/20261015_154252/articles.jsonl"),
        "k3_batch": [str(ROOT / "data/kg/20261016_165931")],
        "gutmgene_dir": ("<LYTEAMWORK>/<USER>/Project/Dietary_cohort"
                         "_of_intestinal_flora/Project/Project03_公共数据库/gutMGene"),
    }
    # 数据腿注册表映射（W2.2）：dest → (注册表键, 是否列表型)
    _DS_MAPPING = {"kg_dir": ("kg_v2", False), "library": ("library", False),
                   "k3_batch": ("k3_batch", True),
                   "gutmgene_dir": ("gutmgene", False)}

    args = ap.parse_args(argv)
    args = data_source_profile.resolve(args, _DS_MAPPING, _ORIG_DEFAULTS)
    args = disease_profile.resolve(args, _ORIG_DEFAULTS)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
