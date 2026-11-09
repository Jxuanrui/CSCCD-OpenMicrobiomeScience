"""③乙 度匹配随机链零模型测试（spec §三 验收条款的机器化）。

覆盖（对应规格 §3.4 验收 + 任务书六条）：
①种子钉扎复现（真实数据三跑 sha 全等——needs_data）
②golden v3 十三件不变（needs_data）
③零模型校准两指标在档 + 区分器三条分支（机械判据）
④n_skipped_by_pool / n_resample 披露与有放回证据
⑤scipy 对拍（可选依赖，跳过时不算失败）
⑥伪代码行 2/6/6b/7 逐条单测（分箱合并方向/池构成/有放回/池空跳过）
另加：冻结常量与预写解读句逐字防手抄、四道门禁 fail-closed、节点锚核对、
边源 sha 双门禁、只增不改幂等护栏、CLI 注册、报告零时间戳与确定性。
"""
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

import kg_tool.cli as cli  # noqa: E402
import kg_tool.degree_matched_null as dmn  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SG_REAL = ROOT / "data" / "kg" / "subgraph_htn_20261101_120403_t2merge"
FROZEN_13 = ROOT / "data" / "logs" / "golden_baseline_v3_20261102_sha256.txt"
SPEC = ROOT / "data" / "logs" / "upgrade_v03_spec_20261105.md"


