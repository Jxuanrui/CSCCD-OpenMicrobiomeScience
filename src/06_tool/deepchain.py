#!/usr/bin/env python3
"""C3 三跳机制深链引擎（H-C 正交知识链接）：菌→代谢物→基因→通路。

规格=data/logs/interact_deepchain_spec_20261101.md §二（逐字对照；改规格须
同步改代码与测试）。

三跳口径（V01 冻结引擎换输入——唯一实质改动=hop1 边集从主图全集改为子图）：
- hop1 菌→代谢物 = 子图边（produces/biotransforms）；
- hop2 代谢物→基因 = gutMGene 表3 全表（1,049 逻辑记录，疾病无关正交知识）；
- hop3 基因→通路 = KEGG 通路成员（V01 图 kegg_rest 快照 participates_in 边，
  基因域=gutMGene 表1∪表3 的 Gene ID 并集）。

为什么 V01 资产要过三道门禁才准用：KEGG 通路成员关系是拼链的"最后一跳"，
来源必须钉死版本——INHERITANCE.md 登记 sha 不等=资产被动过，拼出来的链
不可信，fail-closed 拒跑；对拍锚定（本引擎 (gene,pathway) 对 ⊆ 冻结链
102,854 条的去重对集）防拼错通路号。

死端罚分（THGAgents 借设计不借代码）：走到无出路节点罚 DEAD_END_PENALTY
（常量写死非拟合——本阶段不调参不学习），死端链 pruned=1 逐条落盘不静默。

红线：子图/主图零改动（只写子图目录内两个新文件）；零关联分析；零 LLM
（GraphRAG 只留接口占位 graphrag_explain_slot——模型调用留 MT5）。
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subgraph import read_tsv  # noqa: E402  复用 B 阶段 TSV 读取口径

# ============================================================
# 规格锁定常量（spec §二 逐字；改这里=改规格）
# ============================================================
HOP1_PREDICATES = {"produces", "biotransforms"}   # V01 冻结拼链口径
DEAD_END_PENALTY = 1.0                             # 写死非拟合（spec 逐字）

# V01 过继资产门禁（INHERITANCE.md 登记值）
V01_EDGES_SHA16 = "41833d39db18e3dc"    # INHERITANCE.md KG 边表 20,036 边
V01_CHAINS_SHA16 = "00e9508d17b9f838"   # INHERITANCE.md:18 冻结链
V01_CHAINS_ROWS = 102854                # 102,854 链（csv 逻辑记录）

# hop3 的 kegg_rest 快照（V01 图内 source_id）与基因域表（gutMGene 表1∪表3）
KEGG_SOURCE_ID = "kegg_rest"
PATHWAY_PREDICATE = "participates_in"
TABLE1_NAME = "Gut Microbe-Host Gene.csv"    # 基因域表之一（菌↔基因）
TABLE3_NAME = "Microbial metabolite-Host Gene.csv"  # hop2 主表（代谢物↔基因）

CHAIN_COLS = ["chain_id", "microbe_id", "microbe_name", "metabolite_id",
              "metabolite_name", "hop1_predicate", "hop1_edge_id",
              "hop1_causality", "hop2_gene", "hop2_gene_name",
              "hop2_gutmgene_refs", "metabolite_chebi", "metabolite_kegg",
              "hop3_pathway", "depth", "hop_terminus", "dead_end", "penalty",
              "pruned"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def chain_id(microbe: str, metabolite: str, hop1_edge: str, gene: str,
             pathway: str) -> str:
    """链 id 公式（spec §二）：CH-sha256(五元组)[:16]——同链同 id，
    可独立复算防重排伪造。"""
    raw = f"{microbe}|{metabolite}|{hop1_edge}|{gene}|{pathway}"
    return "CH-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ============================================================
# V01 资产门禁（三道，fail-closed）
# ============================================================
def gate_v01_edges(v01_edges: Path) -> list[dict]:
    """门禁①：V01 边表 sha16==INHERITANCE 登记值；通过则返回 participates_in
    （kegg_rest 快照）边——hop3 唯一通路源。"""
    sha16 = sha256_file(v01_edges)[:16]
    if sha16 != V01_EDGES_SHA16:
        raise SystemExit(f"[红线] V01 边表 sha16={sha16} ≠ 登记 "
                         f"{V01_EDGES_SHA16}——资产被动过，拒跑")
    rows = [r for r in read_tsv(v01_edges)
            if r["predicate"] == PATHWAY_PREDICATE
            and r.get("source_id") == KEGG_SOURCE_ID]
    if not rows:
        raise SystemExit("[红线] V01 边表 kegg_rest participates_in 为空——"
                         "hop3 无源，拒跑")
    return rows


def gate_v01_chains(v01_chains: Path) -> list[dict]:
    """门禁②：冻结链 sha16+行数双验——锚定集的原料本身必须可证未动。"""
    sha16 = sha256_file(v01_chains)[:16]
    with open(v01_chains, encoding="utf-8", newline="") as f:
        n = sum(1 for _ in csv.DictReader(f, delimiter="\t"))
    if sha16 != V01_CHAINS_SHA16 or n != V01_CHAINS_ROWS:
        raise SystemExit(f"[红线] V01 冻结链校验失败：sha16={sha16} "
                         f"(应 {V01_CHAINS_SHA16})，行数={n} "
                         f"(应 {V01_CHAINS_ROWS})——拒跑")
    return read_tsv(v01_chains)


def anchor_check(used_pairs: set[tuple[str, str]],
                 frozen_chains: list[dict]) -> dict:
    """门禁③：对拍锚定——本引擎用到的 (gene,pathway) 对必须 ⊆ 冻结链去重
    对集；violation>0 即抛 SystemExit（拼错通路号在这里被拦）。"""
    frozen = {(r["gene"], r["pathway"]) for r in frozen_chains}
    violations = sorted(used_pairs - frozen)
    if violations:
        show = "; ".join(f"{g}->{p}" for g, p in violations[:5])
        raise SystemExit(f"[红线] 锚定违例 {len(violations)} 对（如 {show}）"
                         "——引擎产物含冻结链外 (基因,通路) 对，拒跑")
    return {"n_pairs_used": len(used_pairs),
            "n_pairs_frozen": len(frozen),
            "violations": 0,
            "machine_proof": True}


# ============================================================
# hop2/hop3 知识源装载（gutMGene 表3 全表 + 表1∪表3 基因域）
# ============================================================
def load_hop2(gutmgene_dir: Path) -> dict[str, dict[str, dict]]:
    """代谢物名(casefold) → {gene_id: {gene_name, refs, chebi, kegg}}。

    refs=表3 Index:PMID（逐条可溯）；chebi/kegg 取该代谢物全部行的非空值
    去重排序——同名代谢物的编号列各行一致时不膨胀，不一致时全披露。
    """
    m2g: dict[str, dict[str, dict]] = defaultdict(dict)
    with open(gutmgene_dir / TABLE3_NAME, encoding="gb18030", newline="") as f:
        for r in csv.DictReader(f):
            key = (r.get("Metabolite") or "").strip().casefold()
            gid = (r.get("Gene ID") or "").strip()
            if not key or not gid:
                continue
            gene = "NCBIGene:" + gid
            slot = m2g[key].setdefault(gene, {
                "gene_name": (r.get("Gene") or "").strip(), "refs": set(),
                "chebi": set(), "kegg": set()})
            slot["refs"].add(f"{r.get('Index', '')}:{r.get('PMID', '')}")
            for col, field in (("Metabolite ChEBI", "chebi"),
                               ("Metabolite KEGG", "kegg")):
                v = (r.get(col) or "").strip()
                if v:
                    slot[field].add(v)
    return m2g


def load_gene_domain(gutmgene_dir: Path) -> set[str]:
    """hop3 基因域（spec §二）=gutMGene 表1∪表3 的 Gene ID 并集——
    通路锚只服务 gutMGene 基因域（域外基因不拉通路）。"""
    domain: set[str] = set()
    for fname in (TABLE1_NAME, TABLE3_NAME):
        with open(gutmgene_dir / fname, encoding="gb18030", newline="") as f:
            for r in csv.DictReader(f):
                gid = (r.get("Gene ID") or "").strip()
                if gid:
                    domain.add("NCBIGene:" + gid)
    return domain


def load_hop3(v01_edges: Path, gene_domain: set[str]) -> dict[str, list[str]]:
    """基因→通路列表（KEGG REST 快照限定 gutMGene 基因域；排序确定性）。"""
    g2p: dict[str, set[str]] = defaultdict(set)
    for e in gate_v01_edges(v01_edges):
        if e["subject"] in gene_domain:
            g2p[e["subject"]].add(e["object"])
    return {g: sorted(ps) for g, ps in g2p.items()}


# ============================================================
# GraphRAG 解释位（占位——零 LLM；实现留 MT5）
# ============================================================
def graphrag_explain_slot(chain_row: dict) -> dict:
    """GraphRAG 解释位的机械占位（spec §二）：拼好"将来给解释模型的上下文"
    （链行原文+三跳证据引用），但不做任何模型调用——本阶段红线零 LLM。

    MT5 接手时在此函数内挂 LLM 即可，接口与上下文字段已冻结。
    """
    return {
        "slot": "graphrag_explain",
        "implemented": False,          # 本阶段恒 False——MT5 前零 LLM 的机器证
        "reserved_for": "MT5",
        "chain_context": {
            "chain_id": chain_row.get("chain_id", ""),
            "hops": [
                {"hop": 1, "triple": [chain_row.get("microbe_id", ""),
                                      chain_row.get("hop1_predicate", ""),
                                      chain_row.get("metabolite_id", "")],
                 "source": "subgraph_edge:"
                           + chain_row.get("hop1_edge_id", "")},
                {"hop": 2, "triple": [chain_row.get("metabolite_id", ""),
                                      "modulates_host_gene",
                                      chain_row.get("hop2_gene", "")],
                 "source": "gutmgene_table3:"
                           + (chain_row.get("hop2_gutmgene_refs") or "")},
                {"hop": 3, "triple": [chain_row.get("hop2_gene", ""),
                                      "participates_in",
                                      chain_row.get("hop3_pathway", "")],
                 "source": "kegg_rest_snapshot"},
            ],
            "depth": chain_row.get("depth"),
            "hop_terminus": chain_row.get("hop_terminus", ""),
            "penalty": chain_row.get("penalty"),
        },
    }


# ============================================================
# 主流程：hop1 子图边 → 分层枚举（死端罚分剪枝）
# ============================================================
def build_metabolite_keys(nodes: list[dict]) -> dict[str, list[str]]:
    """代谢物节点 id → 名字键列表（name+aliases，casefold——hop2 查找用），
    同名键不合并到别的节点：先到先占位与 interact 同口径。"""
    by_key: dict[str, str] = {}
    for n in nodes:
        if n["category"] != "Metabolite":
            continue
        keys = [n["name"].strip().casefold()] + \
            [al.strip().casefold() for al in (n.get("aliases") or "").split("|")
             if al.strip()]
        for k in keys:
            by_key.setdefault(k, n["id"])
    nid2keys: dict[str, list[str]] = defaultdict(list)
    for k, nid in by_key.items():
        nid2keys[nid].append(k)
    return {nid: sorted(ks) for nid, ks in nid2keys.items()}


def assemble_chains(edges: list[dict], nodes: list[dict], m2g: dict,
                    g2p: dict) -> tuple[list[dict], dict]:
    """分层枚举三跳链（spec §二）：hop1 边 → hop2 基因 → hop3 通路；
    无出路即死端（罚分+pruned 落盘），完整链 depth=3。

    返回 (链行列表, 统计)——枚举顺序与排序键固定，同输入同输出。
    """
    node_by_id = {n["id"]: n for n in nodes}
    met_keys = build_metabolite_keys(nodes)
    hop1 = [e for e in edges if e["predicate"] in HOP1_PREDICATES
            and e["subject"].startswith("NCBITaxon:")
            and node_by_id.get(e["object"], {}).get("category") == "Metabolite"]

    chains = []
    stats = {"hop1_edges": len(hop1),
             "deadend_metabolite": 0, "deadend_gene": 0, "complete": 0}
    for e in sorted(hop1, key=lambda x: (x["subject"], x["predicate"],
                                         x["object"], x["edge_id"])):
        mid, oid = e["subject"], e["object"]
        mrow = node_by_id.get(oid, {})
        base = {"microbe_id": mid,
                "microbe_name": node_by_id.get(mid, {}).get("name", ""),
                "metabolite_id": oid, "metabolite_name": mrow.get("name", ""),
                "hop1_predicate": e["predicate"], "hop1_edge_id": e["edge_id"],
                "hop1_causality": e.get("causality", "")}
        # hop2：代谢物全部名字键（name+aliases）并查 gutMGene 表3——
        # 同一基因经多个名字键命中时证据并集（丢 refs=丢溯源，不可接受）
        genes: dict[str, dict] = {}
        for k in met_keys.get(oid, []):
            for g, slot in (m2g.get(k) or {}).items():
                if g not in genes:
                    genes[g] = {"gene_name": slot["gene_name"],
                                "refs": set(), "chebi": set(), "kegg": set()}
                genes[g]["refs"] |= slot["refs"]
                genes[g]["chebi"] |= slot["chebi"]
                genes[g]["kegg"] |= slot["kegg"]
        if not genes:
            # 死端①：代谢物无基因边——罚分剪枝，链止于 depth=1
            chains.append({**base, "chain_id": chain_id(mid, oid,
                                                        e["edge_id"], "", ""),
                           "hop2_gene": "", "hop2_gene_name": "",
                           "hop2_gutmgene_refs": "",
                           "metabolite_chebi": "", "metabolite_kegg": "",
                           "hop3_pathway": "", "depth": 1,
                           "hop_terminus": "metabolite", "dead_end": 1,
                           "penalty": DEAD_END_PENALTY, "pruned": 1})
            stats["deadend_metabolite"] += 1
            continue
        for g in sorted(genes):
            slot = genes[g]
            refs = ";".join(sorted(slot["refs"]))
            chebi = ";".join(sorted(slot["chebi"]))
            kegg = ";".join(sorted(slot["kegg"]))
            pathways = g2p.get(g)
            if not pathways:
                # 死端②：基因无通路边——罚分剪枝，链止于 depth=2
                chains.append({**base, "chain_id": chain_id(
                    mid, oid, e["edge_id"], g, ""),
                    "hop2_gene": g, "hop2_gene_name": slot["gene_name"],
                    "hop2_gutmgene_refs": refs, "metabolite_chebi": chebi,
                    "metabolite_kegg": kegg, "hop3_pathway": "", "depth": 2,
                    "hop_terminus": "gene", "dead_end": 1,
                    "penalty": DEAD_END_PENALTY, "pruned": 1})
                stats["deadend_gene"] += 1
                continue
            for p in pathways:
                chains.append({**base, "chain_id": chain_id(
                    mid, oid, e["edge_id"], g, p),
                    "hop2_gene": g, "hop2_gene_name": slot["gene_name"],
                    "hop2_gutmgene_refs": refs, "metabolite_chebi": chebi,
                    "metabolite_kegg": kegg, "hop3_pathway": p, "depth": 3,
                    "hop_terminus": "pathway", "dead_end": 0,
                    "penalty": 0.0, "pruned": 0})
                stats["complete"] += 1
    chains.sort(key=lambda r: (r["microbe_id"], r["hop1_edge_id"],
                               r["metabolite_id"], r["hop2_gene"],
                               r["hop3_pathway"]))
    return chains, stats


def verify_subgraph_integrity(sg_dir: Path) -> dict:
    """子图完整性校验（与 interact.py 同法）：旧件 sha==SHA256SUMS 冻结清单
    ——"只加新文件不改旧件"的机器证。"""
    frozen = {}
    for line in (sg_dir / "SHA256SUMS").read_text(encoding="utf-8") \
            .splitlines():
        if line.strip():
            sha, _, name = line.partition("  ")
            frozen[name.strip()] = sha
    mismatches = [n for n, s in sorted(frozen.items())
                  if (sg_dir / n).exists() and sha256_file(sg_dir / n) != s]
    return {"files_checked": len(frozen), "mismatches": mismatches,
            "machine_proof": not mismatches}


def write_tsv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C3 三跳机制深链引擎")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--gutmgene-dir", type=Path, default=None,
                    help="gutMGene 目录（缺省=注册表）")
    ap.add_argument("--v01-edges", type=Path, default=None,
                    help="V01 边表（缺省=注册表 v01_edges）")
    ap.add_argument("--v01-chains", type=Path, default=None,
                    help="V01 冻结链（缺省=注册表 v01_chains）")
    args = ap.parse_args(argv)
    import data_source_profile  # noqa: E402  W2.2 数据腿注册表
    args = data_source_profile.resolve(
        args, {"gutmgene_dir": ("gutmgene", False),
               "v01_edges": ("v01_edges", False),
               "v01_chains": ("v01_chains", False)},
        {"gutmgene_dir": "<GUTMGENE_LOCAL>",
         "v01_edges": str(ROOT / "archive/Version01/data/merged/candidate_v3/merged_edges.tsv"),
         "v01_chains": str(ROOT / "archive/Version01/data/logs/mechanism_chains.tsv")})
    sg_dir: Path = args.subgraph_dir

    integrity_before = verify_subgraph_integrity(sg_dir)
    edges = read_tsv(sg_dir / "merged_edges.tsv")
    nodes = read_tsv(sg_dir / "merged_nodes.tsv")

    # —— 三跳知识源（V01 门禁①②在装载内 fail-closed）
    m2g = load_hop2(args.gutmgene_dir)
    gene_domain = load_gene_domain(args.gutmgene_dir)
    g2p = load_hop3(args.v01_edges, gene_domain)
    frozen_chains = gate_v01_chains(args.v01_chains)

    chains, stats = assemble_chains(edges, nodes, m2g, g2p)

    # —— 门禁③：对拍锚定（本引擎用到的全部 (gene,pathway) 对 ⊆ 冻结对集）
    used_pairs = {(c["hop2_gene"], c["hop3_pathway"]) for c in chains
                  if c["depth"] == 3}
    anchor = anchor_check(used_pairs, frozen_chains)

    # —— 落盘（新文件；chains.tsv=完整链+死端剪枝同行，逐条可追不静默）
    write_tsv(sg_dir / "chains.tsv", chains, CHAIN_COLS)

    depth_dist = {str(d): sum(1 for c in chains if c["depth"] == d)
                  for d in (1, 2, 3)}
    sub_manifest = json.loads((sg_dir / "manifest.json").read_text(
        encoding="utf-8"))
    manifest = {
        "module": "src/06_tool/deepchain.py",
        "spec": "data/logs/interact_deepchain_spec_20261101.md#二",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "subgraph_dir": str(sg_dir),
            "subgraph_version": sub_manifest.get("subgraph_version"),
            "hop1_predicates": sorted(HOP1_PREDICATES),
            "dead_end_penalty": DEAD_END_PENALTY,
            "v01_edges": str(args.v01_edges),
            "v01_chains": str(args.v01_chains),
        },
        "inputs_sha256": {
            "gutmgene_table1": sha256_file(args.gutmgene_dir / TABLE1_NAME),
            "gutmgene_table3": sha256_file(args.gutmgene_dir / TABLE3_NAME),
            "v01_edges": sha256_file(args.v01_edges),
            "v01_chains": sha256_file(args.v01_chains),
        },
        "v01_gates": {
            # pass 字段均为机器比对结果（门禁失败在装载期已 SystemExit，
            # 到这里恒 True——但仍按比对式书写，防将来重构破坏口径）
            "edges_sha16": {"expect": V01_EDGES_SHA16,
                            "got": sha256_file(args.v01_edges)[:16],
                            "pass": sha256_file(args.v01_edges)[:16]
                            == V01_EDGES_SHA16},
            "chains_sha16": {"expect": V01_CHAINS_SHA16,
                             "got": sha256_file(args.v01_chains)[:16],
                             "pass": sha256_file(args.v01_chains)[:16]
                             == V01_CHAINS_SHA16},
            "chains_rows": {"expect": V01_CHAINS_ROWS,
                            "got": len(frozen_chains),
                            "pass": len(frozen_chains) == V01_CHAINS_ROWS},
            "anchor": anchor,
        },
        "counts": {
            "n_chains_total": len(chains),
            "complete": stats["complete"],
            "deadend_metabolite": stats["deadend_metabolite"],
            "deadend_gene": stats["deadend_gene"],
            "pruned_total": stats["deadend_metabolite"]
            + stats["deadend_gene"],
            "depth_distribution": depth_dist,
            "hop1_edges": stats["hop1_edges"],
            "distinct_microbes": len({c["microbe_id"] for c in chains}),
            "distinct_metabolites_in_chains": len({c["metabolite_id"]
                                                   for c in chains}),
            "distinct_genes": len({c["hop2_gene"] for c in chains
                                   if c["hop2_gene"]}),
            "distinct_pathways": len({c["hop3_pathway"] for c in chains
                                      if c["hop3_pathway"]}),
            "gutmgene_gene_domain": len(gene_domain),
        },
        "graphrag_slot": {"implemented": False, "reserved_for": "MT5"},
        "integrity_before": integrity_before,
        "determinism": {
            "sort_keys": "microbe_id,hop1_edge_id,metabolite_id,hop2_gene,"
                         "hop3_pathway",
            "no_run_time_in_output_rows": True,
        },
    }
    (sg_dir / "chains_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    integrity_after = verify_subgraph_integrity(sg_dir)
    manifest["integrity_after"] = integrity_after
    (sg_dir / "chains_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    c = manifest["counts"]
    print(f"[deepchain] 链 {c['n_chains_total']}：完整 {c['complete']} | "
          f"死端剪枝 {c['pruned_total']}（代谢物端 {c['deadend_metabolite']}"
          f" + 基因端 {c['deadend_gene']}）| 深度分布 {depth_dist}")
    print(f"  V01 三门禁全过 | 锚定 {anchor['n_pairs_used']}/"
          f"{anchor['n_pairs_frozen']} 对，violation=0")
    print(f"  子图完整性：{'通过' if integrity_after['machine_proof'] else '失败'}"
          f" | chains.tsv={sg_dir / 'chains.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
