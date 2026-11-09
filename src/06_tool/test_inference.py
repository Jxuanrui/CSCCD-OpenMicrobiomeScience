#!/usr/bin/env python3
"""inference.py 单元测试（D1，spec §三 验收 5/6/7/8 之 D1 部分）。

事实集策略与 C 阶段一致：文件级微型"子图+三队列观测层+桥+KO 映射"，
端到端造件跑真实 main() 路径——门禁常量（KO 映射 sha/V01 sha）用
monkeypatch 换成事实集指纹：被测的是门禁逻辑本身，不是特定指纹值。
"""
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inference as inf  # noqa: E402


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def write_tsv(p: Path, cols, rows):
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t",
                           lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ============================================================
# 事实集：微型子图 + 三队列观测层 + 桥 + KO 映射
# ============================================================
CHAINS = [
    # E1 边链：菌 100（两队列桥到不同丰度）；chebi 有映射且 KO 非零
    {"chain_id": "CH-aaa", "microbe_id": "NCBITaxon:100",
     "microbe_name": "Alice alpha", "metabolite_id": "MESH:D1",
     "metabolite_name": "Met A", "metabolite_chebi": "CHEBI:11111",
     "hop1_edge_id": "E1", "depth": "3", "hop_terminus": "pathway",
     "hop2_gene": "NCBIGene:1", "hop2_gene_name": "Tlr4",
     "hop3_pathway": "KEGG:map1", "penalty": "0.0", "pruned": "0"},
    # 菌 200：SelfData 全零（absent 分支）
    {"chain_id": "CH-bbb", "microbe_id": "NCBITaxon:200",
     "microbe_name": "Bob beta", "metabolite_id": "MESH:D1",
     "metabolite_name": "Met A", "metabolite_chebi": "CHEBI:11111",
     "hop1_edge_id": "E1", "depth": "3", "hop_terminus": "pathway",
     "hop2_gene": "NCBIGene:1", "hop2_gene_name": "Tlr4",
     "hop3_pathway": "KEGG:map1", "penalty": "0.0", "pruned": "0"},
    # 菌 999：三队列全不桥（not_bridged）；chebi 空（no_chebi）
    {"chain_id": "CH-ccc", "microbe_id": "NCBITaxon:999",
     "microbe_name": "Nowhere", "metabolite_id": "MESH:D2",
     "metabolite_name": "Met B", "metabolite_chebi": "",
     "hop1_edge_id": "E2", "depth": "1", "hop_terminus": "metabolite",
     "hop2_gene": "", "hop2_gene_name": "", "hop3_pathway": "",
     "penalty": "1.0", "pruned": "1"},
    # chebi 不在映射（unmapped_chebi 分支）
    {"chain_id": "CH-ddd", "microbe_id": "NCBITaxon:100",
     "microbe_name": "Alice alpha", "metabolite_id": "MESH:D3",
     "metabolite_name": "Met C", "metabolite_chebi": "CHEBI:33333",
     "hop1_edge_id": "E2", "depth": "2", "hop_terminus": "gene",
     "hop2_gene": "NCBIGene:2", "hop2_gene_name": "Gene2",
     "hop3_pathway": "", "penalty": "1.0", "pruned": "1"},
]
EDGE_COLS = ["subject", "predicate", "object", "edge_id", "run_batch",
             "retrieved_at", "knowledge_layer", "evidence_granularity",
             "pmids"]
EDGES = [
    {"subject": "NCBITaxon:100", "predicate": "produces", "object": "MESH:D1",
     "edge_id": "E1", "run_batch": "batch1", "retrieved_at": "2026-11-01",
     "knowledge_layer": "local_kg_llm_extracted",
     "evidence_granularity": "名称级", "pmids": "111"},
    {"subject": "NCBITaxon:100", "predicate": "produces", "object": "MESH:D3",
     "edge_id": "E2", "run_batch": "batch1", "retrieved_at": "2026-11-01",
     "knowledge_layer": "local_kg_llm_extracted",
     "evidence_granularity": "名称级", "pmids": "222"},
    {"subject": "NCBITaxon:999", "predicate": "produces", "object": "MESH:D2",
     "edge_id": "E3", "run_batch": "batch1", "retrieved_at": "2026-11-01",
     "knowledge_layer": "local_kg_llm_extracted",
     "evidence_granularity": "名称级", "pmids": "333"},
]

# 桥（覆盖 s__ 优先于 g__：taxid 100 在 SelfData 有两行）
BRIDGE_COLS = ["source", "rank_token", "metaphlan_name", "metaphlan_lineage",
               "match_method", "taxid", "ncbi_scientific_name", "ncbi_rank",
               "in_main_graph"]
