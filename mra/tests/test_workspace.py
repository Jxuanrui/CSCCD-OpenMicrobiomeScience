"""Research Workspace Schema v1（G4）行为检查：校验、事件流、回放、隔离铁律。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mra.workspace import (Evidence, KnowledgeProvenance, ResearchTask,
                           ToolExecution, Workspace, digest)


def test_task_and_execution_validation():
    t = ResearchTask(task_id="T-001", question="UPF 与菌群？", client="zcode", model="")
    assert t.status == "open"
    with pytest.raises(ValidationError):
        ResearchTask(task_id="T-002", question="q", status="bad")
    e = ToolExecution(execution_id="E-1", task_id="T-001",
                      tool_name="r_association", tool_type="r",
                      governance_event_id="abc")
    with pytest.raises(ValidationError):
        ToolExecution(execution_id="E-2", task_id="T-001",
                      tool_name="x", tool_type="r", status="maybe")


def test_provenance_source_type_contract():
    p = KnowledgeProvenance(source_type="LITERATURE", source_name="PubMed",
                            retrieved_at="2026-09-24T00:00:00+00:00",
                            external_ids={"pmid": "123"})
    assert p.source_type in ("LOCAL_KG", "EXTERNAL_LIVE", "LITERATURE",
                             "METHOD_KNOWLEDGE", "CURRENT_STUDY")
    with pytest.raises(ValidationError):
        KnowledgeProvenance(source_type="WIKIPEDIA", source_name="x",
                            retrieved_at="t")


def test_evidence_locked_to_current_study():
    ev = Evidence(evidence_id="EV-1", task_id="T-001", claim="UPF×通路 14 条全↑")
    assert ev.source_type == "CURRENT_STUDY"
    with pytest.raises(ValidationError):  # 隔离铁律：证据不得伪装外部知识
        Evidence(evidence_id="EV-2", task_id="T-001", claim="c",
                 source_type="LOCAL_KG")
    with pytest.raises(ValidationError):
        Evidence(evidence_id="EV-3", task_id="T-001", claim="c",
                 falsification="maybe")


def test_append_only_stream_and_replay(tmp_path):
    ws = Workspace("study-x", root=tmp_path)
    ws.append(ResearchTask(task_id="T-001", question="q", client="cli"))
    ws.append(KnowledgeProvenance(source_type="METHOD_KNOWLEDGE",
                                  source_name="method_kb", retrieved_at="now"))
    ws.append(ToolExecution(execution_id="E-1", task_id="T-001",
                            tool_name="r_association", tool_type="r"))
    ws.append(Evidence(evidence_id="EV-1", task_id="T-001", claim="v1",
                       analysis_version="atlas=v4"))
    ws.append(Evidence(evidence_id="EV-1", task_id="T-001", claim="v2 敏感性修订",
                       analysis_version="atlas=v4",
                       falsification="downgraded"))  # 同 id 修订（后写覆盖）
    state = ws.replay()
    assert state.n_events == 5 and len(state.tasks) == 1
    assert state.knowledge_queries == 1 and state.tool_executions == 1
    assert len(state.evidence) == 1 and state.evidence[0]["claim"].endswith("修订")
    assert [e["seq"] for e in ws.events()] == [1, 2, 3, 4, 5]  # append-only 序号


def test_digest_deterministic():
    assert digest({"b": 1, "a": 2}) == digest({"a": 2, "b": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_bad_study_id_rejected(tmp_path):
    with pytest.raises(ValidationError if False else ValueError):
        Workspace("../escape", root=tmp_path)
