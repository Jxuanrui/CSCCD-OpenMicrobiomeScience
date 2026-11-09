"""U3 路径洗牌对照臂测试（B3，spec 验证条款①②③的机器化）。

覆盖：①种子钉扎复现（同输入双跑 sha 相等——spec 验证①）②洗牌机制
（基因多重集不变/无基因行不动/票①②④逐位不变/票③按冻结规则重查）
③三道机器门禁 fail-closed ④Wilcoxon 手算教科书对照 ⑤退化全零差
⑥只增不改幂等护栏+洗牌卡零落盘+输入零改动（排序键零改动的行为面）
⑦CLI 注册 ⑧golden 13 件不变（spec 验证②，needs_data 档）⑨真数据 Δ
方向断言+钉扎双跑（spec 验证①③，needs_data 档——按实际结果写）。
"""
import csv
import hashlib
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

from kg_tool import shuffle_control as sc  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_RUN = ROOT / "data" / "kg" / "golden_v3_20261102_113337_runA_t2"
GOLDEN_ARM = ROOT / "data" / "kg" / "golden_v3_20261102_113337_runA_arm"
GOLDEN_CARDS = GOLDEN_RUN / "hypothesis_cards.tsv"
GOLDEN_IREP = GOLDEN_RUN / "interaction_report.tsv"
FROZEN_13 = ROOT / "data" / "logs" / "golden_baseline_v3_20261102_sha256.txt"

CARD_COLS = sc.REQUIRED_CARD_COLS
IREP_COLS = ["bucket", "table", "row_index", "pmid", "entity_a", "entity_b",
             "doid", "associative_mode", "matched_node_a", "matched_node_b",
             "subgraph_edge_ids", "subgraph_predicates", "direction_verdict",
             "note"]


# ---------- 夹具 ----------