BRIDGE_ROWS = [
    {"source": "SelfData_222", "rank_token": "s__",
     "metaphlan_name": "Alice alpha", "taxid": "100",
     "metaphlan_lineage": "k__B|g__Alice|s__Alice_alpha"},
    {"source": "SelfData_222", "rank_token": "g__", "metaphlan_name": "Alice",
     "taxid": "100", "metaphlan_lineage": "k__B|g__Alice"},
    {"source": "SelfData_222", "rank_token": "s__",
     "metaphlan_name": "Bob beta", "taxid": "200",
     "metaphlan_lineage": "k__B|g__Bob|s__Bob_beta"},
    {"source": "PRJEB13870", "rank_token": "s__",
     "metaphlan_name": "Alice alpha", "taxid": "100",
     "metaphlan_lineage": "k__B|g__Alice|s__Alice_alpha"},
    {"source": "PRJEB21612", "rank_token": "s__",
     "metaphlan_name": "Bob beta", "taxid": "200",
     "metaphlan_lineage": "k__B|g__Bob|s__Bob_beta"},
    # 票①判据 a 的桥接通道（21612 桥）：Alice alpha 种级 + Alice 属级 + Zeta beta
    {"source": "PRJEB21612", "rank_token": "s__",
     "metaphlan_name": "Alice alpha", "taxid": "100",
     "metaphlan_lineage": "k__B|g__Alice|s__Alice_alpha"},
    {"source": "PRJEB21612", "rank_token": "g__",
     "metaphlan_name": "Alice", "taxid": "555",
     "metaphlan_lineage": "k__B|g__Alice"},
    {"source": "PRJEB21612", "rank_token": "s__",
     "metaphlan_name": "Zeta beta", "taxid": "777",
     "metaphlan_lineage": "k__B|g__Zeta|s__Zeta_beta"},
]
# 队列表（SelfData：Alice 在位/Bob 全零；另两队列错开验证逐队列独立）
TAXO = {
    "SelfData_222": [("k__B|g__Alice|s__Alice_alpha", "0.5", "0.0"),
                     ("k__B|g__Bob|s__Bob_beta", "0.0", "0.0"),
                     ("k__B|g__Alice", "0.1", "0.2")],
    "PRJEB13870": [("k__B|g__Alice|s__Alice_alpha", "0.0", "0.0")],
    "PRJEB21612": [("k__B|g__Bob|s__Bob_beta", "0.3", "0.4")],
}
KO_TAB = {
    "SelfData_222": [("K00001", "1.0", "0.0"), ("K00002", "0.0", "3.0"),
                     ("K09999", "0.0", "0.0")],
    "PRJEB13870": [("K00001", "2.0", "2.0")],
    "PRJEB21612": [("K00001", "0.0", "0.0")],
}
# KO 冻结映射（CHEBI:11111→双 KO 可测；CHEBI:44444→映射但全零 KO）
KO_MAP_COLS = ["chebi", "kos"]
KO_MAP_ROWS = [{"chebi": "CHEBI:11111", "kos": "K00002;K00001"},
               {"chebi": "CHEBI:44444", "kos": "K09999"}]


def make_world(base: Path) -> dict[str, Path]:
    """造全套事实集，返回关键路径（monkeypatch 门禁指纹用）。"""
    sg = base / "sg"
    sg.mkdir(parents=True)
    write_tsv(sg / "chains.tsv", list(CHAINS[0].keys()), CHAINS)
    write_tsv(sg / "merged_edges.tsv", EDGE_COLS, EDGES)
    (sg / "manifest.json").write_text(
        json.dumps({"subgraph_version": "test"}), encoding="utf-8")
    sums = "\n".join(f"{sha256_file(sg / n)}  {n}"
                     for n in ("chains.tsv", "merged_edges.tsv")) + "\n"
    (sg / "SHA256SUMS").write_text(sums, encoding="utf-8")

    bridge = base / "bridge.tsv"
    write_tsv(bridge, BRIDGE_COLS, BRIDGE_ROWS)

    ko_map = base / "ko_map.tsv"
    write_tsv(ko_map, KO_MAP_COLS, KO_MAP_ROWS)

    obs = base / "obs"
    contract_src = {}
    for cohort, taxo_rows in TAXO.items():
        d = obs / cohort / "metaphlan4"
        d.mkdir(parents=True)
        with open(d / "taxonomy.tsv", "w", encoding="utf-8",
                  newline="") as f:
            f.write("ID\tS1\tS2\n")
            for r in taxo_rows:
                f.write("\t".join(r) + "\n")
        kd = obs / cohort / "humann3"
        kd.mkdir(parents=True)
        with open(kd / "ko_unstratified.tsv", "w", encoding="utf-8",
                  newline="") as f:
            f.write("# Gene Family\tS1\tS2\n")
            for r in KO_TAB[cohort]:
                f.write("\t".join(r) + "\n")
        contract_src[cohort] = {
            "metaphlan4/taxonomy.tsv":
                {"sha256": sha256_file(d / "taxonomy.tsv")},
            "humann3/ko_unstratified.tsv":
                {"sha256": sha256_file(kd / "ko_unstratified.tsv")},
        }
    (obs / "manifest.json").write_text(
        json.dumps({"sources": {c: {"files": f} for c, f in contract_src.items()}}),
        encoding="utf-8")
    return {"sg": sg, "bridge": bridge, "ko_map": ko_map, "obs": obs}


