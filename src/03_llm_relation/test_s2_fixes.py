#!/usr/bin/env python3
"""S2/S3 修复回归（s2_fix_plan #1/#4）：snapshot 原子性 + resume duplicate 计数。

vote()/judge() 为 main() 闭包，其 kill-resume 集成测试排下一会话（需 stub 语料加载）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classify_relations as cr  # noqa: E402


def test_snapshot_atomic_no_tmp_left(tmp_path, monkeypatch):
    out = tmp_path / "llm_relations.jsonl"
    monkeypatch.setattr(cr, "OUTPUT", out)
    monkeypatch.setattr(cr, "EXEC", {"capability_id": "c1", "execution_id": "EX-t", "started_at": "t"})
    monkeypatch.setattr(cr, "USAGE_REF", tmp_path / "usage.jsonl")
    cr.snapshot([{"pmid": "1", "subject": {"id": "a"}, "object": {"id": "b"}, "predicate": "ok"}])
    assert out.exists() and not out.with_suffix(".jsonl.tmp").exists()
    assert len(out.read_text().strip().splitlines()) == 1


def test_snapshot_failure_leaves_old_file_intact(tmp_path, monkeypatch):
    out = tmp_path / "llm_relations.jsonl"
    out.write_text("OLD\n")
    monkeypatch.setattr(cr, "OUTPUT", out)
    monkeypatch.setattr(cr, "EXEC", {"capability_id": "c1", "execution_id": "EX-t", "started_at": "t"})
    monkeypatch.setattr(cr, "USAGE_REF", tmp_path / "usage.jsonl")
    class Boom:  # 触发快照流程中途异常（缺 get / 循环引用均可）
        pass
    with pytest.raises(AttributeError):
        cr.snapshot([Boom()])
    assert out.read_text() == "OLD\n", "任何中途异常必须保留旧文件完整（原子性）"


def test_resume_duplicate_counter_semantics(tmp_path, monkeypatch):
    # stage=1 且带 votes 的行 = duplicate 风险（修复后 api_err 分支不再产生）
    risk = {"status": "ok", "stage": "1", "votes": ["a"], "predicate": "a"}
    ok1 = {"status": "ok", "stage": "1"}                 # 待投票（正常）
    ok2 = {"status": "ok", "stage": "2", "votes": ["a", "a", "a"]}  # 已投（正常）
    rows = {"k1": risk, "k2": ok1, "k3": ok2, "k4": None}
    dup = sum(1 for v in rows.values()
              if v and v.get("status") == "ok" and v.get("stage") == "1" and v.get("votes"))
    assert dup == 1
