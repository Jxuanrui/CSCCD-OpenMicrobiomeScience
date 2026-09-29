#!/usr/bin/env python3
"""P0-G 构造性回归：预注册门禁常量锁定——改值即失败（防 batch1 式换指标）。

数值冻结流程：用户确认后同步修改本文件第二份字面量；两处不一致 = FAIL。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_gate_check import PRE_REGISTERED_GATES, check_preregistered  # noqa: E402

# 冻结快照（用户 2026-09-29 确认冻结；变更须两处同步+用户与监工双签）
FROZEN = {
    "route_eval": {
        "overall_precision_min": 0.85,
        "disease_role_min": 0.80,
        "disease_stage_min": 0.75,
        "blind_vs_confirmed_agreement_min": 0.75,
    },
    "v6_closure": {
        "s2_votes_required_ratio": 1.0,
        "error_residual_max": 20,
        "flip_rate_max": 0.15,
        "sampling_pass_line": 0.85,
    },
    "phase_v_prime": {
        "context_precision_min": 0.85,
        "orphan_assertions_max": 0,
        "duplicate_assertions_max": 0,
        "manual_hold_leak_max": 0,
        "provenance_quartet_min": 1.0,
    },
}


def test_constants_frozen():
    for stage, gates in FROZEN.items():
        for k, v in gates.items():
            assert PRE_REGISTERED_GATES[stage][k] == v, \
                f"预注册门禁 {stage}.{k} 被改动：{PRE_REGISTERED_GATES[stage][k]} != 冻结值 {v}（须经用户+监工审）"


def test_check_logic_max_min():
    assert check_preregistered("v6_closure", {"flip_rate_max": 0.10, "error_residual_max": 5,
                                              "s2_votes_required_ratio": 1.0,
                                              "sampling_pass_line": 0.9}) == \
        {"flip_rate_max": "PASS", "error_residual_max": "PASS",
         "s2_votes_required_ratio": "PASS", "sampling_pass_line": "PASS"}
    assert check_preregistered("v6_closure", {"flip_rate_max": 0.20})["flip_rate_max"].startswith("FAIL")
    assert check_preregistered("phase_v_prime", {"context_precision_min": 0.84})["context_precision_min"].startswith("FAIL")
    assert check_preregistered("phase_v_prime", {"orphan_assertions_max": 1})["orphan_assertions_max"].startswith("FAIL")
    assert check_preregistered("v6_closure", {})["flip_rate_max"] == "MISSING"
