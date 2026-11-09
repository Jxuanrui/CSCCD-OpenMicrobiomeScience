"""U2 图结构分 sidecar 测试（B2，spec v1.1 验证条款的机器化）。

覆盖：①种子钉扎复现（同输入两处独立跑 sha 相等——验证条款①）②排序键
零改动行为锁（验证条款③）③golden 12 件不变（验证条款②，needs_data 档）
④sidecar schema 10 列冻结 ⑤LCC 门禁三触发路（占比/种子缺/靶缺）+未触发
全列 ⑥只增不改幂等护栏 ⑦fail-closed 族（缺档/缺列/悬空边/重复 id）。
"""
import csv
import hashlib
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

from kg_tool import graph_scores as gs  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SG_REAL = ROOT / "data" / "kg" / "subgraph_htn_20261101_120403_t2merge"


# ---------- 夹具建子图 ----------

def _write_tsv(path: Path, cols: list[str], rows: list[list[str]]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(cols)
        w.writerows(rows)


def _make_subgraph(d: Path, nodes: list[str], edges: list[tuple[str, str]],
                   chains: list[dict]) -> Path:
    """最小子图：三表齐备。列序刻意加冗余列——证明读列按名不按位。"""
    d.mkdir(parents=True, exist_ok=True)
    _write_tsv(d / "merged_nodes.tsv",
               ["id", "name", "category"],
               [[n, f"name_{n}", "X"] for n in nodes])
    _write_tsv(d / "merged_edges.tsv",
               ["edge_id", "subject", "predicate", "object", "confidence"],
               [[f"E{i}", s, "affects", o, "0.9"] for i, (s, o)
                in enumerate(edges)])
    _write_tsv(d / "top_hypotheses.tsv",
               ["chain_id", "microbe_id", "microbe_name", "hop2_gene",
                "hop2_gene_name", "borda_score"],
               [[c["chain_id"], c["microbe_id"], "m", c["hop2_gene"], "g", "1"]
                for c in chains])
    return d


# 10 节点线图：L0-L1-...-L8 连通 + ISO 孤立（LCC=9/10=90% ≥80%，门禁不触发；
# ISO 在节点表（缺节点率 0）但与 L0 不可达——专测"可达性与缺节点是两回事"）
LINE = [(f"L{i}", f"L{i + 1}") for i in range(8)]
LINE_NODES = [f"L{i}" for i in range(9)] + ["ISO"]
LINE_EDGES = LINE  # ISO 无边

CHAINS_LINE = [
    {"chain_id": "CH-adj", "microbe_id": "L0", "hop2_gene": "L1"},   # 相邻 d=1
    {"chain_id": "CH-far", "microbe_id": "L0", "hop2_gene": "L8"},   # 直径 d=8
    {"chain_id": "CH-iso", "microbe_id": "L0", "hop2_gene": "ISO"},  # 不可达
]


# ---------- ④ schema 冻结 ----------

def test_schema_columns_frozen():
    assert gs.SIDECAR_COLUMNS == [
        "chain_id", "microbe_id", "hop2_gene",
        "proximity_z", "degree_centrality_disclosed", "community_degraded",
        "lcc_share", "seed_missing_rate", "target_missing_rate",
        "degraded_mode"]
    assert len(gs.SIDECAR_COLUMNS) == 10


# ---------- 全列模式（门禁未触发） ----------

def test_full_mode_three_score_columns(tmp_path):
    """90% LCC+零缺节点：门禁不触发——proximity_z/度中心性/社区列全产。"""
    d = _make_subgraph(tmp_path, LINE_NODES, LINE_EDGES, CHAINS_LINE)
    stats = gs.annotate(d)
    assert stats["degraded"] is False
    assert stats["n_proximity_computed"] == 2        # 相邻+直径可算；ISO 不可达=na
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    assert len(rows) == 3
    by = {r["chain_id"]: r for r in rows}
    adj_check = rows[0]["degraded_mode"] == "0"
    assert adj_check
    # ① 相邻对 z<0（比零模型近），直径对 z>0（比零模型远）——方向性机械可验
    z_adj = float(by["CH-adj"]["proximity_z"])
    z_far = float(by["CH-far"]["proximity_z"])
    assert z_adj < 0 < z_far
    assert by["CH-iso"]["proximity_z"] == "na"       # 不可达不硬造 z
    # ② 度中心性手算：L1 度=2（连 L0/L2）→ 2/(10-1)；L8 度=1 → 1/9；ISO 度=0
    assert by["CH-adj"]["degree_centrality_disclosed"] == f"{2 / 9:.6f}"
    assert by["CH-far"]["degree_centrality_disclosed"] == f"{1 / 9:.6f}"
    assert by["CH-iso"]["degree_centrality_disclosed"] == f"{0 / 9:.6f}"
    # ③ 同连通片判定：两 L 节点同片=1；ISO 与 L0 异片=0
    assert by["CH-adj"]["community_degraded"] == "1"
    assert by["CH-iso"]["community_degraded"] == "0"
    # 描述性常量列与门禁值一致
    assert by["CH-adj"]["lcc_share"] == f"{9 / 10:.6f}"
    assert by["CH-adj"]["seed_missing_rate"] == "0.000000"
    assert by["CH-adj"]["target_missing_rate"] == "0.000000"


def test_degree_centrality_star_hand_check(tmp_path):
    """星图手算：中心度=4/(5-1)=1.0，叶=1/4——分母=n_nodes-1 满刻度。"""
    nodes = ["c", "a", "b", "e", "f"]
    edges = [("c", n) for n in ("a", "b", "e", "f")]
    chains = [{"chain_id": "CH-c", "microbe_id": "a", "hop2_gene": "c"},
              {"chain_id": "CH-a", "microbe_id": "c", "hop2_gene": "a"},
              {"chain_id": "CH-x", "microbe_id": "a", "hop2_gene": "ZZZ"}]
    d = _make_subgraph(tmp_path, nodes, edges, chains)
    gs.annotate(d)
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        by = {r["chain_id"]: r for r in csv.DictReader(f, delimiter="\t")}
    assert by["CH-c"]["degree_centrality_disclosed"] == "1.000000"
    assert by["CH-a"]["degree_centrality_disclosed"] == f"{1 / 4:.6f}"
    assert by["CH-x"]["degree_centrality_disclosed"] == "na"   # 缺图基因=na
    assert by["CH-x"]["community_degraded"] == "na"            # 缺任一节点=na
    assert by["CH-x"]["proximity_z"] == "na"                   # 缺图不可算邻近


def test_proximity_z_rng_reset_reproducible():
    """钉扎 RNG 逐次重置：同一对两次独立调用结果逐位相等（链间零传染）。"""
    nodes = [f"L{i}" for i in range(9)]
    adj = {n: set() for n in nodes}
    for i in range(8):
        adj[f"L{i}"].add(f"L{i + 1}")
        adj[f"L{i + 1}"].add(f"L{i}")
    ns = sorted(nodes)
    z1 = gs.proximity_z(adj, ns, ["L0"], ["L8"])
    z2 = gs.proximity_z(adj, ns, ["L0"], ["L8"])
    assert z1 is not None and z1 == z2
    # Guney 集公式手算：d(S,T) 单靶=最短路本身（8）；d 相邻=1
    assert gs.proximity_d(adj, ["L0"], ["L8"]) == 8.0
    assert gs.proximity_d(adj, ["L0"], ["L1"]) == 1.0
    # 多靶平均最短路：{L1,L3} 从 L0 → (1+3)/2=2.0
    assert gs.proximity_d(adj, ["L0"], ["L1", "L3"]) == 2.0
    # 不可达 → None（不折算 inf）
    adj2 = {**adj, "ISO": set()}
    assert gs.proximity_d(adj2, ["L0"], ["ISO"]) is None


# ---------- ⑤ LCC 门禁三触发路 ----------

def test_gate_low_lcc_share_degrades(tmp_path):
    """占比路：4/10=40% <80% → 降级，proximity_z 全 na，零模型零消耗。"""
    nodes = [f"n{i}" for i in range(10)]
    edges = [(f"n{i}", f"n{i + 1}") for i in range(3)]        # 片1: n0-n3
    edges += [(f"n{i}", f"n{i + 1}") for i in range(4, 8)]    # 片2: n4-n8
    chains = [{"chain_id": "CH-1", "microbe_id": "n0", "hop2_gene": "n2"}]
    d = _make_subgraph(tmp_path, nodes, edges, chains)
    stats = gs.annotate(d)
    assert stats["degraded"] is True
    assert stats["n_proximity_computed"] == 0    # 降级=硬分不产（零模型不跑）
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f, delimiter="\t"))
    assert row["proximity_z"] == "na"
    assert row["degraded_mode"] == "1"
    assert row["lcc_share"] == f"{5 / 10:.6f}"
    # 描述性列仍产（降级=只报描述性，不是整行空白）
    assert row["degree_centrality_disclosed"] == f"{2 / 9:.6f}"
    assert row["community_degraded"] == "1"


