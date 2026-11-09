#!/usr/bin/env python3
"""name_bridge.py 单元测试——每条匹配规则一个用例，外加确定性验证。

为什么这么写：菌名桥是推演模块的法定前置（fail-closed），桥错一个名
比少桥十个名危害大——所以 R4（界域收窄/同名多 taxid 不桥）/R5（rank
不符不桥）必须有独立用例锁住；确定性（同输入同输出）是仓库验收标准之一。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from name_bridge import (  # noqa: E402
    KINGDOM_EXCLUDE, KINGDOM_SCOPE, RANK_EXPECTED, UNNAMED_CODE_TOKEN_RE,
    bridge, build_name_lookup, build_parent_and_rank,
    load_metaphlan_features, main, norm_name, parse_dmp_line, unnamed_reason,
)


def write_dmp(path: Path, rows: list[list[str]]) -> None:
    """造 NCBI dump 格式小文件：字段间 \\t|\\t 相连、行尾 \\t| 结束（与真实 dmp 一致）。"""
    path.write_text(
        "".join("\t|\t".join(r) + "\t|\n" for r in rows), encoding="utf-8")


def make_dmps(base: Path) -> tuple[Path, Path]:
    """微型 names/nodes.dmp：覆盖全部规则分支的最小事实集。

    谱系设计：1(root)─2(Bacteria)/2157(Archaea)/4751(Fungi) 三大入界锚；
    33208(Animalia) 排除锚走另一条链；888888(未定界原生生物)直达 root——
    覆盖 R4 三段式判界的每个分支。
    """
    base.mkdir(parents=True, exist_ok=True)
    names, nodes = base / "names.dmp", base / "nodes.dmp"
    # names 行=taxid/name/unique name/name_class
    write_dmp(names, [
        ["1", "root", "", "scientific name"],
        ["2", "Bacteria", "", "scientific name"],
        ["131567", "cellular organisms", "", "scientific name"],
        ["2157", "Archaea", "", "scientific name"],
        ["4751", "Fungi", "", "scientific name"],
        ["33208", "Animalia", "", "scientific name"],
        ["888888", "Protista vague", "", "scientific name"],
        ["562", "Escherichia coli", "", "scientific name"],
        ["562", "E. coli", "", "synonym"],                      # 同义词命中
        ["9999", "Fake coli", "", "scientific name"],
        ["8888", "Fake coli", "", "scientific name"],           # 同名双 taxid（都细菌）
        ["581", "Morganella", "", "scientific name"],           # 细菌属
        ["777001", "Morganella", "", "scientific name"],        # 动物界同名属
        ["1301", "Phocaeicola", "", "scientific name"],
        ["216851", "Akkermansia muciniphila", "", "scientific name"],
        ["1234", "Weird rank bug", "", "scientific name"],
        ["7777", "Anaerostipes hominis (ex Lee et al. 2021)", "",
         "scientific name"],                                     # 行尾括注
        ["660001", "Both duplicated", "", "scientific name"],   # 界内同名双号
        ["660002", "Both duplicated", "", "scientific name"],
        ["770001", "Animal only", "", "scientific name"],       # 只在动物界
        ["5521", "Candida albicans", "", "scientific name"],    # 真菌种
        ["999001", "Giardia thing", "", "scientific name"],     # 原生生物（无锚到 root）
    ])
    # nodes 行=taxid/parent/rank
    write_dmp(nodes, [
        ["1", "1", "no rank"],
        ["131567", "1", "no rank"],
        ["2", "131567", "superkingdom"],
        ["2157", "131567", "superkingdom"],
        ["4751", "2759", "kingdom"],
        ["2759", "131567", "superkingdom"],
        ["33208", "2759", "kingdom"],
        ["888888", "131567", "no rank"],                        # 未定界→直达链上 root
        ["561", "2", "genus"],
        ["562", "561", "species"],
        ["8888", "2", "species"],
        ["9999", "2", "species"],
        ["581", "2", "genus"],
        ["777001", "33208", "genus"],
        ["1301", "2", "genus"],
        ["216851", "2", "species"],
        ["1234", "2", "no rank"],                               # rank 不符
        ["7777", "2", "species"],
        ["660001", "2", "species"],
        ["660002", "2", "species"],
        ["770001", "33208", "species"],
        ["5521", "4751", "species"],
        ["999001", "888888", "species"],
    ])
    return names, nodes


@pytest.fixture()
def tiny_ctx(tmp_path: Path):
    """一次建好 (names, nodes, lookup, parent, rank)——bridge 的全部输入。"""
    names, nodes = make_dmps(tmp_path)
    feats = feats_all()
    lookup = build_name_lookup(feats, names)
    parent, rank = build_parent_and_rank(nodes)
    return names, nodes, lookup, parent, rank


def feats(pairs: list[tuple[str, str]]) -> list[dict]:
    """造 MetaPhlAn 特征输入：pairs=(rank_token, name)。"""
    return [{"source": "T", "rank_token": rk, "metaphlan_name": nm,
             "metaphlan_lineage": f"k__Bacteria|{rk}{nm}"}
            for rk, nm in pairs]


def feats_all() -> list[dict]:
    """把全部判例名并成一份特征集（lookup 一次性建全，单测按需取用）。"""
    return feats([
        ("s__", "Escherichia_coli"), ("s__", "E._coli"),
        ("s__", "Fake_coli"), ("s__", "Nothing_here"),
        ("s__", "Weird_rank_bug"), ("s__", "Anaerostipes_hominis"),
        ("s__", "Both_duplicated"), ("s__", "Animal_only"),
        ("s__", "Candida_albicans"), ("s__", "Giardia_thing"),
        ("g__", "Phocaeicola"), ("g__", "Morganella"),
    ])


def test_parse_dmp_line_real_format():
    """锁真实 dmp 行格式：行尾 '\\t|' 结束符必须被剥掉（曾致全表静默桥空的坑）。"""
    raw = "562\t|\tEscherichia coli\t|\t\t|\tscientific name\t|\n"
    assert parse_dmp_line(raw) == ["562", "Escherichia coli", "",
                                   "scientific name"]


def test_norm_name_underscore_and_case():
    """R2：下划线换空格 + casefold——MetaPhlAn 命名与 NCBI 学名的机械差。"""
    assert norm_name("Escherichia_coli") == "escherichia coli"
    assert norm_name("PHOCAEICOLA") == "phocaeicola"


def test_sci_exact_and_synonym_fallback(tiny_ctx):
    """R3：学名精确命中优先；学名未中才落同义词档。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Escherichia_coli")]), lookup,
                       parent, rank, set())
    assert rows[0]["taxid"] == "562" and rows[0]["match_method"] == "sci_exact"
    assert not unb
    # "E. coli" 不是学名而是 562 的 synonym → 同义词档命中同一 taxid
    rows2, _ = bridge(feats([("s__", "E._coli")]), lookup, parent, rank, set())
    assert rows2[0]["taxid"] == "562"
    assert rows2[0]["match_method"] == "synonym_exact"


