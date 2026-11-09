"""U1 因果语言标注 sidecar 测试（B1，spec v1.1 六项验证的机器化）。

六项：①词边界（"secret" 不得命中 "secretes"，屈折形各自覆盖）②causal
优先于 correlational 记类（hit_words 两类全留）③零命中=none ④行数=断言
行数零跳过 ⑤输出 schema 5 列冻结 ⑥hypothesize 排序键零改动（B0 规格
验证条款③——sidecar 落地不许碰卡片排序）。
另锁：CLI 注册、只增不改幂等护栏、sentence_sha16 指纹。
"""
import csv
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

import pytest  # noqa: E402

from kg_tool import causal_marks as cm  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


# ---------- ① 词边界 ----------

def test_word_boundary_secret_vs_inflections():
    """\"secret\" 不得命中 \"secretes\"：v1.1 补齐屈折形后各词各管各的形。"""
    mark, hits, n = cm.scan_sentence(
        "Collinsella secretes bile acid in the gut.")
    assert mark == "causal"
    assert hits == "secretes"          # 只记命中的屈折形原词
    assert "secret" not in hits.split(",")
    assert "secrete" not in hits.split(",")
    assert n == 1


def test_word_boundary_each_inflexion_covered():
    """四个屈折位形各自被自己的词条命中（secret/secreted/secretion）。"""
    for word in ("secret", "secreted", "secretion"):
        mark, hits, n = cm.scan_sentence(f"a {word} event was observed")
        assert mark == "causal", word
        assert word in hits.split(","), word


def test_word_boundary_prefix_words_do_not_leak():
    """词边界防前缀泄漏：非词条的长词不得让短词条误命中。"""
    mark, hits, n = cm.scan_sentence(
        "the secretion apparatus")  # secretion 命中，secret/secrete 不许命中
    assert "secret" not in hits.split(",")
    assert "secrete" not in hits.split(",")
    assert "secretion" in hits.split(",")


def test_case_insensitive_and_phrase_boundary():
    """大小写不敏感；多词短语同样受 \\b 双端约束。"""
    mark, hits, n = cm.scan_sentence("This INDUCES hypertension.")
    assert mark == "causal" and "induces" in hits.split(",")
    mark, hits, n = cm.scan_sentence("leads to a shift")
    assert mark == "causal" and "leads to" in hits.split(",")
    # "instead leads tomorrow"——"leads to" 短语不成立则不命中
    mark, hits, _ = cm.scan_sentence("instead leads tomorrow")
    assert "leads to" not in hits.split(",")


# ---------- ② causal 优先 ----------

def test_causal_priority_over_correlational():
    """一句双类命中：mark=causal，但 hit_words 两类全留（spec 冻结规则）。"""
    mark, hits, n = cm.scan_sentence(
        "the strain increases the abundance of butyrate")
    assert mark == "causal"
    assert "increases" in hits.split(",")
    assert "abundance of" in hits.split(",")
    assert n == 1


def test_correlational_only_sentence():
    mark, hits, n = cm.scan_sentence(
        "the taxon is enriched in cases and linked to diet")
    assert mark == "correlational"
    assert hits == "linked to,enriched in"  # 词表序非句序（T0 裁定：可对账）
    assert n == 0


# ---------- ③ 零命中 = none ----------

def test_zero_hit_marks_none():
    mark, hits, n = cm.scan_sentence("We sequenced the cohort samples.")
    assert (mark, hits, n) == ("none", "", 0)
    mark, hits, n = cm.scan_sentence("")  # 空句同零命中（仍是行，不跳过）
    assert (mark, hits, n) == ("none", "", 0)


# ---------- ④⑤ 全流程：行数零跳过 + schema 5 列 ----------

