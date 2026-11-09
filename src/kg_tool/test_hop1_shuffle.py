"""③甲 hop1 换跳洗牌测试（spec §二 验收条款的机器化）。

覆盖（对应规格 §2.4 验收 + 任务书六条）：
①种子钉扎复现（双跑/三跑 sha 全等——spec 验收①）
②三道机器门禁 fail-closed（票③往返全等 / votes_total 恒等式 / 票②==depth）
③洗牌机制（代谢物多重集不变、全库参与无分层、菌列固定、票①②逐位不动、
  票③④按新代谢物用生产入口重算）
④排序键零改动（输入四表逐字节不变 + 只新增评估档一件）
⑤边界带（单元 + 端到端渲染）与效应量同段强制并列
⑥Wilcoxon 手算教科书对照（含并列校正）+ scipy 对拍（可选依赖）
另加：冻结常量、预写解读句逐字防手抄、票④可测标志聚合口径与冲突 fail-closed、
只增不改幂等护栏、CLI 注册、needs_data 真实锚（43,773 链/golden 13 件）。
"""
import csv
import hashlib
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

import kg_tool.hop1_shuffle as hs  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SG_REAL = ROOT / "data" / "kg" / "subgraph_htn_20261101_120403_t2merge"
FROZEN_13 = ROOT / "data" / "logs" / "golden_baseline_v3_20261102_sha256.txt"
SPEC = ROOT / "data" / "logs" / "upgrade_v03_spec_20261105.md"
COHORTS = list(hs.COHORTS)


# ---------------------------------------------------------------
# 夹具：6 链 / 3 代谢物（1 可测）/ 2 菌 / 三桶对账两行
# ---------------------------------------------------------------