def _write_tsv(path: Path, cols: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(cols)
        w.writerows(rows)


# ---------------------------------------------------------------
# 夹具（不依赖私有数据：12 节点 / 6 链 / 三队列标注）
# ---------------------------------------------------------------
MIC = ["m1", "m2", "m3"]
MET = {"M1": ("MetA", "CHEBI:1"), "M2": ("MetB", "CHEBI:2"),
       "M3": ("MetC", "")}
GENE = {"NCBIGene:1": "gene_a", "NCBIGene:2": "gene_b",
        "NCBIGene:3": "gene_c"}
PATH = ["h1", "h2", "h3"]
# (chain_id, microbe, metabolite, gene, pathway, depth)
CHAINS = [
    ("CH-1", "m1", "M1", "NCBIGene:1", "h1", 3),
    ("CH-2", "m1", "M2", "NCBIGene:2", "", 2),
    ("CH-3", "m2", "M3", "", "", 1),
    ("CH-4", "m2", "M1", "NCBIGene:3", "h2", 3),
    ("CH-5", "m3", "M2", "NCBIGene:1", "h3", 3),
    ("CH-6", "m3", "M3", "", "", 1),
]
VOTE1 = {"m1": 1, "m2": 0, "m3": 1}          # 102 条匹配器（夹具）
STRAIN = {"m2": 1}                            # 菌株可得（夹具）
KOFLAG = {"M1": 1, "M2": 0, "M3": 0}          # 票④端点可测（夹具）
# 票③索引：锚定节点 {M1}，未锚定名 {"gene_b"}（票③两桶各一）
V3_NODES = {"M1"}
V3_NAMES = {"gene_b"}


class FakeHyp:
    """生产 hypothesize 的最小替身（只覆盖本模块调用的五个入口）。

    为什么要替身：单测要能造"票值与随机链口算对得上"的夹具；生产规则
    本身由 needs_data 真实跑的门禁①-④（票③往返/恒等式/票②/票④）兜住。
    """

    def vote1(self, mid, name, matcher):
        return 1 if mid in matcher["taxids"] else 0

    def vote3(self, chain, idx):
        if chain["microbe_id"] in idx["node_ids"] \
                or chain["metabolite_id"] in idx["node_ids"]:
            return 1
        g = (chain["hop2_gene_name"] or "").strip().casefold()
        return 1 if g and g in idx["free_names"] else 0

    def strain_available(self, mid, name, idx):
        return 1 if mid in idx["taxids"] else 0

    def build_vote3_index(self, rows):
        nodes, names = set(), set()
        for r in rows:
            if r["bucket"] not in ("confirm", "extend"):
                continue
            for side, ent in (("a", "entity_a"), ("b", "entity_b")):
                nid = r.get(f"matched_node_{side}", "") or ""
                if nid:
                    nodes.add(nid)
                else:
                    names.add(r[ent].strip().casefold())
        return {"node_ids": nodes, "free_names": names}

    def load_ext102_rows(self, path):
        return [{"token": "", "name": "alpha", "family": "species"}]

    def build_vote1_matcher(self, ext_rows, bridge_rows):
        return {"taxids": set(VOTE1), "names": set(), "genera": set()}

    def load_strain_index(self, gutmgene_dir):
        return {"taxids": set(STRAIN), "names": set()}


class FakeInf:
    """生产 inference 的最小替身：只给 gate_ko_map 与 chain_ko_ids。"""

    def gate_ko_map(self, path):
        return {"CHEBI:1": ["K1"], "CHEBI:2": ["K2"]}

    def chain_ko_ids(self, chebi, ko_map):
        return list(ko_map.get(chebi, []))


def build_fixture(tmp_path: Path) -> dict:
    """造一个最小可用子图目录 + 两个外部边源 + 数据腿，返回关键路径。"""
    sg = tmp_path / "sg"
    sg.mkdir(parents=True, exist_ok=True)
    # —— chains.tsv
    _write_tsv(sg / "chains.tsv", dmn.REQUIRED_CHAIN_COLS, [
        [cid, mic, {"m1": "Alpha", "m2": "Beta", "m3": "Gamma"}[mic],
         met, MET[met][0], MET[met][1], gene, GENE.get(gene, ""), path,
         str(depth)]
        for cid, mic, met, gene, path, depth in CHAINS])
    # —— hypothesis_cards.tsv（票值按 FakeHyp 口径手算）
    rows = []
    for cid, mic, met, gene, path, depth in CHAINS:
        v1 = VOTE1[mic]
        v3 = 1 if (met in V3_NODES or GENE.get(gene, "").casefold() in V3_NAMES) else 0
        v4 = KOFLAG[met] + STRAIN.get(mic, 0)
        rows.append([cid, mic, {"m1": "Alpha", "m2": "Beta", "m3": "Gamma"}[mic],
                     met, MET[met][0], gene, GENE.get(gene, ""), path,
                     str(depth), str(v1), str(depth), str(v3), str(v4),
                     str(KOFLAG[met]), str(STRAIN.get(mic, 0)),
                     str(v1 + depth + v3 + v4)])
    _write_tsv(sg / "hypothesis_cards.tsv", dmn.REQUIRED_CARD_COLS, rows)
    # —— interaction_report.tsv（票③索引源：一锚定节点 M1 + 一未锚定名 gene_b）
    _write_tsv(sg / "interaction_report.tsv", dmn.REQUIRED_IREP_COLS, [
        ["confirm", "", "", "M1", ""],
        ["extend", "gene_b", "", "", ""],
    ])
    # —— chain_annotations.tsv（票④聚合源：代谢物→measurable 唯一）
    ann = []
    for cid, mic, met, gene, path, depth in CHAINS:
        lab = {"M1": "measurable", "M2": "ko_absent", "M3": "no_chebi"}[met]
        for coh in ("PRJEB13870", "PRJEB21612", "SelfData_222"):
            ann.append([cid, coh, lab])
    _write_tsv(sg / "chain_annotations.tsv", dmn.REQUIRED_ANN_COLS, ann)
    # —— merged_nodes.tsv（表3 名字桥源）
    nodes = []
    for m in MIC:
        nodes.append([m, {"m1": "Alpha", "m2": "Beta", "m3": "Gamma"}[m],
                      "Species", ""])
    for mid, (nm, _c) in MET.items():
        nodes.append([mid, nm, "Metabolite", ""])
    for gid, gn in GENE.items():
        nodes.append([gid, gn, "Gene", ""])
    for p in PATH:
        nodes.append([p, p, "Pathway", ""])
    _write_tsv(sg / "merged_nodes.tsv", dmn.REQUIRED_NODE_COLS, nodes)
    # —— merged_edges.tsv（敏感性臂 hop1 源）
    _write_tsv(sg / "merged_edges.tsv", ["subject", "predicate", "object"], [
        ["m1", "produces", "M2"], ["m3", "biotransforms", "M1"],
        ["m2", "produces", "M3"], ["m2", "alleviates", "M1"],
    ])
    # —— v01 边表（主臂源）
    v01 = tmp_path / "v01_edges.tsv"
    _write_tsv(v01, ["subject", "predicate", "object"], [
        ["m1", "produces", "M1"], ["m2", "produces", "M3"],
        ["M1", "modulates_host_gene", "NCBIGene:1"],
        ["m3", "regulates_host_gene", "NCBIGene:1"],
        ["NCBIGene:1", "participates_in", "h1"],
        ["NCBIGene:2", "participates_in", "h2"],
        ["NCBIGene:3", "participates_in", "h3"],
        ["NCBITaxon:999", "produces", "CHEBI:777"],   # 节点集外，应被丢弃
    ])
    # —— gutMGene 目录（表3 + 表1）
    gm = tmp_path / "gm"
    gm.mkdir(parents=True, exist_ok=True)
    with open(gm / dmn.GUTMGENE_TABLE3_NAME, "w", encoding="gb18030",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["Metabolite", "Gene ID", "Gene", "Index", "PMID",
                    "Metabolite ChEBI", "Metabolite KEGG"])
        # 四条边恰好覆盖链条的四个 (代谢物,基因) 对（hop2 层覆盖率应 100%）
        w.writerow(["MetA", "1", "gene_a", "1", "111", "CHEBI:1", ""])
        w.writerow(["MetB", "2", "gene_b", "2", "222", "CHEBI:2", ""])
        w.writerow(["MetA", "3", "gene_c", "3", "333", "CHEBI:1", ""])
        w.writerow(["MetB", "1", "gene_a", "4", "444", "CHEBI:2", ""])
    with open(gm / "Gut Microbe-Host Gene.csv", "w", encoding="gb18030",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["Gut Microbiota NCBI ID", "Gut Microbiota", "Strain"])
        w.writerow(["m2", "Beta", "DSM1"])
    # —— 其余数据腿
    ext = tmp_path / "ext.tsv"
    _write_tsv(ext, ["feature", "family"], [["s__alpha", "species"]])
    bridge = tmp_path / "bridge.tsv"
    _write_tsv(bridge, ["metaphlan_name", "taxid", "source"],
               [["alpha", "m1", "PRJEB21612"]])
    ko_map = tmp_path / "ko_map.tsv"
    _write_tsv(ko_map, ["chebi", "kos"],
               [["CHEBI:1", "K1"], ["CHEBI:2", "K2"]])
    # —— manifest（counts 与夹具节点数一致；边源 sha 为夹具文件真值）
    manifest = {
        "inputs_sha256": {
            "v01_edges": dmn.sha256_file(v01),
            "gutmgene_table3": dmn.sha256_file(gm / dmn.GUTMGENE_TABLE3_NAME),
        },
        "counts": {"n_chains_total": len(CHAINS), "distinct_microbes": 3,
                   "distinct_metabolites_in_chains": 3, "distinct_genes": 3,
                   "distinct_pathways": 3, "hop1_edges": 3},
    }
    (sg / "chains_manifest.json").write_text(json.dumps(manifest),
                                             encoding="utf-8")
    return {"sg": sg, "v01": v01, "gm": gm, "ext": ext, "bridge": bridge,
            "ko_map": ko_map}


@pytest.fixture()
def fx(tmp_path, monkeypatch):
    """夹具 + 把锚/边源前缀换成夹具值（生产门禁在 needs_data 里另测真值）。"""
    paths = build_fixture(tmp_path)
    monkeypatch.setattr(dmn, "NODE_ANCHORS",
                        {"microbe": 3, "metabolite": 3, "gene": 3,
                         "pathway": 3})
    monkeypatch.setattr(dmn, "V01_EDGES_SHA24",
                        dmn.sha256_file(paths["v01"])[:24])
    monkeypatch.setattr(dmn, "GUTMGENE_TABLE3_SHA24",
                        dmn.sha256_file(
                            paths["gm"] / dmn.GUTMGENE_TABLE3_NAME)[:24])
    monkeypatch.setattr(dmn, "_hypothesize", lambda: FakeHyp())
    monkeypatch.setattr(dmn, "_inference", lambda: FakeInf())
    paths["out"] = tmp_path / "out.md"
    return paths


# ---------------------------------------------------------------
# 冻结常量 / 预写句 / 伪代码对拍（防手抄）
# ---------------------------------------------------------------

def test_frozen_constants_match_spec():
    assert dmn.RNG_SEED == 20261105
    assert dmn.ALPHA == 0.05
    assert (dmn.BOUNDARY_LOW, dmn.BOUNDARY_HIGH) == (0.05, 0.10)
    assert dmn.MIN_BIN_SIZE == 5
    assert dmn.N_CHAINS_ANCHOR == 43773
    assert dmn.NODE_ANCHORS == {"microbe": 192, "metabolite": 201,
                                "gene": 197, "pathway": 219}
    assert dmn.CAL_RATIO_MIN == 0.70 and dmn.CAL_VAR_MIN == 0.50
    assert dmn.V01_EDGES_SHA24 == "41833d39db18e3dca52af284"
    assert dmn.GUTMGENE_TABLE3_SHA24 == "c909912b73df2abc2d69f2fa"


@pytest.mark.needs_data
def test_prewritten_sentences_verbatim_in_frozen_spec():
    """两句解读句必须逐字出现在冻结规格档——防"顺手润色"改写预注册措辞。"""
    text = SPEC.read_text(encoding="utf-8")
    assert dmn.RULE_WEAK_SENTENCE in text
    assert dmn.NO_INFO_SENTENCE in text


def test_pseudocode_steps_cover_every_line():
    steps = [s for s, _, _ in dmn.PSEUDOCODE_STEPS]
    for must in ("行1", "行2", "行3", "行4", "行5", "行6", "行6b", "行7",
                 "行8", "行9"):
        assert must in steps, f"伪代码 {must} 未登记对拍位置"
    assert all(fn for _, _, fn in dmn.PSEUDOCODE_STEPS)


def test_code_locations_have_real_line_numbers():
    for step, fn_name, line in dmn.code_locations():
        assert isinstance(line, int) and line > 0, f"{step}/{fn_name} 行号缺失"


def test_rng_semantics_note_has_required_phrase():
    assert "两检验零模型互不相关（不同抽取机制）" in dmn.RNG_SEMANTICS_NOTE


def test_ambiguities_registered_and_a13_flagged():
    codes = [c for c, _, _ in dmn.AMBIGUITIES]
    assert codes == [f"A{i}" for i in range(1, 16)]
    a13 = next((topic + how) for c, topic, how in dmn.AMBIGUITIES if c == "A13")
    assert "A'" in a13 and "评审" in a13 and "用户" in a13
    # A13 必须点出"v01 命名空间 vs chains MESH"这条磁盘事实，否则等于空口
    assert "CHEBI" in a13 and "MESH" in a13


# ---------------------------------------------------------------
# 度 / 分箱（伪代码行1-2）
# ---------------------------------------------------------------

def test_degrees_are_undirected_and_deduplicated():
    deg = dmn.compute_degrees(["a", "b", "c"], {("a", "b"), ("b", "c")})
    assert deg == {"a": 1, "b": 2, "c": 1}


def test_bins_exact_degree_merge_small_toward_larger():
    # 度 0:1 个 / 度 1:2 个 / 度 2:6 个 → 0 与 1 合并后仍 <5，再并入 2
    nodes, degree = [], {}
    for d, n in ((0, 1), (1, 2), (2, 6), (3, 7)):
        for i in range(n):
            nid = f"n{d}_{i}"
            nodes.append(nid)
            degree[nid] = d
    bins = dmn.bins_with_merge_map(nodes, degree)
    assert bins["n_bins"] == 2
    assert bins["rows"][0]["degrees"] == [0, 1, 2]
    assert bins["rows"][1]["degrees"] == [3]


def test_bins_max_bin_small_merges_into_smaller_side():
    # 最大箱只有 2 个节点（<5）→ 并入其小一侧（前一个箱）
    nodes, degree = [], {}
    for d, n in ((0, 6), (1, 6), (2, 2)):
        for i in range(n):
            nid = f"n{d}_{i}"
            nodes.append(nid)
            degree[nid] = d
    bins = dmn.bins_with_merge_map(nodes, degree)
    assert bins["rows"][-1]["degrees"] == [1, 2]
    assert bins["rows"][0]["degrees"] == [0]


def test_bins_all_nodes_small_single_bin():
    nodes = [f"x{i}" for i in range(3)]
    bins = dmn.bins_with_merge_map(nodes, {n: 7 for n in nodes})
    assert bins["n_bins"] == 1 and bins["rows"][0]["n_members"] == 3


def test_merge_map_covers_every_degree_exactly_once():
    nodes, degree = [], {}
    for d, n in ((0, 2), (1, 2), (2, 3), (3, 5), (9, 1)):
        for i in range(n):
            nid = f"n{d}_{i}"
            nodes.append(nid)
            degree[nid] = d
    bins = dmn.bins_with_merge_map(nodes, degree)
    seen = [d for r in bins["rows"] for d in r["degrees"]]
    assert sorted(seen) == sorted(set(degree.values()))
    assert len(seen) == len(set(seen))          # 无重复归属
    assert sum(r["n_members"] for r in bins["rows"]) == len(nodes)


def test_category_profile_counts_members():
    row = {"members": ["a", "b", "c"]}
    prof = dmn.category_profile(row, {"a": "microbe", "b": "microbe",
                                      "c": "gene"})
    assert prof == {"microbe": 2, "metabolite": 0, "gene": 1, "pathway": 0}


# ---------------------------------------------------------------
# 候选池 / 抽样（行6 / 6b / 7）
# ---------------------------------------------------------------

def _bins(degree_pairs):
    nodes = list(degree_pairs)
    degree = dict(degree_pairs)
    return degree, dmn.bins_with_merge_map(nodes, degree, min_size=1)


def test_candidate_pool_same_category_same_bin_excludes_self():
    degree, bins = _bins({"m1": 2, "m2": 2, "g1": 2, "m3": 3})
    cat = {"m1": "microbe", "m2": "microbe", "g1": "gene", "m3": "microbe"}
    pool = dmn.candidate_pool("m1", cat, bins["deg2bin"], bins["bin2mem"],
                              degree)
    assert pool == ["m2"]           # 同类别同箱、排除自己、不带别的类别


def test_candidate_pool_empty_when_category_singleton_in_bin():
    degree, bins = _bins({"M1": 1, "m1": 1, "g1": 1})
    cat = {"M1": "metabolite", "m1": "microbe", "g1": "gene"}
    assert dmn.candidate_pool("M1", cat, bins["deg2bin"], bins["bin2mem"],
                              degree) == []


def test_sample_skips_chain_and_counts_by_pool():
    cards = [{"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "M1",
              "hop2_gene": "", "hop3_pathway": "", "depth": "1"}]
    # 同箱里菌有两个（池非空），代谢物只有原节点自己（池空）→ 在代谢物位跳过
    degree, bins = _bins({"m1": 1, "m2": 1, "M1": 1})
    cat = {"m1": "microbe", "m2": "microbe", "M1": "metabolite"}
    smp = dmn.sample_random_chains(cards, cat, degree, bins["deg2bin"],
                                   bins["bin2mem"])
    assert smp["n_skipped_by_pool"] == 1
    assert smp["picks"] == [None]
    assert smp["n_skipped_by_category"] == {"metabolite": 1}
    assert smp["n_draws"] == 1          # 菌位抽了一次，代谢物位空→整链弃


def test_sampling_pinned_and_with_replacement():
    cards = [{"chain_id": f"CH-{i}", "microbe_id": "m1",
              "metabolite_id": "M1", "hop2_gene": "", "hop3_pathway": "",
              "depth": "1"} for i in range(40)]
    degree, bins = _bins({"m1": 1, "m2": 1, "m3": 1,
                          "M1": 1, "M2": 1})
    cat = {"m1": "microbe", "m2": "microbe", "m3": "microbe",
           "M1": "metabolite", "M2": "metabolite"}
    a = dmn.sample_random_chains(cards, cat, degree, bins["deg2bin"],
                                 bins["bin2mem"])
    b = dmn.sample_random_chains(cards, cat, degree, bins["deg2bin"],
                                 bins["bin2mem"])
    assert a["picks"] == b["picks"]                       # 钉扎可复现
    assert a["n_duplicate_draws"] > 0                     # 有放回真的发生


def test_n_resample_accounts_box_replays():
    cards = [{"chain_id": f"CH-{i}", "microbe_id": "m1",
              "metabolite_id": "M1", "hop2_gene": "", "hop3_pathway": "",
              "depth": "1"} for i in range(10)]
    degree, bins = _bins({"m1": 1, "m2": 1, "M1": 1, "M2": 1})
    cat = {"m1": "microbe", "m2": "microbe", "M1": "metabolite",
           "M2": "metabolite"}
    smp = dmn.sample_random_chains(cards, cat, degree, bins["deg2bin"],
                                   bins["bin2mem"])
    # 每链 2 次抽取（微/代），池只有两个（类别,箱）→ 回放 = 抽取数 − 池数
    assert smp["n_draws"] == 20
    assert smp["n_boxes_drawn"] == 2
    assert smp["n_resample"] == 20 - 2


def test_chain_node_sequence_truncates_by_depth():
    assert dmn.chain_node_sequence({"chain_id": "x", "microbe_id": "m",
                                    "metabolite_id": "M", "hop2_gene": "",
                                    "hop3_pathway": "", "depth": "1"}) == ["m", "M"]
    assert dmn.chain_node_sequence({"chain_id": "x", "microbe_id": "m",
                                    "metabolite_id": "M", "hop2_gene": "g",
                                    "hop3_pathway": "", "depth": "2"}) == \
        ["m", "M", "g"]
    assert dmn.chain_node_sequence({"chain_id": "x", "microbe_id": "m",
                                    "metabolite_id": "M", "hop2_gene": "g",
                                    "hop3_pathway": "h", "depth": "3"}) == \
        ["m", "M", "g", "h"]


def test_chain_node_sequence_fails_closed_on_empty_slot():
    with pytest.raises(SystemExit, match="结构不自洽"):
        dmn.chain_node_sequence({"chain_id": "x", "microbe_id": "m",
                                 "metabolite_id": "M", "hop2_gene": "",
                                 "hop3_pathway": "h", "depth": "3"})


# ---------------------------------------------------------------
# 统计量 / 校准 / 区分器
# ---------------------------------------------------------------

def test_paired_stats_basic_numbers():
    st = dmn.paired_stats([3, 2, 1, 4], [1, 2, 2, 1], [2, 2, 1, 2],
                          [2, 1, 1, 2])
    assert st["n_pairs"] == 4
    assert st["mean_diff"] == 1.0
    assert st["median_diff"] == 1.0
    assert st["sum_diff"] == 4
    assert st["computable_true"] == 1.75 / 2
    assert st["computable_random"] == 1.5 / 2
    assert st["computable_ratio"] == pytest.approx(1.5 / 1.75)
    assert 0 < st["ovl"] <= 1


def test_paired_stats_rejects_off_domain_computable():
    with pytest.raises(SystemExit, match="可算票数越界"):
        dmn.paired_stats([1], [1], [0], [1])        # 0 不在 {1,2}
    with pytest.raises(SystemExit, match="可算票数越界"):
        dmn.paired_stats([1], [1], [1], [3])


def test_overlap_coefficient_bounds_and_identity():
    assert dmn.overlap_coefficient([1, 2], [1, 2]) == 1.0
    assert dmn.overlap_coefficient([1, 1], [2, 2]) == 0.0
    assert dmn.overlap_coefficient([], [1]) == 0.0
    assert dmn.overlap_coefficient([1, 2, 3, 4], [1, 2, 3, 5]) == pytest.approx(0.75)


def test_discriminator_not_applicable_when_h1_holds():
    st = {"wilcoxon": {"degenerate": False, "p_two_sided": 1e-12},
          "mean_diff": 0.5, "computable_ratio": 0.1, "var_ratio": 0.1}
    assert dmn.discriminator(st)["verdict"] == "不适用"


def test_discriminator_rule_weak_by_metric1():
    st = {"wilcoxon": {"degenerate": False, "p_two_sided": 0.42},
          "mean_diff": -0.01, "computable_ratio": 0.69, "var_ratio": 0.9}
    d = dmn.discriminator(st)
    assert d["verdict"] == "尺子弱" and d["metric1"] is True
    assert d["sentence"] == dmn.RULE_WEAK_SENTENCE


def test_discriminator_rule_weak_by_metric2():
    st = {"wilcoxon": {"degenerate": False, "p_two_sided": 0.42},
          "mean_diff": 0.0, "computable_ratio": 0.99, "var_ratio": 0.49}
    d = dmn.discriminator(st)
    assert d["verdict"] == "尺子弱" and d["metric2"] is True


def test_discriminator_no_info_when_calibrated():
    st = {"wilcoxon": {"degenerate": False, "p_two_sided": 0.42},
          "mean_diff": 0.0, "computable_ratio": 0.995, "var_ratio": 0.95}
    d = dmn.discriminator(st)
    assert d["verdict"] == "路径无信息"
    assert d["sentence"] == dmn.NO_INFO_SENTENCE


def test_discriminator_not_applicable_on_opposite_direction():
    st = {"wilcoxon": {"degenerate": False, "p_two_sided": 1e-9},
          "mean_diff": -0.3, "computable_ratio": 0.1, "var_ratio": 0.1}
    d = dmn.discriminator(st)
    assert d["verdict"] == "不适用" and "方向与 H1 相反" in d["reason"]


def test_fmt_p_never_prints_zero_for_underflow():
    assert dmn.fmt_p(0.0) == "<1e-300（双精度下溢）"
    assert dmn.fmt_p(0.045) == "4.500000e-02"


def test_conclusion_sentence_uses_underflow_marker():
    st = {"wilcoxon": {"degenerate": False, "n_eff": 10, "p_two_sided": 0.0,
                       "z": 86.0, "rank_biserial": 0.5},
          "mean_diff": 0.5}
    s = dmn.conclusion_sentence(st)
    assert "双精度下溢" in s and "H1 方向成立" in s


# ---------------------------------------------------------------
# 端到端（夹具）：门禁/幂等/报告
# ---------------------------------------------------------------

def test_run_end_to_end_and_report_sections(fx):
    st = dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                 fx["bridge"], fx["ko_map"])
    text = fx["out"].read_text(encoding="utf-8")
    assert st["idempotent"] is False
    # §二 manifest 必须含 n_edges 与各源贡献（spec §3.4⑦）
    assert "n_edges（无向去重后）" in text
    assert "gutMGene 表3 边保留" in text
    assert "真链三层边在图中的覆盖率" in text
    # §三 分箱表 + 合并映射
    assert "精确 degree 值（合并映射）" in text
    # §五 披露
    assert "n_skipped_by_pool" in text and "n_resample" in text
    # §九 校准两指标 + 区分器
    assert "可算票比例" in text and "方差比" in text
    assert "区分器判定" in text
    # 两检验零模型互不相关声明
    assert "两检验零模型互不相关（不同抽取机制）" in text
    # 两臂都在档
    # v1.6 臂交换后：主口径=construction（真实链构造图），spec 降参考臂
    assert "主臂 `construction`" in text and "敏感性臂 `spec`" in text


