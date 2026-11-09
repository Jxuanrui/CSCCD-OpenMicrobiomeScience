#!/usr/bin/env python3
"""interact.py + deepchain.py 单元测试（C 阶段规格 §三.4 ≥15 项）。

测试基建设计：一个微型"子图+gutMGene+V01 资产"事实集覆盖全部判定分支——
三桶边界（confirm/conflict/extend/unmatched/混合反向/单侧锚定）、链拼接
正确性、死端罚分行为、V01 门禁 fail-closed、锚定拦截、GraphRAG 占位零
LLM、确定性双跑逐字节相等。

为什么用文件级事实集而不是 mock：两个模块的消费单元是磁盘文件（TSV/CSV/
dmp），端到端造件最接近真实运行路径——mock dict 会绕过编码（gb18030）与
解析分支，测不到真实坑。
"""
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deepchain import (  # noqa: E402
    DEAD_END_PENALTY, HOP1_PREDICATES, assemble_chains, chain_id,
    gate_v01_chains, graphrag_explain_slot, load_hop2, load_hop3,
    load_gene_domain, main as deepchain_main)
from interact import (  # noqa: E402
    main as interact_main, classify_row, build_node_index, build_pair_edges)


# ============================================================
# 事实集工厂
# ============================================================
ECOLS = ["subject", "predicate", "object", "source_type", "evidence_tier",
         "pmids", "years", "support_count", "confidence", "polarity",
         "last_updated", "relation_status", "canonical_view", "source_ref",
         "curator", "source_id", "retrieved_at", "version", "knowledge_layer",
         "edge_id", "run_batch", "evidence_granularity", "provenance",
         "causality", "causality_basis", "causality_diag"]


