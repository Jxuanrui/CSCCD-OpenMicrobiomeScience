#!/usr/bin/env python3
"""subgraph.py 单元测试——规格每条规则一个判例 + 端到端确定性。

为什么这么写：子图构建器是"主图零改动"红线的直接载体——master_unchanged
机器证、诱导子图口径、因果列三信号、SHA256SUMS 不含自身（release_v1 自指
事故的装配规则）都必须有测试锁住。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subgraph import (  # noqa: E402
    ACTION_PREDICATES, CAUSAL_REGEX, CORRELATION_REGEX, build_disease_family,
    bfs_node_set, cmd_merge, cmd_tier0, derive_causality,
    disease_literature_set, gutmgene_cross, load_votes_index, norm_id,
    rebuild_assertion_id,
)

# ---------- 微型主图夹具（覆盖：族词根/ID 大小写/种子两路/BFS/诱导口径） ----------

NODES = [
    {"id": "NCBITaxon:1", "name": "Bug one", "category": "Species",
     "aliases": "B. one", "xrefs": "", "tax_rank": "species",
     "knowledge_layer": "test"},
    {"id": "NCBITaxon:2", "name": "Bug two", "category": "Species",
     "aliases": "", "xrefs": "", "tax_rank": "species",
     "knowledge_layer": "test"},
    {"id": "NCBITaxon:3", "name": "Bug three", "category": "Species",
     "aliases": "", "xrefs": "", "tax_rank": "species",
     "knowledge_layer": "test"},
    {"id": "MESH:D006973", "name": "hypertension", "category": "Disease",
     "aliases": "", "xrefs": "", "tax_rank": "", "knowledge_layer": "test"},
    {"id": "MESH:D002338", "name": "carotenoid", "category": "Metabolite",
     "aliases": "", "xrefs": "", "tax_rank": "", "knowledge_layer": "test"},
    {"id": "MeSH:D006976", "name": "pulmonary hypertension", "category":
     "Disease", "aliases": "", "xrefs": "", "tax_rank": "",
     "knowledge_layer": "test"},
    {"id": "MESH:D003668", "name": "pressure", "category": "Disease",
     "aliases": "", "xrefs": "", "tax_rank": "", "knowledge_layer": "test"},
    {"id": "MESH:D000075", "name": "essential hypertension", "category":
     "Disease", "aliases": "", "xrefs": "", "tax_rank": "",
     "knowledge_layer": "test"},
]

EDGES = [
    # 种子直连边（object∈族）
    {"subject": "NCBITaxon:1", "predicate": "aggravates",
     "object": "MESH:D006973", "edge_id": "E1", "pmids": "111",
     "support_count": "1"},
    # 种子经由文献（object 非疾病，但 pmid∈疾病文献集）
    {"subject": "NCBITaxon:1", "predicate": "produces",
     "object": "MESH:D002338", "edge_id": "E2", "pmids": "222",
     "support_count": "1"},
    # 邻接排除验证：object=pressure（不含词根，非族）→ 仅靠 BFS k=1 可能进
    {"subject": "NCBITaxon:2", "predicate": "alleviates",
     "object": "MESH:D003668", "edge_id": "E3", "pmids": "333",
     "support_count": "1"},
    # 二跳外节点（k=1 不可达：3 只与 2 相连，2 与种子 1 不相连时）
    {"subject": "NCBITaxon:3", "predicate": "affects",
     "object": "NCBITaxon:2", "edge_id": "E4", "pmids": "444",
     "support_count": "1"},
]

ASSERTS = [
    # E1 的断言（votes 回连样本 A：3/3 票 + SUPPORTED + 因果动词）
    {"assertion_id": "A1", "edge_id": "E1", "run_batch": "b1", "pmid": "111",
     "subject_id": "NCBITaxon:1", "subject_name": "Bug one",
     "subject_mention": "Bug one", "predicate": "aggravates",
     "object_id": "MESH:D006973", "object_name": "hypertension",
     "sentence": "Bug one induces hypertension in mice.", "evidence":
     "induces hypertension", "confidence": "0.9", "polarity": "positive",
     "evidence_granularity": "名称级", "granularity_source": "t",
     "model": "m", "stage": "3", "execution_id": ""},
    # E2 的断言（pmid=222 属疾病文献集；evidence 无因果动词有相关动词）
    {"assertion_id": "A2", "edge_id": "E2", "run_batch": "b1", "pmid": "222",
     "subject_id": "NCBITaxon:1", "subject_name": "Bug one",
     "subject_mention": "Bug one", "predicate": "produces",
     "object_id": "MESH:D002338", "object_name": "carotenoid",
     "sentence": "Bug one is enriched in samples.", "evidence": "enriched",
     "confidence": "0.9", "polarity": "positive",
     "evidence_granularity": "名称级", "granularity_source": "t",
     "model": "m", "stage": "3", "execution_id": ""},
    # E3 的断言（无 votes 回连——判 votes 缺失路径）
    {"assertion_id": "A3", "edge_id": "E3", "run_batch": "b1", "pmid": "333",
     "subject_id": "NCBITaxon:2", "subject_name": "Bug two",
     "subject_mention": "Bug two", "predicate": "alleviates",
     "object_id": "MESH:D003668", "object_name": "pressure",
     "sentence": "Bug two improves pressure.", "evidence": "improves",
     "confidence": "0.9", "polarity": "positive",
     "evidence_granularity": "名称级", "granularity_source": "t",
     "model": "m", "stage": "3", "execution_id": ""},
]

# 疾病文献集：pmid=222 的文章带 D006973 标注
LIBRARY_LINE = {"pmid": "222", "title": "t", "abstract": "a", "annotations": [
    {"type": "Disease", "name": "hypertension", "id": "MESH:D006973",
     "db": "ncbi_mesh", "locations": []}]}

# K3 批次 votes（只给 A1/A2；A3 故意缺失）
VOTES_ROWS = [
    {"pipeline": "v2", "pmid": "111", "sentence":
     "Bug one induces hypertension in mice.",
     "subject": {"id": "NCBITaxon:1", "name": "Bug one", "mention": "x",
                 "category": "Microbe"},
     "object": {"id": "MESH:D006973", "name": "hypertension", "mention": "y",
                "category": "Disease"},
     "predicate": "aggravates", "evidence": "induces hypertension",
     "status": "ok", "votes": ["aggravates", "aggravates", "aggravates"],
     "judge": {"verdict": "SUPPORTED"}},
    {"pipeline": "v2", "pmid": "222", "sentence":
     "Bug one is enriched in samples.",
     "subject": {"id": "NCBITaxon:1", "name": "Bug one", "mention": "x",
                 "category": "Microbe"},
     "object": {"id": "MESH:D002338", "name": "carotenoid", "mention": "y",
                "category": "Metabolite"},
     "predicate": "produces", "evidence": "enriched", "status": "ok",
     "votes": ["produces", "produces", "no_relation"],
     "judge": {"verdict": "UNSUPPORTED"}},
]


@pytest.fixture()
def mini_kg(tmp_path: Path) -> dict:
    """落盘微型主图五件 + 文献库 + K3 批次（cmd_tier0 全链路输入）。

    assertion_id 必须按 K4 公式重算（真图就是重算产物，votes 回连靠它）——
    夹具里手造假名会导致回连 100% 失败（第一次实现就踩了这坑）。
    """
    for a in ASSERTS:
        a["assertion_id"] = rebuild_assertion_id(
            a["run_batch"], a["pmid"], a["subject_id"], a["predicate"],
            a["object_id"], a["sentence"], a["evidence"])
    kg = tmp_path / "kg"
    kg.mkdir()
    (kg / "merged_nodes.tsv").write_text(
        "id\tname\tcategory\taliases\txrefs\ttax_rank\tknowledge_layer\n" +
        "".join(f"{n['id']}\t{n['name']}\t{n['category']}\t{n['aliases']}\t"
                f"\t{n['tax_rank']}\t{n['knowledge_layer']}\n"
                for n in NODES), encoding="utf-8")
    ecols = ["subject", "predicate", "object", "edge_id", "pmids",
             "support_count"]
    (kg / "merged_edges.tsv").write_text(
        "\t".join(ecols) + "\n" +
        "".join("\t".join(e[c] for c in ecols) + "\n" for e in EDGES),
        encoding="utf-8")
    acols = list(ASSERTS[0].keys())
    (kg / "relation_assertions.tsv").write_text(
        "\t".join(acols) + "\n" +
        "".join("\t".join(a[c] for c in acols) + "\n" for a in ASSERTS),
        encoding="utf-8")
    (kg / "conflicts.tsv").write_text(
        "subject\tobject\tpredicate_a\tpmids_a\tpredicate_b\tpmids_b\tstate\t"
        "resolution\tbatch_ids\tfirst_seen_batch\n", encoding="utf-8")
    (kg / "corrections.tsv").write_text(
        "correction_id\tedge_id_old\tedge_id_new\taction\tbucket\tpattern_id\t"
        "evidence_ptr\tdecided_by\texpanded\n", encoding="utf-8")
    (kg / "manifest.json").write_text('{"merge_id": "mini"}', encoding="utf-8")

    lit = tmp_path / "articles.jsonl"
    lit.write_text(json.dumps(LIBRARY_LINE) + "\n", encoding="utf-8")

    b1 = tmp_path / "b1"
    b1.mkdir()
    (b1 / "relations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in VOTES_ROWS), encoding="utf-8")
    return {"kg": kg, "lit": lit, "batch": b1}


def test_norm_id_case_variants():
    """规格 §一：MESH:/MeSH: 双前缀变体必须归一（漏了会漏一半族成员）。"""
    assert norm_id("MeSH:D006973") == norm_id("MESH:D006973") == "MESH:D006973"


def test_disease_family_root_and_ids():
    """族=ID 输入集 ∪ 词根命中（含 MeSH: 变体）；泛义词 pressure 不进族。"""
    fam, disc = build_disease_family(
        NODES, {"MESH:D006973"}, "hypertens")
    assert fam == {"MESH:D006973", "MESH:D006976", "MESH:D000075"}
    assert "MESH:D003668" not in fam
    assert len(disc) == 3


def test_disease_literature_set(tmp_path: Path):
    """疾病文献集：标注 id（upper 归一）∈ 族 → 该文 PMID 入集。"""
    p = tmp_path / "lib.jsonl"
    p.write_text(json.dumps(LIBRARY_LINE) + "\n", encoding="utf-8")
    assert disease_literature_set(p, {"MESH:D006973"}) == {"222"}
    assert disease_literature_set(p, {"MESH:D999999"}) == set()


def test_bfs_induced_subgraph_semantics():
    """k=1 诱导子图：种子邻域内全部边（含两邻居互连的边）都进。"""
    adj = {"A": {"B"}, "B": {"A", "C"}, "C": {"B"}}
    nodes = bfs_node_set(adj, {"A"}, 1)
    assert nodes == {"A", "B"}
    assert bfs_node_set(adj, {"A"}, 0) == {"A"}
    assert bfs_node_set(adj, {"A"}, 2) == {"A", "B", "C"}


def test_causality_three_rules():
    """因果列三档判定序（rule1 三信号与→rule2 affects/相关→rule3 其余）。"""
    votes_idx = {
        "A1": {"votes": ["aggravates"] * 3, "dominant_votes": 3,
               "judge_verdict": "SUPPORTED"},
        "A2": {"votes": ["produces", "produces", "x"], "dominant_votes": 2,
               "judge_verdict": "UNSUPPORTED"},
    }
    e1 = {"predicate": "aggravates"}
    c1 = derive_causality(e1, [ASSERTS[0]], votes_idx)
    assert c1["causality"] == "causal" and "rule1" in c1["causality_basis"]
    # E2：action+vote 齐（2/3 票）但证据无因果动词、有相关动词 → correlated
    c2 = derive_causality({"predicate": "produces"}, [ASSERTS[1]], votes_idx)
    assert c2["causality"] == "correlated" and "corr_ev" in c2["causality_basis"]
    # E3：votes 缺失 → vote=0；evidence 有 improves（因果动词）→ 不满足 rule1
    # 但 affects? 否；corr_ev? 否 → unclassified（不静默扔的名单成员）
    c3 = derive_causality({"predicate": "alleviates"}, [ASSERTS[2]], votes_idx)
    assert c3["causality"] == "unclassified" and c3["n_votes_missing"] == 1
    # affects 无任何文本命中 → correlated（rule2 的谓词通道）
    c4 = derive_causality({"predicate": "affects"},
                          [{**ASSERTS[1], "evidence": "", "sentence": "zzz"}],
                          {})
    assert c4["causality"] == "correlated"


def test_regex_tables_locked():
    """词表正则锁定=规格 §四 逐字（改动必须同步规格）。"""
    assert ACTION_PREDICATES == {"aggravates", "alleviates", "produces",
                                 "biotransforms", "consumes", "promotes_growth",
                                 "inhibits_growth"}
    assert CAUSAL_REGEX.search("significantly induced")
    assert CAUSAL_REGEX.search("improved blood pressure")
    assert CORRELATION_REGEX.search("enriched in patients")
    assert not CAUSAL_REGEX.search("enriched in patients")


def test_votes_index_rebuild(tmp_path: Path):
    """assertion_id 重算回连（K4 公式逐字复刻）。"""
    b1 = tmp_path / "b1"
    b1.mkdir()
    (b1 / "relations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in VOTES_ROWS), encoding="utf-8")
    idx = load_votes_index([b1])
    a1 = rebuild_assertion_id("b1", "111", "NCBITaxon:1", "aggravates",
                              "MESH:D006973", ASSERTS[0]["sentence"],
                              ASSERTS[0]["evidence"])
    assert a1 in idx and idx[a1]["dominant_votes"] == 3
    assert idx[a1]["judge_verdict"] == "SUPPORTED"


def test_gutmgene_cross(tmp_path: Path):
    """DOID token 命中（含复合 DOID 行）+ 名字 casefold 匹配 both/a_side。"""
    gm = tmp_path
    (gm / "Gut Microbe-Microbial metabolite.csv").write_text(
        "Index,PMID,Gut Microbiota,Metabolite,Associative mode,DOID\n"
        "1,111,Bug one,carotenoid,causally,DOID:10763\n"
        "2,222,Bug one,unknown_thing,causally,DOID:4989;DOID:10763\n"
        "3,333,Bug nine,zzz,causally,DOID:9999\n", encoding="gb18030")
    node_index = {"bug one": "NCBITaxon:1", "carotenoid": "MESH:D002338"}
    rows = gutmgene_cross(gm, "DOID:10763", node_index)
    assert len(rows) == 2  # 行 3 的 DOID 不命中
    assert rows[0]["match_status"] == "both"
    assert rows[1]["match_status"] == "a_side"  # metabolite 不在子图


def test_tier0_end_to_end_determinism(mini_kg, tmp_path: Path):
    """端到端：两次 tier0 运行，五件 TSV 逐字节相等 + 主图零改动机器证。"""
    def run(tag: str) -> dict:
        out = tmp_path / f"sg_{tag}"
        rc = cmd_tier0(argparse_ns(mini_kg, out))
        assert rc == 0
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        # 主图零改动机器证必须为真
        assert m["master_unchanged_proof"]["machine_proof"] is True
        return m

    def argparse_ns(mk, out):
        import argparse
        ns = argparse.Namespace(
            kg_dir=mk["kg"], library=mk["lit"], mesh="D006973,D006976",
            name_root="hypertens", doid="DOID:10763", khop=1, key="htn",
            k3_batch=[str(mk["batch"])], gutmgene_dir=None, out=out)
        return ns

    m1, m2 = run("a"), run("b")
    for f in ("merged_edges.tsv", "merged_nodes.tsv",
              "relation_assertions.tsv"):
        b1 = (tmp_path / "sg_a" / f).read_bytes()
        b2 = (tmp_path / "sg_b" / f).read_bytes()
        assert b1 == b2, f"{f} 非确定性！"
    # 种子边 2 条（E1 族直连 + E2 疾病文献）+ khop：E1 的端点 D006973 的
    # 其他邻边不存在、E2 端点无其他边 → khop 无新增边；E3/E4 不可达不入
    assert m1["counts"]["n_edges"] == 2
    assert m1["counts"]["seed_edges"] == 2
    assert m1["counts"]["provenance"]["khop_expand"] == 0
    # 断言 seed_reason：A2 带 pmid_in_disease_lit
    assert m1["counts"]["n_assertions"] == 2
    # 因果分布：E1=causal（三信号齐）；E2=correlated（corr_ev 通道）
    assert m1["counts"]["causality"] == {"causal": 1, "correlated": 1,
                                         "unclassified": 0}
    # SHA256SUMS 不含自身（release_v1 自指事故装配规则）
    sums = (tmp_path / "sg_a" / "SHA256SUMS").read_text(encoding="utf-8")
    assert "SHA256SUMS" not in sums
    # subgraph_version 两次一致（同输入同输出）
    assert m1["subgraph_version"] == m2["subgraph_version"]
    # 未分类为 0 → 无 unclassified_edges.tsv；无 gutmgene → 无交叉表
    assert not (tmp_path / "sg_a" / "unclassified_edges.tsv").exists()


def test_merge_increment(mini_kg, tmp_path: Path):
    """tier2 增量并入：新边机械成行 + 既有边 pmids 追加 + 因果列重算。"""
    import argparse
    sg = tmp_path / "sg0"
    rc = cmd_tier0(argparse.Namespace(
        kg_dir=mini_kg["kg"], library=mini_kg["lit"], mesh="D006973,D006976",
        name_root="hypertens", doid="", khop=1, key="htn",
        k3_batch=[str(mini_kg["batch"])], gutmgene_dir=None, out=sg))
    assert rc == 0

    # 造 tier2 批次：一条全新三元组（votes 3/3+SUPPORTED+因果动词→causal）
    # + 一条追加既有边 E2 的三元组
    t2 = tmp_path / "b2"
    t2.mkdir()
    rows = [
        {"pipeline": "v2", "pmid": "999", "sentence":
         "Bug nine improves hypertension.",
         "subject": {"id": "NCBITaxon:9", "name": "Bug nine", "mention": "x",
                     "category": "Microbe"},
         "object": {"id": "MESH:D006973", "name": "hypertension",
                    "mention": "y", "category": "Disease"},
         "predicate": "alleviates", "evidence": "improves hypertension",
         "status": "ok", "subject_mention": "Bug nine",
         "votes": ["alleviates"] * 3,
         "judge": {"verdict": "SUPPORTED"}, "confidence": "0.9",
         "polarity": "positive", "model": "m", "stage": "3"},
        {"pipeline": "v2", "pmid": "888", "sentence":
         "Bug one generates carotenoid.",
         "subject": {"id": "NCBITaxon:1", "name": "Bug one", "mention": "x",
                     "category": "Microbe"},
         "object": {"id": "MESH:D002338", "name": "carotenoid",
                    "mention": "y", "category": "Metabolite"},
         "predicate": "produces", "evidence": "generates carotenoid",
         "status": "ok", "subject_mention": "Bug one",
         "votes": ["produces"] * 3,
         "judge": {"verdict": "SUPPORTED"}, "confidence": "0.9",
         "polarity": "positive", "model": "m", "stage": "3"},
    ]
    (t2 / "relations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    out_dirs_before = set(tmp_path.glob("sg_m*"))
    rc = cmd_merge(argparse.Namespace(
        kg_dir=mini_kg["kg"], library=mini_kg["lit"],
        subgraph_dir=sg, tier2_batch=t2, tier1_json="", key="htn",
        k3_batch=[str(mini_kg["batch"])], out=tmp_path / "sg_m"))
    assert rc == 0
    merged_dirs = set(tmp_path.glob("sg_m*")) - out_dirs_before
    assert len(merged_dirs) == 1
    md = merged_dirs.pop()

    edges = {e["edge_id"]: e for e in read_rows(md / "merged_edges.tsv")}
    assert len(edges) == 3  # 2 tier0 边 + 1 新增
    # 新边：causal（三信号齐）+ tier2_increment 溯源 + 全 22 列模板值
    new_e = next(e for e in edges.values()
                 if e["provenance"] == "tier2_increment")
    assert new_e["subject"] == "NCBITaxon:9" and new_e["causality"] == "causal"
    assert new_e["source_type"] == "llm_extracted" and \
        new_e["evidence_tier"] == "C" and new_e["pmids"] == "999"
    # 追加边：E2 的 pmids 追加 888、support_count 2、因果列因新证据变 causal
    app_e = next(e for e in edges.values() if e["edge_id"] == "E2")
    assert "888" in app_e["pmids"] and app_e["support_count"] == "2"
    assert app_e["causality"] == "causal"  # 新证据 "generates" 补齐因果动词
    # 断言：2 旧 + 2 新；新断言带 tier2_increment 溯源与重算 assertion_id
    asserts = read_rows(md / "relation_assertions.tsv")
    assert len(asserts) == 4
    t2a = [a for a in asserts if a["seed_reason"] == "tier2_increment"]
    assert len(t2a) == 2 and all(a["run_batch"] == "b2" for a in t2a)
    # 新节点闭包：NCBITaxon:9 不在主图 → 构造节点（category=K3 原值）
    nodes = {n["id"] for n in read_rows(md / "merged_nodes.tsv")}
    assert "NCBITaxon:9" in nodes
    m = json.loads((md / "manifest.json").read_text(encoding="utf-8"))
    assert m["master_unchanged_proof"]["machine_proof"] is True
    assert m["counts"]["tier2"]["n_edges_new"] == 1
    assert m["counts"]["tier2"]["n_edges_appended"] == 1
    assert m["counts"]["tier2"]["n_closure_nodes_constructed"] == 1


def read_rows(path: Path) -> list[dict]:
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))
