"""meta-review 回路行为检查：错误模式提炼与摘要注入。"""
from __future__ import annotations

from mra.research.loop import meta_review, state_digest
from mra.research.session import Finding, ResearchSession


def _session_with_errors(tmp_path):
    s = ResearchSession(question="q", target="t", run_id="meta-test", root=tmp_path)
    for i in range(4):
        s.add_finding(Finding(
            claim=f"r_association -> KeyError: 未知暴露表 guess_{i}",
            tool="planner_step",
            inputs={"tool": "r_association", "args": {}},
            evidence={"result": "error"}))
    return s


def test_meta_review_extracts_error_pattern(tmp_path):
    s = _session_with_errors(tmp_path)
    critique = meta_review(s)
    assert "r_association×4" in critique
    assert "契约" in critique  # 改进指令在场


def test_digest_clean_when_no_errors(tmp_path):
    s = ResearchSession(question="q", target="t", run_id="clean", root=tmp_path)
    assert meta_review(s) == "" or "改进要点" not in meta_review(s)
    assert "question: q" in state_digest(s)