def test_gate_target_missing_degrades(tmp_path):
    """靶缺路（=生产实况）：基因全不在图 → 缺节点率 100%>10% → 降级。"""
    nodes = [f"L{i}" for i in range(9)]
    edges = [(f"L{i}", f"L{i + 1}") for i in range(8)]
    chains = [{"chain_id": "CH-1", "microbe_id": "L0", "hop2_gene": "NCBIGene:3586"}]
    d = _make_subgraph(tmp_path, nodes, edges, chains)
    stats = gs.annotate(d)
    assert stats["degraded"] is True
    assert stats["gate"]["target_missing_rate"] == 1.0
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f, delimiter="\t"))
    assert row["proximity_z"] == "na"
    assert row["degree_centrality_disclosed"] == "na"
    assert row["community_degraded"] == "na"
    assert row["target_missing_rate"] == "1.000000"
    assert row["degraded_mode"] == "1"


def test_gate_seed_missing_degrades(tmp_path):
    """种子缺路：菌缺图 1/2=50%>10% → 降级。"""
    nodes = ["L0", "L1", "L2"]
    edges = [("L0", "L1"), ("L1", "L2")]
    chains = [{"chain_id": "CH-1", "microbe_id": "L0", "hop2_gene": "L2"},
              {"chain_id": "CH-2", "microbe_id": "MISSING:1", "hop2_gene": "L2"}]
    d = _make_subgraph(tmp_path, nodes, edges, chains)
    stats = gs.annotate(d)
    assert stats["degraded"] is True
    assert stats["gate"]["seed_missing_rate"] == 0.5