def test_sci_stripped_paren_tier(tiny_ctx):
    """R3 第二档：NCBI 学名带行尾括注 "(ex …)"，裸名剥注后命中。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Anaerostipes_hominis")]), lookup,
                       parent, rank, set())
    assert rows and rows[0]["taxid"] == "7777"
    assert rows[0]["match_method"] == "sci_exact_stripped" and not unb


def test_kingdom_scope_resolves_cross_kingdom_homonym(tiny_ctx):
    """R4：Morganella 属名细菌/动物界同名——收窄到细菌谱系后唯一命中。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("g__", "Morganella")]), lookup, parent, rank,
                       set())
    assert rows and rows[0]["taxid"] == "581" and not unb


def test_kingdom_scope_empty(tiny_ctx):
    """R4：候选只在动物界 → kingdom_scope_empty，不桥不静默。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Animal_only")]), lookup, parent, rank,
                       set())
    assert not rows and unb[0]["reason"] == "kingdom_scope_empty"
    assert unb[0]["candidates"] == ["770001"]


def test_fungi_and_protist_in_scope(tiny_ctx):
    """R4：真菌（Candida，Fungi 锚）与未定界原生生物（直达 root 兜底）都界内。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Candida_albicans"),
                              ("s__", "Giardia_thing")]),
                       lookup, parent, rank, set())
    assert not unb
    got = {r["metaphlan_name"]: r["taxid"] for r in rows}
    assert got["Candida_albicans"] == "5521"
    assert got["Giardia_thing"] == "999001"


