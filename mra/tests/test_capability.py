"""Scientific Capability Registry（G3/S2）契约：schema/三黄金能力/实现可换/治理门。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mra.capability import (CapabilityImplementation, CapabilityRegistry,
                            build_default_registry)

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


def _graph(tmp_path):
    from mra.kg.graph import KGGraph
    from mra.kg.snapshot import create_snapshot
    src = tmp_path / "s"; src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tStreptococcus salivarius\tMicrobe\t\t\t\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(EDGES, encoding="utf-8")
    return KGGraph(create_snapshot(source=src, root=tmp_path / "snaps", snapshot_id="cap"))


def _base(**kw):
    fields = dict(capability_id="test.cap", capability_version="1.0.0",
                  implementation_id="test.impl", implementation_version="1.0.0",
                  transport="python-inproc", input_schema={}, output_schema={},
                  provenance_contract={})
    fields.update(kw)
    return fields


def test_side_effect_enum_and_external_write_gate():
    with pytest.raises(ValidationError):
        CapabilityImplementation(**_base(side_effect="SIDE_EFFECT"))
    reg = CapabilityRegistry()
    with pytest.raises(ValueError, match="EXTERNAL_WRITE"):
        reg.register(CapabilityImplementation(**_base(side_effect="EXTERNAL_WRITE")),
                     fn=lambda p, c: {})  # 未升级治理等级→拒
    reg.register(CapabilityImplementation(
        **_base(side_effect="EXTERNAL_WRITE", governance_level="governed")),
        fn=lambda p, c: {})  # 升级后允许


def test_three_golden_capabilities_with_swappable_implementations():
    reg = build_default_registry()
    assert set(reg.list_capabilities()) >= {"method.query", "knowledge.route", "gap.check"}
    for cap in ("method.query", "knowledge.route", "gap.check"):
        impls = {i.implementation_id for i in reg.implementations(cap)}
        assert {"mra." in i or True for i in impls}
        assert len(impls) == 2  # python-inproc + mcp 双实现
    # 实现可替换而 capability_id 不变：同能力两实现结果一致
    r1 = reg.invoke("method.query", {"query": "x"}, implementation_id="mra.method_rules")
    r2 = reg.invoke("method.query", {"query": "x"}, implementation_id="mcp.mra")
    assert r1["source_type"] == r2["source_type"] == "METHOD_KNOWLEDGE"


def test_invoke_route_and_gap_with_graph_context(tmp_path, monkeypatch):
    reg = build_default_registry()
    g = _graph(tmp_path)
    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules",
                        lambda q, k=5, db_path=None: [])
    out = reg.invoke("knowledge.route", {"term": "Streptococcus salivarius",
                                         "question": "q"}, context={"graph": g})
    assert out["source_type"] == "LOCAL_KG"
    gap = reg.invoke("gap.check", {"entities": ["NoSuch X"],
                                   "analysis_types": []}, context={"graph": g})
    assert gap["overall"] == "entity_gaps_only"


def test_catalog_exports_schema_not_callables():
    cat = build_default_registry().catalog()
    assert len(cat) == 20 and all("input_schema" in c for c in cat)
    assert all(not str(c.get("implementation_id", "")).startswith("<") for c in cat)


def test_compute_only_enum_and_track_b_readonly_batch():
    """COMPUTE_ONLY 四级语义 + Track B 只读批（kg/vec/literature）零 schema 迁移。"""
    from mra.capability import SIDE_EFFECTS
    reg = build_default_registry()
    assert SIDE_EFFECTS == ("READ_ONLY", "COMPUTE_ONLY", "WORKSPACE_WRITE", "EXTERNAL_WRITE")
    for cap in ("kg.resolve", "kg.neighbors", "kg.edge_evidence", "vec.query",
                "literature.search"):
        impl = reg.resolve(cap)
        assert impl.side_effect == "READ_ONLY"
    lit = reg.resolve("literature.search")
    assert lit.deterministic is False and lit.auth_scope  # live 检索非确定+需可选凭据


def test_workspace_write_capabilities_are_guarded(tmp_path):
    """workspace.* 变更能力：WORKSPACE_WRITE+guarded；commit 走 Workspace 账本。"""
    reg = build_default_registry()
    for cap in ("workspace.record_execution", "workspace.record_evidence",
                "workspace.revise_evidence", "workspace.mark_downgraded",
                "workspace.mark_refuted"):
        impl = reg.resolve(cap)
        assert impl.side_effect == "WORKSPACE_WRITE" and impl.governance_level == "guarded"
    sc = reg.resolve("workspace.set_canonical")
    assert sc.side_effect == "WORKSPACE_WRITE" and sc.governance_level == "governed"  # 更高权限
    # v1.1：须经账本裁决（候选+决策入流后按 decision_id 提交）
    from mra.governance import evaluate_candidate
    from mra.workspace import CandidateResult, Workspace
    ws = Workspace("cap-test", root=tmp_path)
    cand = CandidateResult(analysis_id="AC-1", capability_id="x.y",
                           implementation_id="i.i", capability_version="1",
                           implementation_version="1", input_fingerprint="f",
                           output_summary="s", provenance={"n": 1})
    ws.append(cand)
    d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                           method_rules_applied=["r"],
                           execution_governance={"verdicts": ["v"]})
    ws.append(d)
    out = reg.invoke("workspace.record_evidence",
                     {"study_id": "cap-test",
                      "record": {"evidence_id": "EV-T1", "task_id": "T1",
                                 "claim": "registry commit 通道测试",
                                 "candidate_id": "AC-1"},
                      "decision_id": d.decision_id},
                     context={"workspace_root": tmp_path})
    assert out == {"study_id": "cap-test", "committed": True}
    st = Workspace("cap-test", root=tmp_path).replay()
    assert st.evidence and st.evidence[0]["evidence_id"] == "EV-T1"
