"""L2 评估回归断言（监工 P0-1④）——四层数字一漂即红。

为什么必须钉死：脚本纯确定性（零 RNG、零 LLM），输入快照都在盘；
**数字变化只可能来自代码/口径改动**。所以"不等"就是事故信号，
不是"结果更新"。改口径必须先改这里并留 CHANGELOG。
"""
import glob
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.needs_data


def _latest_eval_dir():
    ds = sorted(glob.glob(str(ROOT / "data/logs/l2_eval_*")))
    return Path(ds[-1]) if ds else None


def test_frozen_numbers_match_latest_artifact():
    d = _latest_eval_dir()
    if d is None:
        pytest.skip("无 l2_eval_* 产物")
    res = json.loads((d / "l2_eval.json").read_text(encoding="utf-8"))
    L = res["layers"]
    assert (L["L2_strict"]["n_pairs"], L["L2_strict"]["library_has_pair"]) == (534, 54)
    assert (L["L2_ext_bidir_le1"]["n_pairs"],
            L["L2_ext_bidir_le1"]["library_has_pair"]) == (706, 72)
    assert (L["L2_ext_bidir_le2"]["n_pairs"],
            L["L2_ext_bidir_le2"]["library_has_pair"]) == (829, 77)
    assert (L["L2_ext_bidir_le3"]["n_pairs"],
            L["L2_ext_bidir_le3"]["library_has_pair"]) == (905, 79)
    dr = L["L2_strict"]["direction"]
    assert (dr["comparable_pairs"], dr["consistent"]) == (46, 27)
    assert res["self_conflict_full_pool"]["n_pairs"] == 36
    assert res["pool_total_pairs"] == 1545


def test_ci_and_sidecars_present():
    d = _latest_eval_dir()
    if d is None:
        pytest.skip("无 l2_eval_* 产物")
    res = json.loads((d / "l2_eval.json").read_text(encoding="utf-8"))
    lo, hi = res["layers"]["L2_strict"]["library_has_pair_ci95"]
    assert 0 < lo < res["layers"]["L2_strict"]["library_has_pair_rate"] < hi < 1
    for name in ("SHA256SUMS", "l2_conflict_pairs.tsv",
                 "l2_strict_pair_detail.tsv", "l2_eval_report.md"):
        assert (d / name).exists(), f"{name} 缺失"


def test_timeslice_frozen_numbers():
    """序1 冻结数字回归（cutoff=2023 唯一主值；改口径必先改此测试+CHANGELOG）。"""
    import glob
    ds = sorted(glob.glob(str(ROOT / "data/logs/timeslice_*")))
    if not ds:
        pytest.skip("无 timeslice 产物")
    r = json.loads((Path(ds[-1]) / "timeslice_eval.json").read_text(encoding="utf-8"))
    assert r["n_exam"] == 1976          # 预注册考卷
    assert r["cutoff"] == 2023
    assert (r["kg_at_pairs"], r["full_graph_pairs"]) == (671, 1545)
    assert r["strict_hits"] == 2
    assert r["verdict"] == "CASE_ONLY"  # 冻结规则：1-4 命中=仅案例披露
    assert r["contamination"]["assert_all_hit_evidence_le_cutoff"] == "PASS"
