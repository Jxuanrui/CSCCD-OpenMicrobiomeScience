"""S0 契约测试：Scientific Core 零 dsh import + upstream 钉版 + standalone 冒烟。"""
from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "mra"
ADAPTER = SRC / "dsh_adapter"
FORBIDDEN = re.compile(r"dsh|deepseek.harness|deepseek_harness|@deepseek-ai", re.IGNORECASE)


def test_scientific_core_has_zero_dsh_import():
    """dsh 耦合只准存在于 dsh_adapter/ —— 其余模块出现即 FAIL。"""
    violations = []
    for path in SRC.rglob("*.py"):
        if ADAPTER in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line) and ("import" in line or "from " in line):
                violations.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()[:70]}")
    assert not violations, "Scientific Core 出现 dsh 耦合:\n" + "\n".join(violations)


def test_upstream_lock_is_pinned():
    lock = json.loads((ADAPTER / "UPSTREAM.lock.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{40}", lock["commit"])  # 精确 commit，非 tag/浮动
    assert lock["version"].startswith("0.")
    assert lock["pin_policy"]


def test_standalone_fallback_smoke(monkeypatch, tmp_path):
    """standalone 降级后端：三个 S1 切片能力不经 dsh 直接可用。"""
    from mra.research.loop import ResearchContext, dispatch

    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules",
                        lambda q, k=5, db_path=None: [{"rule_id": "method-x", "structured": True}])
    ctx = ResearchContext.__new__(ResearchContext)
    ctx.session = type("S", (), {})()
    ctx.graph = None
    r1 = dispatch({"tool": "method_query", "args": {"query": "x"}}, ctx)
    assert r1["source_type"] == "METHOD_KNOWLEDGE"
    monkeypatch.setattr("mra.knowledge.gap.detect_gaps",
                        lambda g, entities, analysis_types: {"status": "OK", "overall": "no_gap_detected"})
    r2 = dispatch({"tool": "gap_check", "args": {"entities": [], "analysis_types": []}}, ctx)
    assert r2["overall"] == "no_gap_detected"
