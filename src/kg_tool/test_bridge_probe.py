"""桥接探测器的门禁单测（P0 四项的点位守护）。

为什么这些测试重要：crosswalk 一旦进评估分母，任何"规范化写松了/child_of
漏进来"都会直接改变主数字，且很难在结果层看出。这里把规则钉成测试，
规则改了测试先红。
"""
import csv

import pytest

from kg_tool.bridge_probe import (EVAL_RELATIONS, canon_mesh,
                                  load_crosswalk_for_eval, mesh_tokens)


# ---------- 规范化（P0-3①） ----------
@pytest.mark.parametrize("raw,want", [
    ("MESH:D012817", "D012817"),
    ("MeSH:D012817", "D012817"),      # 双拼写必须归一
    ("MeSH:C536657", "C536657"),      # C=Supplementary 也合法
    ("MESH:OMIM:603663", None),       # 畸形：MeSH 前缀套 OMIM 号
    ("MESH:OMIM:109730", None),
    ("D012817", "D012817"),           # 无前缀也接受
    ("MESH:", None),
    ("MESH:abc", None),
])
def test_canon_mesh(raw, want):
    assert canon_mesh(raw) == want


def test_canon_mesh_prevents_fake_intersection():
    """4 个 OMIM 畸形 id 若被切成同一串，会凭空造出交点——必须全部判 None。"""
    bad = ["MESH:OMIM:603663", "MESH:OMIM:612348",
           "MESH:OMIM:614878", "MESH:OMIM:109730"]
    assert {canon_mesh(x) for x in bad} == {None}


@pytest.mark.parametrize("cell,want", [
    ("D012817", {"D012817"}),
    ("*,D020181,D010372", {"D020181", "D010372"}),  # 多值格必须切开
    ("*,*", set()),
    ("*", set()),
    ("*;D001005", {"D001005"}),
    ("", set()),
    ("D05", set()),                                  # 位数不足不算 UID
])
def test_mesh_tokens(cell, want):
    assert mesh_tokens(cell) == want


# ---------- child_of 不进评估（P0-3③） ----------
def _write(tmp_path, rows):
    p = tmp_path / "cw.tsv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["src_system", "src_id", "dst_system", "dst_id",
                    "relation", "evidence", "sha16"])
        w.writerows(rows)
    return p


def test_child_of_hard_filtered(tmp_path):
    p = _write(tmp_path, [
        ["NCBI:taxid", "1", "lib:taxid", "1", "exact", "exact:1", "a" * 16],
        ["NCBI:taxid", "2", "lib:taxid", "9", "parent_of", "parent_of:9|rank=genus",
         "b" * 16],
        ["NCBI:taxid", "3", "lib:taxid", "8", "child_of", "child_of:8|rank=genus",
         "c" * 16],
    ])
    rows = load_crosswalk_for_eval(p)
    assert [r["relation"] for r in rows] == ["exact", "parent_of"]
    assert "child_of" not in EVAL_RELATIONS


def test_unknown_relation_fails_closed(tmp_path):
    p = _write(tmp_path, [
        ["NCBI:taxid", "1", "lib:taxid", "1", "grandparent_of", "x", "d" * 16]])
    with pytest.raises(SystemExit):
        load_crosswalk_for_eval(p)


# ---------- sha16（P0-3④） ----------
def test_sha16_definition_and_sensitivity():
    import hashlib
    def sha16(src, rel, ev):
        s = f"NCBI:taxid|{src}|gutMDisorder:taxid|{src}|{rel}|{ev}"
        return hashlib.sha256(s.encode()).hexdigest()[:16]
    a = sha16("102106", "exact", "exact:102106")
    assert len(a) == 16 and a == sha16("102106", "exact", "exact:102106")
    assert a != sha16("102106", "synonym", "exact:102106")  # relation 变→指纹变
    assert a != sha16("102106", "exact", "exact:102107")    # 证据变→指纹变


# ---------- 祖先距离上限（红线④：语义降格只可登记） ----------
def test_ancestor_dist_cap_filters_deep_bridge(tmp_path):
    p = _write(tmp_path, [
        ["MeSH:UID", "D1", "gutMDisorder:MeSH", "D1", "exact", "exact:D1", "a" * 16],
        ["MeSH:UID", "D2", "gutMDisorder:MeSH", "D9", "parent_of",
         "parent_of:D9|dist=1|tree=C14.1", "b" * 16],
        ["MeSH:UID", "D3", "gutMDisorder:MeSH", "D8", "parent_of",
         "parent_of:D8|dist=3|tree=C14", "c" * 16],
        ["MeSH:UID", "D4", "gutMDisorder:MeSH", "D7", "parent_of",
         "parent_of:D7|dist=4|tree=C", "d" * 16],
        ["MeSH:UID", "D5", "gutMDisorder:MeSH", "D6", "parent_of",
         "parent_of:D6|dist=7|tree=C", "e" * 16],
    ])
    rows = load_crosswalk_for_eval(p)          # 默认 cap=3
    assert [r["src_id"] for r in rows] == ["D1", "D2", "D3"]
    # 放宽 cap 才可把深桥纳入（仅供敏感性，不作主值）
    assert len(load_crosswalk_for_eval(p, max_dist=99)) == 5


def test_germ_rows_without_dist_are_unaffected(tmp_path):
    p = _write(tmp_path, [
        ["NCBI:taxid", "1", "gutMDisorder:taxid", "2", "parent_of",
         "parent_of:2|rank=genus", "f" * 16],
    ])
    assert len(load_crosswalk_for_eval(p)) == 1
