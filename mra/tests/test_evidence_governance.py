"""Evidence Governance 铁律与 CandidateResult 契约（2026-09-24 用户裁决六铁律）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import CandidateResult, Workspace


def _cand(**kw):
    fields = dict(analysis_id="A-1", capability_id="association.partial_spearman",
                  implementation_id="mra.r", capability_version="1.0.0",
                  implementation_version="1.0.0", input_fingerprint="sha256:x",
                  output_summary="n=10", provenance={"n": 10})
    fields.update(kw)
    return CandidateResult(**fields)


def _gate_ok(cand=None):
    return evaluate_candidate(cand or _cand(),
                              method_rules_applied=["method-zero-variance-guard-001"],
                              sensitivity_status="none_required",
                              execution_governance={"verdicts": ["MULT:PASS"]})


def test_candidate_result_is_not_evidence_and_gate_enforced():
    cand = _cand()
    assert _gate_ok(cand)["allow_evidence"] is True
    assert _gate_ok(cand)["canonical_eligible"] is True
    # 阻断级 warning / 缺规则 / 缺执行治理 → 拒绝
    assert evaluate_candidate(_cand(warnings=[{"level": "blocking", "message": "x"}]),
                              method_rules_applied=["r"], execution_governance={"v": 1}
                              )["allow_evidence"] is False
    assert evaluate_candidate(_cand(), execution_governance={"verdicts": ["v"]}
                              )["allow_evidence"] is False  # 无方法规则
    with pytest.raises(ValidationError):
        _cand(warnings=[{"level": "fatal", "message": "x"}])


def test_record_evidence_cannot_bypass_gate(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ev = {"evidence_id": "EV-G1", "task_id": "T1", "claim": "c"}
    with pytest.raises(ValueError, match="治理门"):
        reg.invoke("workspace.record_evidence", {"study_id": "s", "record": ev}, context=ctx)
    out = reg.invoke("workspace.record_evidence",
                     {"study_id": "s", "record": ev, "governance": _gate_ok()},
                     context=ctx)
    assert out["committed"] is True


def _seed_evidence(reg, tmp_path, eid="EV-G1"):
    reg.invoke("workspace.record_evidence",
               {"study_id": "s", "record": {"evidence_id": eid, "task_id": "T1",
                                            "claim": "c"},
                "governance": _gate_ok()}, context={"workspace_root": tmp_path})


def test_state_mutations_keep_history_and_require_reason(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_evidence(reg, tmp_path)
    with pytest.raises(ValueError, match="reason"):
        reg.invoke("workspace.mark_downgraded",
                   {"study_id": "s", "evidence_id": "EV-G1"}, context=ctx)
    out = reg.invoke("workspace.mark_downgraded",
                     {"study_id": "s", "evidence_id": "EV-G1",
                      "reason": "敏感性检验推翻"}, context=ctx)
    assert out["supersedes_seq"] == 1
    ws = Workspace("s", root=tmp_path)
    events = [e for e in ws.events() if e["record_type"] == "Evidence"]
    assert len(events) == 2  # 历史不覆盖（append-only）
    st = ws.replay()
    assert st.evidence[0]["falsification"] == "downgraded"  # 现值由流重建


def test_refuted_cannot_silently_become_canonical(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_evidence(reg, tmp_path)
    reg.invoke("workspace.mark_refuted",
               {"study_id": "s", "evidence_id": "EV-G1", "reason": "对照否决"}, context=ctx)
    with pytest.raises(ValueError, match="铁律1/2"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV-G1",
                    "supporting_lineage": ["EV-G2"], "reason": "r"}, context=ctx)
    # 提供新治理事件后允许（铁律2：再升级须新治理事件）
    out = reg.invoke("workspace.set_canonical",
                     {"study_id": "s", "evidence_id": "EV-G1",
                      "supporting_lineage": ["EV-G2"], "reason": "重验证通过",
                      "revalidation_governance_event": "gov-evt-9"}, context=ctx)
    assert out["committed"] is True
    assert Workspace("s", root=tmp_path).replay().evidence[0]["canonical"] is True


def test_set_canonical_requires_lineage_and_blocks_compute_caller(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_evidence(reg, tmp_path)
    with pytest.raises(ValueError, match="铁律3"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV-G1", "reason": "r"}, context=ctx)
    with pytest.raises(ValueError, match="铁律4"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV-G1",
                    "supporting_lineage": ["x"], "reason": "r"},
                   context={**ctx, "caller_capability": "association.partial_spearman"})


def test_compute_capability_is_compute_only_in_registry():
    impl = build_default_registry().resolve("association.partial_spearman")
    assert impl.side_effect == "COMPUTE_ONLY"  # 纯计算≠WORKSPACE_WRITE（四级语义）