def test_run_report_has_no_timestamp_and_is_deterministic(fx, tmp_path):
    dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"], fx["bridge"],
            fx["ko_map"])
    second = tmp_path / "out2.md"
    dmn.run(fx["sg"], second, fx["v01"], fx["gm"], fx["ext"], fx["bridge"],
            fx["ko_map"])
    a, b = fx["out"].read_bytes(), second.read_bytes()
    assert a == b
    text = a.decode("utf-8")
    for bad in ("2026-11-05 ", "UTC", "生成时间"):
        assert bad not in text


def test_run_idempotent_noop_then_drift_refused(fx):
    dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"], fx["bridge"],
            fx["ko_map"])
    again = dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                    fx["bridge"], fx["ko_map"])
    assert again["idempotent"] is True
    fx["out"].write_text("tampered", encoding="utf-8")
    with pytest.raises(SystemExit, match="拒写"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_gate_votes_identity_fails_closed(fx):
    p = fx["sg"] / "hypothesis_cards.tsv"
    rows = list(csv.DictReader(open(p, encoding="utf-8"), delimiter="\t"))
    rows[0]["votes_total"] = "99"
    _write_tsv(p, dmn.REQUIRED_CARD_COLS,
               [[r[c] for c in dmn.REQUIRED_CARD_COLS] for r in rows])
    with pytest.raises(SystemExit, match="恒等式门禁失配"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_gate_vote2_depth_fails_closed(fx):
    p = fx["sg"] / "hypothesis_cards.tsv"
    rows = list(csv.DictReader(open(p, encoding="utf-8"), delimiter="\t"))
    rows[0]["vote2_kg_depth"] = "1"
    rows[0]["votes_total"] = str(int(rows[0]["votes_total"]) - 2)
    _write_tsv(p, dmn.REQUIRED_CARD_COLS,
               [[r[c] for c in dmn.REQUIRED_CARD_COLS] for r in rows])
    with pytest.raises(SystemExit, match="恒等式门禁失配"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_gate_vote3_roundtrip_fails_closed(fx):
    p = fx["sg"] / "hypothesis_cards.tsv"
    rows = list(csv.DictReader(open(p, encoding="utf-8"), delimiter="\t"))
    rows[0]["vote3_gutmgene_cross"] = "0" if rows[0]["vote3_gutmgene_cross"] == "1" else "1"
    rows[0]["votes_total"] = str(int(rows[0]["votes_total"])
                                 + (1 if rows[0]["vote3_gutmgene_cross"] == "1" else -1))
    _write_tsv(p, dmn.REQUIRED_CARD_COLS,
               [[r[c] for c in dmn.REQUIRED_CARD_COLS] for r in rows])
    with pytest.raises(SystemExit, match="票③往返核验失配"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_node_anchor_mismatch_fails_closed(fx, monkeypatch):
    monkeypatch.setattr(dmn, "NODE_ANCHORS",
                        {"microbe": 3, "metabolite": 4, "gene": 3,
                         "pathway": 3})
    with pytest.raises(SystemExit, match="拿错链表"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_source_sha_gate_fails_on_tamper(fx):
    fx["v01"].write_text("subject\tpredicate\tobject\nx\ty\tz\n",
                         encoding="utf-8")
    with pytest.raises(SystemExit, match="边源被动过"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_missing_ext_tsv_fails_closed(fx):
    fx["ext"].unlink()
    with pytest.raises(SystemExit, match="缺票①源"):
        dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                fx["bridge"], fx["ko_map"])


def test_graph_manifest_sources_and_coverage(fx):
    st = dmn.run(fx["sg"], fx["out"], fx["v01"], fx["gm"], fx["ext"],
                 fx["bridge"], fx["ko_map"])
    prim, sens = st["arms"]["spec"], st["arms"]["construction"]
    # 主臂：v01 里节点集外的那条边必须被丢弃（丢 1 行）
    assert prim["graph"]["v01_rows"] == 8
    assert prim["graph"]["v01_dropped"] == 1
    # 敏感性臂：子图 hop1 只认 produces/biotransforms（alleviates 不进图）
    assert sens["graph"]["sub_kept"] == 3
    # 两臂的 hop2 层同源（表3）→ 覆盖率都应 100%
    assert prim["coverage"]["hop2"]["rate"] == 1.0
    assert sens["coverage"]["hop2"]["rate"] == 1.0
    # 敏感性臂 hop1 覆盖率高于主臂（夹具精心造：主臂缺 m1-M2/m3-M1）
    assert sens["coverage"]["hop1"]["rate"] >= prim["coverage"]["hop1"]["rate"]


def test_build_graph_arm_rejects_unknown_arm(fx):
    with pytest.raises(SystemExit, match="未知图臂"):
        dmn.build_graph_arm("nope", ["m1"], {"m1": "microbe"},
                            {"m1": "Alpha"}, [], [], [], [])


# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------

def test_cli_registers_degree_matched_null():
    assert "degree-matched-null" in cli.MODULES
    mf = cli.MODULES["degree-matched-null"][0]
    assert (cli.ROOT / "src" / mf).exists()


def test_cli_end_to_end_via_module_main(fx, monkeypatch, capsys):
    # 关闭 obs 自动解析（真 obs 会激活生产对拍→fixture 假 ko_map 撞 sha 红线）
    monkeypatch.setattr(dmn, "resolve_default_legs", lambda a, b: (a, None))
    rc = dmn.main(["--subgraph-dir", str(fx["sg"]),
                   "--output", str(fx["out"]),
                   "--v01-edges", str(fx["v01"]),
                   "--gutmgene-dir", str(fx["gm"]),
                   "--ext-tsv", str(fx["ext"]),
                   "--bridge-tsv", str(fx["bridge"]),
                   "--ko-map", str(fx["ko_map"])])
    assert rc == 0
    out = capsys.readouterr().out
    assert "n_skipped_by_pool" in out and "区分器" in out


# ---------------------------------------------------------------
# needs_data：真实锚 + 三跑复现 + golden 十三件
# ---------------------------------------------------------------

needs = pytest.mark.needs_data


def _real_paths() -> dict:
    main = Path("<REPO_ROOT>")
    return {
        "v01": main / "archive/Version01/data/merged/candidate_v3/merged_edges.tsv",
        "gm": Path("<LYTEAMWORK>/<USER>/Project/"
                   "Dietary_cohort_of_intestinal_flora/Project/"
                   "Project03_公共数据库/gutMGene"),
        "ext": main / ("data/observation/h1_htn_external_20261030_151856/"
                       "ext_21612_D_vs_H_discovery_20261030_151856.tsv"),
        "bridge": main / "data/logs/name_bridge_20261101/name_bridge_cohorts.tsv",
        "ko": main / ("archive/Version01/data/logs/external_evidence/"
                      "metabolite_ko_map_v2.tsv"),
    }


def _skip_if_no_data() -> dict:
    p = _real_paths()
    for k in ("v01", "ext", "bridge", "ko"):
        if not p[k].exists():
            pytest.skip(f"缺数据腿 {p[k]}")
    if not (p["gm"] / dmn.GUTMGENE_TABLE3_NAME).exists():
        pytest.skip("缺 gutMGene 表3")
    if not (SG_REAL / "chains.tsv").exists():
        pytest.skip("缺真实子图")
    return p


@needs
def test_real_run_three_times_shas_equal(tmp_path):
    p = _skip_if_no_data()
    outs = []
    for i in range(3):
        out = tmp_path / f"run{i}.md"
        dmn.run(SG_REAL, out, p["v01"], p["gm"], p["ext"], p["bridge"], p["ko"])
        outs.append(out.read_bytes())
    assert outs[0] == outs[1] == outs[2]
    assert len(outs[0]) > 10000


@needs
def test_real_anchors_gates_and_disclosures(tmp_path):
    p = _skip_if_no_data()
    st = dmn.run(SG_REAL, tmp_path / "real.md", p["v01"], p["gm"], p["ext"],
                 p["bridge"], p["ko"])
    assert st["n_cards"] == dmn.N_CHAINS_ANCHOR
    assert st["node_counts"] == dmn.NODE_ANCHORS
    assert st["gates"]["gate_pass"] is True
    prim, sens = st["arms"]["spec"], st["arms"]["construction"]
    # spec 字面臂的图缺陷必须被机器数记录下来（A13 的立论基础）
    assert prim["coverage"]["hop1"]["rate"] < 0.10
    assert prim["zero_degree"] > 0
    # 构造忠实臂必须重建 hop1 层
    assert sens["coverage"]["hop1"]["rate"] == 1.0
    assert sens["zero_degree"] == 0
    # 披露量都在
    assert prim["sampling"]["n_skipped_by_pool"] > 0
    assert prim["sampling"]["n_resample"] > 0
    assert prim["sampling"]["n_duplicate_draws"] > 0
    assert 0 < prim["computable_ratio"] <= 2


@needs
def test_golden_13_unchanged():
    _skip_if_no_data()
    rows = dmn.verify_golden_baseline(SG_REAL, FROZEN_13)
    assert len(rows) == 13
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
