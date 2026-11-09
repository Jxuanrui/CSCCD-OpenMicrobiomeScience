"""②乙 菌→代谢物网络邻近度测试（spec §一 验收条款的机器化）。

覆盖（对应规格 §1.5 验收 + 任务书八条）：
①AMSPL 公式正确性手算对拍（含"每种子最近"与"全部配对平均"、"逐靶最近"
  三种口径在小图上数值互不相同——防旧口径回归）
②种子钉扎双跑 sha 全等
③排序键零改动行为锁（四表逐字节不变 + 只新增 sidecar + 排名键取值）
④schema 9 列冻结
⑤两读法方向一致性逻辑（同号/异号/不可算/零）
⑥不可达率>50% 降级路径（含 50% 边界不降级、部分不可达 na 两支）
⑦只增不改幂等护栏（no-op / tampered fail-closed）
⑧边界带标签（单元 + 端到端渲染）
另加：度匹配分箱规则（<5 向大侧、最大箱并入小侧）、Menche separation 手算、
fail-closed 族、冻结常量、CLI 注册、needs_data 真实锚（golden 13 件+池 287/274）。
"""
import csv
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

import kg_tool.proximity as prox  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SG_REAL = ROOT / "data" / "kg" / "subgraph_htn_20261101_120403_t2merge"


# ---------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------