def write_tsv(path: Path, cols: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def edge(subj, pred, obj, eid, polarity="positive", causality="causal"):
    return {"subject": subj, "predicate": pred, "object": obj,
            "polarity": polarity, "edge_id": eid, "causality": causality,
            "support_count": "1", "confidence": "0.9"}


def make_subgraph(base: Path) -> Path:
    """微型 HTN 子图：两菌三代谢物，覆盖 hop1 全部谓词分支。

    节点：NCBITaxon:817 Bacteroides fragilis｜NCBITaxon:562 Escherichia coli
    代谢物：Indole（无基因=gutMGene 表3 不含→死端①）
           Butyrate（有基因无通路→死端②）
           Arachidonic Acid（有基因有通路→完整三跳）
    """
    sg = base / "subgraph"
    sg.mkdir(parents=True)
    nodes = [
        {"id": "NCBITaxon:817", "name": "Bacteroides fragilis",
         "category": "Species", "aliases": "B. fragilis", "xrefs": "",
         "tax_rank": "species", "knowledge_layer": "local_kg"},
        {"id": "NCBITaxon:562", "name": "Escherichia coli",
         "category": "Species", "aliases": "E. coli", "xrefs": "",
         "tax_rank": "species", "knowledge_layer": "local_kg"},
        {"id": "MESH:C001", "name": "Indole", "category": "Metabolite",
         "aliases": "", "xrefs": "", "tax_rank": "",
         "knowledge_layer": "local_kg"},
        {"id": "MESH:C002", "name": "Butyrate", "category": "Metabolite",
         "aliases": "butyric acid", "xrefs": "", "tax_rank": "",
         "knowledge_layer": "local_kg"},
        {"id": "MESH:C003", "name": "Arachidonic Acid", "category": "Metabolite",
         "aliases": "", "xrefs": "", "tax_rank": "",
         "knowledge_layer": "local_kg"},
        {"id": "MESH:D006973", "name": "hypertension", "category": "Disease",
         "aliases": "", "xrefs": "", "tax_rank": "",
         "knowledge_layer": "local_kg"},
    ]
    write_tsv(sg / "merged_nodes.tsv",
              ["id", "name", "category", "aliases", "xrefs", "tax_rank",
               "knowledge_layer"], nodes)
    edges = [
        # hop1 合格边：菌→代谢物 produces/biotransforms
        edge("NCBITaxon:817", "produces", "MESH:C003", "E1"),
        edge("NCBITaxon:817", "biotransforms", "MESH:C002", "E2",
             causality="correlated"),
        edge("NCBITaxon:562", "produces", "MESH:C001", "E3"),
        # hop1 排除边：谓词不符（consumes/affects 不进链）
        edge("NCBITaxon:562", "consumes", "MESH:C003", "E4"),
        edge("NCBITaxon:817", "affects", "MESH:D006973", "E5"),
        # 三桶用边：conflict 样例（817 consumes C002=反向证据）
        edge("NCBITaxon:817", "consumes", "MESH:C002", "E6",
             causality="correlated"),
        # 三桶用边：confirm 样例（817 produces C003=生产向相合）
        # （与 E1 同对——gutMGene 行锚定 817×C003 时命中 E1）
    ]
    write_tsv(sg / "merged_edges.tsv", ECOLS, edges)
    write_tsv(sg / "relation_assertions.tsv",
              ["assertion_id", "edge_id", "pmid", "subject_id", "predicate",
               "object_id"], [{"assertion_id": "RA-x", "edge_id": "E1",
                               "pmid": "1", "subject_id": "NCBITaxon:817",
                               "predicate": "produces",
                               "object_id": "MESH:C003"}])
    write_tsv(sg / "conflicts.tsv", ["subject", "object"], [])
    write_tsv(sg / "corrections.tsv", ["edge_id_new"], [])
    (sg / "manifest.json").write_text(json.dumps(
        {"subgraph_version": "test", "counts": {}}, ensure_ascii=False),
        encoding="utf-8")
    (sg / "subgraph_report.md").write_text("# test\n", encoding="utf-8")
    # SHA256SUMS 冻结清单（完整性校验对象）
    sums = []
    for p in sorted(sg.iterdir()):
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            sums.append(f"{h}  {p.name}")
    (sg / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return sg


def make_gutmgene(base: Path) -> Path:
    """微型 gutMGene 三表（gb18030 真实编码口径）。

    表2（菌↔代谢物，DOID 过滤后 3 行）：
      817×Arachidonic Acid=both 命中+子图有生产边→confirm
      817×Butyrate=both 命中+子图只有 consumes 边→conflict
      99999 名菌×Indole=单侧命中（代谢物侧）→extend
    表3（代谢物↔基因，全表不过滤）：
      Arachidonic Acid→基因 G1(100) 有通路 / G2(200) 无通路→死端②
      Butyrate→G2(200) 无通路→死端②
      Indole 不在表3→死端①
    表1（菌↔基因）：G1 在表1（基因域），G3(300) 只在表1（域内但无链）
    """
    gm = base / "gutmgene"
    gm.mkdir()
    t1 = [("Index", "PMID", "Gut Microbiota", "Gut Microbiota NCBI ID",
           "Gene", "Gene ID", "DOID", "Associative mode"),
          ("1", "111", "Bacteroides fragilis", "817", "GENE1", "100",
           "DOID:10763", "causally"),
          ("2", "111", "Some microbe", "99999", "GENE3", "300",
           "DOID:10763", "correlatively")]
    t2 = [("Index", "PMID", "Gut Microbiota", "Gut Microbiota NCBI ID",
           "Metabolite", "Metabolite ChEBI", "Metabolite KEGG", "DOID",
           "Associative mode"),
          ("10", "222", "Bacteroides fragilis", "817", "Arachidonic Acid",
           "CHEBI:15843", "C00219", "DOID:10763", "causally"),
          ("11", "222", "Bacteroides fragilis", "817", "Butyrate",
           "CHEBI:15740", "", "DOID:10763", "causally"),
          ("12", "333", "Nowhere bug", "99999", "Indole", "", "",
           "DOID:10763", "correlatively"),
          ("13", "444", "Out of scope bug", "88888", "Ghost metabolite",
           "", "", "DOID:10763", "causally")]
    t3 = [("Index", "PMID", "Metabolite", "Metabolite ChEBI",
           "Metabolite KEGG", "Gene", "Gene ID", "DOID"),
          ("20", "555", "Arachidonic Acid", "CHEBI:15843", "C00219",
           "GENE1", "100", ""),
          ("21", "555", "Arachidonic Acid", "CHEBI:15843", "C00219",
           "GENE2", "200", ""),
          ("22", "666", "Butyrate", "CHEBI:15740", "", "GENE2", "200", ""),
          ("23", "777", "butyric acid", "CHEBI:15740", "", "GENE2", "200", "")]
    for fname, rows in (("Gut Microbe-Host Gene.csv", t1),
                        ("Gut Microbe-Microbial metabolite.csv", t2),
                        ("Microbial metabolite-Host Gene.csv", t3)):
        with open(gm / fname, "w", encoding="gb18030", newline="") as f:
            w = csv.writer(f)
            w.writerows(rows)
    return gm


def make_v01(base: Path) -> tuple[Path, Path]:
    """微型 V01 资产：先按真实公式造好，再回填 deepchain 门禁常量——
    门禁逻辑测的是"登记值不等→拒跑"，用动态替换常量保持测试文件真实。"""
    v01_dir = base / "v01"
    v01_dir.mkdir()
    edges = [
        {"subject": "NCBIGene:100", "predicate": "participates_in",
         "object": "KEGG:hsa00010", "source_id": "kegg_rest"},
        {"subject": "NCBIGene:100", "predicate": "participates_in",
         "object": "KEGG:hsa05010", "source_id": "kegg_rest"},
        {"subject": "NCBIGene:300", "predicate": "participates_in",
         "object": "KEGG:hsa05010", "source_id": "kegg_rest"},
        {"subject": "NCBIGene:100", "predicate": "participates_in",
         "object": "KEGG:hsa99999", "source_id": "other_source"},
    ]
    write_tsv(v01_dir / "merged_edges.tsv",
              ["subject", "predicate", "object", "source_id"], edges)
    chains = [
        {"microbe_name": "m", "subject": "NCBITaxon:1", "metabolite":
         "CHEBI:1", "hop1": "produces", "subject_g": "CHEBI:1",
         "gene": "NCBIGene:100", "pathway": "KEGG:hsa00010"},
        {"microbe_name": "m", "subject": "NCBITaxon:1", "metabolite":
         "CHEBI:1", "hop1": "produces", "subject_g": "CHEBI:1",
         "gene": "NCBIGene:100", "pathway": "KEGG:hsa05010"},
        {"microbe_name": "m", "subject": "NCBITaxon:1", "metabolite":
         "CHEBI:1", "hop1": "produces", "subject_g": "CHEBI:1",
         "gene": "NCBIGene:300", "pathway": "KEGG:hsa05010"},
    ]
    write_tsv(v01_dir / "mechanism_chains.tsv",
              ["microbe_name", "subject", "metabolite", "hop1", "subject_g",
               "gene", "pathway"], chains)
    return v01_dir / "merged_edges.tsv", v01_dir / "mechanism_chains.tsv"


@pytest.fixture()
def ctx(tmp_path: Path, monkeypatch):
    """一次建好全部输入并把 deepchain 门禁常量替换为事实集的真实指纹。"""
    import deepchain
    sg = make_subgraph(tmp_path)
    gm = make_gutmgene(tmp_path)
    v01e, v01c = make_v01(tmp_path)
    sha_e = hashlib.sha256(v01e.read_bytes()).hexdigest()[:16]
    sha_c = hashlib.sha256(v01c.read_bytes()).hexdigest()[:16]
    n_rows = len(v01c.read_text(encoding="utf-8").splitlines()) - 1
    monkeypatch.setattr(deepchain, "V01_EDGES_SHA16", sha_e)
    monkeypatch.setattr(deepchain, "V01_CHAINS_SHA16", sha_c)
    monkeypatch.setattr(deepchain, "V01_CHAINS_ROWS", n_rows)
    return {"sg": sg, "gm": gm, "v01e": v01e, "v01c": v01c,
            "deepchain": deepchain}


def run_interact(ctx) -> dict:
    rc = interact_main(["--subgraph-dir", str(ctx["sg"]),
                        "--gutmgene-dir", str(ctx["gm"]),
                        "--doid", "DOID:10763"])
    assert rc == 0
    rows = list(csv.DictReader(
        open(ctx["sg"] / "interaction_report.tsv", encoding="utf-8"),
        delimiter="\t"))
    man = json.loads((ctx["sg"] / "interaction_manifest.json")
                     .read_text(encoding="utf-8"))
    return {"rows": rows, "manifest": man}


def run_deepchain(ctx) -> dict:
    rc = ctx["deepchain"].main(
        ["--subgraph-dir", str(ctx["sg"]), "--gutmgene-dir", str(ctx["gm"]),
         "--v01-edges", str(ctx["v01e"]), "--v01-chains", str(ctx["v01c"])])
    assert rc == 0
    rows = list(csv.DictReader(
        open(ctx["sg"] / "chains.tsv", encoding="utf-8"), delimiter="\t"))
    man = json.loads((ctx["sg"] / "chains_manifest.json")
                     .read_text(encoding="utf-8"))
    return {"rows": rows, "manifest": man}


# ============================================================
# C2 三桶对账（interact.py）
# ============================================================
def test_bucket_confirm_production_agree(ctx):
    """三桶边界①：两侧命中+子图生产向连边（E1 produces）→confirm
    (production_agree)。"""
    r = run_interact(ctx)
    row = next(x for x in r["rows"] if x["entity_b"] == "Arachidonic Acid"
               and x["table"].endswith("metabolite.csv"))
    assert row["bucket"] == "confirm"
    assert row["direction_verdict"] == "production_agree"
    assert row["matched_node_a"] == "NCBITaxon:817"
    assert row["matched_node_b"] == "MESH:C003"
    assert row["subgraph_predicates"] == "produces"


def test_bucket_conflict_consumption(ctx):
    """三桶边界②：两侧命中+子图存在反向证据边（consumes）→conflict——
    生产向 biotransforms 与消耗向 consumes 并存=mixed_opposed（警示）。"""
    r = run_interact(ctx)
    row = next(x for x in r["rows"] if x["entity_b"] == "Butyrate"
               and x["table"].endswith("metabolite.csv"))
    assert row["bucket"] == "conflict"
    assert row["direction_verdict"] == "mixed_opposed"
    assert row["subgraph_predicates"] == "biotransforms;consumes"
    assert set(row["subgraph_edge_ids"].split(";")) == {"E2", "E6"}


def test_bucket_conflict_mixed_and_negative_polarity():
    """三桶边界③：生产边+反向边并存=mixed_opposed；仅 negative 极性的
    生产边也算反向证据（all_opposed）—— polarity 与谓词双通道判反向。"""
    nodes = [
        {"id": "NCBITaxon:817", "name": "Bacteroides fragilis",
         "category": "Species", "aliases": "", "xrefs": "", "tax_rank": "",
         "knowledge_layer": ""},
        {"id": "X:1", "name": "Bug one", "category": "Metabolite",
         "aliases": "", "xrefs": "", "tax_rank": "", "knowledge_layer": ""},
        {"id": "X:2", "name": "Bug two", "category": "Metabolite",
         "aliases": "", "xrefs": "", "tax_rank": "", "knowledge_layer": ""},
    ]
    edges = [edge("NCBITaxon:817", "produces", "X:1", "M1"),
             edge("NCBITaxon:817", "consumes", "X:1", "M2"),
             edge("NCBITaxon:817", "produces", "X:2", "M3",
                  polarity="negative")]
    idx = build_node_index(nodes)
    pe = build_pair_edges(edges)
    r1 = classify_row({"entity_a": "Bacteroides fragilis",
                       "entity_b": "Bug one"}, idx, pe)
    assert r1["bucket"] == "conflict"
    assert r1["direction_verdict"] == "mixed_opposed"
    assert r1["note"] == "反向证据边 1/2"
    r2 = classify_row({"entity_a": "Bacteroides fragilis",
                       "entity_b": "Bug two"}, idx, pe)
    assert r2["bucket"] == "conflict"
    assert r2["direction_verdict"] == "all_opposed"  # 仅 polarity=negative


def test_bucket_extend_and_unmatched(ctx):
    """三桶边界④：单侧锚定（代谢物命中、菌不在子图）→extend=候选新知；
    两侧都不命中→unmatched 披露不入桶。"""
    r = run_interact(ctx)
    ext = next(x for x in r["rows"] if x["entity_a"] == "Nowhere bug")
    assert ext["bucket"] == "extend"
    assert ext["matched_node_b"] == "MESH:C001" and ext["matched_node_a"] == ""
    unm = next(x for x in r["rows"] if x["entity_a"] == "Out of scope bug")
    assert unm["bucket"] == "unmatched"
    # 表1（菌↔基因）单侧命中也走 extend——基因不在子图=新知候选
    t1 = next(x for x in r["rows"] if x["table"].endswith("Host Gene.csv")
              and x["entity_a"] == "Bacteroides fragilis")
    assert t1["bucket"] == "extend" and t1["matched_node_a"] == "NCBITaxon:817"


def test_interact_doid_token_filter(ctx):
    """DOID 过滤=token 级精确：表2 有 DOID:10763 命中行，非目标行不进台账。"""
    r = run_interact(ctx)
    # 事实集表2 共 4 行全带 DOID:10763；表1 2 行；表3 不参与 DOID 过滤（无该列行）
    n_t2 = sum(1 for x in r["rows"] if x["table"].endswith("metabolite.csv"))
    assert n_t2 == 4
    assert all("DOID:10763" in x["doid"] for x in r["rows"])
    assert r["manifest"]["counts"]["buckets"]["confirm"] == 1


def test_interact_zero_modification_and_determinism(ctx):
    """红线：运行后子图旧件 sha 与冻结清单全等（只加新文件）；双跑逐字节相等。"""
    sums = (ctx["sg"] / "SHA256SUMS").read_text(encoding="utf-8")
    run_interact(ctx)
    b1 = (ctx["sg"] / "interaction_report.tsv").read_bytes()
    for line in sums.splitlines():
        sha, _, name = line.partition("  ")
        if name.strip() == "SHA256SUMS":
            continue
        got = hashlib.sha256((ctx["sg"] / name.strip()).read_bytes()).hexdigest()
        assert got == sha, f"旧件被改动：{name}"
    run_interact(ctx)
    b2 = (ctx["sg"] / "interaction_report.tsv").read_bytes()
    assert b1 == b2


# ============================================================
# C3 三跳链（deepchain.py）
# ============================================================
def test_chain_assembly_complete_three_hops(ctx):
    """链拼接正确性①：完整三跳=菌→代谢物→基因→通路，五元组/溯源列全对。"""
    r = run_deepchain(ctx)
    comp = [c for c in r["rows"] if c["depth"] == "3"]
    # 817×Arachidonic Acid (E1) → G1(100) 有 2 条通路；G2(200) 无通路
    # 562 不产生 AA；E4 consumes 不进 hop1 → 完整链=1 hop1 边×1 基因×2 通路
    assert len(comp) == 2
    assert all(c["hop1_edge_id"] == "E1" and c["hop2_gene"] == "NCBIGene:100"
               for c in comp)
    assert {c["hop3_pathway"] for c in comp} == \
        {"KEGG:hsa00010", "KEGG:hsa05010"}
    assert comp[0]["hop1_causality"] == "causal"
    assert comp[0]["hop2_gene_name"] == "GENE1"
    assert "CHEBI:15843" in comp[0]["metabolite_chebi"]
    # hop2 引用逐条可溯：Index:PMID（含别名 butyric acid 行并查去重基因）
    assert "20:555" in comp[0]["hop2_gutmgene_refs"]


def test_chain_hop1_predicate_filter(ctx):
    """链拼接正确性②：hop1 只吃 produces/biotransforms——consumes/affects
    边（E4/E5）不产生任何链。"""
    r = run_deepchain(ctx)
    used_edges = {c["hop1_edge_id"] for c in r["rows"]}
    assert used_edges == {"E1", "E2", "E3"}
    assert HOP1_PREDICATES == {"produces", "biotransforms"}


def test_chain_deadend_metabolite_penalty(ctx):
    """罚分行为①：代谢物无基因边（Indole 不在表3）→depth=1 死端，
    penalty=DEAD_END_PENALTY、pruned=1、hop_terminus=metabolist——逐条落盘。"""
    r = run_deepchain(ctx)
    dead = [c for c in r["rows"] if c["hop1_edge_id"] == "E3"]
    assert len(dead) == 1
    assert dead[0]["depth"] == "1" and dead[0]["dead_end"] == "1"
    assert dead[0]["penalty"] == "1.0" and dead[0]["pruned"] == "1"
    assert dead[0]["hop_terminus"] == "metabolite"
    assert dead[0]["metabolite_name"] == "Indole"


def test_chain_deadend_gene_penalty(ctx):
    """罚分行为②：基因无通路边（G2/NCBIGene:200 不在 V01 kegg 快照）→
    depth=2 死端；Butyrate 别名行（butyric acid）并查不重复计数。"""
    r = run_deepchain(ctx)
    dead2 = [c for c in r["rows"] if c["depth"] == "2"]
    # E1×AA→G2 死端 + E2×Butyrate→G2 死端（表3 两行同基因去重后各 1）
    assert len(dead2) == 2
    assert all(c["hop2_gene"] == "NCBIGene:200" and c["penalty"] == "1.0"
               and c["hop_terminus"] == "gene" for c in dead2)
    but = next(c for c in dead2 if c["metabolite_name"] == "Butyrate")
    assert but["hop1_edge_id"] == "E2"
    # 但 two 名字键（butyrate/butyric acid）命中同一基因 → 证据并集不丢
    assert but["hop2_gutmgene_refs"] == "22:666;23:777"


def test_penalty_constant_hardcoded():
    """罚分常量写死非拟合（spec 逐字）：DEAD_END_PENALTY 恒 1.0——
    改常量=改规格，本测试锁死。"""
    assert DEAD_END_PENALTY == 1.0


def test_chain_depth_distribution_counts(ctx):
    """计数披露：深度分布 {1:1, 2:2, 3:2} 与链总数/剪枝总数可复算一致。"""
    r = run_deepchain(ctx)
    c = r["manifest"]["counts"]
    assert c["depth_distribution"] == {"1": 1, "2": 2, "3": 2}
    assert c["n_chains_total"] == 5 and c["complete"] == 2
    assert c["pruned_total"] == 3
    assert c["deadend_metabolite"] == 1 and c["deadend_gene"] == 2


def test_v01_gate_fail_closed_on_tamper(ctx):
    """V01 门禁 fail-closed：冻结链被改一字→sha 不等→SystemExit 拒跑。"""
    text = ctx["v01c"].read_text(encoding="utf-8")
    ctx["v01c"].write_text(text.replace("hsa00010", "hsa00011"),
                           encoding="utf-8")
    with pytest.raises(SystemExit, match="冻结链校验失败"):
        ctx["deepchain"].main(
            ["--subgraph-dir", str(ctx["sg"]),
             "--gutmgene-dir", str(ctx["gm"]),
             "--v01-edges", str(ctx["v01e"]),
             "--v01-chains", str(ctx["v01c"])])
    # 边表被改同样拒跑（装载 hop3 时即拦）
    ctx["v01c"].write_text(text, encoding="utf-8")
    etext = ctx["v01e"].read_text(encoding="utf-8")
    ctx["v01e"].write_text(etext + '\nNCBIGene:9\tparticipates_in\t'
                           'KEGG:x\tkegg_rest_extra\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="资产被动过"):
        ctx["deepchain"].main(
            ["--subgraph-dir", str(ctx["sg"]),
             "--gutmgene-dir", str(ctx["gm"]),
             "--v01-edges", str(ctx["v01e"]),
             "--v01-chains", str(ctx["v01c"])])


def test_anchor_check_blocks_out_of_frozen_pairs():
    """对拍锚定：引擎产出 (基因,通路) 对不在冻结链对集 → SystemExit。"""
    import deepchain
    frozen = [{"gene": "NCBIGene:100", "pathway": "KEGG:hsa00010"}]
    with pytest.raises(SystemExit, match="锚定违例"):
        deepchain.anchor_check({("NCBIGene:100", "KEGG:hsa99999")}, frozen)
    ok = deepchain.anchor_check({("NCBIGene:100", "KEGG:hsa00010")}, frozen)
    assert ok["violations"] == 0 and ok["machine_proof"] is True


def test_hop3_restricted_to_gutmgene_gene_domain(ctx):
    """hop3 基因域：V01 kegg 快照里基因域外的 participates_in 边不进链
    （G3/NCBIGene:300 在域内但无 hop2 命中；域外基因直接不可见）。"""
    g2p = load_hop3(ctx["v01e"], load_gene_domain(ctx["gm"]))
    assert "NCBIGene:300" in g2p            # 表1 基因在域内（有通路）
    domain = load_gene_domain(ctx["gm"])
    assert "NCBIGene:100" in domain and "NCBIGene:200" in domain
    # 事实集 V01 边表的 KEGG:hsa99999 边 source=other_source——非 kegg_rest
    assert "KEGG:hsa99999" not in {p for ps in g2p.values() for p in ps}


def test_graphrag_slot_placeholder_zero_llm(ctx):
    """GraphRAG 解释位占位：implemented=False 恒成立（本阶段零 LLM 的
    机器断言），上下文拼装是纯机械字段映射。"""
    r = run_deepchain(ctx)
    comp = next(c for c in r["rows"] if c["depth"] == "3")
    slot = graphrag_explain_slot(comp)
    assert slot["implemented"] is False and slot["reserved_for"] == "MT5"
    assert slot["chain_context"]["hops"][2]["triple"] == \
        ["NCBIGene:100", "participates_in", "KEGG:hsa00010"]
    assert r["manifest"]["graphrag_slot"]["implemented"] is False


def test_deepchain_zero_modification_and_determinism(ctx):
    """红线+确定性：旧件 sha 与冻结清单全等；chains.tsv 双跑逐字节相等；
    chain_id 可按公式独立复算。"""
    sums = (ctx["sg"] / "SHA256SUMS").read_text(encoding="utf-8")
    run_deepchain(ctx)
    b1 = (ctx["sg"] / "chains.tsv").read_bytes()
    for line in sums.splitlines():
        sha, _, name = line.partition("  ")
        if name.strip() == "SHA256SUMS":
            continue
        got = hashlib.sha256((ctx["sg"] / name.strip()).read_bytes()).hexdigest()
        assert got == sha, f"旧件被改动：{name}"
    run_deepchain(ctx)
    b2 = (ctx["sg"] / "chains.tsv").read_bytes()
    assert b1 == b2
    rows = list(csv.DictReader(open(ctx["sg"] / "chains.tsv",
                                    encoding="utf-8"), delimiter="\t"))
    c = next(x for x in rows if x["depth"] == "3")
    assert c["chain_id"] == chain_id(c["microbe_id"], c["metabolite_id"],
                                     c["hop1_edge_id"], c["hop2_gene"],
                                     c["hop3_pathway"])


def test_hop2_alias_match_and_evidence_aggregation(ctx):
    """hop2 匹配：子图代谢物 aliases（butyric acid）也命中表3；同一
    (代谢物,基因) 的多行证据合并进 refs 不重复成链。"""
    m2g = load_hop2(ctx["gm"])
    assert "butyric acid" in m2g and "NCBIGene:200" in m2g["butyric acid"]
    # arachidonic acid 行 20 → G1；键级隔离（Butyrate 键只有 G2）
    assert m2g["arachidonic acid"]["NCBIGene:100"]["refs"] == {"20:555"}
    # 完整链里 Butyrate→G1 不存在（Butyrate 两键都只连 G2）
    r = run_deepchain(ctx)
    assert not [c for c in r["rows"] if c["metabolite_name"] == "Butyrate"
                and c["hop2_gene"] == "NCBIGene:100"]


def test_assemble_chains_counts_consistent(ctx):
    """枚举一致性：hop1 边数=3（E1/E2/E3），链数=死端+完整可复算。"""
    from subgraph import read_tsv
    edges = read_tsv(ctx["sg"] / "merged_edges.tsv")
    nodes = read_tsv(ctx["sg"] / "merged_nodes.tsv")
    m2g = load_hop2(ctx["gm"])
    g2p = load_hop3(ctx["v01e"], load_gene_domain(ctx["gm"]))
    chains, stats = assemble_chains(edges, nodes, m2g, g2p)
    assert stats["hop1_edges"] == 3
    assert len(chains) == stats["complete"] + stats["deadend_metabolite"] \
        + stats["deadend_gene"] == 5
    # 排序键固定（防字典序漂移）
    keys = [(c["microbe_id"], c["hop1_edge_id"], c["metabolite_id"],
             c["hop2_gene"], c["hop3_pathway"]) for c in chains]
    assert keys == sorted(keys)