def _make_subgraph(d: Path, rows: list[dict]) -> Path:
    """最小子图：列序刻意与真实表不同——证明读列按名不按位。"""
    cols = ["edge_id", "assertion_id", "predicate", "sentence"]
    with open(d / "relation_assertions.tsv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return d


def test_full_run_rows_schema_and_order(tmp_path):
    rows = [
        {"assertion_id": "RA-1", "sentence":
         "A increases B and is associated with C"},
        {"assertion_id": "RA-2", "sentence":
         "A is correlated with B"},
        {"assertion_id": "RA-3", "sentence": "plain descriptive text"},
        {"assertion_id": "RA-4", "sentence": ""},  # 空句也不许跳行
    ]
    d = _make_subgraph(tmp_path, rows)
    stats = cm.annotate(d)

    out = tmp_path / cm.SIDECAR_NAME
    assert stats["n_rows"] == 4                     # ④ 行数=断言行数
    assert stats["counts"] == {"causal": 1, "correlational": 1, "none": 2}
    with open(out, encoding="utf-8", newline="") as f:
        rd = csv.reader(f, delimiter="\t")
        data = list(rd)
    assert data[0] == cm.SIDECAR_COLUMNS           # ⑤ 5 列冻结
    assert len(cm.SIDECAR_COLUMNS) == 5
    assert len(data) == 5                           # 表头+4 行零跳过
    assert [r[0] for r in data[1:]] == ["RA-1", "RA-2", "RA-3", "RA-4"]
    # 行序=断言原序（零排序变更）；列值抽查
    assert data[1][1] == "causal"
    assert data[1][2] == "increases,associated with"
    assert data[1][3] == cm.sentence_sha16(rows[0]["sentence"])
    assert data[1][4] == "1"
    assert data[4][1] == "none" and data[4][2] == "" and data[4][4] == "0"


def test_missing_column_fails_closed(tmp_path):
    """缺必需列 fail-closed：不许在半张表上产出 sidecar。"""
    (tmp_path / "relation_assertions.tsv").write_text(
        "assertion_id\tnote\nRA-1\tx\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="缺列"):
        cm.annotate(tmp_path)


def test_append_only_idempotent_and_drift_refused(tmp_path):
    """只增不改护栏：重跑字节一致=幂等 no-op；漂移=fail-closed 拒写。"""
    d = _make_subgraph(tmp_path, [
        {"assertion_id": "RA-1", "sentence": "X inhibits Y"}])
    first = cm.annotate(d)
    assert first["idempotent"] is False
    payload = (d / cm.SIDECAR_NAME).read_bytes()
    second = cm.annotate(d)
    assert second["idempotent"] is True
    assert (d / cm.SIDECAR_NAME).read_bytes() == payload  # 未改一字节
    # 篡改既有 sidecar 后重跑 → 拒绝（防两次产物混放/漂移无痕覆盖）
    (d / cm.SIDECAR_NAME).write_bytes(b"tampered\n")
    with pytest.raises(SystemExit, match="不一致"):
        cm.annotate(d)


# ---------- ⑥ hypothesize 排序键零改动（B0 验证条款③） ----------

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
    # 同一键兼容内存卡（int 型，测试对拍路径）
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
         "penalty": "0.0", "chain_id": "CH-a"},   # 全同→id 升序在前
        {"votes_total": "4", "borda_score": "6", "kg_coi_rate": "0.5",
         "penalty": "0.5", "chain_id": "CH-z"},   # 罚分高→殿后
        {"votes_total": "4", "borda_score": "9", "kg_coi_rate": "0.1",
         "penalty": "0.9", "chain_id": "CH-y"},   # Borda 最高→同票中第一
        {"votes_total": "2", "borda_score": "99", "kg_coi_rate": "1.0",
         "penalty": "0.0", "chain_id": "CH-x"},   # 总票低→必最后
    ]
    got = [c["chain_id"] for c in sorted(cards, key=hyp.rank_key)]
    assert got == ["CH-y", "CH-a", "CH-b", "CH-z", "CH-x"]


# ---------- CLI 注册 ----------

def test_cli_registers_causal_marks():
    assert "causal-marks" in cli.MODULES
    mf = cli.MODULES["causal-marks"][0]
    assert (cli.ROOT / "src" / mf).exists()
