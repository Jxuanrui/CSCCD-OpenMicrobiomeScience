"""七新模块最小回归断言（完整性修复项 4：冻结数字/结构防漂移）。"""
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (ROOT / "data/logs").exists(),
                                reason="无 data/logs 快照区")


def _latest(pat):
    ds = sorted((ROOT / "data/logs").glob(pat))
    return ds[-1] if ds else None


def test_benchmark_frozen():
    d = _latest("benchmark_*")
    if not d: pytest.skip("无产物")
    j = json.loads((d / "benchmark.json").read_text(encoding="utf-8"))
    assert j["A_benchmark"]["exam_size"] == 1976
    assert j["A_benchmark"]["verdict"] == "INSUFFICIENT_OVERLAP"
    assert j["B_bin_hitrate"]["verdict"] == "LOW_VS_HIGH_DISCRIMINATION_NON_MONOTONIC"


def test_counterfactual_frozen():
    d = _latest("counterfactual_*")
    if not d: pytest.skip("无产物")
    j = json.loads((d / "cf_manifest.json").read_text(encoding="utf-8"))
    assert j["n_cards_baseline"] == 43773
    assert j["n_counterfactual_runs"] == 10


def test_timeslice_frozen():
    d = _latest("timeslice_*")
    if not d: pytest.skip("无产物")
    j = json.loads((d / "timeslice_eval.json").read_text(encoding="utf-8"))
    assert j["n_exam"] == 1976 and j["verdict"] == "CASE_ONLY"


def test_crosswalk_assets():
    b = ROOT / "bridge/l2_crosswalk.tsv"
    if not b.exists(): pytest.skip("无资产")
    import csv
    from collections import Counter
    c = Counter(r["relation"] for r in csv.DictReader(open(b, encoding="utf-8"), delimiter="\t"))
    assert c == {"exact": 269, "parent_of": 174, "synonym": 2, "child_of": 59}


def test_baseline_reconcile_pass():
    d = _latest("baseline_reconcile_*")
    if not d: pytest.skip("无产物")
    j = json.loads((d / "reconcile.json").read_text(encoding="utf-8"))
    assert j["verdict"] == "PASS"


def test_year_probe_design_a():
    d = _latest("year_probe_*")
    if not d: pytest.skip("无产物")
    j = json.loads((d / "year_probe.json").read_text(encoding="utf-8"))
    assert j["verdict"] == "DESIGN_A_EDGE_SLICING"
