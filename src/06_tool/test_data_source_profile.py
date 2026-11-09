"""数据腿注册表测试（W2.2）。

第一测同样是 golden 的单元级保证：注册表解析出的每条腿必须与改造前的
argparse 默认值同一路径——否则带注册表的全链必炸指纹闸。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

pytestmark = pytest.mark.needs_data  # CI 分流标记（A2：公网 CI 无私有数据，此档测试本地跑）

import data_source_profile as dsp

GM = ("<LYTEAMWORK>/<USER>/Project/Dietary_cohort_of_intestinal_flora"
      "/Project/Project03_公共数据库/gutMGene")
ROOT = dsp.ROOT


def test_registry_resolves_to_legacy_defaults():
    """注册表解析值=各模块改造前默认路径（golden 前提，逐腿断言）。"""
    cases = [
        ({"kg_dir": ("kg_v2", False)}, {"kg_dir": str(ROOT / "data/kg/k6_full_20261108_malformedfix")}),
        ({"library": ("library", False)},
         {"library": str(ROOT / "data/literature/20261015_154252/articles.jsonl")}),
        ({"k3_batch": ("k3_batch", True)},
         {"k3_batch": [str(ROOT / "data/kg/20261016_165931")]}),
        ({"gutmgene_dir": ("gutmgene", False)}, {"gutmgene_dir": GM}),
        ({"v01_edges": ("v01_edges", False)},
         {"v01_edges": str(ROOT / "archive/Version01/data/merged/candidate_v3/merged_edges.tsv")}),
        ({"v01_chains": ("v01_chains", False)},
         {"v01_chains": str(ROOT / "archive/Version01/data/logs/mechanism_chains.tsv")}),
        ({"ko_map": ("ko_map", False)},
         {"ko_map": str(ROOT / "archive/Version01/data/logs/external_evidence/metabolite_ko_map_v2.tsv")}),
        ({"names_dmp": ("ncbi_names", False)}, {"names_dmp": str(ROOT / "data/raw/names.dmp")}),
        ({"nodes_dmp": ("ncbi_nodes", False)}, {"nodes_dmp": str(ROOT / "data/raw/nodes.dmp")}),
        ({"kg_nodes": ("kg_nodes", False)},
         {"kg_nodes": str(ROOT / "data/kg/k6_full_20261108_malformedfix/merged_nodes.tsv")}),
    ]
    for mapping, orig in cases:
        dest = next(iter(mapping))
        ns = argparse.Namespace(**{dest: None})
        dsp.resolve(ns, mapping, orig)
        assert str(getattr(ns, dest)) == str(next(iter(orig.values()))), f"腿 {dest} 注册表≠旧默认"


def test_explicit_over_registry(monkeypatch):
    """显式传参不经注册表（也不触发锚校验）。"""
    ns = argparse.Namespace(v01_edges=Path("/tmp/someone_else.tsv"))
    dsp.resolve(ns, {"v01_edges": ("v01_edges", False)},
                {"v01_edges": "orig"})
    assert str(ns.v01_edges) == "/tmp/someone_else.tsv"


def test_tampered_anchor_fails_closed(tmp_path, monkeypatch):
    """故意改 sha → 启动即败（W2.2 验收硬指标）。"""
    reg = {k: (dict(v) if isinstance(v, dict) else v)
            for k, v in dsp._load_json(dsp.REGISTRY_PATH).items()}  # _readme 等非字典条目原样保留
    reg["ko_map"]["anchor_sha256"] = "0" * 64  # 篡改锚
    fake = tmp_path / "data_sources.json"
    fake.write_text(__import__("json").dumps(reg), encoding="utf-8")
    monkeypatch.setattr(dsp, "REGISTRY_PATH", fake)
    ns = argparse.Namespace(ko_map=None)
    with pytest.raises(SystemExit) as e:
        dsp.resolve(ns, {"ko_map": ("ko_map", False)}, {"ko_map": "orig"})
    assert "启动即败" in str(e.value)


def test_missing_local_key_fails_closed(tmp_path, monkeypatch):
    """本机条目缺本地覆盖件 → 报错并给出可操作提示（不静默）。"""
    reg = {"_readme": "", "gutmgene": {"local_key": "gutmgene_dir",
                                       "anchor_file": "x", "anchor_sha256": "y"}}
    fake = tmp_path / "data_sources.json"
    fake.write_text(__import__("json").dumps(reg), encoding="utf-8")
    monkeypatch.setattr(dsp, "REGISTRY_PATH", fake)
    monkeypatch.setattr(dsp, "LOCAL_PATH", tmp_path / "no_such_local.json")
    ns = argparse.Namespace(gutmgene_dir=None)
    with pytest.raises(SystemExit) as e:
        dsp.resolve(ns, {"gutmgene_dir": ("gutmgene", False)},
                    {"gutmgene_dir": "orig"})
    assert "no_such_local" in str(e.value) or "覆盖件" in str(e.value)


def test_kg_root_env_precedence(monkeypatch):
    """KG_ROOT > 自动探测（spec §四）。"""
    monkeypatch.setenv("KG_ROOT", "/tmp/fake_root")
    assert dsp.get_root() == Path("/tmp/fake_root")
    monkeypatch.delenv("KG_ROOT")
    assert dsp.get_root() == Path(__file__).resolve().parents[2]