def run_main(world, tmp, monkeypatch):
    """跑 main()（门禁指纹换成事实集指纹）——返回落盘行。"""
    monkeypatch.setattr(inf, "KO_MAP_SHA16",
                        sha256_file(world["ko_map"])[:16])
    out = tmp / "sg_out"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(world["sg"], out)
    rc = inf.main(["--subgraph-dir", str(out), "--obs-dir", str(world["obs"]),
                   "--bridge-tsv", str(world["bridge"]),
                   "--ko-map", str(world["ko_map"])])
    assert rc == 0
    with open(out / "chain_annotations.tsv", encoding="utf-8") as f:
        return out, list(csv.DictReader(f, delimiter="\t"))


@pytest.fixture()
def world(tmp_path):
    return make_world(tmp_path)


# ============================================================
# 门禁（fail-closed）
# ============================================================
def test_ko_map_gate_fail_closed(world, monkeypatch):
    """门禁⑥：KO 映射 sha 不等于登记值 → SystemExit（防资产被换）。"""
    monkeypatch.setattr(inf, "KO_MAP_SHA16", "deadbeefdeadbeef")
    with pytest.raises(SystemExit, match="拒跑"):
        inf.gate_ko_map(world["ko_map"])


def test_observation_sha_contract_gate(world, monkeypatch):
    """门禁③④：观测层 sha 契约失配 → SystemExit；正确 → machine_proof。"""
    obs = world["obs"]
    (obs / "SelfData_222/metaphlan4/taxonomy.tsv").write_text(
        "ID\tS1\tS2\nk__B|g__Alice|s__Alice_alpha\t9\t9\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="契约失配"):
        inf.gate_observation_files(obs, inf.COHORTS)


# ============================================================
# 三项标注的机械定义（纯函数）
# ============================================================
def test_microbe_presence_branches(world):
    """present / absent / not_bridged 三分支 + 中位数与检出率。"""
    bridge = inf.load_bridge(world["bridge"])
    taxo = inf.load_taxonomy(world["obs"], "SelfData_222")
    r = inf.annotate_microbe("NCBITaxon:100", "SelfData_222", bridge, taxo)
    assert r["microbe_presence"] == "present"
    assert r["microbe_match_rank"] == "species"          # s__ 优先于 g__
    assert r["microbe_median_abundance"] == "0.25"       # median(0.5,0)
    assert r["microbe_detection_rate"] == "0.5"
    r2 = inf.annotate_microbe("NCBITaxon:200", "SelfData_222", bridge, taxo)
    assert r2["microbe_presence"] == "absent"
    assert r2["microbe_detection_rate"] == "0"
    r3 = inf.annotate_microbe("NCBITaxon:999", "SelfData_222", bridge, taxo)
    assert r3["microbe_presence"] == "not_bridged"
    assert r3["microbe_median_abundance"] == ""


def test_bridge_species_preferred_and_per_cohort(world):
    """桥取行确定性：同 taxid 多行取 s__；不同队列各自独立取行。"""
    bridge = inf.load_bridge(world["bridge"])
    assert bridge[("SelfData_222", "100")]["rank_token"] == "s__"
    assert ("SelfData_222", "999") not in bridge
    taxo13870 = inf.load_taxonomy(world["obs"], "PRJEB13870")
    r = inf.annotate_microbe("NCBITaxon:100", "PRJEB13870", bridge, taxo13870)
    assert r["microbe_presence"] == "absent"  # 13870 里 Alice 全零


def test_ko_label_enum_and_potential(world, monkeypatch):
    """ko_label 四值枚举 + 潜能=命中 KO 按字典序求和（顺序锁定）。"""
    monkeypatch.setattr(inf, "KO_MAP_SHA16",
                        sha256_file(world["ko_map"])[:16])
    ko_map = inf.gate_ko_map(world["ko_map"])
    _, table = inf.load_ko_table(world["obs"], "SelfData_222")
    r = inf.annotate_ko("CHEBI:11111", ko_map, table)
    # 潜能 S1=K00001(1.0)+K00002(0.0)=1.0；S2=0+3=3 → measurable
    assert r["ko_label"] == "measurable"
    assert r["n_ko_mapped"] == 2
    assert r["ko_ids"] == "K00001;K00002"
    assert r["ko_median_abundance"] == "2"        # median(1.0, 3.0)
    assert r["ko_detection_rate"] == "1"
    # 映射到但全零 → ko_absent（21612 的 K00001 全零）
    _, t21612 = inf.load_ko_table(world["obs"], "PRJEB21612")
    r2 = inf.annotate_ko("CHEBI:11111", ko_map, t21612)
    assert r2["ko_label"] == "ko_absent"
    # chebi 不在冻结映射 / 链无 chebi
    assert inf.annotate_ko("CHEBI:33333", ko_map, table)["ko_label"] \
        == "unmapped_chebi"
    assert inf.annotate_ko("", ko_map, table)["ko_label"] == "no_chebi"


def test_chain_ko_ids_multi_chebi_union(monkeypatch, world):
    """链 chebi 多值（`;` 拆分）→ KO 并集（排序锁定）。"""
    monkeypatch.setattr(inf, "KO_MAP_SHA16",
                        sha256_file(world["ko_map"])[:16])
    ko_map = inf.gate_ko_map(world["ko_map"])
    assert inf.chain_ko_ids("CHEBI:44444;CHEBI:11111", ko_map) \
        == ["K00001", "K00002", "K09999"]


# ============================================================
# 端到端（main：结构/三列四键/零改动/确定性/红线）
# ============================================================
def test_main_rows_and_mandatory_columns(world, tmp_path, monkeypatch):
    """验收⑦：行数=链×队列；强制三列+溯源四键逐行非空。"""
    out, rows = run_main(world, tmp_path, monkeypatch)
    assert len(rows) == len(CHAINS) * 3
    assert [r["cohort"] for r in rows[:3]] == inf.COHORTS  # 队列序锁定
    for r in rows:
        assert r["kg_version"] == inf.KG_VERSION
        assert r["evidence_granularity"] == "名称级"
        assert r["k5_gate_status"].startswith("gate_pass=False")
        for k in ("source_id", "retrieved_at", "version", "knowledge_layer"):
            assert r[k], f"溯源键 {k} 空"
        assert r["metabolite_measurability"] == "not_available"
    # 表头白名单=零检验列（红线机器证：无任何 t/p/q/检验字段）
    with open(out / "chain_annotations.tsv", encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
    assert not any(h for h in header
                   if h.split("_")[0] in ("t", "p", "q", "pval", "stat"))
    assert set(header) == set(inf.ANNOTATION_COLS)


def test_main_zero_modification(world, tmp_path, monkeypatch):
    """验收⑧：旧件 sha 与 SHA256SUMS 全等（只增不改）。"""
    out, _ = run_main(world, tmp_path, monkeypatch)
    proof = inf.verify_subgraph_integrity(out)
    assert proof["machine_proof"] is True
    assert (out / "chain_annotations.tsv").exists()  # 新件在位


def test_main_determinism_dual_run(world, tmp_path, monkeypatch):
    """验收⑥：双跑 chain_annotations.tsv 逐字节相等。"""
    out1, _ = run_main(world, tmp_path / "a", monkeypatch)
    out2, _ = run_main(world, tmp_path / "b", monkeypatch)
    b1 = (out1 / "chain_annotations.tsv").read_bytes()
    b2 = (out2 / "chain_annotations.tsv").read_bytes()
    assert b1 == b2


def test_source_never_reads_group_metadata():
    """红线：源码零 `metadata` 字样=组标签结构性隔离（不读分组文件）。"""
    import re as _re
    src = Path(inf.__file__).read_text(encoding="utf-8")
    # 精确断言：只禁文件操作（open/read），注释/文档字符串里的合法提及不算
    file_ops = _re.findall(r'(?:open|read_text|read_csv|load)[^\n]{0,60}metadata', src)
    assert not file_ops, f"源码存在 metadata 文件操作: {file_ops}"
    assert "ttest" not in src and "mannwhitney" not in src


def test_main_annotation_semantics_e2e(world, tmp_path, monkeypatch):
    """端到端语义抽查：CH-aaa 三队列 KO 标签逐队列独立（measurable/
    measurable/ko_absent——21612 的 K00001 全零）。"""
    out, rows = run_main(world, tmp_path, monkeypatch)
    by = {(r["chain_id"], r["cohort"]): r for r in rows}
    assert by[("CH-aaa", "SelfData_222")]["ko_label"] == "measurable"
    assert by[("CH-aaa", "PRJEB21612")]["ko_label"] == "ko_absent"
    assert by[("CH-ccc", "SelfData_222")]["ko_label"] == "no_chebi"
    assert by[("CH-ddd", "SelfData_222")]["ko_label"] == "unmapped_chebi"
    assert by[("CH-bbb", "PRJEB21612")]["microbe_presence"] == "present"
    assert by[("CH-bbb", "SelfData_222")]["microbe_presence"] == "absent"
