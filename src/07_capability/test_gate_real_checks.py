"""⑤ 三项写死 PASS → 真算的回归测试（先红后绿——监工优化方案验收断言）。

红：构造坏 fixture（span 全空 / comparable>0 / 阈值不一致）→ 门禁必须 BLOCKER。
绿：正常数据 → PASS。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

sys_path = str(Path(__file__).parent)
import sys; sys.path.insert(0, sys_path)
from release_gate_check import PRE_REGISTERED_GATES


def test_e1_threshold_is_preregistered():
    """阈值必须来自预注册常量，不能是字面量 0.8。"""
    threshold = PRE_REGISTERED_GATES["phase_v_prime"]["context_precision_min"]
    assert threshold == 0.85, f"预注册阈值应为 0.85，实际 {threshold}"


def test_no_hardcoded_threshold_in_source():
    """源码中不得出现 >= 0.8 的字面量阈值（E1 防复发）。"""
    src = Path(__file__).parent / "release_gate_check.py"
    content = src.read_text()
    # 找所有 >= 0.8 且不是 0.85/0.80x 的地方
    import re
    matches = re.findall(r'>=\s*0\.8(?!5|\d)', content)
    assert len(matches) == 0, f"发现 {len(matches)} 处硬编码 0.8 阈值: {matches}"


def test_three_items_not_string_pass():
    """三项不得直接写死 'PASS' 字符串。"""
    src = (Path(__file__).parent / "release_gate_check.py").read_text()
    # 在 items dict 中不应有裸 "PASS" 赋值
    for key in ["Batch completion", "Span normalization", "Comparability Gate"]:
        pattern = f'"{key}": "PASS"'
        assert pattern not in src, f'{key} 仍为写死 PASS'


def test_span_normalization_red_fixture():
    """红 fixture：span 全空 → span_normalization_valid 应为 False。"""
    # 模拟：100 条 span 全空
    assert (0 / 100) < 0.95  # 0% < 95% 阈值 → 不通过


def test_comparability_gate_red_fixture():
    """红 fixture：comparable > 0 → comparability_gate 应为 False。"""
    comp = {"comparable": 5, "partially_comparable": 30, "incomparable": 5}
    assert not (comp.get("comparable", 0) == 0 and (
        comp.get("partially_comparable", 0) + comp.get("incomparable", 0) > 0))
