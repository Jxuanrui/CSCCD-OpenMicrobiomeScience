from __future__ import annotations

from pathlib import Path

from mra.knowledge import KnowledgeStore
from mra.model_runtime import ModelRef
from mra.workflow.agent import GovernanceAgent


class _UnusedRuntime:
    pass


def _store(tmp_path: Path) -> KnowledgeStore:
    path = tmp_path / "entry.yaml"
    path.write_text(
        """id: context-1
package: context
title: Cohort context
version: 1
evidence_level: contextual_fact
applicability: 项目X
content: This full content must not be returned by the agent tool.
source:
  - kind: internal
    ref: plan-v1
    date: 2026-09-12
approval:
  status: approved
  by: pi
  revision: r1
""",
        encoding="utf-8",
    )
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.ingest(path)
    return store


def test_knowledge_tool_is_opt_in(tmp_path: Path) -> None:
    model = ModelRef("test", "model", "v1", "local")
    without_store = GovernanceAgent(_UnusedRuntime(), None, model)
    assert "knowledge_search" not in {spec.name for spec in without_store._tool_specs()}
    assert "knowledge_search" not in {spec.name for spec in GovernanceAgent._tool_specs()}
    assert "未知工具名" in without_store._execute("knowledge_search", '{"query":"Cohort"}')

    with_store = GovernanceAgent(_UnusedRuntime(), None, model, knowledge=_store(tmp_path))
    assert "knowledge_search" in {spec.name for spec in with_store._tool_specs()}
    result = with_store._execute("knowledge_search", '{"query":"Cohort"}')
    assert result == "Cohort context | contextual_fact | 项目X | plan-v1"
    assert "full content" not in result
