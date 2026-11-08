"""crosswalk 资产对账单测（监工 P1-2）——防未来重跑悄悄改数。

为什么要有：资产包是论文数字的直接来源，重跑一次若行数/档位变了而文档没跟，
就会出现"文档与资产两套口径"（本项目历史上反复踩过）。这里把**当前冻结值**
钉成断言：改了资产就必须改测试，改了测试就有 diff 可审。
"""
import json
from collections import Counter
from pathlib import Path

import pytest

from kg_tool.bridge_probe import EVAL_RELATIONS, load_crosswalk_for_eval

BRIDGE = Path(__file__).resolve().parents[2] / "bridge"

pytestmark = pytest.mark.skipif(
    not (BRIDGE / "l2_crosswalk.tsv").exists(),
    reason="bridge/ 资产包未在盘（如浅克隆），跳过对账")


def _rows():
    import csv
    with open(BRIDGE / "l2_crosswalk.tsv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_relation_counts_frozen():
    c = Counter(r["relation"] for r in _rows())
    assert c == {"exact": 269, "parent_of": 174, "synonym": 2, "child_of": 59}
    assert sum(c.values()) == 504


def test_manifest_matches_tsv():
    m = json.loads((BRIDGE / "crosswalk_manifest.json").read_text(encoding="utf-8"))
    assert m["rows"]["total"] == len(_rows()) == 504
    assert m["rows"]["germ"] == {"exact": 186, "synonym": 2, "parent_of": 74}
    assert m["rows"]["disease"] == {"exact": 83, "parent_of": 100, "child_of": 59}
    # 三档机器值（P0-A 修复后真值；旧 1235 假数不得回归）
    bt = m["evaluable_pairs"]["by_tier"]
    assert bt == {"strict_exact": 534, "with_parent_dist_le3": 794,
                  "with_parent_any_dist": 815}
    assert m["evaluable_pairs"]["our_pairs"] == 1545


def test_manifest_pct_is_machine_derived():
    m = json.loads((BRIDGE / "crosswalk_manifest.json").read_text(encoding="utf-8"))
    pct = m["evaluable_pairs"]["by_tier_pct"]
    n = m["evaluable_pairs"]["our_pairs"]
    for k, v in m["evaluable_pairs"]["by_tier"].items():
        assert abs(pct[k] - v / n) < 1e-6, f"{k} 百分比与边池不自洽"


def test_eval_loader_excludes_child_of_and_deep():
    rows = load_crosswalk_for_eval(BRIDGE / "l2_crosswalk.tsv")
    assert all(r["relation"] in EVAL_RELATIONS for r in rows)
    assert not any(r["relation"] == "child_of" for r in rows)
    # dist>=4 的 15 条桥必须被挡在评估外
    assert not any("dist=4" in r["evidence"] for r in rows)
    assert len(rows) == 504 - 59 - 15


def test_sha16_self_consistent():
    import hashlib
    for r in _rows():
        s = (f"{r['src_system']}|{r['src_id']}|{r['dst_system']}|"
             f"{r['dst_id']}|{r['relation']}|{r['evidence']}")
        assert hashlib.sha256(s.encode()).hexdigest()[:16] == r["sha16"]
