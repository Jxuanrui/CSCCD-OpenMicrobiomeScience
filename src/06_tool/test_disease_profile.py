"""疾病档案制测试（W2.1）。

为什么第一测是"档案=旧默认逐字全等"：这是 golden 等价在单元级的保证——
档案值若与改造前默认值有任何一字之差，带 --disease 的全链必炸指纹闸。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

pytestmark = pytest.mark.needs_data  # CI 分流标记（A2：公网 CI 无私有数据，此档测试本地跑）

import disease_profile
from hypothesize import EXT102_TSV


def test_htn_profile_matches_legacy_defaults():
    """htn 档案六字段与改造前 argparse 默认逐字一致（golden 前提）。"""
    d = disease_profile.load("htn")
    assert d["mesh_ids"] == "D006973,D006976"
    assert d["name_root"] == "hypertens"
    assert d["doid"] == "DOID:10763"
    assert d["key"] == "htn"
    assert d["disease_query"] == '"Hypertension"[MeSH] OR "hypertension"[TIAB]'
    # ext102：档案存仓内相对路径，锚定 ROOT 后必须与模块原默认同一文件
    assert (disease_profile.ROOT / d["ext102"]) == EXT102_TSV


def test_resolution_order_explicit_over_profile_over_default():
    """三档优先级：显式 > 档案 > 原默认。"""
    import argparse
    orig = {"mesh": "D006973,D006976", "doid": "DOID:10763"}
    # ① 显式最高
    ns = argparse.Namespace(disease="htn", mesh="MESH:X", doid=None)
    disease_profile.resolve(ns, orig)
    assert ns.mesh == "MESH:X" and ns.doid == "DOID:10763"
    # ② 档案次之
    ns = argparse.Namespace(disease="htn", mesh=None, doid=None)
    disease_profile.resolve(ns, orig)
    assert ns.mesh == "D006973,D006976" and ns.doid == "DOID:10763"
    # ③ 无 --disease 用原默认（行为与改造前一致）
    ns = argparse.Namespace(disease=None, mesh=None, doid=None)
    disease_profile.resolve(ns, orig)
    assert ns.mesh == "D006973,D006976" and ns.doid == "DOID:10763"


def test_unknown_disease_fails_closed():
    import argparse
    ns = argparse.Namespace(disease="no_such_disease", mesh=None, doid=None)
    with pytest.raises(SystemExit):
        disease_profile.resolve(ns, {"mesh": "m", "doid": "d"})


def test_null_ext102_fails_closed(tmp_path, monkeypatch):
    """ext102 空值的病（无外部佐证源）当前版本挡住，防张冠李戴落 HTN 文件。"""
    p = tmp_path / "empty.json"
    p.write_text('{"key":"x","mesh_ids":"m","name_root":"n","doid":"d",'
                 '"disease_query":"q","ext102":null}', encoding="utf-8")
    monkeypatch.setattr(disease_profile, "DISEASE_DIR", tmp_path)
    with pytest.raises(SystemExit):
        disease_profile.load("empty")


def test_null_generic_field_fails_closed(tmp_path, monkeypatch):
<<<<<<< HEAD
    """非 ext102 字段显式 null/空串同样 fail-closed（评审 P1 收口）。"""
=======
    """非 ext102 字段显式 null/空串同样 fail-closed（监工 P1 收口）。"""
>>>>>>> origin/main
    p = tmp_path / "hole.json"
    p.write_text('{"key":"x","mesh_ids":null,"name_root":"n","doid":"d",'
                 '"disease_query":"q","ext102":"some/file.tsv"}',
                 encoding="utf-8")
    monkeypatch.setattr(disease_profile, "DISEASE_DIR", tmp_path)
    with pytest.raises(SystemExit):
        disease_profile.load("hole")
