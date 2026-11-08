"""②甲 子图基因层嫁接 + proximity 激活 测试（spec §四 验收条款的机器化）。

覆盖（对应任务书七项验收 + 规格 §4.5）：
①嫁接行数与锚（337 边/205 基因/23 代谢物…——夹具级口径 + needs_data 真实锚）
②新目录隔离（主子图件逐字节不变；新目录前缀保留原件行）
③manifest 完整（version 指纹/输入 sha/嫁接行数/跳过清单/各源边数）
④proximity 激活复核（LCC≥80%、种子/靶集缺节点率≤10%、不可达率）
⑤幂等护栏（no-op / 篡改 fail-closed）
⑥degraded_mode=0（新目录不再降级）
⑦新目录不再触发降级阈值（门禁三率逐项断言）
另加：join 键 casefold 精确（禁擅改，P1 条件②）、空 Gene ID 计数、V01 门禁
fail-closed、sidecar 十列 schema、双跑逐字节全等、描述性判定标签（不写 H1）、
CLI 注册、冻结常量、needs_data 真实锚 + golden 十三件。
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

import kg_tool.gene_graft as gg  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SG_REAL = ROOT / "data" / "kg" / "subgraph_htn_20261101_120403_t2merge"
MAIN_REPO = Path("<REPO_ROOT>")


# ---------------------------------------------------------------
# 夹具：最小子图 + 表3 + V01 三件
# ---------------------------------------------------------------

def _write_tsv(path: Path, cols: list[str], rows: list[list]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        w.writerows(rows)


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _make_subgraph(d: Path) -> Path:
    """最小子图：6 菌（同度）+3 代谢物+1 疾病；含 chains/top_hypotheses/manifest。

    列序刻意与真实子图不同（证明按列名读、不是按列位），但**必须含
    gene_graft.REQUIRED_* 全部列**——缺列会让嫁接值在写回时被静默丢弃，
    模块按 fail-closed 处理。
    """
    d.mkdir(parents=True, exist_ok=True)
    nodes = [
        ("M1", "microbe one", "Species"), ("M2", "microbe two", "Species"),
        ("M3", "microbe three", "Species"), ("M4", "microbe four", "Species"),
        ("M5", "microbe five", "Species"), ("M6", "microbe six", "Species"),
        # 名字与表3 大小写不同（证明 casefold 归一）；ZZZ 无表3 行
        ("MET:BUT", "Butyrate", "Metabolite"),
        ("MET:ACE", "acetate", "Metabolite"),
        ("MET:ZZZ", "Unknownmet", "Metabolite"),
        ("DIS:1", "hypertension", "Disease"),
    ]
    _write_tsv(d / "merged_nodes.tsv",
               ["id", "name", "category", "aliases", "xrefs",
                "tax_rank", "knowledge_layer"],
               [[i, n, c, "", "", "", "local_kg_llm_extracted"]
                for i, n, c in nodes])
    edges = [("M1", "MET:BUT"), ("M2", "MET:BUT"), ("M3", "MET:BUT"),
             ("M4", "MET:ACE"), ("M5", "MET:ACE"), ("M6", "MET:ZZZ"),
             ("MET:BUT", "DIS:1"), ("MET:ACE", "DIS:1"), ("MET:ZZZ", "DIS:1")]
    # 边表列=真实子图列集（27 列）的子集，但覆盖 REQUIRED_EDGE_COLS 全项
    ecols = ["subject", "predicate", "object", "source_type", "evidence_tier",
             "pmids", "support_count", "canonical_view", "source_ref",
             "curator", "source_id", "version", "knowledge_layer", "edge_id",
             "run_batch", "evidence_granularity", "provenance", "confidence"]
    _write_tsv(d / "merged_edges.tsv", ecols,
               [[s, "produces", o, "llm_extracted", "C", "", "1",
                 "derived_summary", "unattributed", "automated_pipeline",
                 "subgraph", "pipeline v2", "local_kg_llm_extracted",
                 f"E{i}", "run1", "名称级", "khop_expand", "0.9"]
                for i, (s, o) in enumerate(edges)])
    (d / "manifest.json").write_text(json.dumps(
        {"subgraph_id": d.name, "subgraph_version": "deadbeefcafe0001"},
        ensure_ascii=False), encoding="utf-8")
    _write_tsv(d / "chains.tsv",
               ["chain_id", "microbe_id", "metabolite_id", "depth", "hop2_gene"],
               [["CH-1", "M1", "MET:BUT", "3", "NCBIGene:2867"],
                ["CH-2", "M2", "MET:BUT", "3", "NCBIGene:3586"],
                ["CH-3", "M4", "MET:ACE", "3", "NCBIGene:2865"],
                ["CH-4", "M6", "MET:ZZZ", "1", ""]])
    _write_tsv(d / "hypothesis_cards.tsv",
               ["chain_id", "microbe_id", "metabolite_id", "hop2_gene",
                "votes_total", "borda_score"],
               [["CH-1", "M1", "MET:BUT", "NCBIGene:2867", "5", "9"],
                ["CH-2", "M2", "MET:BUT", "NCBIGene:3586", "4", "8"],
                ["CH-3", "M4", "MET:ACE", "NCBIGene:2865", "3", "7"]])
    _write_tsv(d / "top_hypotheses.tsv",
               ["chain_id", "microbe_id", "metabolite_id", "hop2_gene",
                "votes_total", "borda_score"],
               [["CH-1", "M1", "MET:BUT", "NCBIGene:2867", "5", "9"],
                ["CH-2", "M2", "MET:BUT", "NCBIGene:3586", "4", "8"],
                ["CH-3", "M4", "MET:ACE", "NCBIGene:2865", "3", "7"]])
    return d


def _make_gutmgene(d: Path) -> Path:
    """表3 最小夹具（gb18030；含空 Gene ID 行 + 未命中代谢物行）。"""
    d.mkdir(parents=True, exist_ok=True)
    cols = ["Index", "PMID", "Metabolite", "Metabolite ChEBI", "Gene",
            "Gene ID", "Associative mode"]
    rows = [
        ["1", "111", "Butyrate", "CHEBI:17968", "FFAR2", "2867", "causally"],
        ["1", "112", "Butyrate", "CHEBI:17968", "FFAR2", "2867", "causally"],
        ["2", "113", "Butyrate", "CHEBI:17968", "IL10", "3586", "causally"],
        # 大小写差异（子图写 acetate 小写）——casefold 后必须命中
        ["3", "114", "Acetate", "CHEBI:30089", "FFAR3", "2865", "causally"],
        # 空 Gene ID：无基因身份可挂，必须计数而非静默
        ["4", "115", "Acetate", "CHEBI:30089", "XXX", "", "causally"],
        # 子图没有的代谢物名（不得凭空造节点）
        ["5", "116", "Nowhere", "", "YYY", "9999", "causally"],
    ]
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    w.writerows(rows)
    (d / gg.TABLE3_NAME).write_bytes(buf.getvalue().encode("gb18030"))
    return d


def _make_v01(d: Path) -> dict:
    """V01 三件最小夹具（edges/chains/nodes）。"""
    d.mkdir(parents=True, exist_ok=True)
    edges = d / "merged_edges.tsv"
    _write_tsv(edges, ["subject", "predicate", "object", "source_id"],
               [["NCBIGene:3586", "participates_in", "KEGG:hsa04630", "kegg_rest"],
                ["NCBIGene:2867", "participates_in", "KEGG:hsa04080", "kegg_rest"],
                # 非 kegg_rest 源——不得进通路腿
                ["NCBIGene:2865", "participates_in", "KEGG:hsa99999", "other"]])
    chains = d / "mechanism_chains.tsv"
    _write_tsv(chains, ["gene", "pathway"],
               [["NCBIGene:3586", "KEGG:hsa04630"],
                ["NCBIGene:2867", "KEGG:hsa04080"]])
    nodes = d / "merged_nodes.tsv"
    _write_tsv(nodes, ["id", "name", "category"],
               [["NCBIGene:3586", "IL10", "Gene"],
                ["NCBIGene:2867", "FFAR2", "Gene"],
                ["NCBIGene:2865", "FFAR3", "Gene"],
                ["KEGG:hsa04630", "JAK-STAT", "Pathway"],
                ["KEGG:hsa04080", "Neuroactive", "Pathway"]])
    return {"edges": edges, "chains": chains, "nodes": nodes}


@pytest.fixture()
def fx(tmp_path, monkeypatch):
    """完整夹具 + V01 三门禁常量按夹具文件 sha/行数改写（仅测试内）。"""
    sg = _make_subgraph(tmp_path / "sg")
    gm = _make_gutmgene(tmp_path / "gm")
    v01 = _make_v01(tmp_path / "v01")
    dc = gg._load_deepchain()
    monkeypatch.setattr(dc, "V01_EDGES_SHA16", _sha16(v01["edges"]))
    monkeypatch.setattr(dc, "V01_CHAINS_SHA16", _sha16(v01["chains"]))
    monkeypatch.setattr(dc, "V01_CHAINS_ROWS", 2)
    monkeypatch.setattr(gg, "V01_NODES_SHA16", _sha16(v01["nodes"]))
    return {"sg": sg, "gm": gm, **v01}


def _run(fx, out: Path) -> dict:
    return gg.compute(fx["sg"], fx["gm"], fx["edges"], fx["chains"],
                      fx["nodes"], out)


# ---------------------------------------------------------------
# 冻结常量与 schema
# ---------------------------------------------------------------

def test_frozen_constants():
    """规格/探测钉扎常量逐字（改这里=改规格，必须过审）。"""
    assert gg.RNG_SEED == 20261105
    assert gg.N_PERM == 1000
    assert gg.ALPHA == 0.05
    assert gg.UNREACHABLE_MAX == 0.50
    assert gg.LCC_SHARE_MIN == 0.80
    assert gg.MISSING_RATE_MAX == 0.10
    assert gg.GENE_CATEGORY == "Gene"
    assert gg.PATHWAY_CATEGORY == "Pathway"
    assert gg.ANCHOR_GRAFT_GENE_EDGES == 337
    assert gg.ANCHOR_GENE_NODES == 205
    assert gg.ANCHOR_COVERED_METABOLITES == 23
    assert gg.ANCHOR_GENE_PATHWAY_EDGES == 962
    assert gg.ANCHOR_PATHWAY_NODES == 219
    assert gg.ANCHOR_COMPUTABLE_CHAINS == 43236
    assert gg.ACTIVE_SIDECAR_NAME == "graph_structure_scores_active.tsv"


def test_active_sidecar_schema_is_u2_ten_columns():
    """§4.3 "schema 同 U2 十列"——直接复用 U2 常量，多/少一列即破坏比对。"""
    from kg_tool import graph_scores as gs
    assert gg.ACTIVE_COLUMNS == gs.SIDECAR_COLUMNS
    assert gg.ACTIVE_COLUMNS == [
        "chain_id", "microbe_id", "hop2_gene", "proximity_z",
        "degree_centrality_disclosed", "community_degraded", "lcc_share",
        "seed_missing_rate", "target_missing_rate", "degraded_mode"]
    assert len(gg.ACTIVE_COLUMNS) == 10


def test_descriptive_verdict_never_says_h1():
    """②甲 是构造操作（§0.3）——判定标签不得出现 "H1"/"显著支持"字样。"""
    cases = [(None, None), (-2.0, 0.001), (2.0, 0.001), (-0.5, 0.07),
             (-0.5, 0.5)]
    for z, p in cases:
        label = gg.descriptive_verdict(z, p)
        assert "H1" not in label
        assert "支持" not in label
        assert label.startswith("描述性") or label.startswith("不可算")


# ---------------------------------------------------------------
# ① 嫁接行数/覆盖/空值与跳过清单（夹具级）
# ---------------------------------------------------------------

def test_graft_counts_and_skip_list(fx):
    """夹具口径：3 基因边/3 基因节点/2 基因-通路边/2 通路节点 + 跳过清单。"""
    g = gg.build_graft(fx["sg"], fx["gm"], fx["edges"], fx["chains"],
                       fx["nodes"])
    st = g["stats"]
    assert st["graft"]["n_gene_edges"] == 3        # (BUT×2 基因)+(ACE×1)
    assert st["graft"]["n_gene_nodes"] == 3
    assert st["graft"]["n_gene_pathway_edges"] == 2
    assert st["graft"]["n_pathway_nodes"] == 2
    assert st["graft"]["n_genes_without_pathway"] == 1   # 2865 无通路边
    assert st["join"]["n_matched_metabolites"] == 2      # butyrate/acetate
    assert st["join"]["n_rows_empty_gene_id"] == 1       # 空 Gene ID 计数
    assert [s["id"] for s in g["skipped"]] == ["MET:ZZZ"]
    # 链条覆盖：ZZZ 链不可算 → 3/4
    assert st["chain_coverage"]["n_chains_computable"] == 3
    assert st["chain_coverage"]["n_chains_total"] == 4
    # 同名代谢物-基因对多行：PMIDs 并集去重、support_count=2
    edge_lines = [ln for ln in g["edge_text"].splitlines()
                  if "NCBIGene:2867" in ln and "MET:BUT" in ln]
    assert len(edge_lines) == 1
    assert "111;112" in edge_lines[0]


def test_join_key_is_casefold_exact_not_fuzzy(fx):
    """join 键=名字 casefold 精确（P1 条件②）：大小写可命中，近似名不得命中。"""
    sg = _make_subgraph(fx["sg"].parent / "sg2")
    # 把代谢物改成同义词写法（表3 是 "Nowhere"，子图写 "nowhere else"）
    nodes = (sg / "merged_nodes.tsv").read_text(encoding="utf-8")
    nodes = nodes.replace("Unknownmet", "Nowhere else")
    (sg / "merged_nodes.tsv").write_text(nodes, encoding="utf-8")
    g = gg.build_graft(sg, fx["gm"], fx["edges"], fx["chains"], fx["nodes"])
    # "Nowhere else" ≠ "nowhere"（精确匹配，不做包含/模糊）
    assert g["stats"]["join"]["n_matched_metabolites"] == 2
    assert [s["id"] for s in g["skipped"]] == ["MET:ZZZ"]


def test_edge_columns_and_predicates(fx):
    """新边谓词：代谢物-基因=modulates_host_gene（A13 订正）、基因-通路=participates_in。"""
    g = gg.build_graft(fx["sg"], fx["gm"], fx["edges"], fx["chains"],
                       fx["nodes"])
    rows = list(csv.DictReader(g["edge_text"].splitlines(), delimiter="\t"))
    graft_rows = [r for r in rows if r.get("run_batch") == "v03_gene_graft"]
    assert len(graft_rows) == 5      # 3 + 2
    met_gene = [r for r in graft_rows
                if r["predicate"] == gg.GUTMGENE_PREDICATE]
    gene_pw = [r for r in graft_rows
               if r["predicate"] == gg.KEGG_PREDICATE]
    assert len(met_gene) == 3 and len(gene_pw) == 2
    assert all(r["source_id"] == "gutmgene_v3" for r in met_gene)
    assert all(r["source_id"] == "kegg_rest" for r in gene_pw)
    assert all(r["provenance"] == "gene_graft" for r in graft_rows)
    # edge_id 唯一且为 16 hex
    ids = [r["edge_id"] for r in graft_rows]
    assert len(set(ids)) == len(ids)
    assert all(len(i) == 16 and int(i, 16) >= 0 for i in ids)


# ---------------------------------------------------------------
# ② 新目录隔离（主子图零改动 + 前缀保留）
# ---------------------------------------------------------------

def _snapshot(d: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.iterdir()) if p.is_file()}


def test_main_subgraph_untouched_and_prefix_preserved(fx, tmp_path):
    """主子图件跑前跑后逐字节不变；新目录保留原件行逐字节前缀。"""
    before = _snapshot(fx["sg"])
    out = tmp_path / "newdir"
    _run(fx, out)
    assert _snapshot(fx["sg"]) == before           # 主子图零改动
    assert not (fx["sg"] / gg.ACTIVE_SIDECAR_NAME).exists()
    # 前缀保留：新 merged_nodes 以原件全部文本开头
    orig = (fx["sg"] / "merged_nodes.tsv").read_bytes()
    new = (out / "merged_nodes.tsv").read_bytes()
    assert new.startswith(orig)
    orig_e = (fx["sg"] / "merged_edges.tsv").read_bytes()
    assert (out / "merged_edges.tsv").read_bytes().startswith(orig_e)


def test_new_dir_is_separate_and_has_expected_files(fx, tmp_path):
    """新目录文件集固定六件（不多不少；防两次运行混放）。"""
    out = tmp_path / "newdir"
    _run(fx, out)
    assert {p.name for p in out.iterdir()} == {
        "merged_nodes.tsv", "merged_edges.tsv", "top_hypotheses.tsv",
        "chains.tsv",
        "graft_manifest.json", gg.ACTIVE_SIDECAR_NAME, "SHA256SUMS"}


# ---------------------------------------------------------------
# ③ manifest 完整
# ---------------------------------------------------------------

def test_manifest_complete(fx, tmp_path):
    """manifest 必含：version 指纹/输入 sha/嫁接行数/跳过清单/各源边数/门禁。"""
    out = tmp_path / "newdir"
    _run(fx, out)
    man = json.loads((out / "graft_manifest.json").read_text(encoding="utf-8"))
    for key in ("graft_version", "graft_fingerprint", "source_subgraph",
                "inputs_sha256", "v01_gates", "v01_anchor", "join", "graft",
                "counts", "n_edges_by_source", "chain_coverage",
                "chain_metabolite_distribution_top", "skipped_metabolites",
                "anchors", "disclosure", "determinism"):
        assert key in man, f"manifest 缺 {key}"
    assert man["graft_version"].startswith("deadbeef+graft.")
    assert man["source_subgraph"]["subgraph_version"] == "deadbeefcafe0001"
    assert set(man["inputs_sha256"]) == {
        "subgraph_merged_nodes", "subgraph_merged_edges", "subgraph_manifest",
        "subgraph_chains", "subgraph_top_hypotheses", "gutmgene_table3",
        "v01_edges", "v01_chains", "v01_nodes"}
    assert man["n_edges_by_source"] == {
        "subgraph_merged_edges": 9, "gutmgene_table3": 3, "kegg_rest": 2}
    assert len(man["skipped_metabolites"]) == 1
    assert all(v["pass"] for v in man["anchors"].values()) is False
    # 夹具数字≠生产锚 → 必须如实标 DEVIATE（不是静默通过）
    assert man["anchors"]["n_gene_edges"] == {
        "expect": 337, "got": 3, "pass": False}
    assert man["determinism"]["no_run_time_in_output_rows"] is True


def test_manifest_is_deterministic_no_timestamp(fx, tmp_path):
    """manifest 不含时间戳/目录名——不同目录双跑逐字节全等的前提。"""
    a, b = tmp_path / "a", tmp_path / "b"
    _run(fx, a)
    _run(fx, b)
    ma = (a / "graft_manifest.json").read_bytes()
    assert ma == (b / "graft_manifest.json").read_bytes()
    assert str(a).encode() not in ma


# ---------------------------------------------------------------
# ④⑥⑦ proximity 激活复核（门禁三率 + degraded_mode=0）
# ---------------------------------------------------------------

def test_activation_gate_and_degraded_zero(fx, tmp_path):
    """新目录不再触发降级阈值：LCC=1.0、缺节点率 0、degraded_mode=0。"""
    out = tmp_path / "newdir"
    st = _run(fx, out)
    act = st["activation"]
    gate = act["gate"]
    assert gate["lcc_share"] == pytest.approx(1.0)
    assert gate["seed_missing_rate"] == 0.0
    assert gate["target_missing_rate"] == 0.0
    assert gate["degraded"] is False
    assert act["degraded"] is False
    lines = (out / gg.ACTIVE_SIDECAR_NAME).read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == gg.ACTIVE_COLUMNS
    rows = [ln.split("\t") for ln in lines[1:]]
    assert len(rows) == 3
    assert all(len(r) == 10 for r in rows)
    assert {r[9] for r in rows} == {"0"}           # degraded_mode 全 0
    assert all(r[6] == "1.000000" for r in rows)   # lcc_share 列
    assert all(r[7] == "0.000000" and r[8] == "0.000000" for r in rows)


def test_activation_population_amspl_and_chain_rows(fx, tmp_path):
    """人口级 AMSPL=2（M1/M2/M4 到端点基因都 2 跳）；逐链 z 可算且有限。"""
    out = tmp_path / "newdir"
    st = _run(fx, out)
    pop = st["activation"]["population"]
    assert (pop["n_seeds"], pop["n_targets"], pop["n_pairs"]) == (3, 3, 3)
    assert pop["d_c"] == pytest.approx(2.0)
    assert pop["unreachable_rate"] == 0.0
    assert pop["z"] is not None and pop["p"] is not None
    rows = st["activation"]["rows"]
    assert [r["d_obs"] for r in rows] == [2, 2, 2]
    assert all(r["z"] is not None for r in rows)
    # 度中心性/同连通片：新目录全连通 → community=1
    assert all(r["community"] == "1" for r in rows)
    assert all(r["degree_centrality"] is not None for r in rows)


def test_pair_distance_table_matches_bfs(fx, tmp_path):
    """表驱动零模型与 proximity.bfs_distances 同口径（防两套距离实现漂移）。"""
    import kg_tool.proximity as prox
    out = tmp_path / "newdir"
    _run(fx, out)
    nodes, g = prox.load_graph(out)
    adj = {n: g[n]["adj"] for n in nodes}
    seeds = [n for n in nodes if g[n]["category"] in prox.SEED_CATEGORIES]
    genes = [n for n in nodes if g[n]["category"] == gg.GENE_CATEGORY]
    table = gg.pair_distance_table(adj, seeds, genes)
    for s in seeds:
        dist = prox.bfs_distances(adj, s)
        for t in genes:
            assert table[s].get(t) == dist.get(t)


# ---------------------------------------------------------------
# ⑤ 幂等护栏
# ---------------------------------------------------------------

def test_idempotent_rerun_and_tamper_fail_closed(fx, tmp_path):
    """同目录重跑=no-op；任一字节篡改=拒写（不半覆盖）。"""
    out = tmp_path / "newdir"
    first = _run(fx, out)
    before = _snapshot(out)
    second = _run(fx, out)
    assert first["shas"] == second["shas"]
    assert _snapshot(out) == before
    # 篡改 sidecar → 拒写
    p = out / gg.ACTIVE_SIDECAR_NAME
    p.write_text(p.read_text(encoding="utf-8") + "tampered\n",
                 encoding="utf-8")
    with pytest.raises(SystemExit, match="拒写"):
        _run(fx, out)
    # 篡改节点表 → 同样拒写（且不写任何件）
    p2 = out / "merged_nodes.tsv"
    p2.write_text(p2.read_text(encoding="utf-8") + "M9\tx\tSpecies\n",
                  encoding="utf-8")
    with pytest.raises(SystemExit, match="拒写"):
        _run(fx, out)


def test_extra_file_in_out_dir_fails_closed(fx, tmp_path):
    """新目录出现多余文件=两次运行混放 → fail-closed。"""
    out = tmp_path / "newdir"
    _run(fx, out)
    (out / "stray.tsv").write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="多余文件"):
        _run(fx, out)


# ---------------------------------------------------------------
# 双跑复现 + 评估档
# ---------------------------------------------------------------

def test_double_run_all_bytes_equal(fx, tmp_path):
    """不同目录双跑：全部产物逐字节全等（零时间戳/排序键固定）。"""
    a, b = tmp_path / "runA", tmp_path / "runB"
    ra = _run(fx, a)
    rb = _run(fx, b)
    assert ra["shas"] == rb["shas"]
    assert ra["eval_md"] == rb["eval_md"]


def test_eval_md_contains_p1_conditions(fx, tmp_path):
    """监工 P1 两条件强制入档：SCFA 限定句 + join 键未改声明；且不写 H1。"""
    md = _run(fx, tmp_path / "newdir")["eval_md"]
    assert "主要反映 SCFA 主轴" in md
    assert "有效自由度远小于链数" in md
    assert "禁擅改" in md or "join 键未改" in md
    assert "构造性循环" in md
    assert "不计入检验族" in md
    assert "H1" not in md
    assert "degraded_mode = 0" in md


# ---------------------------------------------------------------
# V01 门禁 fail-closed
# ---------------------------------------------------------------

def test_v01_gate_fails_on_tampered_edges(fx, tmp_path, monkeypatch):
    """V01 边表 sha 不符 → 拒跑（资产被动过）。"""
    dc = gg._load_deepchain()
    monkeypatch.setattr(dc, "V01_EDGES_SHA16", "0" * 16)
    with pytest.raises(SystemExit, match="资产被动过"):
        gg.build_graft(fx["sg"], fx["gm"], fx["edges"], fx["chains"],
                       fx["nodes"])


def test_v01_gate_fails_on_tampered_nodes(fx, monkeypatch):
    """V01 节点表 sha 不符（第四道门禁）→ 拒跑。"""
    monkeypatch.setattr(gg, "V01_NODES_SHA16", "0" * 16)
    with pytest.raises(SystemExit, match="节点表 sha16"):
        gg.build_graft(fx["sg"], fx["gm"], fx["edges"], fx["chains"],
                       fx["nodes"])


def test_gene_node_id_collision_fails_closed(tmp_path, monkeypatch, fx):
    """子图已有同 id 基因节点 → 拒跑（同 id 两义=度/距离失真）。"""
    sg = _make_subgraph(tmp_path / "sg3")
    p = sg / "merged_nodes.tsv"
    p.write_text(p.read_text(encoding="utf-8")
                 + "NCBIGene:2867\tFFAR2\tGene\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="冲突"):
        gg.build_graft(sg, fx["gm"], fx["edges"], fx["chains"], fx["nodes"])


# ---------------------------------------------------------------
# 读档 fail-closed
# ---------------------------------------------------------------

def test_missing_manifest_fails_closed(fx, tmp_path):
    sg = _make_subgraph(tmp_path / "sg4")
    (sg / "manifest.json").unlink()
    with pytest.raises(SystemExit, match="version 指纹"):
        gg.build_graft(sg, fx["gm"], fx["edges"], fx["chains"], fx["nodes"])


def test_missing_table3_fails_closed(fx, tmp_path):
    gm = _make_gutmgene(tmp_path / "gm2")
    (gm / gg.TABLE3_NAME).unlink()
    with pytest.raises(SystemExit, match="缺输入"):
        gg.build_graft(fx["sg"], gm, fx["edges"], fx["chains"], fx["nodes"])


# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------

def test_cli_registers_gene_graft():
    assert "gene-graft" in cli.MODULES
    mf = cli.MODULES["gene-graft"][0]
    assert (cli.ROOT / "src" / mf).exists()


def test_cli_end_to_end(fx, tmp_path, capsys):
    """CLI 端到端：主干输出含锚核对/门禁/degraded_mode。"""
    rc = gg.main(["--subgraph-dir", str(fx["sg"]),
                  "--out-dir", str(tmp_path / "cli_out"),
                  "--gutmgene-dir", str(fx["gm"]),
                  "--v01-edges", str(fx["edges"]),
                  "--v01-chains", str(fx["chains"]),
                  "--v01-nodes", str(fx["nodes"]),
                  "--eval-out", str(tmp_path / "eval.md")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "degraded_mode=0" in out
    assert "锚核对" in out
    assert (tmp_path / "eval.md").exists()


def test_cli_golden_verify_mode(fx, tmp_path, capsys):
    """--verify-golden 只核验不写产物（缺清单=fail-closed）。"""
    gf = tmp_path / "golden.txt"
    (fx["sg"] / "merged_nodes.tsv")
    sha = hashlib.sha256((fx["sg"] / "merged_nodes.tsv").read_bytes()).hexdigest()
    gf.write_text(f"{sha}  merged_nodes.tsv\n", encoding="utf-8")
    rc = gg.main(["--subgraph-dir", str(fx["sg"]), "--verify-golden",
                  "--golden-list", str(gf)])
    assert rc == 0
    assert "不一致 0 件" in capsys.readouterr().out


# ---------------------------------------------------------------
# needs_data：真实锚 + golden 十三件 + 激活实值
# ---------------------------------------------------------------

needs = pytest.mark.needs_data


def _real_paths() -> dict:
    return {
        "sg": MAIN_REPO / "data/kg/subgraph_htn_20261101_120403_t2merge",
        "gm": Path(gg.GUTMGENE_DIR_DEFAULT),
        "edges": MAIN_REPO / gg.V01_EDGES_DEFAULT,
        "chains": MAIN_REPO / gg.V01_CHAINS_DEFAULT,
        "nodes": MAIN_REPO / gg.V01_NODES_DEFAULT,
    }


def _skip_if_no_data(p: dict) -> dict:
    for k in ("edges", "chains", "nodes"):
        if not p[k].exists():
            pytest.skip(f"缺 V01 数据腿 {p[k]}")
    if not (p["gm"] / gg.TABLE3_NAME).exists():
        pytest.skip("缺 gutMGene 表3")
    if not (p["sg"] / "chains.tsv").exists():
        pytest.skip("缺真实子图")
    return p


@needs
def test_real_anchor_counts(tmp_path):
    """真实锚：337/205/962/219/23 + 43,236 可算链 + V01 四门禁全过。"""
    p = _skip_if_no_data(_real_paths())
    g = gg.build_graft(p["sg"], p["gm"], p["edges"], p["chains"], p["nodes"])
    st = g["stats"]
    assert st["graft"]["n_gene_edges"] == 337
    assert st["graft"]["n_gene_nodes"] == 205
    assert st["graft"]["n_gene_pathway_edges"] == 962
    assert st["graft"]["n_pathway_nodes"] == 219
    assert st["graft"]["n_genes_without_pathway"] == 147
    assert st["join"]["n_matched_metabolites"] == 23
    assert st["join"]["n_subgraph_metabolites"] == 274
    assert st["join"]["n_skipped_metabolites"] == 251
    assert st["join"]["n_table3_rows"] == 1049
    assert st["join"]["n_table3_distinct_names"] == 122
    assert st["join"]["n_rows_empty_gene_id"] == 1
    assert st["chain_coverage"]["n_chains_computable"] == 43236
    assert st["chain_coverage"]["n_chains_total"] == 43773
    assert all(v["pass"] for v in g["manifest"]["anchors"].values())
    assert all(v["pass"] for v in g["manifest"]["v01_gates"].values())
    assert g["manifest"]["v01_anchor"]["violations"] == 0
    assert 0.97 < g["manifest"]["chain_share_top4"] < 0.98
    assert g["manifest"]["counts"]["n_nodes_after"] == 1181
    assert g["manifest"]["counts"]["n_edges_rows_after"] == 3955


@needs
def test_real_activation_degraded_zero(tmp_path):
    """真实激活：LCC=1.0、缺节点率 0、degraded_mode=0、人口结构锚。"""
    p = _skip_if_no_data(_real_paths())
    st = gg.compute(p["sg"], p["gm"], p["edges"], p["chains"], p["nodes"],
                    tmp_path / "out")
    act = st["activation"]
    assert act["gate"]["lcc_share"] == pytest.approx(1.0)
    assert act["gate"]["seed_missing_rate"] == 0.0
    assert act["gate"]["target_missing_rate"] == 0.0
    assert act["degraded"] is False
    pop = act["population"]
    # v1.8 人口改全链（规则 10）：种子=全链 distinct 菌、靶集=全链 distinct 端点基因
    assert pop["n_seeds"] >= 100 and pop["n_targets"] >= 190
    assert pop["n_pairs"] >= 6000   # distinct (菌,基因) 对（实测 6,957）
    assert len(act["rows"]) <= 100  # 逐链 TOP-N 上限（构造性循环下不铺全链）
    assert pop["unreachable_rate"] == 0.0
    # 构造性循环的机器证据：全部 TOP 链端点距离=2
    assert {r["d_obs"] for r in act["rows"]} == {2}
    side = (tmp_path / "out" / gg.ACTIVE_SIDECAR_NAME).read_text(
        encoding="utf-8").splitlines()
    assert len(side) == 101                    # 表头 + TOP-100 逐链行（v1.8）
    assert {ln.split("\t")[9] for ln in side[1:]} == {"0"}


@needs
def test_real_double_run_equal_and_golden_unchanged(tmp_path):
    """双跑逐字节全等 + 主子图 golden 十三件跑后仍逐字节不变。"""
    p = _skip_if_no_data(_real_paths())
    a = gg.compute(p["sg"], p["gm"], p["edges"], p["chains"], p["nodes"],
                   tmp_path / "a")
    b = gg.compute(p["sg"], p["gm"], p["edges"], p["chains"], p["nodes"],
                   tmp_path / "b")
    assert a["shas"] == b["shas"]
    assert a["eval_md"] == b["eval_md"]
    rows = gg.verify_golden_baseline(p["sg"],
                                     ROOT / gg.GOLDEN_LIST_DEFAULT)
    assert len(rows) == 13
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