def _write_tsv(path: Path, cols: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(cols)
        w.writerows(rows)


def _make_subgraph(d: Path, nodes: list[tuple[str, str]],
                   edges: list[tuple[str, str]],
                   top_chains: list[dict],
                   cards: list[dict] | None = None) -> Path:
    """最小子图：四表齐备（列序/冗余列刻意不同，证明按列名读不按列位）。"""
    d.mkdir(parents=True, exist_ok=True)
    _write_tsv(d / "merged_nodes.tsv", ["id", "name", "category"],
               [[n, f"name_{n}", c] for n, c in nodes])
    _write_tsv(d / "merged_edges.tsv",
               ["edge_id", "subject", "predicate", "object", "confidence"],
               [[f"E{i}", s, "affects", o, "0.9"]
                for i, (s, o) in enumerate(edges)])
    _write_tsv(d / "top_hypotheses.tsv",
               ["chain_id", "microbe_id", "metabolite_id",
                "votes_total", "borda_score"],
               [[c["chain_id"], c["microbe_id"], c["metabolite_id"],
                 c.get("votes_total", "1"), c.get("borda_score", "1")]
                for c in top_chains])
    # v1.7 主口径需要 chains.tsv（全链 hop1 人口来源）；夹具用 top_chains+cards
    # 的并集去重模拟（最小化但保证人群非空且含同一批配对）
    _chain_rows = {c["chain_id"]: c for c in
                   (top_chains + (cards if cards is not None else []))}
    _write_tsv(d / "chains.tsv",
               ["chain_id", "microbe_id", "metabolite_id"],
               [[c["chain_id"], c["microbe_id"], c["metabolite_id"]]
                for c in _chain_rows.values()])
    _write_tsv(d / "hypothesis_cards.tsv",
               ["chain_id", "microbe_id", "metabolite_id",
                "votes_total", "borda_score", "microbe_name"],
               [[c["chain_id"], c["microbe_id"], c["metabolite_id"],
                 c.get("votes_total", "1"), c.get("borda_score", "1"), "m"]
                for c in (cards if cards is not None else top_chains)])
    return d


def _adj(edges: list[tuple[str, str]], extra: list[str] | None = None
         ) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {n: set() for n in (extra or [])}
    for s, o in edges:
        adj.setdefault(s, set()).add(o)
        adj.setdefault(o, set()).add(s)
    return adj


# 生产型夹具：6 菌 + 4 代谢物 + 1 疾病枢纽，连通、距离各异
PROD_NODES = ([(f"m{i}", "Species") for i in range(1, 7)]
              + [(f"t{i}", "Metabolite") for i in range(1, 5)]
              + [("h", "Disease")])
PROD_EDGES = [("m1", "t1"), ("m2", "t1"), ("m3", "t2"), ("m4", "t2"),
              ("m5", "t3"), ("m6", "t3"), ("t1", "h"), ("t2", "h"),
              ("t3", "h"), ("t4", "h"), ("t4", "m1")]
PROD_TOP = [
    {"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t1",
     "votes_total": "5", "borda_score": "9"},
    {"chain_id": "CH-2", "microbe_id": "m2", "metabolite_id": "t1",
     "votes_total": "4", "borda_score": "8"},
    {"chain_id": "CH-3", "microbe_id": "m3", "metabolite_id": "t2",
     "votes_total": "3", "borda_score": "7"},
]
# 卡片文件刻意逆序写（chain_id 降序）——证明读档后按排名键重排，非文件行序
PROD_CARDS = [
    {"chain_id": "CH-5", "microbe_id": "m5", "metabolite_id": "t3",
     "votes_total": "1", "borda_score": "5"},
    {"chain_id": "CH-4", "microbe_id": "m4", "metabolite_id": "t2",
     "votes_total": "2", "borda_score": "6"},
    {"chain_id": "CH-3", "microbe_id": "m3", "metabolite_id": "t2",
     "votes_total": "3", "borda_score": "7"},
    {"chain_id": "CH-2", "microbe_id": "m2", "metabolite_id": "t1",
     "votes_total": "4", "borda_score": "8"},
    {"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t1",
     "votes_total": "5", "borda_score": "9"},
]


# ---------------------------------------------------------------
# ④ schema 9 列 + 冻结常量
# ---------------------------------------------------------------

def test_schema_columns_frozen():
    """sidecar schema 必须逐字 9 列（spec §1.4；多一列=破坏冻结口径）。"""
    assert prox.SIDECAR_COLUMNS == [
        "chain_id", "microbe_id", "metabolite_id", "pair_distance",
        "null_mean", "null_sd", "proximity_z", "read_mode", "n_pairs"]
    assert len(prox.SIDECAR_COLUMNS) == 9


def test_frozen_constants():
    """规格钉扎常量（种子/置换数/α/分箱阈值/不可达阈值/池锚）逐字。"""
    assert prox.RNG_SEED == 20261105
    assert prox.N_PERM == 1000
    assert prox.ALPHA == 0.05
    assert prox.BOUNDARY_LOW == 0.05 and prox.BOUNDARY_HIGH == 0.10
    assert prox.MIN_BIN_SIZE == 5
    assert prox.UNREACHABLE_MAX == 0.50
    assert prox.TOP_N_SENS == 100
    assert prox.ANCHOR_POOL == {"seed": 287, "target": 274}
    assert prox.SIDECAR_NAME == "microbe_metabolite_proximity.tsv"


# ---------------------------------------------------------------
# ① AMSPL 公式正确性（手算对拍）
# ---------------------------------------------------------------

# 路径 t2 - x - x2 - t1 - m1 - m2（5 边）：
#   d(m1,t1)=1 d(m1,t2)=4 d(m2,t1)=2 d(m2,t2)=5
#   AMSPL  = (min(1,4)+min(2,5))/2 = 1.5
#   全部配对平均 = (1+4+2+5)/4 = 3.0
#   逐靶最近（U2 旧口径） = (min(1,2)+min(4,5))/2 = 2.5 —— 三值互不相同
PATH_ADJ = _adj([("t2", "x"), ("x", "x2"), ("x2", "t1"),
                 ("t1", "m1"), ("m1", "m2")])


def test_amspl_hand_check_is_per_seed_closest():
    """AMSPL=1.5（每种子取最近靶，再平均）——不是 3.0（全部配对）、不是 2.5（逐靶）。"""
    d_c, rate = prox.amspl(PATH_ADJ, ["m1", "m2"], ["t1", "t2"])
    assert d_c == pytest.approx(1.5)
    assert rate == 0.0
    # 辅助量（不参与 z）——与主指标不同值，证明没把两者混用
    assert prox.mean_all_pairs_distance(PATH_ADJ, ["m1", "m2"],
                                        ["t1", "t2"]) == pytest.approx(3.0)
    # U2 旧口径反算（逐靶最近）：2.5——本模块**不应**等于它
    old = ((min(1, 2) + min(4, 5)) / 2)
    assert old == pytest.approx(2.5)
    assert d_c != pytest.approx(old)


def test_amspl_single_target_is_mean_over_seeds():
    """|B|=1：d_c=(1/|A|)Σ_a d(a,b) 手算（(1+2)/2=1.5）。"""
    d_c, _ = prox.amspl(PATH_ADJ, ["m1", "m2"], ["t1"])
    assert d_c == pytest.approx((1 + 2) / 2)


def test_amspl_unreachable_returns_none_with_rate():
    """不可达不折大数：任一全靶不可达的种子 → d_c=None；占比照实报。"""
    adj = _adj([("m1", "t1"), ("z1", "z2")], extra=["m2"])
    d_c, rate = prox.amspl(adj, ["m1", "m2"], ["t1"])
    assert d_c is None
    assert rate == pytest.approx(0.5)      # 2 配对里 1 个不可达


def test_internal_amspl_excludes_self():
    """内部 AMSPL 排除自身项（否则 min 恒 0）；|S|<2 无定义。"""
    assert prox.internal_amspl(PATH_ADJ, ["t1", "t2"]) == pytest.approx(3.0)
    assert prox.internal_amspl(PATH_ADJ, ["m1", "m2"]) == pytest.approx(1.0)
    assert prox.internal_amspl(PATH_ADJ, ["t1"]) is None


def test_menche_separation_hand_check():
    """s_AB = d_AB−(d_AA+d_BB)/2 = 1.5−(1+3)/2 = −0.5（模块重叠方向）。"""
    d_ab, _ = prox.amspl(PATH_ADJ, ["m1", "m2"], ["t1", "t2"])
    m = prox.menche_separation(PATH_ADJ, ["m1", "m2"], ["t1", "t2"], d_ab)
    assert m["d_ab"] == pytest.approx(1.5)
    assert m["d_aa"] == pytest.approx(1.0)
    assert m["d_bb"] == pytest.approx(3.0)
    assert m["s_ab"] == pytest.approx(-0.5)
    # |B|=1 → d_BB 无定义 → s_AB=na（不硬造 0）
    m1 = prox.menche_separation(PATH_ADJ, ["m1", "m2"], ["t1"], d_ab)
    assert m1["s_ab"] is None


# ---------------------------------------------------------------
# 度匹配分箱（spec §1.3 规则）
# ---------------------------------------------------------------

def test_degree_bins_small_merge_upward_and_max_merge_downward():
    """>=5 成立的精确度箱独立；<5 的向大侧合并；最大箱<5 并入小侧。"""
    pool = ([f"a{i}" for i in range(5)]          # 度 1 ×5 → 独立箱
            + ["b1", "b2"]                        # 度 2 ×2 ┐
            + ["c1"]                              # 度 3 ×1 ├ 向大侧并入度 5
            + ["d1"]                              # 度 4 ×1 ┘
            + [f"e{i}" for i in range(5)]         # 度 5 ×5
            + ["f1"])                             # 度 9 ×1 → 最大箱并入小侧
    degree = {}
    for n in pool[:5]:
        degree[n] = 1
    degree["b1"] = degree["b2"] = 2
    degree["c1"] = 3
    degree["d1"] = 4
    for n in pool[9:14]:
        degree[n] = 5
    degree["f1"] = 9
    deg2bin, bin2mem = prox.degree_bins(pool, degree)
    assert len(bin2mem) == 2
    assert bin2mem[deg2bin[1]] == sorted(pool[:5])
    merged = bin2mem[deg2bin[2]]
    assert set(merged) == set(pool[5:])           # 2,3,4,5,9 全并入第二箱
    # 箱摘要：箱号按度升序、成员数可核
    _c, summary = prox.null_candidates(pool, degree)
    assert summary[0][1:] == (1, 1, 5)
    assert summary[1][1:] == (2, 9, len(pool) - 5)


def test_degree_bins_small_pool_single_bin():
    """整池不足 5 → 单箱兜住（不得空箱/丢节点）。"""
    pool = ["a", "b", "c"]
    degree = {"a": 1, "b": 2, "c": 3}
    _d2b, bin2mem = prox.degree_bins(pool, degree)
    assert list(bin2mem.values()) == [["a", "b", "c"]]


def test_null_candidate_pool_excludes_self():
    """候选池排除自身（同规格 §3.2 步 6 口径），且非空。"""
    pool = ["a", "b", "c", "d"]
    degree = {n: 2 for n in pool}
    cands, _ = prox.null_candidates(pool, degree)
    for n in pool:
        assert n not in cands[n]
        assert len(cands[n]) == 3


# ---------------------------------------------------------------
# ⑦ 幂等护栏 + ② 钉扎双跑
# ---------------------------------------------------------------

def test_seed_pinned_two_runs_sha_equal(tmp_path):
    """同输入两处独立跑 → sidecar 与评估档均逐字节相等（验证条款①）。"""
    d1 = _make_subgraph(tmp_path / "r1", PROD_NODES, PROD_EDGES,
                        PROD_TOP, PROD_CARDS)
    d2 = _make_subgraph(tmp_path / "r2", PROD_NODES, PROD_EDGES,
                        PROD_TOP, PROD_CARDS)
    prox.annotate(d1, tmp_path / "r1" / "eval.md")
    prox.annotate(d2, tmp_path / "r2" / "eval.md")
    s1 = hashlib.sha256((d1 / prox.SIDECAR_NAME).read_bytes()).hexdigest()
    s2 = hashlib.sha256((d2 / prox.SIDECAR_NAME).read_bytes()).hexdigest()
    assert s1 == s2
    assert ((tmp_path / "r1" / "eval.md").read_bytes()
            == (tmp_path / "r2" / "eval.md").read_bytes())


def test_append_only_idempotent_and_drift_refused(tmp_path):
    """只增不改：二次跑=no-op；被篡改=fail-closed 拒写。"""
    d = _make_subgraph(tmp_path, PROD_NODES, PROD_EDGES, PROD_TOP, PROD_CARDS)
    first = prox.annotate(d)
    assert first["idempotent"] is False
    payload = (d / prox.SIDECAR_NAME).read_bytes()
    second = prox.annotate(d)
    assert second["idempotent"] is True
    assert (d / prox.SIDECAR_NAME).read_bytes() == payload
    (d / prox.SIDECAR_NAME).write_bytes(b"tampered\n")
    with pytest.raises(SystemExit, match="不一致"):
        prox.annotate(d)


def test_sidecar_rows_and_columns(tmp_path):
    """行数=三口径行数之和（v1.7：主口径全链 + 两参考读数）；每行 9 列。"""
    d = _make_subgraph(tmp_path, PROD_NODES, PROD_EDGES, PROD_TOP, PROD_CARDS)
    stats = prox.annotate(d)
    with open(d / prox.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        rows = list(reader)
    assert header == prox.SIDECAR_COLUMNS
    assert all(len(r) == 9 for r in rows)
    modes = [r[7] for r in rows]
    # v1.7 三口径：主口径 ALL_CHAINS（夹具=3+5 去重链）+ REF_TOP10(3) + REF_TOP100(5)
    n_all = stats["results"][0]["n_cards"]
    assert len(rows) == n_all + 3 + 5
    assert modes.count("ALL_CHAINS") == n_all
    assert modes.count("REF_TOP10") == 3 and modes.count("REF_TOP100") == 5
    # 读出各口径的 n_pairs（口径内唯一）
    pairs_by_mode = {}
    for m, npr in zip(modes, [r[8] for r in rows]):
        pairs_by_mode.setdefault(m, set()).add(npr)
    for m, vals in pairs_by_mode.items():
        assert len(vals) == 1, f"{m} 内 n_pairs 不唯一: {vals}"
    # 主口径 n_pairs 应 >= 参考读数（全链人口 ≥ 卡片子集）
    assert int(list(pairs_by_mode["ALL_CHAINS"])[0]) >= 3
    assert stats["results"][0]["read_mode"] == "ALL_CHAINS"


# ---------------------------------------------------------------
# ③ 排序键零改动行为锁
# ---------------------------------------------------------------

def test_rank_key_matches_spec_and_ignores_file_order(tmp_path):
    """排名键=votes↓,borda↓,chain_id↑；文件逆序写也要按排名键取前 n。"""
    d = _make_subgraph(tmp_path, PROD_NODES, PROD_EDGES, PROD_TOP, PROD_CARDS)
    ranked = prox.load_top_cards_ranked(d, n=3)
    assert [c["chain_id"] for c in ranked] == ["CH-1", "CH-2", "CH-3"]
    # 文件行序是 CH-5..CH-1（逆序）——若实现按"文件前 n 行"会得到 CH-5
    raw = [r["chain_id"] for r in csv.DictReader(
        open(d / "hypothesis_cards.tsv", encoding="utf-8"), delimiter="\t")]
    assert raw[:2] == ["CH-5", "CH-4"]
    assert prox.rank_key({"votes_total": "5", "borda_score": "9",
                          "chain_id": "CH-2"}) == (-5.0, -9.0, "CH-2")


def test_compute_never_touches_inputs_and_only_adds_sidecar(tmp_path):
    """四表逐字节不变 + 目录里只多出 sidecar（排序键零改动行为锁）。"""
    d = _make_subgraph(tmp_path, PROD_NODES, PROD_EDGES, PROD_TOP, PROD_CARDS)
    names = ["merged_nodes.tsv", "merged_edges.tsv",
             "top_hypotheses.tsv", "hypothesis_cards.tsv", "chains.tsv"]
    before = {n: hashlib.sha256((d / n).read_bytes()).hexdigest() for n in names}
    prox.annotate(d)
    after = {n: hashlib.sha256((d / n).read_bytes()).hexdigest() for n in names}
    assert before == after
    added = sorted(p.name for p in d.iterdir()) 
    assert [a for a in added if a not in names] == [prox.SIDECAR_NAME]


# ---------------------------------------------------------------
# ⑤ 两读法方向一致性
# ---------------------------------------------------------------

def test_two_read_consistency_same_sign():
    r = prox.two_read_consistency(-2.0, -1.0)
    assert r["consistent"] is True and r["main"] == r["sens"] == "closer"
    r2 = prox.two_read_consistency(1.5, 0.4)
    assert r2["consistent"] is True and r2["main"] == "farther"


def test_two_read_consistency_opposite_or_undefined():
    assert prox.two_read_consistency(-2.0, 1.0)["consistent"] is False
    assert prox.two_read_consistency(None, -1.0)["consistent"] is False
    assert prox.two_read_consistency(0.0, -1.0)["consistent"] is False
    assert "不可算" in prox.two_read_consistency(None, 1.0)["reason"]


def test_eval_md_reports_consistency(tmp_path):
    """评估档必须落参考叙述句（v1.7：一致性降为参考，不再作判定前置）。"""
    d = _make_subgraph(tmp_path, PROD_NODES, PROD_EDGES, PROD_TOP, PROD_CARDS)
    stats = prox.annotate(d, tmp_path / "eval.md")
    md = (tmp_path / "eval.md").read_text(encoding="utf-8")
    assert "参考叙述" in md and "不作为出分前置" in md
    word = "同向" if stats["consistency"]["consistent"] else "异向"
    assert f"**{word}**" in md
    # v1.7：旧"个位数"句已删（主口径 710 对非个位数），改验新披露句
    assert "可出主数字" in md and "仅作定性参考" in md
    assert "非逐位复现" in md                  # NetMedPy 近似口径披露


# ---------------------------------------------------------------
# ⑥ 不可达降级路径
# ---------------------------------------------------------------

def test_unreachable_over_50pct_degrades(tmp_path):
    """不可达率 100%>50% → 图不适用：只报不可达率，不出 z（sidecar 全 na）。"""
    nodes = [("m1", "Species"), ("m2", "Species"),
             ("t1", "Metabolite"), ("t2", "Metabolite")]
    edges = [("m1", "m2"), ("t1", "t2")]      # 两个互不连通片
    pop = [{"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t1"}]
    d = _make_subgraph(tmp_path, nodes, edges, pop)
    stats = prox.annotate(d)
    res = stats["results"][0]
    assert res["not_applicable"] is True
    assert res["unreachable_rate"] == 1.0
    assert res["z"] is None and res["p"] is None
    assert "图不适用" in res["verdict"]
    with open(d / prox.SIDECAR_NAME, encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f, delimiter="\t"))
    for col in ("pair_distance", "null_mean", "null_sd", "proximity_z"):
        assert row[col] == "na"


def test_partial_unreachable_is_na_not_degrade(tmp_path):
    """不可达率=50%（不 >50%）→ 不降级，但存在全靶不可达种子 → d_c 不可算 na。"""
    nodes = [("m1", "Species"), ("m3", "Species"),
             ("t1", "Metabolite"), ("t2", "Metabolite")]
    edges = [("m1", "t1"), ("t1", "t2")]      # m3 孤立
    pop = [{"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t1"},
           {"chain_id": "CH-2", "microbe_id": "m3", "metabolite_id": "t2"}]
    d = _make_subgraph(tmp_path, nodes, edges, pop)
    res = prox.annotate(d)["results"][0]
    assert res["not_applicable"] is False
    assert res["unreachable_rate"] == pytest.approx(0.5)
    assert res["d_c"] is None and res["z"] is None
    assert res["verdict"] == "不可算（存在全靶不可达种子）"


# ---------------------------------------------------------------
# ⑧ 边界带
# ---------------------------------------------------------------

def test_boundary_band_labels():
    """p∈(0.05,0.10) 才是边界带（开区间）；显著/不显著标签如预指定。"""
    assert prox.boundary_label(0.07) == "边界带"
    assert prox.boundary_label(0.0999) == "边界带"
    assert prox.boundary_label(0.05) == ""
    assert prox.boundary_label(0.10) == ""
    assert prox.boundary_label(None) == ""
    assert prox.verdict_label(-2.0, 0.07) == "边界带（不显著，不追加检验）"
    assert prox.verdict_label(-2.0, 0.01) == "显著更近（H1 方向）"
    assert prox.verdict_label(2.0, 0.01) == "显著更远（与 H1 反向）"
    assert prox.verdict_label(-0.5, 0.60) == "不显著"
    assert prox.verdict_label(None, None) == "不可算"


def test_boundary_band_rendered_end_to_end(tmp_path, monkeypatch):
    """端到端：p=0.07 → 评估档出现边界带段且不追加事后检验。"""
    d = _make_subgraph(tmp_path, PROD_NODES, PROD_EDGES, PROD_TOP, PROD_CARDS)
    monkeypatch.setattr(prox, "one_sided_p", lambda d_obs, nulls: 0.07)
    stats = prox.annotate(d, tmp_path / "eval.md")
    assert stats["results"][0]["boundary"] == "边界带"
    md = (tmp_path / "eval.md").read_text(encoding="utf-8")
    assert "**边界带**" in md and "不追加任何事后检验" in md


# ---------------------------------------------------------------
# 统计量单元（z / p / 置换）
# ---------------------------------------------------------------

def test_z_score_and_one_sided_p():
    """z 用总体标准差（ddof=0）；p=(#≤+1)/(n+1)；sd=0/样本<2 → na。"""
    z, mean, sd = prox.z_score(1.0, [1.0, 2.0, 3.0])
    assert mean == pytest.approx(2.0) and sd == pytest.approx(0.8164965809)
    assert z == pytest.approx(-1.2247448714)
    assert prox.z_score(1.0, [2.0, 2.0, 2.0])[0] is None      # sd=0 退化
    assert prox.z_score(1.0, [2.0]) == (None, None, None)     # 样本不足
    assert prox.one_sided_p(1.0, [1.0, 1.5, 2.0]) == pytest.approx(0.5)
    assert prox.one_sided_p(0.5, [1.0, 2.0]) == pytest.approx(1 / 3)


def test_permutation_null_pinned_and_bounds():
    """置换分布：钉扎双跑同序、全部落在可达区间、计数自洽。"""
    adj = PATH_ADJ
    cands = {n: [o for o in ("m1", "m2") if o != n] for n in ("m1", "m2")}
    cands.update({n: [o for o in ("t1", "t2") if o != n] for n in ("t1", "t2")})
    a = prox.permutation_null(adj, cands, ["m1", "m2"], ["t1", "t2"],
                              n_perm=50)
    b = prox.permutation_null(adj, cands, ["m1", "m2"], ["t1", "t2"],
                              n_perm=50)
    assert a["nulls"] == b["nulls"]
    assert len(a["nulls"]) + a["n_unreachable"] == 50
    assert all(0 <= x <= 5 for x in a["nulls"])


# ---------------------------------------------------------------
# fail-closed 族
# ---------------------------------------------------------------

def test_missing_inputs_fail_closed(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(SystemExit, match="缺输入"):
        prox.annotate(tmp_path)


def test_missing_columns_fail_closed(tmp_path):
    (tmp_path / "merged_nodes.tsv").write_text("name\tcategory\na\tX\n",
                                               encoding="utf-8")
    (tmp_path / "merged_edges.tsv").write_text("s\to\na\tb\n", encoding="utf-8")
    (tmp_path / "top_hypotheses.tsv").write_text("x\n1\n", encoding="utf-8")
    (tmp_path / "hypothesis_cards.tsv").write_text("x\n1\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="缺列"):
        prox.annotate(tmp_path)


def test_dangling_edge_fail_closed(tmp_path):
    """悬空边不静默吞（防度分布/最短路失真无痕）。"""
    pop = [{"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t1"}]
    d = _make_subgraph(tmp_path, [("m1", "Species"), ("t1", "Metabolite")],
                       [("m1", "GHOST")], pop)
    with pytest.raises(SystemExit, match="不在节点表"):
        prox.annotate(d)


def test_duplicate_node_id_fail_closed(tmp_path):
    pop = [{"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t1"}]
    d = _make_subgraph(tmp_path,
                       [("m1", "Species"), ("t1", "Metabolite"),
                        ("m1", "Species")], [("m1", "t1")], pop)
    with pytest.raises(SystemExit, match="重复"):
        prox.annotate(d)


def test_population_node_missing_fail_closed(tmp_path):
    """人口节点不在图 → fail-closed（缺节点≠不可达，不得折算成 inf）。"""
    pop = [{"chain_id": "CH-1", "microbe_id": "m1", "metabolite_id": "t9"}]
    d = _make_subgraph(tmp_path,
                       [("m1", "Species"), ("m2", "Species"),
                        ("t1", "Metabolite"), ("t2", "Metabolite")],
                       [("m1", "t1"), ("m2", "t2")], pop)
    with pytest.raises(SystemExit, match="不在 merged_nodes"):
        prox.annotate(d)


def test_empty_population_field_fail_closed(tmp_path):
    pop = [{"chain_id": "CH-1", "microbe_id": "", "metabolite_id": "t1"}]
    d = _make_subgraph(tmp_path,
                       [("m1", "Species"), ("m2", "Species"),
                        ("t1", "Metabolite"), ("t2", "Metabolite")],
                       [("m1", "t1"), ("m2", "t2")], pop)
    with pytest.raises(SystemExit, match="菌或代谢物为空"):
        prox.annotate(d)


def test_population_category_mismatch_fail_closed(tmp_path):
    """卡片把疾病节点填成 microbe_id → 类别校验 fail-closed（不落到 KeyError）。"""
    pop = [{"chain_id": "CH-1", "microbe_id": "d1", "metabolite_id": "t1"}]
    d = _make_subgraph(tmp_path,
                       [("m1", "Species"), ("m2", "Species"),
                        ("t1", "Metabolite"), ("t2", "Metabolite"),
                        ("d1", "Disease")],
                       [("m1", "t1"), ("m2", "t2"), ("d1", "t1")], pop)
    with pytest.raises(SystemExit, match="类别不符池定义"):
        prox.annotate(d)


# ---------------------------------------------------------------
# golden 核验（证①的机器化；夹具无清单→只测解析与判定）
# ---------------------------------------------------------------

def test_verify_golden_baseline_rows(tmp_path):
    """清单逐行解析（含第 13 行 "(=...)" 注记截断）；不符则 ok=False。"""
    sg = tmp_path / "sg"
    sg.mkdir()
    (sg / "a.tsv").write_bytes(b"AAA")
    (sg / "b.tsv").write_bytes(b"BBB")
    sha_a = hashlib.sha256(b"AAA").hexdigest()
    sha_b = hashlib.sha256(b"BBB").hexdigest()
    gl = tmp_path / "golden.txt"
    gl.write_text(f"{sha_a}  a.tsv\n{sha_b}  c.tsv(=runA/other.tsv)\n",
                  encoding="utf-8")
    rows = prox.verify_golden_baseline(sg, gl)
    assert [r["name"] for r in rows] == ["a.tsv", "c.tsv"]
    assert rows[0]["ok"] is True
    assert rows[1]["ok"] is False and rows[1]["actual"] == "MISSING"
    with pytest.raises(SystemExit, match="缺 golden"):
        prox.verify_golden_baseline(sg, tmp_path / "nope.txt")


# ---------------------------------------------------------------
# CLI 注册
# ---------------------------------------------------------------

def test_cli_registers_proximity():
    assert "proximity" in cli.MODULES
    mf = cli.MODULES["proximity"][0]
    assert (cli.ROOT / "src" / mf).exists()


# ---------------------------------------------------------------
# needs_data：真实锚（公网 CI 无私有数据则跳过）
# ---------------------------------------------------------------

@pytest.mark.needs_data
def test_golden_13_unchanged():
    """golden v3 十三件逐字节不变（验收条款②）。"""
    assert SG_REAL.exists(), f"私有数据不在位：{SG_REAL}"
    gl = ROOT / prox.GOLDEN_LIST_DEFAULT
    rows = prox.verify_golden_baseline(SG_REAL, gl)
    assert len(rows) == 13
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]


@pytest.mark.needs_data
def test_real_anchors_pools_and_u2_loader_agrees():
    """真实锚：池 287/274、TOP10 n_pairs=2；且本模块建图与 U2 口径逐节点相等。"""
    if not SG_REAL.exists():
        pytest.skip(f"私有数据不在位：{SG_REAL}")
    from kg_tool import graph_scores as gs
    nodes, g = prox.load_graph(SG_REAL)
    nodes_u2, adj_u2 = gs.load_graph(SG_REAL)
    assert nodes == nodes_u2
    assert {n: g[n]["adj"] for n in nodes} == adj_u2
    seeds = [n for n in nodes if g[n]["category"] in prox.SEED_CATEGORIES]
    targets = [n for n in nodes if g[n]["category"] == prox.TARGET_CATEGORY]
    assert (len(seeds), len(targets)) == (287, 274)
    top = prox.load_top_hypotheses(SG_REAL)
    _s, _t, n_pairs = prox.population(top)
    assert (len(top), n_pairs) == (10, 2)
    assert len(prox.load_top_cards_ranked(SG_REAL, 100)) == 100