def test_duplicate_name_fail_closed(tiny_ctx):
    """R4：界域收窄后仍同名多 taxid 不桥——候选全披露。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Fake_coli")]), lookup, parent, rank,
                       set())
    assert not rows and unb[0]["reason"] == "duplicate_name"
    assert sorted(unb[0]["candidates"]) == ["8888", "9999"]
    rows2, unb2 = bridge(feats([("s__", "Both_duplicated")]), lookup, parent,
                         rank, set())
    assert not rows2 and unb2[0]["reason"] == "duplicate_name"


def test_rank_mismatch_fail_closed(tiny_ctx):
    """R5：s__ 桥到非 species rank 不桥（防污染下游 join）。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Weird_rank_bug")]), lookup, parent,
                       rank, set())
    assert not rows and unb[0]["reason"] == "rank_mismatch"
    # 反例：g__ 档位 Phocaeicola=genus 合法命中
    rows2, unb2 = bridge(feats([("g__", "Phocaeicola")]), lookup, parent,
                         rank, set())
    assert rows2 and rows2[0]["taxid"] == "1301" and not unb2


def test_name_not_found_disclosed(tiny_ctx):
    """未命中进未桥接名单，不静默丢弃。"""
    _, _, lookup, parent, rank = tiny_ctx
    rows, unb = bridge(feats([("s__", "Nothing_here")]), lookup, parent,
                       rank, set())
    assert not rows and unb[0]["reason"] == "name_not_found"


def test_rule_tables_locked():
    """规则表锁定（改这里必须同步改规格文档与 CHANGELOG）。"""
    assert RANK_EXPECTED == {"s__": "species", "g__": "genus"}
    assert KINGDOM_SCOPE == {2, 2157, 4751, 10239}
    assert KINGDOM_EXCLUDE == {33208, 33090}


def test_unnamed_reason_genome_code_token_level():
    """P0 口径：基因组码 token 级判别——复合名（Prevotella_SGB1680 /
    GGB80011_SGB15265）必须被剔出有名分母；UMGS 码同族剔除。"""
    assert unnamed_reason("Prevotella_SGB1680") == "genome_code"
    assert unnamed_reason("GGB80011_SGB15265") == "genome_code"
    assert unnamed_reason("UMGS1234") == "genome_code"
    # UBA 码不在裁定枚举（SGB/GGB/UMGS）内——按有名未命中披露，不剔除
    assert unnamed_reason("Clostridiales_bacterium_UBA1390") is None


def test_unnamed_reason_placeholders():
    """P0 口径：_unclassified 占位与 _sp_ 菌株指代剔除；正常双名/属名保留。"""
    assert unnamed_reason("Anaerolineaceae_unclassified") == "unclassified"
    assert unnamed_reason("Clostridium_sp_") == "sp_ref"
    assert unnamed_reason("Lachnospira_sp_NSJ_43") == "sp_ref"
    assert unnamed_reason("Phocaeicola_coprocola") is None
    assert unnamed_reason("Phocaeicola") is None
    # 不误伤含 "sp" 子串的正常词（如 Species 类词根不会出现，但锁行为）
    assert UNNAMED_CODE_TOKEN_RE.match("SGB1") and not \
        UNNAMED_CODE_TOKEN_RE.match("Sporobacter")


def test_loader_only_s_and_g(tmp_path: Path):
    """R1：t__（株级）超范围不产出特征。"""
    tsv = tmp_path / "taxonomy.tsv"
    tsv.write_text(
        "ID\tS1\n"
        "k__Bacteria|p__P|c__C|o__O|f__F|g__G|s__Good_one\t1.0\n"
        "k__Bacteria|p__P|c__C|o__O|f__F|g__G|s__Good_one|t__Str1\t0.5\n"
        "k__Bacteria|p__P|c__C|o__O|f__F|g__Gen\t0.1\n",
        encoding="utf-8")
    feats = load_metaphlan_features(tsv, "X")
    assert {(f["rank_token"], f["metaphlan_name"]) for f in feats} == {
        ("s__", "Good_one"), ("g__", "Gen")}