# ---------- ① 种子钉扎复现（验证条款①） ----------

def test_seed_pinned_reproduction_two_runs(tmp_path):
    """同输入两处独立跑 → sidecar 逐字节相等（sha256 对比）。"""
    inputs = (LINE_NODES, LINE_EDGES, CHAINS_LINE)
    d1 = _make_subgraph(tmp_path / "run1", *inputs)
    d2 = _make_subgraph(tmp_path / "run2", *inputs)
    gs.annotate(d1)
    gs.annotate(d2)
    s1 = hashlib.sha256((d1 / gs.SIDECAR_NAME).read_bytes()).hexdigest()
    s2 = hashlib.sha256((d2 / gs.SIDECAR_NAME).read_bytes()).hexdigest()
    assert s1 == s2
    # 降级夹具同样钉扎复现（描述性列路径零随机）
    inputs_deg = ([f"L{i}" for i in range(9)], LINE,
                  [{"chain_id": "CH-1", "microbe_id": "L0",
                    "hop2_gene": "NCBIGene:3586"}])
    d3 = _make_subgraph(tmp_path / "run3", *inputs_deg)
    d4 = _make_subgraph(tmp_path / "run4", *inputs_deg)
    gs.annotate(d3)
    gs.annotate(d4)
    s3 = hashlib.sha256((d3 / gs.SIDECAR_NAME).read_bytes()).hexdigest()
    s4 = hashlib.sha256((d4 / gs.SIDECAR_NAME).read_bytes()).hexdigest()
    assert s3 == s4


