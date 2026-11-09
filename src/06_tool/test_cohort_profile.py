"""队列档案测试（W2.4）——首测仍是 golden 的单元级保证。"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

pytestmark = pytest.mark.needs_data  # CI 分流标记（A2：公网 CI 无私有数据，此档测试本地跑）

import cohort_profile
import ingest
<<<<<<< HEAD
htn_ingest = ingest  # 旧名兼容别名（评审 P1-1：旧名 grep 清零后此行可删）
=======
htn_ingest = ingest  # 旧名兼容别名（监工 P1-1：旧名 grep 清零后此行可删）
>>>>>>> origin/main


def test_htn_cohort_matches_legacy_defaults():
    """htn 队列档案解析值=各模块改造前默认（golden 前提）。"""
    d = cohort_profile.load("htn")
    assert str(cohort_profile.ROOT / d["obs_dir"]) \
        == str(htn_ingest.ROOT / "data/observation/htn_v1")
    assert str(cohort_profile.ROOT / d["bridge_tsv"]) \
        == str(cohort_profile.ROOT
               / "data/logs/name_bridge_20261101/name_bridge_cohorts.tsv")
    assert d["source_names"] == ["SelfData_222", "PRJEB13870", "PRJEB21612"]
<<<<<<< HEAD
    # 双档一致性（评审 W2.4 审 P2-1）：git 锚档与 local 功能档 expected_n 防漂移
=======
    # 双档一致性（监工 W2.4 审 P2-1）：git 锚档与 local 功能档 expected_n 防漂移
>>>>>>> origin/main
    assert d["expected_n"] == {k: v["expected_n"]
                               for k, v in d["ingest_sources"].items()}
    # 本机覆盖件合并后：ingest 三源 root 与模块内 SOURCES 逐字一致
    for name, cfg in htn_ingest.SOURCES.items():
        assert str(d["ingest_sources"][name]["root"]) == str(cfg["root"])
        assert d["ingest_sources"][name]["expected_n"] == cfg["expected_n"]


def test_resolution_order():
    """显式 > 档案 > 原默认（obs_dir/bridge_tsv）。"""
    orig = {"obs_dir": Path("/orig/obs"), "bridge_tsv": Path("/orig/bridge")}
    ns = argparse.Namespace(cohort="htn", obs_dir=None, bridge_tsv=None,
                            taxonomy=None, source=None)
    cohort_profile.resolve(ns, orig)
    assert str(ns.obs_dir).endswith("data/observation/htn_v1")
    ns = argparse.Namespace(cohort=None, obs_dir=None, bridge_tsv=None)
    cohort_profile.resolve(ns, orig)
    assert ns.obs_dir == Path("/orig/obs")
    ns = argparse.Namespace(cohort="htn", obs_dir=Path("/explicit"),
                            bridge_tsv=None, taxonomy=None, source=None)
    cohort_profile.resolve(ns, orig)
    assert ns.obs_dir == Path("/explicit")


def test_cohort_supplies_taxonomy_and_sources():
    """--cohort 时 name_bridge 的 taxonomy/source 列表由档案供。"""
    ns = argparse.Namespace(cohort="htn", obs_dir=None, bridge_tsv=None,
                            taxonomy=None, source=None)
    cohort_profile.resolve(ns, {})
    assert len(ns.taxonomy) == 3 and len(ns.source) == 3
    assert ns.source == ["SelfData_222", "PRJEB13870", "PRJEB21612"]


def test_unknown_cohort_fails_closed():
    ns = argparse.Namespace(cohort="no_such", obs_dir=None, bridge_tsv=None)
    with pytest.raises(SystemExit):
        cohort_profile.resolve(ns, {})


def test_ingest_dryrun_writes_nothing(tmp_path, capsys):
    """dryrun 只列清单零写入。"""
    rc = htn_ingest.main(["--out-root", str(tmp_path / "obs"),
                          "--cohort", "htn", "--dryrun"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "未读写任何文件" in out and "SelfData_222" in out
    assert not (tmp_path / "obs").exists()