def test_cli_determinism_and_coverage(tmp_path: Path):
    """端到端：同输入两次运行，桥接 TSV 逐字节相等（验收标准"同输入同输出"）。"""
    names, nodes = make_dmps(tmp_path / "shared")
    tax = tmp_path / "taxonomy.tsv"
    tax.write_text(
        "ID\tS1\nk__Bacteria|p__P|g__Phocaeicola|s__Escherichia_coli\t1\n",
        encoding="utf-8")
    kg = tmp_path / "merged_nodes.tsv"
    kg.write_text(
        "id\tname\tcategory\nNCBITaxon:562\tEscherichia coli\tSpecies\n",
        encoding="utf-8")

    def run(tag: str) -> tuple[bytes, dict]:
        out = tmp_path / f"out_{tag}"
        rc = main(["--names-dmp", str(names), "--nodes-dmp", str(nodes),
                   "--taxonomy", str(tax), "--source", "T",
                   "--kg-nodes", str(kg), "--out-dir", str(out)])
        assert rc == 0
        return (out / "name_bridge_cohorts.tsv").read_bytes(), \
            json.loads((out / "coverage_summary.json").read_text())

    b1, j1 = run("a")
    b2, j2 = run("b")
    assert b1 == b2
    # 覆盖度口径：taxid 桥接口径 s__=1/1；in_main_graph 口径也是 1/1（562 在图）
    s = j1["sources"][0]
    assert s["coverage_s"]["n_bridged"] == 1 and s["coverage_s"]["coverage"] == 1.0
    assert s["coverage_s_in_main_graph"]["n_bridged"] == 1
    # j2 除 generated_at 外应与 j1 逐键相等（时间戳是元数据不是数据）
    j1["overall"].pop("generated_at")
    j2["overall"].pop("generated_at")
    assert j1 == j2


def test_cli_named_denominator_fields(tmp_path: Path):
    """P0 整改：named_d/named_cov 机器字段——无名码剔出分母后桥质量口径。

    事实集：s__ 共 4 特征（Escherichia_coli 有名桥通 + Prevotella_SGB1680
    基因组码 + Bacteria_unclassified 占位 + Clostridium_sp_ 菌株指代）
    → 全特征口径 1/4=25%，有名口径 1/1=100%——有名分母剔掉"无桥可桥"。
    """
    names, nodes = make_dmps(tmp_path / "dmps")
    tax = tmp_path / "taxonomy.tsv"
    tax.write_text(
        "ID\tS1\n"
        "k__Bacteria|p__P|g__Escherichia|s__Escherichia_coli\t1\n"
        "k__Bacteria|p__P|g__Prevotella|s__Prevotella_SGB1680\t0.5\n"
        "k__Bacteria|g__Bacteria_unclassified|s__Bacteria_unclassified\t0.2\n"
        "k__Bacteria|p__P|g__Clostridium|s__Clostridium_sp_\t0.1\n"
        "k__Bacteria|p__P|g__GGB80011\t0.05\n",
        encoding="utf-8")
    kg = tmp_path / "merged_nodes.tsv"
    kg.write_text("id\tname\tcategory\nNCBITaxon:562\tX\tSpecies\n",
                  encoding="utf-8")
    out = tmp_path / "out"
    rc = main(["--names-dmp", str(names), "--nodes-dmp", str(nodes),
               "--taxonomy", str(tax), "--source", "T",
               "--kg-nodes", str(kg), "--out-dir", str(out)])
    assert rc == 0
    j = json.loads((out / "coverage_summary.json").read_text())
    s = j["sources"][0]
    cs = s["coverage_s"]
    assert cs["n_features"] == 4 and cs["n_bridged"] == 1
    assert cs["coverage"] == 0.25
    # P0 机器字段：named_d=1（三个无名特征全剔除）、named_cov=100%
    assert cs["named_d"] == 1 and cs["named_bridged"] == 1
    assert cs["named_cov"] == 1.0
    # 剔除原因审计披露：genome_code/unclassified/sp_ref 各计一
    assert s["named_excluded"]["s__"] == {"genome_code": 1,
                                          "sp_ref": 1, "unclassified": 1}
    # g__ 档：GGB 码属级特征剔除后 named_d=0（None 披露不崩）
    assert s["coverage_g"]["n_features"] == 1
    assert s["coverage_g"]["named_d"] == 0
    assert s["coverage_g"]["named_cov"] is None
    assert s["named_excluded"]["g__"] == {"genome_code": 1}
