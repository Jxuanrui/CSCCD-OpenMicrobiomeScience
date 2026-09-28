"""Method KB（P0-3）行为检查：结构化解析、ingest+检索回路、legacy 兼容、loop 动作接线。"""
from __future__ import annotations

from pathlib import Path

import pytest

from mra.knowledge.method_rules import (
    METHOD_KEYS, ingest_method_dir, parse_rule, search_method_rules)
from mra.knowledge.store import KnowledgeStore

METHODS_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "methods"


def test_all_repo_method_rules_are_structured(tmp_path):
    """仓库自带方法规则 YAML 必须含固定七键 content（防自由文本混入新条目）。"""
    import yaml
    files = sorted(METHODS_DIR.glob("*.yaml"))
    structured_files = [p for p in files if "zero-variance" in p.name or "sample-alignment" in p.name]
    assert structured_files, "本轮新增规则文件缺失"
    for path in structured_files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        content = yaml.safe_load(doc["content"])
        assert set(METHOD_KEYS) <= set(content), f"{path.name} content 缺键"


def test_ingest_and_search_roundtrip(tmp_path):
    db = tmp_path / "k.db"
    n = ingest_method_dir(METHODS_DIR, db)
    assert n >= 12
    rules = search_method_rules("常数 零方差 伪显著", k=5, db_path=db)
    assert rules and rules[0]["structured"]
    assert rules[0]["rule_id"].startswith("method-")
    assert "risk" in rules[0] and "action" in rules[0]
    hit = search_method_rules("特异性对照 否决", k=5, db_path=db)
    assert any("specificity" in r["rule_id"] for r in hit)


def test_legacy_free_text_tolerated(tmp_path):
    db = tmp_path / "k.db"
    store = KnowledgeStore(str(db))
    store.ingest.__doc__  # noqa: B018
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(
        "id: method-legacy-001\npackage: methods\ntitle: 旧式自由文本\nversion: 1\n"
        "evidence_level: confirmed_rule\napplicability: 兼容测试。\n"
        "source: [{kind: internal, ref: test, date: '2026-09-24'}]\n"
        "approval: {status: approved, by: human, revision: test}\n"
        "content: |\n  这是旧式非结构化内容。\n", encoding="utf-8")
    store.ingest(legacy)
    store.close()
    rules = search_method_rules("旧式", k=3, db_path=db)
    assert rules and not rules[0]["structured"]
    assert "content" in rules[0]


def test_method_query_dispatch(monkeypatch):
    """研究循环 method_query 动作返回 METHOD_KNOWLEDGE 标记的结构化规则。"""
    from mra.research.loop import ResearchContext, dispatch

    class FakeSession:
        pass

    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules",
                        lambda q, k=5, db_path=None: [
                            {"rule_id": "method-zero-variance-guard-001",
                             "title": "常数/零方差列必须在统计前拒绝",
                             "trigger": "唯一值<2", "risk": "伪显著",
                             "action": "四道闸", "contraindication": "单层不够",
                             "validation_status": "validated_in_real_study"}])
    ctx = ResearchContext.__new__(ResearchContext)
    ctx.session = FakeSession()
    result = dispatch({"tool": "method_query", "args": {"query": "零方差"}}, ctx)
    assert result["source_type"] == "METHOD_KNOWLEDGE"
    assert result["rules"][0]["rule_id"] == "method-zero-variance-guard-001"