def test_row_order_follows_top_hypotheses(tmp_path):
    """行序=top_hypotheses 文件原序（零排序变更红线）。"""
    chains = [{"chain_id": f"CH-{i}", "microbe_id": "L0", "hop2_gene": "L1"}
              for i in (7, 3, 9, 1)]
    d = _make_subgraph(tmp_path, LINE_NODES, LINE_EDGES, chains)
    gs.annotate(d)
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        ids = [r["chain_id"] for r in csv.DictReader(f, delimiter="\t")]
    assert ids == ["CH-7", "CH-3", "CH-9", "CH-1"]


def test_top100_cap(tmp_path):
    """TOP100 帽：第 101 链不入人口（spec "TOP100 卡"）。"""
    chains = [{"chain_id": f"CH-{i:03d}", "microbe_id": "L0", "hop2_gene": "L1"}
              for i in range(105)]
    d = _make_subgraph(tmp_path, LINE_NODES, LINE_EDGES, chains)
    stats = gs.annotate(d)
    assert stats["n_chains"] == 100
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        assert sum(1 for _ in csv.DictReader(f, delimiter="\t")) == 100


def test_empty_hop2_gene_na_and_excluded_from_targets(tmp_path):
    """空 hop2_gene 行不跳行：分数列 na；且不计入靶集分母。"""
    chains = [{"chain_id": "CH-ok", "microbe_id": "L0", "hop2_gene": "L1"},
              {"chain_id": "CH-empty", "microbe_id": "L2", "hop2_gene": ""}]
    d = _make_subgraph(tmp_path, LINE_NODES, LINE_EDGES, chains)
    stats = gs.annotate(d)
    assert stats["n_chains"] == 2 and stats["n_targets"] == 1
    with open(d / gs.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        by = {r["chain_id"]: r for r in csv.DictReader(f, delimiter="\t")}
    for col in ("proximity_z", "degree_centrality_disclosed",
                "community_degraded"):
        assert by["CH-empty"][col] == "na"


# ---------- 只增不改护栏 ----------

def test_append_only_idempotent_and_drift_refused(tmp_path):
    d = _make_subgraph(tmp_path, LINE_NODES, LINE_EDGES, CHAINS_LINE)
    first = gs.annotate(d)
    assert first["idempotent"] is False
    payload = (d / gs.SIDECAR_NAME).read_bytes()
    second = gs.annotate(d)
    assert second["idempotent"] is True
    assert (d / gs.SIDECAR_NAME).read_bytes() == payload
    (d / gs.SIDECAR_NAME).write_bytes(b"tampered\n")
    with pytest.raises(SystemExit, match="不一致"):
        gs.annotate(d)


# ---------- ⑦ fail-closed 族 ----------

def test_missing_input_files_fail_closed(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(SystemExit, match="缺输入"):
        gs.annotate(tmp_path)


def test_missing_columns_fail_closed(tmp_path):
    (tmp_path / "merged_nodes.tsv").write_text("name\tcategory\na\tX\n",
                                               encoding="utf-8")
    (tmp_path / "merged_edges.tsv").write_text("s\to\na\tb\n", encoding="utf-8")
    (tmp_path / "top_hypotheses.tsv").write_text("x\n1\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="缺列"):
        gs.annotate(tmp_path)


def test_dangling_edge_fails_closed(tmp_path):
    """悬空边不静默吞（防连通片/度失真无痕）——拒算。"""
    chains = [{"chain_id": "CH-1", "microbe_id": "L0", "hop2_gene": "L1"}]
    d = _make_subgraph(tmp_path, ["L0", "L1"], [("L0", "GHOST")], chains)
    with pytest.raises(SystemExit, match="不在节点表"):
        gs.annotate(d)


def test_duplicate_node_id_fails_closed(tmp_path):
    chains = [{"chain_id": "CH-1", "microbe_id": "L0", "hop2_gene": "L1"}]
    d = _make_subgraph(tmp_path, ["L0", "L1", "L0"], [], chains)
    with pytest.raises(SystemExit, match="重复"):
        gs.annotate(d)


def test_empty_chain_id_fails_closed(tmp_path):
    chains = [{"chain_id": "", "microbe_id": "L0", "hop2_gene": "L1"}]
    d = _make_subgraph(tmp_path, LINE_NODES, LINE_EDGES, chains)
    with pytest.raises(SystemExit, match="chain_id 为空"):
        gs.annotate(d)


# ---------- ② 排序键零改动行为锁（验证条款③） ----------

def _load_hypothesize():
    """06_tool 目录名带连字符不是合法包名——按仓内约定路径加载。"""
    spec = spec_from_file_location(
        "hypothesize_frozen_probe", ROOT / "src" / "06_tool" / "hypothesize.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hypothesize_rank_key_frozen():
    """排序键必须仍是冻结五元组：总票↓ Borda↓ 验证率↓ 罚分↑ id↑。

    sidecar 只增不排序——本断言锁死 hypothesize.rank_key 行为零改动，
    任何触碰排序键的提交都会在此爆红（B0 规格验证条款③）。
    """
    hyp = _load_hypothesize()
    card = {"votes_total": "3", "evidence_syn_score": "0.5", "borda_score": "10", "kg_coi_rate": "0.5",
            "penalty": "0.25", "chain_id": "CH-001"}
    assert hyp.rank_key(card) == (-3, -0.5, -10, -0.5, 0.25, "CH-001")  # v0.4：syn 进第二键
    card_i = {k: (v if k == "chain_id" else
                  float(v) if "." in v else int(v))
              for k, v in card.items()}
    assert hyp.rank_key(card_i) == hyp.rank_key(card)


def test_hypothesize_rank_key_ordering_frozen():
    """冻结序行为锁：同票比 Borda，全同比罚分，罚分同比 id 升序。"""
    hyp = _load_hypothesize()
    cards = [
        {"votes_total": "4", "borda_score": "6", "kg_coi_rate": "0.5",
         "penalty": "0.0", "chain_id": "CH-b"},
        {"votes_total": "4", "borda_score": "6", "kg_coi_rate": "0.5",
         "penalty": "0.0", "chain_id": "CH-a"},
        {"votes_total": "4", "borda_score": "6", "kg_coi_rate": "0.5",
         "penalty": "0.5", "chain_id": "CH-z"},
        {"votes_total": "4", "borda_score": "9", "kg_coi_rate": "0.1",
         "penalty": "0.9", "chain_id": "CH-y"},
        {"votes_total": "2", "borda_score": "99", "kg_coi_rate": "1.0",
         "penalty": "0.0", "chain_id": "CH-x"},
    ]
    got = [c["chain_id"] for c in sorted(cards, key=hyp.rank_key)]
    assert got == ["CH-y", "CH-a", "CH-b", "CH-z", "CH-x"]


# ---------- ③ golden 12 件不变（验证条款②，needs_data 档） ----------

@pytest.mark.needs_data
def test_golden_12_unchanged():
    """子图 golden 12 件 sha 逐字节不变（sidecar 落盘不许扰任何既有件）。

    SHA256SUMS 混两种相对口径：8 条裸文件名（对子图目录）+4 条 data/kg/
    前缀（对仓根）——分别按各自基准解析后重算比对。
    """
    assert SG_REAL.exists(), f"私有数据不在位：{SG_REAL}"
    n = 0
    for line in (SG_REAL / "SHA256SUMS").read_text().splitlines():
        if not line.strip():
            continue
        sha, name = line.split(None, 1)
        name = name.strip()
        p = (ROOT / name) if name.startswith("data/") else (SG_REAL / name)
        assert p.exists(), f"golden 件缺失：{name}"
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        assert got == sha, f"golden 件漂移：{name}"
        n += 1
    assert n == 12


# ---------- CLI 注册 ----------

def test_cli_registers_graph_scores():
    assert "graph-scores" in cli.MODULES
    mf = cli.MODULES["graph-scores"][0]
    assert (cli.ROOT / "src" / mf).exists()
