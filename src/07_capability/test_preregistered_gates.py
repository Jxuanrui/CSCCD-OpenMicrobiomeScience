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


def test_flip_rate_v1_denominator_anchor():
    """§十九签署条件③（2026-09-30 监工深夜审）：v1 口径分母锚定。

    flip_rate_v1 的分母为 7,945 Food 基线候选总数（release_gate_check.py 注释锚）。
    任何引入 flip_rate_v2（"被降级 ok 边/原有 ok 边"）的改动，不得改变 v1 的判定
    地位与分母；本断言锚定 v1 常量与分母文本，防止门禁被悄然放宽。
    """
    import release_gate_check as rgc
    assert PRE_REGISTERED_GATES["v6_closure"]["flip_rate_max"] == 0.15
    src = Path(rgc.__file__).read_text(encoding="utf-8")
    assert "7,945" in src or "7945" in src, (
        "v1 口径分母 7,945 锚文本缺失——flip_rate_v1 分母被改动（§十九条件③，须经双签）")
    # v2 若被实现，只能以"并列报告"存在，不得替换 v1 判定（嵌套键级排除）
    for stage, gates in PRE_REGISTERED_GATES.items():
        for k in gates:
            assert "flip_rate_v2" not in k, (
                f"v2 键 {stage}.{k} 进入判定常量表——v2 只报告不判定（§十九条件①③）")