def _write_tsv(path: Path, cols: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(cols)
        w.writerows(rows)


# 代谢物身份：M1 可测（chebi 在三队列都 measurable）/ M2 不可测 / M3 无 chebi
MET = {"M1": ("SCFAs", "CHEBI:1", 1),
       "M2": ("Bile acid", "CHEBI:2", 0),
       "M3": ("VitB", "", 0)}
# (chain_id, microbe_id, metabolite_id, depth, v1, strain)
CHAIN_DEF = [
    ("CH-1", "m1", "M1", 3, 1, 0),
    ("CH-2", "m1", "M2", 2, 0, 1),
    ("CH-3", "m2", "M3", 1, 0, 0),
    ("CH-4", "m2", "M1", 3, 1, 1),
    ("CH-5", "m3", "M2", 2, 1, 0),
    ("CH-6", "m3", "M1", 1, 0, 1),
]
# 三桶对账：M1 锚定（票③=1）；"gene_m3" 未锚定名（M3 链靠基因名吃票③）
# 列序严格按 REQUIRED_IREP_COLS + 溯源列（按名读，故顺序必须与表头一致）
IREP_ROWS = [
    ["confirm", "SCFAs", "geneX", "M1", "",
     "Gut Microbe-Host Gene.csv", "1", "12345", "DOID:10763", "causally",
     "", "", "", ""],
    ["confirm", "gene_m3", "VitB", "", "M9",
     "Gut Microbe-Host Gene.csv", "2", "67890", "DOID:10763", "causally",
     "", "", "", ""],
]


def _make_fixture(d: Path) -> Path:
    """最小子图（列序刻意不同，证明按列名读不按列位）——四表齐备。"""
    d.mkdir(parents=True, exist_ok=True)
    _write_tsv(d / "chains.tsv", hs.REQUIRED_CHAIN_COLS,
               [[cid, f"NCBITaxon:{mid[-1]}", m, MET[m][0], MET[m][1], str(depth)]
                for cid, mid, m, depth, _v1, _s in CHAIN_DEF])
    _write_tsv(d / "interaction_report.tsv", hs.REQUIRED_IREP_COLS
               + ["table", "row_index", "pmid", "doid", "associative_mode",
                  "subgraph_edge_ids", "subgraph_predicates",
                  "direction_verdict", "note"], IREP_ROWS)
    # 标注：每链 × 三队列；M1=measurable，M2=ko_absent，M3=no_chebi
    ann_rows = []
    for cid, _mid, m, _depth, _v1, _s in CHAIN_DEF:
        label = ("measurable" if MET[m][2] == 1
                 else ("no_chebi" if not MET[m][1] else "ko_absent"))
        for coh in COHORTS:
            ann_rows.append([cid, coh, label, str(3 if label == "measurable"
                                                 else 0),
                             "", "", "", "", "", "", "", ""])
    _write_tsv(d / "chain_annotations.tsv", hs.REQUIRED_ANN_COLS, ann_rows)
    # 卡片：票③按"锚定/未锚定名"规则、票④=endpoint+strain、votes_total 恒等
    card_rows = []
    for cid, mid, m, depth, v1, strain in CHAIN_DEF:
        v3 = 1 if m == "M1" else (1 if cid == "CH-3" else 0)
        endpoint = MET[m][2]
        v4 = endpoint + strain
        card_rows.append([
            cid, f"NCBITaxon:{mid[-1]}", f"bug_{mid[-1]}", m, MET[m][0],
            f"G{mid[-1]}", f"gene_{m.lower()}", "KEGG:hsa00010", str(depth),
            str(v1), str(depth), str(v3), str(v4), str(endpoint), str(strain),
            str(v1 + depth + v3 + v4)])
    _write_tsv(d / "hypothesis_cards.tsv", hs.REQUIRED_CARD_COLS, card_rows)
    return d


def _read(path: Path, cols: list[str]) -> list[dict]:
    return hs.read_tsv(path, cols, "夹具")


def _load(d: Path):
    cards = _read(d / "hypothesis_cards.tsv", hs.REQUIRED_CARD_COLS)
    chains = {c["chain_id"]: c for c in
              _read(d / "chains.tsv", hs.REQUIRED_CHAIN_COLS)}
    irep = _read(d / "interaction_report.tsv", hs.REQUIRED_IREP_COLS)
    ann = _read(d / "chain_annotations.tsv", hs.REQUIRED_ANN_COLS)
    return cards, chains, irep, ann


# ---------------------------------------------------------------
# ①冻结常量 + 预写句防手抄
# ---------------------------------------------------------------

def test_frozen_constants_match_spec():
    """常量必须与 §2.2/§2.3 逐字一致（改了=改规格，须过审）。"""
    assert hs.RNG_SEED == 20261105
    assert hs.ALPHA == 0.05
    assert (hs.BOUNDARY_LOW, hs.BOUNDARY_HIGH) == (0.05, 0.10)
    assert hs.N_CHAINS_ANCHOR == 43773
    assert hs.COHORTS == ("PRJEB13870", "PRJEB21612", "SelfData_222")


@pytest.mark.needs_data
def test_prewritten_sentence_verbatim_in_frozen_spec():
    """预写解读句必须与冻结规格逐字一致（防"顺手润色"改掉预注册措辞）。"""
    text = SPEC.read_text(encoding="utf-8")
    assert hs.PREWRITTEN_NULL_SENTENCE in text
    assert "菌-代谢物配对本身不携带四票可捕捉的特异信号" in text
    assert "不做族内 α 校正" in hs.FAMILY_NOTE
    assert "未做族内校正" in text or "不做族内 α 校正" in text


def test_required_columns_frozen():
    assert hs.REQUIRED_CARD_COLS[:4] == ["chain_id", "microbe_id",
                                         "microbe_name", "metabolite_id"]
    assert hs.REQUIRED_CHAIN_COLS == ["chain_id", "microbe_id",
                                      "metabolite_id", "metabolite_name",
                                      "metabolite_chebi", "depth"]
    assert hs.REQUIRED_ANN_COLS == ["chain_id", "cohort", "ko_label"]


# ---------------------------------------------------------------
# ②洗牌机制
# ---------------------------------------------------------------

def test_permutation_is_bijection_pinned_and_not_identity():
    d1 = hs.metabolite_permutation(100)
    d2 = hs.metabolite_permutation(100)
    assert d1 == d2                      # 种子钉扎：同输入同输出
    assert sorted(d1) == list(range(100))  # 双射（多重集守恒的前提）
    assert d1 != list(range(100))        # 真洗过，不是恒等
    assert hs.metabolite_permutation(100, seed=1) != d1


def test_compute_preserves_multiset_and_uses_all_rows(tmp_path):
    """§2.2 v1.1 勘误：全库 43,773 链均参与，**无分层**。"""
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    assert st["n_cards"] == len(cards)
    donors = hs.metabolite_permutation(len(cards))
    # 双重集相等（置换不增不减）
    from collections import Counter
    assert Counter(c["metabolite_id"] for c in cards) == Counter(
        cards[i]["metabolite_id"] for i in donors)
    # 无分层：donor 覆盖全部行号（不是"只动某个子层"）
    assert sorted(donors) == list(range(len(cards)))


def test_vote3_recomputed_by_production_rule_with_new_metabolite(tmp_path):
    """票③' 必须等于生产 vote3(新代谢物) 的逐行结果——不近似、不自搭规则。"""
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    hyp = hs.load_hypothesize()
    idx = hyp.build_vote3_index(irep)
    lookup = hs.build_ko_lookup(chains, ann)["lookup"]
    donors = hs.metabolite_permutation(len(cards))
    re = hs.recompute_shuffled_votes(cards, idx, lookup, donors)
    for i, c in enumerate(cards):
        new_mid = cards[donors[i]]["metabolite_id"]
        assert re["vote3"][i] == hyp.vote3(
            {"microbe_id": c["microbe_id"], "metabolite_id": new_mid,
             "hop2_gene_name": c["hop2_gene_name"]}, idx)
    # 至少有一行真的变了（否则尺子没动，测试也就白跑）
    assert any(re["vote3"][i] != int(c["vote3_gutmgene_cross"])
               for i, c in enumerate(cards))


def test_vote4_recomputed_by_new_metabolite_and_total_invariant(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    lookup = hs.build_ko_lookup(chains, ann)["lookup"]
    idx = hs.load_hypothesize().build_vote3_index(irep)
    donors = hs.metabolite_permutation(len(cards))
    re = hs.recompute_shuffled_votes(cards, idx, lookup, donors)
    for i, c in enumerate(cards):
        new_mid = cards[donors[i]]["metabolite_id"]
        assert re["vote4"][i] == (lookup[new_mid]
                                  + int(c["strain_available"]))
    # 票④总量守恒（可测标志是代谢物的函数 + 多重集守恒）
    assert st["vote4_sum_true"] == st["vote4_sum_shuffled"]
    # 票①/票②逐位不动：votes_total' 减去 票③'④' 必须回到票①+票②
    for i, c in enumerate(cards):
        assert re["votes_total"][i] - re["vote3"][i] - re["vote4"][i] == (
            int(c["vote1_ext_cohort"]) + int(c["vote2_kg_depth"]))


def test_ko_lookup_is_any_cohort_measurable(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    _cards, chains, _irep, ann = _load(d)
    ann = [dict(r) for r in ann]
    # 把 M2 的全部链只在最后一个队列改成 measurable → 任一队列即可
    for r in ann:
        if r["chain_id"] in ("CH-2", "CH-5") and r["cohort"] == COHORTS[-1]:
            r["ko_label"] = "measurable"
    rep = hs.build_ko_lookup(chains, ann)
    assert rep["lookup"]["M2"] == 1
    assert rep["n_measurable"] == 2


def test_ko_lookup_inconsistent_flag_fails_closed(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    _cards, chains, _irep, ann = _load(d)
    ann = [dict(r) for r in ann]
    # 同代谢物 M1 的某一条链改成不可测 → 聚合口径不成立（不是"取多数"）
    for r in ann:
        if r["chain_id"] == "CH-4":
            r["ko_label"] = "ko_absent"
    with pytest.raises(SystemExit, match="聚合口径不成立"):
        hs.build_ko_lookup(chains, ann)


def test_missing_annotation_fails_closed(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    _cards, chains, _irep, ann = _load(d)
    ann = [r for r in ann if r["chain_id"] != "CH-3"]
    with pytest.raises(SystemExit, match="缺 D1 标注"):
        hs.build_ko_lookup(chains, ann)


# ---------------------------------------------------------------
# ③三门禁 fail-closed
# ---------------------------------------------------------------

def test_gates_pass_on_fixture(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    g = st["gates"]
    assert g["gate_pass"] and g["vote3_roundtrip_rows"] == len(cards)
    assert g["votes_identity_rows"] == len(cards)
    assert g["vote2_depth_rows"] == len(cards)


def test_gate_vote3_roundtrip_mismatch_fails_closed(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    cards[0]["vote3_gutmgene_cross"] = "0"      # 卡片列被改 → 与索引不同源
    cards[0]["votes_total"] = str(int(cards[0]["votes_total"]) - 1)
    with pytest.raises(SystemExit, match="票③往返核验失配"):
        hs.compute_shuffle(cards, chains, irep, ann)


def test_gate_votes_identity_fails_closed(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    cards[1]["votes_total"] = str(int(cards[1]["votes_total"]) + 1)
    with pytest.raises(SystemExit, match="恒等式门禁失配"):
        hs.compute_shuffle(cards, chains, irep, ann)


def test_gate_vote2_depth_fails_closed(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    c = cards[2]
    c["vote2_kg_depth"] = "2"                  # 票②≠depth
    c["votes_total"] = str(int(c["votes_total"]) + 1)
    with pytest.raises(SystemExit, match="恒等式门禁失配"):
        hs.compute_shuffle(cards, chains, irep, ann)


def test_gate_vote4_lookup_fails_closed(tmp_path):
    """卡片 endpoint 列与本次可测查表不同源 → 换尺子量，必须拒算。"""
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    c = cards[0]                               # CH-1 原 endpoint=1
    c["endpoint_meas_ko"] = "0"
    c["vote4_measurability"] = str(int(c["vote4_measurability"]) - 1)
    c["votes_total"] = str(int(c["votes_total"]) - 1)
    with pytest.raises(SystemExit, match="恒等式门禁失配"):
        hs.compute_shuffle(cards, chains, irep, ann)


# ---------------------------------------------------------------
# ④Wilcoxon 手算教科书对照
# ---------------------------------------------------------------

def test_wilcoxon_hand_check_no_ties():
    """d=[1,-2,3,4,-5,6]：秩 1..6，W+=14、W-=7、S=21、var=22.75。"""
    w = hs.wilcoxon_signed_rank_normal([1, -2, 3, 4, -5, 6])
    assert w["n_eff"] == 6 and w["n_zero"] == 0
    assert (w["w_plus"], w["w_minus"], w["rank_sum"]) == (14, 7, 21)
    assert w["tie_correction"] == 0
    z = (14 - 10.5 - 0.5) / math.sqrt(22.75)
    assert abs(w["z"] - z) < 1e-12
    assert abs(w["p_two_sided"] - math.erfc(abs(z) / math.sqrt(2))) < 1e-15
    assert abs(w["rank_biserial"] - 1 / 3) < 1e-15


def test_wilcoxon_hand_check_with_ties_and_zeros():
    """d=[1,1,-1,-2,0,0]：|1| 三并取中秩 2，|2| 秩 4；零差弃置 n_eff=4。"""
    w = hs.wilcoxon_signed_rank_normal([1, 1, -1, -2, 0, 0])
    assert w["n_eff"] == 4 and w["n_zero"] == 2
    assert (w["w_plus"], w["w_minus"]) == (4, 6)
    assert w["tie_correction"] == 24           # 3³−3
    var = 4 * 5 * 9 / 24 - 24 / 48             # = 7.0
    assert abs(w["z"] - ((4 - 5) + 0.5) / math.sqrt(var)) < 1e-12


def test_wilcoxon_degenerate_all_zero():
    w = hs.wilcoxon_signed_rank_normal([0, 0, 0])
    assert w["degenerate"] and w["n_eff"] == 0 and w["p_two_sided"] == 1.0
    assert "退化" in hs.direction_conclusion(w, 4.0, 4.0)


def test_wilcoxon_matches_scipy_bitwise_if_available():
    """与 scipy approx 模式逐位对拍（scipy 不在环境则跳过，不装哑巴）。"""
    scipy_stats = pytest.importorskip("scipy.stats")
    for diffs in ([1, -2, 3, 4, -5, 6],
                  [1, 1, -1, -2, 0, 0],
                  [2, 2, 2, -1, -3, 5, 5, -4, 0, 1]):
        w = hs.wilcoxon_signed_rank_normal(diffs)
        nz = [x for x in diffs if x != 0]
        got = scipy_stats.wilcoxon(nz, zero_method="wilcox",
                                   alternative="two-sided", correction=True,
                                   mode="approx")
        assert abs(float(got.statistic) - min(w["w_plus"], w["w_minus"])) == 0
        assert float(got.pvalue) == w["p_two_sided"]      # 逐位全等，非"接近"


# ---------------------------------------------------------------
# ⑤边界带 + 效应量同段
# ---------------------------------------------------------------

def test_boundary_band_labels():
    assert hs.boundary_label(0.07) == "边界带"
    assert hs.boundary_label(0.0999) == "边界带"
    assert hs.boundary_label(0.05) == ""        # 开区间：0.05 本身不算
    assert hs.boundary_label(0.10) == ""
    assert hs.boundary_label(0.049) == ""
    assert hs.boundary_label(0.20) == ""


def test_boundary_band_keeps_conclusion_wording_and_adds_no_test(tmp_path, monkeypatch):
    """边界带只加标签：结论句式不变、不追加任何事后检验（§2.3 预指定）。"""
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    md = hs.render_report(d / "hypothesis_cards.tsv", d / "chains.tsv",
                          d / "interaction_report.tsv",
                          d / "chain_annotations.tsv", st)
    assert "不在边界带" in md
    st["wilcoxon"] = dict(st["wilcoxon"], p_two_sided=0.07)
    st["wilcoxon"]["degenerate"] = False
    md2 = hs.render_report(d / "hypothesis_cards.tsv", d / "chains.tsv",
                           d / "interaction_report.tsv",
                           d / "chain_annotations.tsv", st)
    assert "边界带" in md2
    assert "不追加任何事后检验" in md2
    # 主结论句式不变：仍是无显著差异那句
    assert "无显著差异（p=7.0000e-02" in md2 or "无显著差异" in md2


def test_effect_size_reported_in_same_paragraph_as_p(tmp_path):
<<<<<<< HEAD
    """评审风险提醒的机器化：p 与 r 必须同段并列（防 p 极小 + r≈0 被误读）。"""
=======
    """监工风险提醒的机器化：p 与 r 必须同段并列（防 p 极小 + r≈0 被误读）。"""
>>>>>>> origin/main
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    md = hs.render_report(d / "hypothesis_cards.tsv", d / "chains.tsv",
                          d / "interaction_report.tsv",
                          d / "chain_annotations.tsv", st)
    hit = [ln for ln in md.splitlines() if "双侧 p=" in ln]
    assert len(hit) == 1
    assert "rank-biserial r=" in hit[0] and "z=" in hit[0]
    assert "B3 先例正是 r≈0" in hit[0]


def test_negative_result_uses_prewritten_sentence(tmp_path):
    """负结果按 §2.1 预写解读句入档（不粉饰）。"""
    d = _make_fixture(tmp_path / "sg")
    cards, chains, irep, ann = _load(d)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    md = hs.render_report(d / "hypothesis_cards.tsv", d / "chains.tsv",
                          d / "interaction_report.tsv",
                          d / "chain_annotations.tsv", st)
    if st["wilcoxon"]["p_two_sided"] >= hs.ALPHA:
        assert hs.PREWRITTEN_NULL_SENTENCE in md
        assert "预写解读句生效" in md


# ---------------------------------------------------------------
# ⑥排序键零改动 + 落档护栏 + 确定性
# ---------------------------------------------------------------

def test_no_card_materialized_and_inputs_untouched(tmp_path):
    """洗牌卡中间态零落盘 + 输入文件零改动（排序键零改动的行为面）。"""
    d = _make_fixture(tmp_path / "sg")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in d.iterdir() if p.is_file()}
    out = tmp_path / "report.md"
    hs.run(d, out)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in d.iterdir() if p.is_file()}
    assert before == after
    produced = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert produced == ["report.md"]           # 没有任何洗牌卡 TSV


def test_report_deterministic_and_no_timestamp(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    hs.run(d, tmp_path / "a.md")
    hs.run(d, tmp_path / "b.md")
    assert (tmp_path / "a.md").read_bytes() == (tmp_path / "b.md").read_bytes()
    text = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "random.Random(20261105)" in text
    assert "rank-biserial" in text and "方向结论" in text
    # 零时间戳：任何 YYYY-MM-DD HH:MM 都不许出现（否则三跑 sha 不可能全等）
    assert not re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text)


def test_append_only_idempotent_and_drift_refused(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    out = tmp_path / "r.md"
    s1 = hs.run(d, out)
    assert s1["idempotent"] is False
    s2 = hs.run(d, out)
    assert s2["idempotent"] is True
    out.write_text("tampered", encoding="utf-8")
    with pytest.raises(SystemExit, match="只增不改区拒写"):
        hs.run(d, out)


def test_missing_inputs_and_columns_fail_closed(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(SystemExit, match="缺输入"):
        hs.run(d, tmp_path / "r.md")
    d2 = _make_fixture(tmp_path / "sg2")
    (d2 / "chain_annotations.tsv").write_text("chain_id\tcohort\n",
                                              encoding="utf-8")
    with pytest.raises(SystemExit, match="缺列"):
        hs.run(d2, tmp_path / "r2.md")


def test_card_chain_or_name_mismatch_fails_closed(tmp_path):
    d = _make_fixture(tmp_path / "sg")
    cards, _chains, _irep, _ann = _load(d)
    cards[0]["metabolite_name"] = "WRONG"
    _write_tsv(d / "hypothesis_cards.tsv", hs.REQUIRED_CARD_COLS,
               [[c[k] for k in hs.REQUIRED_CARD_COLS] for c in cards])
    with pytest.raises(SystemExit, match="不一致"):
        hs.run(d, tmp_path / "r.md")

    d2 = _make_fixture(tmp_path / "sg2")
    rows = _read(d2 / "chains.tsv", hs.REQUIRED_CHAIN_COLS)
    rows = [r for r in rows if r["chain_id"] != "CH-6"]
    _write_tsv(d2 / "chains.tsv", hs.REQUIRED_CHAIN_COLS,
               [[r[k] for k in hs.REQUIRED_CHAIN_COLS] for r in rows])
    with pytest.raises(SystemExit, match="缺失"):
        hs.run(d2, tmp_path / "r2.md")


def test_cli_registers_hop1_shuffle():
    assert "hop1-shuffle" in cli.MODULES
    mf = cli.MODULES["hop1-shuffle"][0]
    assert (cli.ROOT / "src" / mf).exists()


def test_cli_end_to_end_via_module_main(tmp_path, monkeypatch):
    """CLI 子命令转发面：main([...]) 真跑一遍（不碰 typer，测 argparse 侧）。"""
    d = _make_fixture(tmp_path / "sg")
    out = tmp_path / "r.md"
    # 环境无关化（v0.3 合入修正）：主仓有真实数据腿时默认路径会自动启用
    # 生产对拍，而 fixture 假数据（CHEBI:1）不在真 ko_map → fail-closed；
    # worktree 因缺数据腿跳过=假绿。故把默认路径根指向不存在处，
    # 本测试只验 argparse 转发面（真数据对拍由 needs_data 档负责）。
    monkeypatch.setattr(hs, "resolve_default_legs", lambda a, b: (None, None))
    assert hs.main(["--subgraph-dir", str(d), "--output", str(out)]) == 0
    assert out.exists()


# ---------------------------------------------------------------
# ⑦ needs_data 真实锚（统一口径 -m "not needs_data" 跳过）
# ---------------------------------------------------------------

@pytest.mark.needs_data
def test_real_run_three_times_shas_equal(tmp_path):
    """真数据三跑评估档 sha 全等（验收①：种子钉扎）。"""
    if not (SG_REAL / "hypothesis_cards.tsv").exists():
        pytest.skip(f"私有数据不在位：{SG_REAL}")
    shas = []
    for i in range(3):
        out = tmp_path / f"r{i}.md"
        hs.run(SG_REAL, out)
        shas.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert len(set(shas)) == 1, shas


@pytest.mark.needs_data
def test_real_anchors_and_gates():
    """真实锚：43,773 链 / 201 代谢物 / 三门禁全过 / 恒等式守恒。"""
    if not (SG_REAL / "hypothesis_cards.tsv").exists():
        pytest.skip(f"私有数据不在位：{SG_REAL}")
    cards, chains, irep, ann = _load(SG_REAL)
    st = hs.compute_shuffle(cards, chains, irep, ann)
    assert st["n_cards"] == hs.N_CHAINS_ANCHOR
    assert st["n_distinct_metabolites"] == 201
    assert st["gates"]["gate_pass"]
    assert st["dist_true"][5] == 20856 and st["dist_shuffled"] is not None
    assert sum(st["dist_true"].values()) == st["n_cards"]
    assert sum(st["dist_shuffled"].values()) == st["n_cards"]
    assert st["vote4_sum_true"] == st["vote4_sum_shuffled"]
    # 均值差恒等式（A8）
    assert abs(st["mean_diff"] - st["sum_diff"] / st["n_cards"]) < 1e-12
    # 零差行弃置后 n_eff = n_pairs − n_zero
    w = st["wilcoxon"]
    assert w["n_eff"] + w["n_zero"] == w["n_pairs"] == st["n_cards"]
    assert w["n_eff"] > 0


@pytest.mark.needs_data
def test_golden_13_unchanged():
    """golden v3 十三件 sha 逐字节不变（验收②）。"""
    if not FROZEN_13.exists():
        pytest.skip(f"冻结清单不在位：{FROZEN_13}")
    rows = hs.verify_golden_baseline(SG_REAL, FROZEN_13)
    assert len(rows) == 13
    bad = [r for r in rows if not r["ok"]]
    assert not bad, bad
