#!/usr/bin/env python3
"""D2 机制假设卡片（关键路径终点件——交付用户实验室的假设包）。

规格=data/logs/hypothesize_spec_20261101.md §二（逐字对照；改规格须同步改
代码与测试）。

设计立场（为什么这么做）：
- 四票制优先级=全机械零 LLM 零拟合（2026-10-31 T2 定版"第四票=端点可测性
  +菌株可得性两子分+Borda 次级列"）：每票都是字符串/整数运算，可由三源
  独立重算——排序可复是 NC 方法学三件套之一。
- KG-CoI 步进机械验证比原版更严：验证也不用 LLM，纯查表"这一步在图上有
  边吗"，逐步支持物（边/PMID）照标，没撑着的照标。
- 21612 的 102 条=外部队队**佐证票**（降级裁定）：加分但"35% 用药+零个体
  协变量"警示句逐卡强制捆绑（grep 可查）。
- 空证对照臂：证据输入全置空重跑，四票中证据驱动的三票全零——证明票值由
  输入驱动、无隐藏常量。

红线：零关联分析；零 LLM；子图/主图零改动（只写子图目录内四个新文件）。
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
import tokenize
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from subgraph import read_tsv  # noqa: E402  复用 B 阶段 TSV 读取口径
from inference import KG_VERSION, K5_GATE_STATUS  # noqa: E402  强制三列同源
from deepchain import (  # noqa: E402  复用 V01 门禁（sha 同源）
    KEGG_SOURCE_ID, PATHWAY_PREDICATE, V01_EDGES_SHA16, gate_v01_edges)

# ============================================================
# 规格锁定常量（spec §二 逐字；改这里=改规格）
# ============================================================
EXT102_TSV = ROOT / ("data/observation/h1_htn_external_20261030_151856/"
                     "ext_21612_D_vs_H_discovery_20261030_151856.tsv")
EXT102_COHORT = "PRJEB21612"          # 102 条列表的来源队列（桥接用）
TABLE1_NAME = "Gut Microbe-Host Gene.csv"   # gutMGene 表1（菌株可得性源）
VOTE_BUCKETS = {"confirm", "extend"}  # 加分桶（conflict 不加分=fail-closed）

# 混杂警示句（spec §二.2 逐字；"35% 用药"可 grep——强制捆绑每卡）
CONFOUND_WARNING = ("21612 佐证票警示：~35% 用药+零个体协变量——"
                    "外部队列描述性旁证，不作因果解释、不回填 H1/H2")

TOP_N = 10  # top 榜行数

CARD_COLS = [
    # 链回显（自包含卡片，审计不必回查 chains.tsv）
    "chain_id", "microbe_id", "microbe_name", "metabolite_id",
    "metabolite_name", "hop2_gene", "hop2_gene_name", "hop3_pathway",
    "depth", "hop_terminus", "penalty", "pruned",
    # 四票（全机械）
    "vote1_ext_cohort", "confound_warning", "vote2_kg_depth",
    "vote3_gutmgene_cross", "evidence_syn_score", "ev3_gutmgene_n",
    "ev3_bugsigdb_n", "vote4_measurability",
    "endpoint_meas_ko", "strain_available", "votes_total", "borda_score",
    # KG-CoI 步进机械验证（纯查表）
    "step1_verified", "step2_verified", "step3_verified",
    "kg_coi_verified_steps", "kg_coi_total_steps", "kg_coi_rate",
    "step1_support", "step2_support", "step3_support",
    # 强制三列 + 溯源四键
    "kg_version", "evidence_granularity", "k5_gate_status",
    "source_id", "retrieved_at", "version", "knowledge_layer",
]

# 红线自查 grep 禁用串（spec §二.7——零 LLM/零拟合/零差异检验三组）
FORBIDDEN_PATTERNS = re.compile(
    r"llm|openai|anthropic|\bgpt\b|http|requests|urllib|socket|sklearn|"
    r"\bfit\b|t_test|ttest|mannwhitney|anova|regression|linregress|"
    r"permutation|scipy", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# 票①：21612 外部队列佐证（三判据任一命中，全部机械运算）
# ============================================================
def load_ext102_rows(path: Path) -> list[dict]:
    """102 条发现列表 → {token, name, family}（token 判种/属档——判据 c 前提；
    文件不存在=空证对照臂喂空表）。"""
    if not path.exists():
        return []
    out = []
    for r in read_tsv(path):
        last = r["feature"].split("|")[-1]
        token = last[:3] if last[:3] in ("s__", "g__") else ""
        out.append({"token": token,
                    "name": (last[3:] if token else last).replace("_", " "),
                    "family": r["family"]})
    return out


def build_vote1_matcher(ext_rows: list[dict],
                        bridge_rows: list[dict]) -> dict:
    """合并三判据索引：taxids（判据 a）+ 全名集合（b）+ 属级名集合（c）。"""
    name2taxid = {b["metaphlan_name"]: b["taxid"] for b in bridge_rows
                  if b["source"] == EXT102_COHORT and b.get("taxid")}
    taxids: set[str] = set()
    names: set[str] = set()
    genera: set[str] = set()
    for r in ext_rows:
        if r["name"] in name2taxid:
            taxids.add(name2taxid[r["name"]])           # (a)
        names.add(r["name"].casefold())                 # (b)
        if r["token"] == "g__" or r["family"] == "genus":
            genera.add(r["name"].casefold())            # (c) 属级特征名
    return {"taxids": taxids, "names": names, "genera": genera}


def vote1(microbe_id: str, microbe_name: str, matcher: dict) -> int:
    """票①：链菌 ∈ 102 条列表（判据 a/b/c 任一命中=1；属级链节点不吃种级
    发现的票——保守向，spec §二.2）。"""
    taxid = microbe_id.split(":", 1)[1] if ":" in microbe_id else microbe_id
    if taxid in matcher["taxids"]:
        return 1
    name = microbe_name.strip().casefold()
    if name in matcher["names"]:
        return 1
    # (c) 种⊆属：链菌名以"102 属级特征名+空格"开头才加分
    for g in matcher["genera"]:
        if name.startswith(g + " "):
            return 1
    return 0


# ============================================================
# 票③：gutMGene 三桶对账佐证（confirm/extend 加分，conflict 不加）
# ============================================================
def build_vote3_index(interaction_rows: list[dict]) -> dict:
    """三桶对账行 → {节点 id 集, 未锚定侧实体名集}（spec §二.2③）。"""
    node_ids: set[str] = set()
    free_names: set[str] = set()
    for r in interaction_rows:
        if r["bucket"] not in VOTE_BUCKETS:
            continue
        for side, entity in (("a", "entity_a"), ("b", "entity_b")):
            nid = r.get(f"matched_node_{side}", "") or ""
            if nid:
                node_ids.add(nid)               # (a) 锚定节点
            else:
                free_names.add(r[entity].strip().casefold())  # (b) 未锚定名
    return {"node_ids": node_ids, "free_names": free_names}


def vote3(chain: dict, idx: dict) -> int:
    """票③：对账行锚定到本链任一实体（菌/代谢物节点 id 或基因名）即=1。"""
    if chain["microbe_id"] in idx["node_ids"] \
            or chain["metabolite_id"] in idx["node_ids"]:
        return 1
    gene = (chain["hop2_gene_name"] or "").strip().casefold()
    if gene and gene in idx["free_names"]:
        return 1
    return 0


# ---- v0.4 票③多源综合（2026-11-08 用户批，调和和；见 vote3_redesign_research）----
import csv as _csv
import glob as _glob

VOTE3_SAT_K = 3          # 冻结：单源 3 条独立记录饱和
VOTE3_WEIGHTS = {"gutmgene": 1.0, "bugsigdb": 1.0}   # 冻结源权重
VOTE3_HARMONIC_MAX = 1.644934            # ζ(2)，归一上限


def build_vote3_counts(interaction_rows: list[dict]) -> dict:
    """对账行 → 实体→记录数（票①版的 set 升级为计数，兼容保 set 键）。"""
    node_n: dict[str, int] = {}
    name_n: dict[str, int] = {}
    for r in interaction_rows:
        if r["bucket"] not in VOTE_BUCKETS:
            continue
        for side, entity in (("a", "entity_a"), ("b", "entity_b")):
            nid = r.get(f"matched_node_{side}", "") or ""
            if nid:
                node_n[nid] = node_n.get(nid, 0) + 1
            else:
                k = r[entity].strip().casefold()
                name_n[k] = name_n.get(k, 0) + 1
    return {"node_n": node_n, "name_n": name_n}


def build_vote3_bugsigdb(disease_names: set[str],
                         bugsigdb_dir: Path | None = None) -> dict:
    """BugSigDB 边表 → {taxid: 记录数}（条件名与本病名精确匹配，冻结规则）。

    匹配规则（冻结）：LFS:COND:xxx 去前缀、下划线转空格、casefold 后
    与疾病节点名集**精确**相等——不做模糊/编辑距离（红线：禁模糊匹配）。
    """
    if bugsigdb_dir is None:
        hits = sorted(_glob.glob(str(ROOT / "data/external/bugsigdb_*")))
        bugsigdb_dir = Path(hits[-1]) if hits else None
    out: dict[str, int] = {}
    if bugsigdb_dir is None or not (bugsigdb_dir / "edges.tsv").exists():
        return out
    names = {n.casefold() for n in disease_names}
    with open(bugsigdb_dir / "edges.tsv", encoding="utf-8", newline="") as f:
        for r in _csv.DictReader(f, delimiter="\t"):
            cond = r.get("object", "")
            if not cond.startswith("LFS:COND:"):
                continue
            cname = cond.split("LFS:COND:", 1)[1].replace("_", " ").casefold()
            if cname in names:
                t = r.get("subject", "")
                if t.startswith("NCBITaxon:"):
                    out[t] = out.get(t, 0) + 1
    return out


def vote3_synth(chain: dict, idx: dict, bs: dict) -> tuple[int, float, int, int]:
    """多源综合票③：返回 (整数票, 综合分, gutmgene 记录数, bugsigdb 记录数)。

    调和和（冻结）：仅对**有命中的已接入源**按 w·s 降序 Σ/(i²·ζ(2))；
    未接入源跳位不占位；无命中=（0, 0.0, …）。
    """
    n_gm = idx["node_n"].get(chain["microbe_id"], 0) \
        + idx["node_n"].get(chain["metabolite_id"], 0)
    g = (chain["hop2_gene_name"] or "").strip().casefold()
    n_gm += idx["name_n"].get(g, 0) if g else 0
    n_bs = bs.get(chain["microbe_id"], 0)
    terms = []
    if n_gm:
        terms.append(VOTE3_WEIGHTS["gutmgene"] * min(1.0, n_gm / VOTE3_SAT_K))
    if n_bs:
        terms.append(VOTE3_WEIGHTS["bugsigdb"] * min(1.0, n_bs / VOTE3_SAT_K))
    terms.sort(reverse=True)
    score = round(sum(s / ((i + 1) ** 2) for i, s in enumerate(terms))
                  / VOTE3_HARMONIC_MAX, 6) if terms else 0.0
    return (int(bool(terms)), score, n_gm, n_bs)


# ============================================================
# 票④：可测性两子分（端点基因级可测 + 菌株可得性）
# ============================================================
def load_strain_index(gutmgene_dir: Path) -> dict:
    """gutMGene 表1 → {taxids, names}（Strain 列非空的行——菌株可得性源；
    目录不存在=空证对照臂）。"""
    taxids: set[str] = set()
    names: set[str] = set()
    fpath = gutmgene_dir / TABLE1_NAME
    if not fpath.exists():
        return {"taxids": taxids, "names": names}
    with open(fpath, encoding="gb18030", newline="") as f:
        for r in csv.DictReader(f):
            if not (r.get("Strain") or "").strip():
                continue
            tid = (r.get("Gut Microbiota NCBI ID") or "").strip()
            nm = (r.get("Gut Microbiota") or "").strip().casefold()
            if tid:
                taxids.add(tid)
            if nm:
                names.add(nm)
    return {"taxids": taxids, "names": names}


def strain_available(microbe_id: str, microbe_name: str, idx: dict) -> int:
    """菌株可得性 0/1：表1 同 taxid 行有菌株号（名称 casefold 兜底）。"""
    taxid = microbe_id.split(":", 1)[1] if ":" in microbe_id else microbe_id
    if taxid in idx["taxids"]:
        return 1
    if microbe_name.strip().casefold() in idx["names"]:
        return 1
    return 0


def endpoint_meas_ko(chain_id: str, ko_measurable_chains: set[str]) -> int:
    """端点基因级可测 0/1：D1 标注中该链任一队列 KO 机器非零（measurable）。"""
    return 1 if chain_id in ko_measurable_chains else 0


def load_ko_measurable(annotations: list[dict]) -> set[str]:
    """chain_annotations.tsv → ko_label=measurable 的链 id 集（任一队列）。"""
    return {r["chain_id"] for r in annotations if r["ko_label"] == "measurable"}


# ============================================================
# KG-CoI 步进机械验证（纯查表：这一步在图上有边吗）
# ============================================================
def build_edge_lookups(edges: list[dict]) -> dict:
    """子图边集 → 两个查找表：edge_id→边（step1 严格三元组核对）+
    无向节点对→边列表（step2 代谢物↔基因任一谓词任一方向）。"""
    by_id = {e["edge_id"]: e for e in edges}
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        by_pair[tuple(sorted((e["subject"], e["object"])))].append(e)
    return {"by_id": by_id, "by_pair": by_pair}


def build_kegg_pairs(v01_edges: Path) -> set[tuple[str, str]]:
    """V01 kegg_rest participates_in 边集（sha 门禁复用 deepchain——step3 查表）。"""
    return {(e["subject"], e["object"]) for e in gate_v01_edges(v01_edges)}


def kg_coi_steps(chain: dict, edge_lk: dict,
                 kegg_pairs: set[tuple[str, str]]) -> dict:
    """逐步机械验证（spec §二.3）：

    step1 菌→代谢物：edge_id 存在且 subject/object 与链行全等（防重排伪造）；
    step2 代谢物→基因：子图无向对 (metabolite_id, hop2_gene) 有边；
    step3 基因→通路：(hop2_gene, hop3_pathway) ∈ V01 kegg_rest 快照。
    支持物逐串照标（subgraph_edge+pmids / gutmgene_only / kegg 快照）；
    步不存在（链短于该步）= verified 0 + 支持物空——没撑着的照标。
    """
    # —— step1
    e1 = edge_lk["by_id"].get(chain["hop1_edge_id"])
    if e1 is not None and e1["subject"] == chain["microbe_id"] \
            and e1["object"] == chain["metabolite_id"]:
        s1, v1 = f"subgraph_edge:{e1['edge_id']}|pmids:{e1.get('pmids', '')}", 1
    else:
        s1, v1 = "", 0
    # —— step2（链无 hop2 基因=步不存在）
    if chain["hop2_gene"]:
        hits = edge_lk["by_pair"].get(
            tuple(sorted((chain["metabolite_id"], chain["hop2_gene"]))), [])
        if hits:
            best = sorted(hits, key=lambda e: e["edge_id"])[0]  # 确定性取首
            s2 = f"subgraph_edge:{best['edge_id']}|pmids:{best.get('pmids', '')}"
            v2 = 1
        else:
            # 子图无此边：gutMGene 表3 证据照列但不计 verified（只认图内边）
            refs = chain.get("hop2_gutmgene_refs", "")
            s2 = f"gutmgene_only:{refs}" if refs else ""
            v2 = 0
    else:
        s2, v2 = "", 0
    # —— step3（链无通路=步不存在）
    if chain["hop3_pathway"]:
        ok = (chain["hop2_gene"], chain["hop3_pathway"]) in kegg_pairs
        s3 = (f"kegg_rest_snapshot:{chain['hop3_pathway']}" if ok else "")
        v3 = 1 if ok else 0
    else:
        s3, v3 = "", 0
    depth = int(chain["depth"])
    return {"step1_verified": v1, "step2_verified": v2, "step3_verified": v3,
            "kg_coi_verified_steps": v1 + v2 + v3,
            "kg_coi_total_steps": depth,
            "kg_coi_rate": format((v1 + v2 + v3) / depth, ".6f"),
            "step1_support": s1, "step2_support": s2, "step3_support": s3}


# ============================================================
# Borda 次级列（并列共享点变体：Σ_票 该票取值严格低于本卡的卡数）
# ============================================================
def borda_scores(cards: list[dict]) -> list[int]:
    """对四票列各算"严格低于"计数再求和——机械整合四票分布差异。

    为什么用"严格低于"而不是名次差：并列卡共享同一分（无并列仲裁），
    全体确定性可复算；一次排序累计 O(n log n)，无随机无拟合。
    """
    votes = ["vote1_ext_cohort", "vote2_kg_depth",
             "vote3_gutmgene_cross", "vote4_measurability"]
    total = [0] * len(cards)
    for col in votes:
        vals = [int(c[col]) for c in cards]
        counts = Counter(vals)
        lower: dict[int, int] = {}
        run = 0
        for v in sorted(counts):
            lower[v] = run
            run += counts[v]
        for i, v in enumerate(vals):
            total[i] += lower[v]
    return total


# ============================================================
# 完整性校验（与前两模块同法——"只增不改"机器证）
# ============================================================
def verify_subgraph_integrity(sg_dir: Path) -> dict:
    frozen = {}
    for line in (sg_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
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


# ============================================================
# 卡片装配（证据输入参数化——空证对照臂=同一函数喂空输入）
# ============================================================
def assemble_cards(chains: list[dict], edges: list[dict],
                   kegg_pairs: set[tuple[str, str]],
                   ko_measurable: set[str],
                   matcher: dict, vote3_idx: dict,
                   strain_idx: dict, vote3_bs: dict | None = None) -> list[dict]:
    """每链一卡（spec §二.1-3）：四票+KG-CoI+三列四键；排序 chain_id。

    证据输入全部走参量——空证对照臂=同函数喂全空索引（票值必须全零），
    这正是"无隐藏常量"的机械证。
    """
    edge_lk = build_edge_lookups(edges)
    hop1 = edge_lk["by_id"]
    cards = []
    missing_keys: list[str] = []
    for ch in chains:
        e = hop1.get(ch["hop1_edge_id"], {})
        four_keys = {
            "source_id": f"{e.get('run_batch', '')}|{ch['hop1_edge_id']}",
            "retrieved_at": e.get("retrieved_at", ""),
            "version": KG_VERSION,
            "knowledge_layer": e.get("knowledge_layer", ""),
        }
        granularity = e.get("evidence_granularity", "")
        if not all(four_keys.values()) or not granularity:
            missing_keys.append(ch["chain_id"])
        m_ko = endpoint_meas_ko(ch["chain_id"], ko_measurable)
        s_str = strain_available(ch["microbe_id"], ch["microbe_name"],
                                 strain_idx)
        v1 = vote1(ch["microbe_id"], ch["microbe_name"], matcher)
        v2 = int(ch["depth"])
        v3, ev_score, n_gm, n_bs = vote3_synth(
            ch, vote3_idx, vote3_bs or {})
        v4 = m_ko + s_str
        cards.append({
            "chain_id": ch["chain_id"], "microbe_id": ch["microbe_id"],
            "microbe_name": ch["microbe_name"],
            "metabolite_id": ch["metabolite_id"],
            "metabolite_name": ch["metabolite_name"],
            "hop2_gene": ch["hop2_gene"], "hop2_gene_name": ch["hop2_gene_name"],
            "hop3_pathway": ch["hop3_pathway"], "depth": ch["depth"],
            "hop_terminus": ch["hop_terminus"], "penalty": ch["penalty"],
            "pruned": ch["pruned"],
            "vote1_ext_cohort": v1, "confound_warning": CONFOUND_WARNING,
            "vote2_kg_depth": v2, "vote3_gutmgene_cross": v3,
            "evidence_syn_score": ev_score, "ev3_gutmgene_n": n_gm,
            "ev3_bugsigdb_n": n_bs,
            "vote4_measurability": v4, "endpoint_meas_ko": m_ko,
            "strain_available": s_str,
            "votes_total": v1 + v2 + v3 + v4,
            **kg_coi_steps(ch, edge_lk, kegg_pairs),
            "kg_version": KG_VERSION, "evidence_granularity": granularity,
            "k5_gate_status": K5_GATE_STATUS, **four_keys,
        })
    if missing_keys:
        raise SystemExit(f"[红线] 溯源四键/粒度列缺失 {len(missing_keys)} 链"
                         f"（如 {missing_keys[:3]}）——fail-closed 拒落盘")
    cards.sort(key=lambda c: c["chain_id"])
    for c, b in zip(cards, borda_scores(cards)):
        c["borda_score"] = b
    return cards


def rank_key(c: dict) -> tuple:
    """top 榜排序键（spec §二.5 依次）：总票↓ Borda↓ 验证率↓ 罚分↑ id↑。

    读 TSV 回读时值全为字符串——这里显式转型，同一键既排内存卡也排
    回读卡（测试对拍需要）。
    """
    # v0.4：evidence_syn_score 进第二键；缺列（旧版卡片表）按 0——
    # 向后兼容读 golden v5 时代产物，排序退化为旧四键语义
    return (-int(c["votes_total"]),
            -float(c.get("evidence_syn_score", 0) or 0),
            -int(c["borda_score"]),
            -float(c["kg_coi_rate"]), float(c["penalty"]), c["chain_id"])


# ============================================================
# 红线自查（零 LLM/零拟合/零差异检验——grep 代码路径）
# ============================================================
def code_only_text(source: str) -> str:
    """剥掉注释与字符串字面量后的纯代码文本。

    为什么先剥再 grep：注释/文档串里的"零 LLM""回归判定"等治理用语会
    假命中禁用词表——红线 grep 要查的是**代码路径**真的没调这些东西，
    tokenize 级剥离是机械无歧义的实现。
    """
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            out.append(tok.string)
    return " ".join(out)


def red_line_grep() -> dict:
    """对本模块+inference 模块的纯代码文本 grep 禁用串（spec §二.7）。

    命中即 fail——排序/标注路径零 LLM 调用、零拟合库、零检验函数。
    """
    hits = []
    for mod in (Path(__file__).resolve(),
                Path(__file__).resolve().parent / "inference.py"):
        code = code_only_text(mod.read_text(encoding="utf-8"))
        m = FORBIDDEN_PATTERNS.search(code)
        if m:
            hits.append(f"{mod.name}:{m.group(0)}")
    return {"pattern": FORBIDDEN_PATTERNS.pattern, "hits": hits,
            "pass": not hits}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D2 机制假设卡片（四票制全机械）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--gutmgene-dir", type=Path, default=None,
                    help="gutMGene 目录（缺省=注册表）")
    ap.add_argument("--v01-edges", type=Path, default=None,
                    help="V01 边表（缺省=注册表 v01_edges）")
    # W2.1 疾病档案：ext102（外部队列佐证源）归疾病档案管——换病不再张冠李戴
    ap.add_argument("--disease", default=None,
                    help="疾病档案 key（configs/diseases/；档案给 ext102）")
    ap.add_argument("--ext102", type=Path, default=None,
                    help="外证 102 条列表（不传且无 --disease=用原默认 HTN 文件）")
    ap.add_argument("--cohort", default=None,
                    help="队列档案 key（configs/cohorts/）")
    # 阶段 3 轨A1：空证对照臂正式参数（进程内直算——只换票①匹配器，
    # 免旧手工编排的"复制目录+掏空重跑"；产物与重跑版逐字节等价由
    # golden 验收证明 sha=3d4fa018…）
    ap.add_argument("--null-arm-input", type=Path, default=None,
                    help="掏空的外证文件（仅表头）——提供时启用空证臂")
    ap.add_argument("--null-arm-out", type=Path, default=None,
                    help="空证臂卡片输出文件（如 null_control_arm_cards.tsv）")
    ap.add_argument("--bridge-tsv", type=Path, default=None,
                    help="菌名桥 TSV（缺省=cohort 档案）")
    args = ap.parse_args(argv)
    import disease_profile  # noqa: E402  同目录（本文件头部已挂 sys.path）
    # 原默认=EXT102_TSV（HTN 外证文件），一字不改——golden 等价前提；
    # 档案路径为仓内相对路径，resolve 内部锚定 ROOT（与 EXT102_TSV 同源）
    args = disease_profile.resolve(args, {"ext102": EXT102_TSV})
    import data_source_profile  # noqa: E402  W2.2 数据腿注册表
    import cohort_profile  # noqa: E402  W2.4 队列档案
    args = cohort_profile.resolve(
        args, {"bridge_tsv": ROOT / "data/logs/name_bridge_20261101/name_bridge_cohorts.tsv"})
    args = data_source_profile.resolve(
        args, {"gutmgene_dir": ("gutmgene", False),
               "v01_edges": ("v01_edges", False)},
        {"gutmgene_dir": "<GUTMGENE_LOCAL>",
         "v01_edges": str(ROOT / "archive/Version01/data/merged/candidate_v3/merged_edges.tsv")})
    sg_dir: Path = args.subgraph_dir

    integrity_before = verify_subgraph_integrity(sg_dir)
    chains = read_tsv(sg_dir / "chains.tsv")
    edges = read_tsv(sg_dir / "merged_edges.tsv")
    annotations = read_tsv(sg_dir / "chain_annotations.tsv")  # D1 法定前置
    kegg_pairs = build_kegg_pairs(args.v01_edges)  # sha 门禁在装载内 fail-closed

    # —— 证据索引（三源）
    matcher = build_vote1_matcher(load_ext102_rows(args.ext102),
                                  read_tsv(args.bridge_tsv))
    vote3_idx = build_vote3_index(read_tsv(sg_dir / "interaction_report.tsv"))
    vote3_counts = build_vote3_counts(
        read_tsv(sg_dir / "interaction_report.tsv"))
    # 病名集：供 BugSigDB 条件精确匹配；测试夹具的最小目录可能无节点表——
    # 缺件时空集（BugSigDB 源自然零命中），生产路径恒有该件
    _dis_names = ({n["name"] for n in read_tsv(sg_dir / "merged_nodes.tsv")
                   if n.get("category") == "Disease" and n.get("name")}
                  if (sg_dir / "merged_nodes.tsv").exists() else set())
    vote3_bs = build_vote3_bugsigdb(_dis_names)
    strain_idx = load_strain_index(args.gutmgene_dir)
    ko_measurable = load_ko_measurable(annotations)

    # —— 正式臂 + 空证对照臂（同一装配函数，证据索引全空）
    cards = assemble_cards(chains, edges, kegg_pairs, ko_measurable,
                           matcher, vote3_counts, strain_idx, vote3_bs)
    empty_matcher = {"taxids": set(), "names": set(), "genera": set()}
    null_cards = assemble_cards(
        chains, edges, kegg_pairs, set(), empty_matcher,
        {"node_n": {}, "name_n": {}}, {"taxids": set(), "names": set()}, {})
    # 空证臂机械断言：证据驱动三票全零（vote2=链结构属性不随证据变）
    bad = [c["chain_id"] for c in null_cards
           if c["vote1_ext_cohort"] or c["vote3_gutmgene_cross"]
           or c["vote4_measurability"]]
    if bad:
        raise SystemExit(f"[红线] 空证对照臂非零票 {len(bad)} 卡——存在隐藏"
                         f"常量，拒落盘")

    # —— 落盘（全量卡 / top 榜 / 空证臂）
    write_tsv(sg_dir / "hypothesis_cards.tsv", cards, CARD_COLS)
    write_tsv(sg_dir / "hypothesis_cards_null_control.tsv", null_cards,
              CARD_COLS)
    top = sorted(cards, key=rank_key)[:TOP_N]
    write_tsv(sg_dir / "top_hypotheses.tsv", top, CARD_COLS)

    # —— 空证对照臂（阶段 3 轨A1 正式参数）：与主臂同装配函数，
    # 唯一差异=票①匹配器来自掏空的外证文件——vote3/vote4 索引保持真实
    # （这正是"掏空 102 条列表后 7 票全消、6 票仍在"的口径）
    if args.null_arm_input and args.null_arm_out:
        arm_matcher = build_vote1_matcher(
            load_ext102_rows(args.null_arm_input),
            read_tsv(args.bridge_tsv))
        arm_cards = assemble_cards(chains, edges, kegg_pairs, ko_measurable,
                                   arm_matcher, vote3_counts, strain_idx,
                                   vote3_bs)
        args.null_arm_out.parent.mkdir(parents=True, exist_ok=True)
        write_tsv(args.null_arm_out, arm_cards, CARD_COLS)
        n7 = sum(1 for c in arm_cards if int(c["votes_total"]) >= 7)
        print(f"[null-arm] 空证臂 {len(arm_cards)} 卡 → {args.null_arm_out}"
              f"（≥7 票 {n7} 张——应全消）")

    # —— 机器计数入 manifest（数字机器输出制）
    def dist(col: str) -> dict[str, int]:
        d: dict[str, int] = defaultdict(int)
        for c in cards:
            d[str(c[col])] += 1
        return dict(sorted(d.items()))
    coi_dist = defaultdict(int)
    for c in cards:
        coi_dist[f"{c['kg_coi_verified_steps']}/{c['kg_coi_total_steps']}"] += 1
    sub_manifest = json.loads((sg_dir / "manifest.json").read_text(
        encoding="utf-8"))
    grep_check = red_line_grep()
    manifest = {
        "module": "src/06_tool/hypothesize.py",
        "spec": "data/logs/hypothesize_spec_20261101.md#二",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "subgraph_dir": str(sg_dir),
            "subgraph_version": sub_manifest.get("subgraph_version"),
            "ext102": str(args.ext102),
            "gutmgene_dir": str(args.gutmgene_dir),
        },
        "inputs_sha256": {
            "chains": sha256_file(sg_dir / "chains.tsv"),
            "chain_annotations": sha256_file(sg_dir / "chain_annotations.tsv"),
            "interaction_report": sha256_file(sg_dir / "interaction_report.tsv"),
            "ext102": sha256_file(args.ext102),
            "bridge": sha256_file(args.bridge_tsv),
            "gutmgene_table1": sha256_file(args.gutmgene_dir / TABLE1_NAME),
            "v01_edges": sha256_file(args.v01_edges),
        },
        "gates": {
            "v01_edges_sha16": {"expect": V01_EDGES_SHA16,
                                "got": sha256_file(args.v01_edges)[:16],
                                "pass": sha256_file(args.v01_edges)[:16]
                                == V01_EDGES_SHA16},
        },
        "counts": {
            "n_cards": len(cards),
            "n_null_control": len(null_cards),
            "vote_dist": {col: dist(col) for col in
                          ("vote1_ext_cohort", "vote2_kg_depth",
                           "vote3_gutmgene_cross", "vote4_measurability",
                           "votes_total")},
            "kg_coi_rate_dist": dict(sorted(coi_dist.items())),
            "top_hypotheses": [
                {"chain_id": c["chain_id"],
                 "microbe": c["microbe_name"],
                 "metabolite": c["metabolite_name"],
                 "gene": c["hop2_gene_name"],
                 "pathway": c["hop3_pathway"],
                 "votes_total": c["votes_total"],
                 "kg_coi_rate": c["kg_coi_rate"]}
                for c in top],
        },
        "red_lines": {
            "grep_zero_llm_zero_fitting_zero_testing": grep_check,
            "null_control_assert": {"evidence_votes_all_zero": True,
                                    "n_checked": len(null_cards)},
            "zero_modification": "子图旧件 sha 与 SHA256SUMS 全等（integrity 字段）",
        },
        "integrity_before": integrity_before,
        "determinism": {
            "cards_sort": "chain_id",
            "top_sort": "votes_total desc, borda desc, kg_coi_rate desc, "
                        "penalty asc, chain_id asc",
            "no_run_time_in_output_rows": True,
        },
    }
    (sg_dir / "hypothesis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    integrity_after = verify_subgraph_integrity(sg_dir)
    manifest["integrity_after"] = integrity_after
    (sg_dir / "hypothesis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[hypothesize] 卡片 {len(cards)} 张 | 空证对照 {len(null_cards)} 张"
          f"（证据票全零断言过）")
    print(f"  票分布 votes_total={manifest['counts']['vote_dist']['votes_total']}")
    print(f"  红线 grep（零 LLM/零拟合/零检验）："
          f"{'通过' if grep_check['pass'] else '失配 ' + str(grep_check['hits'])}")
    print(f"  —— top{TOP_N} ——")
    for c in top:
        print(f"  {c['microbe_name']} → {c['metabolite_name']} → "
              f"{c['hop2_gene_name']} → {c['hop3_pathway']} | 票 "
              f"{c['votes_total']} | 验证率 {c['kg_coi_rate']}")
    print(f"  产物={sg_dir / 'hypothesis_cards.tsv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