def _write_tsv(path: Path, cols: list[str], rows: list[list[str]]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(cols)
        w.writerows(rows)


def _card(cid, microbe, met, gene, gene_name, depth, v1, v3, v4):
    """卡片行——票②=depth、votes_total=v1+depth+v3+v4（装配恒等式）。"""
    v2 = depth
    return [cid, microbe, f"bug_{microbe[-1]}", met, f"met_{met[-1]}",
            gene, gene_name, "KEGG:hsa00010", str(depth), str(v1),
            str(v2), str(v3), str(v4), "0", "0",
            str(v1 + v2 + v3 + v4)]


def _irep(bucket, ea, eb, mna, mnb):
    return [bucket, "Gut Microbe-Host Gene.csv", "1", "12345", ea, eb,
            "DOID:10763", "causally", mna, mnb, "", "", "", ""]


# 六卡夹具（chain_id 升序=卡片文件序）：
#   票③手算（索引：node_ids={NCBITaxon:1, MESH:D2}，
#   free_names={genea, genec}）——
#   CH-A 菌 NCBITaxon:1 命中 node_ids → v3=1（配行内 v3=1）
#   CH-B 代谢物 MESH:D2 命中 node_ids → v3=1；基因 geneb 不命中
#   CH-C 菌/代谢物不命中，基因 genea 命中 free_names → v3=1
#   CH-D 无基因行；菌/代谢物不命中 → v3=0
#   CH-E 基因 genec 命中 free_names → v3=1
#   CH-F 三子句全不命中 → v3=0
FIXT_CARDS = [
    _card("CH-A", "NCBITaxon:1", "MESH:D1", "NCBIGene:1", "GeneA", 3, 1, 1, 1),
    _card("CH-B", "NCBITaxon:2", "MESH:D2", "NCBIGene:2", "GeneB", 3, 0, 1, 2),
    _card("CH-C", "NCBITaxon:3", "MESH:D3", "NCBIGene:1", "GeneA", 3, 0, 1, 1),
    _card("CH-D", "NCBITaxon:4", "MESH:D4", "", "", 1, 0, 0, 0),
    _card("CH-E", "NCBITaxon:5", "MESH:D5", "NCBIGene:3", "GeneC", 3, 0, 1, 1),
    _card("CH-F", "NCBITaxon:6", "MESH:D6", "NCBIGene:2", "GeneB", 2, 0, 0, 1),
]
FIXT_IREP = [
    _irep("confirm", "NCBITaxon:1bug", "GeneA", "NCBITaxon:1", ""),
    _irep("extend", "MESH:D2", "whatever", "MESH:D2", ""),
    _irep("extend", "GeneC", "else", "", ""),          # 未锚定侧名=genec
    _irep("conflict", "GeneB", "x", "", ""),           # conflict 桶不进索引
]


def _make_fixture(d: Path) -> tuple[Path, Path]:
    d.mkdir(parents=True, exist_ok=True)
    cards, irep = d / "hypothesis_cards.tsv", d / "interaction_report.tsv"
    _write_tsv(cards, CARD_COLS, FIXT_CARDS)
    _write_tsv(irep, IREP_COLS, FIXT_IREP)
    return cards, irep


def _read(path: Path, cols: list[str]) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return [{k: r[k] for k in cols}
                for r in csv.DictReader(f, delimiter="\t")]


# ---------- ② 洗牌机制 ----------

def test_gate_roundtrip_and_identity_pass_on_fixture(tmp_path):
    """夹具过三道门禁（票③手算值与索引重算全等=夹具自洽）。"""
    cards, irep = _make_fixture(tmp_path)
    stats = sc.run(cards, irep, tmp_path / "out.md")
    assert stats["gates"]["gate_pass"] is True
    assert stats["n_cards"] == 6 and stats["n_stratum"] == 5 \
        and stats["n_no_gene"] == 1


def test_shuffle_preserves_gene_multiset_and_untouched_rows():
    """基因列多重集不变+无基因行票值零改动（置换不增删、分层不动）。"""
    cards = _read_cards_from_rows(FIXT_CARDS)
    irep_rows = [dict(zip(IREP_COLS, r)) for r in FIXT_IREP]
    donors = sc.gene_permutation(cards)
    stratum = [i for i, c in enumerate(cards) if c["hop2_gene"].strip()]
    assert sorted(donors) == stratum                      # 置换=同层双射
    old_ms = sorted(cards[i]["hop2_gene"] for i in stratum)
    new_ms = sorted(cards[d]["hop2_gene"] for d in donors)
    assert old_ms == new_ms                               # 多重集逐元素保持
    # 无基因行不在置换内（行号集合不含 CH-D 下标 3）
    assert 3 not in donors and donors.count(3) == 0


def _read_cards_from_rows(rows):
    return [dict(zip(CARD_COLS, r)) for r in rows]


def test_votes_124_invariant_and_v3_recompute_by_rule(tmp_path):
    """票①②④逐位不变+票③按冻结规则以新基因名重查（钉扎置换手算对照）。

    测试侧独立复刻钉扎洗牌与四票重算（同式不同码路径），整组统计
    （sum_diff/n_pos/n_neg/W±/z/p/r）与 compute_shuffle 输出全量对拍。
    """
    cards, irep = _make_fixture(tmp_path)
    rows = _read(cards, CARD_COLS)
    irep_rows = _read(irep, IREP_COLS)
    hyp = sc.load_hypothesize()
    idx = hyp.build_vote3_index(irep_rows)

    stratum = [i for i, c in enumerate(rows) if c["hop2_gene"].strip()]
    donors = list(stratum)
    random.Random(sc.RNG_SEED).shuffle(donors)   # 测试侧复刻钉扎洗牌

    diffs_manual = [0] * len(rows)
    for k, i in enumerate(stratum):
        src = donors[k]
        # 票③'=冻结 vote3(新基因名)；票①②④取原卡列（洗牌不动其查询输入）
        v3p = hyp.vote3({"microbe_id": rows[i]["microbe_id"],
                         "metabolite_id": rows[i]["metabolite_id"],
                         "hop2_gene_name": rows[src]["hop2_gene_name"]}, idx)
        vt_p = (int(rows[i]["vote1_ext_cohort"])
                + int(rows[i]["vote2_kg_depth"]) + v3p
                + int(rows[i]["vote4_measurability"]))
        diffs_manual[i] = int(rows[i]["votes_total"]) - vt_p

    stats = sc.compute_shuffle(rows, irep_rows)
    w = stats["wilcoxon"]
    assert stats["sum_diff"] == sum(diffs_manual)
    assert w["n_pos"] == sum(1 for d in diffs_manual if d > 0)
    assert w["n_neg"] == sum(1 for d in diffs_manual if d < 0)
    assert w["n_eff"] == sum(1 for d in diffs_manual if d != 0)
    assert w == sc.wilcoxon_signed_rank_normal(diffs_manual)  # 整组对拍
    # 分布/均值恒等式（回归锁：曾有无基因行被误记 0 的缺陷——均值差
    # 必须逐位等于 sum_diff/n，分布总行数必须等于卡数）
    assert sum(stats["dist_shuffled"].values()) == stats["n_cards"]
    assert sum(stats["dist_true"].values()) == stats["n_cards"]
    assert math.isclose(stats["mean_true"] - stats["mean_shuffled"],
                        stats["sum_diff"] / stats["n_cards"], rel_tol=1e-12)


# ---------- ③ 机器门禁 fail-closed ----------

def test_gate_roundtrip_mismatch_fails_closed(tmp_path):
    """卡片票③与索引重算不符 → 拒出统计（防拿错 interaction_report）。"""
    cards, irep = _make_fixture(tmp_path)
    rows = [dict(zip(CARD_COLS, r)) for r in FIXT_CARDS]
    rows[2]["vote3_gutmgene_cross"] = "0"     # CH-C 手算应为 1（基因子句）
    rows[2]["votes_total"] = str(int(rows[2]["vote1_ext_cohort"])
                                 + int(rows[2]["vote2_kg_depth"]) + 0
                                 + int(rows[2]["vote4_measurability"]))
    _write_tsv(cards, CARD_COLS,
               [[r[c] for c in CARD_COLS] for r in rows])
    with pytest.raises(SystemExit, match="票③往返核验失配"):
        sc.run(cards, irep, tmp_path / "out.md")


def test_gate_votes_identity_fails_closed(tmp_path):
    """votes_total ≠ 四票和 → 拒出统计。"""
    cards, irep = _make_fixture(tmp_path)
    rows = [dict(zip(CARD_COLS, r)) for r in FIXT_CARDS]
    rows[0]["votes_total"] = "9"
    _write_tsv(cards, CARD_COLS,
               [[r[c] for c in CARD_COLS] for r in rows])
    with pytest.raises(SystemExit, match="恒等式门禁失配"):
        sc.run(cards, irep, tmp_path / "out.md")


def test_missing_columns_and_files_fail_closed(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(SystemExit, match="缺输入"):
        sc.run(d / "hypothesis_cards.tsv", d / "interaction_report.tsv",
               tmp_path / "out.md")
    cards = d / "hypothesis_cards.tsv"
    _write_tsv(cards, ["chain_id", "votes_total"], [["CH-1", "3"]])
    with pytest.raises(SystemExit, match="缺列"):
        sc.run(cards, d / "interaction_report.tsv", tmp_path / "out.md")


# ---------- ④ Wilcoxon 手算教科书对照 ----------

def test_wilcoxon_textbook_hand_check():
    """教科书例：diffs=[+2,-1,+1,0,+3]——中间秩/并列校正/CC/r 手算全对。

    手算：非零 d={+2,-1,+1,+3}（正 3 负 1 零 1）；|d| 并列 1,1 →
    中间秩 1.5,1.5,3,4；W+=3+1.5+4=8.5，W-=1.5，S=10，μ=5；
    σ²=4·5·9/24−(2³−2)/48=7.5−0.125=7.375；
    z=(8.5−5−0.5)/√7.375；p=erfc(z/√2)；r=(8.5−1.5)/10=0.7。
    """
    w = sc.wilcoxon_signed_rank_normal([2, -1, 1, 0, 3])
    assert w["n_eff"] == 4 and w["n_pos"] == 3 and w["n_neg"] == 1 \
        and w["n_zero"] == 1
    assert w["w_plus"] == 8.5 and w["w_minus"] == 1.5 \
        and w["rank_sum"] == 10
    assert w["tie_correction"] == 6                     # 2³−2
    assert math.isclose(w["z"], 3.0 / math.sqrt(7.375), rel_tol=1e-12)
    assert math.isclose(w["p_two_sided"], math.erfc(w["z"] / math.sqrt(2)),
                        rel_tol=1e-12)
    assert math.isclose(w["rank_biserial"], 0.7, rel_tol=1e-12)
    assert w["degenerate"] is False


def test_wilcoxon_degenerate_all_zero_diffs():
    """全零差（如全库同基因）→ 退化披露而非硬造 z。"""
    w = sc.wilcoxon_signed_rank_normal([0, 0, 0])
    assert w["degenerate"] is True and w["n_eff"] == 0 \
        and w["p_two_sided"] == 1.0 and w["rank_biserial"] == 0.0


def test_direction_conclusion_negative_result_wording():
    """负结果措辞=预注册精神（p≥α → H1 不成立如实入档）。"""
    w = {"degenerate": False, "n_eff": 10, "p_two_sided": 0.9}
    s = sc.direction_conclusion(w, 4.85, 4.85)
    assert "无显著差异" in s and "H1" in s
    w2 = {"degenerate": False, "n_eff": 10, "p_two_sided": 1e-9}
    assert "显著高于" in sc.direction_conclusion(w2, 5.0, 4.0)
    assert "显著低于" in sc.direction_conclusion(w2, 4.0, 5.0)


# ---------- ① 种子钉扎复现 + 只增不改 + 零落盘红线 ----------

def test_seed_pinned_two_runs_identical(tmp_path):
    """同输入（同路径）双跑 → 评估档逐字节相等（sha256 对比；spec 验证①）。

    评估档 §一 嵌输入路径（溯源需要）——故双跑用同一输入路径、两个
    输出路径（输出路径不嵌档，§七 用占位符）——这恰是复现场景：任何
    人重跑同输入应得同字节。
    """
    cards, irep = _make_fixture(tmp_path)
    sc.run(cards, irep, tmp_path / "r1.md")
    sc.run(cards, irep, tmp_path / "r2.md")
    s1 = hashlib.sha256((tmp_path / "r1.md").read_bytes()).hexdigest()
    s2 = hashlib.sha256((tmp_path / "r2.md").read_bytes()).hexdigest()
    assert s1 == s2


def test_append_only_idempotent_and_drift_refused(tmp_path):
    """只增不改护栏：已存在且一致=幂等 no-op；漂移=拒写。"""
    cards, irep = _make_fixture(tmp_path)
    first = sc.run(cards, irep, tmp_path / "out.md")
    assert first["idempotent"] is False
    payload = (tmp_path / "out.md").read_bytes()
    second = sc.run(cards, irep, tmp_path / "out.md")
    assert second["idempotent"] is True
    assert (tmp_path / "out.md").read_bytes() == payload
    (tmp_path / "out.md").write_bytes(b"tampered\n")
    with pytest.raises(SystemExit, match="不一致"):
        sc.run(cards, irep, tmp_path / "out.md")


def test_no_card_materialized_and_inputs_untouched(tmp_path):
    """洗牌卡中间态零落盘（spec 条款）+输入文件零改动（只读红线）。"""
    cards, irep = _make_fixture(tmp_path)
    before = (hashlib.sha256(cards.read_bytes()).hexdigest(),
              hashlib.sha256(irep.read_bytes()).hexdigest())
    sc.run(cards, irep, tmp_path / "report.md")
    after = (hashlib.sha256(cards.read_bytes()).hexdigest(),
             hashlib.sha256(irep.read_bytes()).hexdigest())
    assert before == after
    # 输出目录内只有评估档一个产物——没有任何洗牌卡 TSV
    produced = sorted(p.name for p in tmp_path.iterdir()
                      if p.is_file() and p.name != "hypothesis_cards.tsv"
                      and p.name != "interaction_report.tsv")
    assert produced == ["report.md"]


def test_report_deterministic_no_timestamp(tmp_path):
    """评估档零时间戳（日期由文件名携带）——确定性渲染。"""
    cards, irep = _make_fixture(tmp_path)
    sc.run(cards, irep, tmp_path / "a.md")
    sc.run(cards, irep, tmp_path / "b.md")
    assert (tmp_path / "a.md").read_bytes() == (tmp_path / "b.md").read_bytes()
    text = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "random.Random(20261105)" in text      # 种子钉扎入档
    assert "rank-biserial" in text and "方向结论" in text


# ---------- CLI ----------

def test_cli_registers_shuffle_control():
    assert "shuffle-control" in cli.MODULES
    mf = cli.MODULES["shuffle-control"][0]
    assert (cli.ROOT / "src" / mf).exists()


# ---------- ⑧⑨ needs_data 档（真数据；统一口径 -m "not needs_data" 跳过） ----------

@pytest.mark.needs_data
def test_real_data_delta_direction_and_anchors():
    """真数据 Δ 方向断言（按实际结果写）+回归锚（任何扰动钉扎洗牌即爆红）。

    实测（2026-11-05，主仓 golden runA_t2 卡片）：n=43,773，n_eff=4,788
    （正 2,390/负 2,398），W+=5,722,855，z=-0.115609，p=0.9080，
    r_rb=-0.001671——**负结果：无显著差异**（p≥0.05），H1 不成立，
    如实入档=预注册精神。锚值锁死：洗牌实现/种子/输入任一漂移即失配。
    """
    assert GOLDEN_CARDS.exists(), f"私有数据不在位：{GOLDEN_CARDS}"
    cards = _read(GOLDEN_CARDS, sc.REQUIRED_CARD_COLS)
    irep = _read(GOLDEN_IREP, IREP_COLS)
    stats = sc.compute_shuffle(cards, irep)
    w = stats["wilcoxon"]
    assert stats["n_cards"] == 43773 and stats["n_stratum"] == 43236
    assert w["n_eff"] == 4788 and w["n_pos"] == 2390 and w["n_neg"] == 2398
    assert w["w_plus"] == 5722855 and w["w_minus"] == 5742011
    assert math.isclose(w["z"], -0.115609, abs_tol=1e-5)
    assert w["p_two_sided"] > 0.05                       # 负结果断言
    assert abs(w["rank_biserial"]) < 0.01                # 效应≈0
    concl = sc.direction_conclusion(w, stats["mean_true"],
                                    stats["mean_shuffled"])
    assert "无显著差异" in concl


@pytest.mark.needs_data
def test_real_data_seed_pinned_double_run(tmp_path):
    """真数据钉扎双跑（spec 验证①）：两跑评估档 sha 相等。"""
    assert GOLDEN_CARDS.exists(), f"私有数据不在位：{GOLDEN_CARDS}"
    sc.run(GOLDEN_CARDS, GOLDEN_IREP, tmp_path / "r1.md")
    sc.run(GOLDEN_CARDS, GOLDEN_IREP, tmp_path / "r2.md")
    s1 = hashlib.sha256((tmp_path / "r1.md").read_bytes()).hexdigest()
    s2 = hashlib.sha256((tmp_path / "r2.md").read_bytes()).hexdigest()
    assert s1 == s2


@pytest.mark.needs_data
def test_golden_13_unchanged():
    """golden 13 件 sha 逐字节不变（spec 验证②——零主链产物变更）。

    冻结清单前 12 件在 runA_t2 目录，第 13 件（null_control_arm_cards
    前缀，名带注记）=runA_arm 目录的 hypothesis_cards.tsv——与
    scripts/golden_check.py ②同解析规则。
    """
    assert FROZEN_13.exists(), f"冻结清单不在位：{FROZEN_13}"
    n = 0
    for line in FROZEN_13.read_text().splitlines():
        if not line.strip():
            continue
        fsha, fname = line.split(None, 1)
        fname = fname.strip()
        if fname.startswith("null_control_arm_cards"):
            p = GOLDEN_ARM / "hypothesis_cards.tsv"
        else:
            p = GOLDEN_RUN / fname
        assert p.exists(), f"golden 件缺失：{fname}"
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        assert got == fsha, f"golden 件漂移：{fname}"
        n += 1
    assert n == 13
