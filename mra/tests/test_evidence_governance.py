"""Evidence Governance v1.1：GovernanceDecision 一等账本 + 六验 + 六铁律。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import (CandidateResult, GovernanceDecision, Workspace,
                           digest)


def _cand(**kw):
    fields = dict(analysis_id="A-1", capability_id="association.partial_spearman",
                  implementation_id="mra.r", capability_version="1.0.0",
                  implementation_version="1.0.0", input_fingerprint="sha256:x",
                  output_summary="n=10", provenance={"n": 10})
    fields.update(kw)
    return CandidateResult(**fields)


def _seed(tmp_path, study="s", cand=None, decision_kwargs=None):
    """候选+裁决入账本，返回 (ws, decision)。"""
    reg = build_default_registry()
    ws = Workspace(study, root=tmp_path)
    cand = cand or _cand()
    ws.append(cand)
    seq = ws.events()[-1]["seq"]
    decision = evaluate_candidate(
        cand, candidate_event_seq=seq,
        method_rules_applied=["method-zero-variance-guard-001"],
        execution_governance={"verdicts": ["MULT:PASS"]}, **(decision_kwargs or {}))
    ws.append(decision)
    return ws, decision, reg


def test_decision_is_ledger_object_and_gate_math(tmp_path):
    ws, d, _ = _seed(tmp_path)
    assert d.allow_evidence and d.canonical_eligible and not d.blocking_reasons
    st = ws.replay()
    assert st.governance_decisions == 1 and st.candidate_results == 1
    bad = evaluate_candidate(_cand(warnings=[{"level": "blocking", "message": "x"}]),
                             candidate_event_seq=1,
                             method_rules_applied=["r"],
                             execution_governance={"verdicts": ["v"]})
    assert not bad.allow_evidence and bad.blocking_reasons
    with pytest.raises(ValidationError):
        _cand(warnings=[{"level": "fatal", "message": "x"}])


def test_record_evidence_requires_ledger_verified_decision(tmp_path):
    ws, d, reg = _seed(tmp_path)
    ctx = {"workspace_root": tmp_path}
    ev = {"evidence_id": "EV-1", "task_id": "T1", "claim": "c",
          "candidate_id": "A-1"}
    # 裸 allow_evidence / 缺 decision_id → 拒
    with pytest.raises(ValueError, match="不再被接受|decision_id"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "record": ev,
                    "governance": {"allow_evidence": True}}, context=ctx)
    # 正道：decision_id → 六验通过
    out = reg.invoke("workspace.record_evidence",
                     {"study_id": "s", "record": ev, "decision_id": d.decision_id},
                     context=ctx)
    assert out["committed"]


def test_six_verifications_negative_paths(tmp_path):
    ws, d, reg = _seed(tmp_path)
    ctx = {"workspace_root": tmp_path}
    ev = {"evidence_id": "EV-1", "task_id": "T1", "claim": "c", "candidate_id": "A-1"}
    # 1) 不存在的裁决
    with pytest.raises(ValueError, match="不在 Scientific Ledger"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "record": ev, "decision_id": "D-NONE"}, context=ctx)
    # 2) 候选不对应（错 candidate_id）
    with pytest.raises(ValueError, match="不对应"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "record": {**ev, "candidate_id": "A-OTHER"},
                    "decision_id": d.decision_id}, context=ctx)
    # 3) 指纹篡改：改写流外重建候选再裁决（seq 对但 hash 变）——模拟篡改
    tampered = _cand(effect_estimate={"x": 1})
    d2 = GovernanceDecision(decision_id="D-TAMPER", analysis_id="A-1",
                            candidate_event_seq=d.candidate_event_seq,
                            candidate_hash=digest(tampered.model_dump()),
                            allow_evidence=True)
    ws.append(tampered.__class__(**{**ws.events()[1]["record"]})) if False else None
    ws.append(d2)
    with pytest.raises(ValueError, match="指纹"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "record": ev, "decision_id": "D-TAMPER"}, context=ctx)
    # 4) allow=False 的裁决
    ws.append(GovernanceDecision(decision_id="D-DENY", analysis_id="A-1",
                                 candidate_event_seq=d.candidate_event_seq,
                                 candidate_hash=d.candidate_hash,
                                 allow_evidence=False))
    with pytest.raises(ValueError, match="allow_evidence=False"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "record": ev, "decision_id": "D-DENY"}, context=ctx)
    # 5) 失效裁决（被再裁决取代）
    ws.append(evaluate_candidate(_cand(), candidate_event_seq=d.candidate_event_seq,
                                 method_rules_applied=["r2"],
                                 execution_governance={"verdicts": ["v"]},
                                 decision_id="D-NEW",
                                 supersedes_decision_id=d.decision_id))
    with pytest.raises(ValueError, match="已失效"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "record": ev, "decision_id": d.decision_id}, context=ctx)


def _seed_evidence(tmp_path, eid="EV-1"):
    ws, d, reg = _seed(tmp_path)
    reg.invoke("workspace.record_evidence",
               {"study_id": "s", "record": {"evidence_id": eid, "task_id": "T1",
                                            "claim": "c", "candidate_id": "A-1"},
                "decision_id": d.decision_id}, context={"workspace_root": tmp_path})
    return ws, d, reg


def test_mutations_append_only_and_replay(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_evidence(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        reg.invoke("workspace.mark_downgraded",
                   {"study_id": "s", "evidence_id": "EV-1"}, context=ctx)
    reg.invoke("workspace.mark_downgraded",
               {"study_id": "s", "evidence_id": "EV-1", "reason": "敏感性推翻",
                "actor": "t"}, context=ctx)
    evs = [e for e in Workspace("s", root=tmp_path).events()
           if e["record_type"] == "Evidence"]
    assert len(evs) == 2
    assert Workspace("s", root=tmp_path).replay().evidence[0]["falsification"] == "downgraded"


def test_canonical_requires_eligible_decision_or_governance_event(tmp_path):
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    _seed_evidence(tmp_path)
    with pytest.raises(ValueError, match="decision_id"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV-1",
                    "supporting_lineage": ["x"], "reason": "r"}, context=ctx)
    with pytest.raises(ValueError, match="铁律4"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV-1",
                    "supporting_lineage": ["x"], "reason": "r",
                    "decision_id": "whatever"},
                   context={**ctx, "caller_capability": "association.partial_spearman"})
    # refuted → 无新治理事件不得 canonical
    reg.invoke("workspace.mark_refuted",
               {"study_id": "s", "evidence_id": "EV-1", "reason": "否决"}, context=ctx)
    with pytest.raises(ValueError, match="canonical"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV-1",
                    "supporting_lineage": ["x"], "reason": "r",
                    "decision_id": "D-NEW-VALID"}, context=ctx)
    reg.invoke("workspace.set_canonical",
               {"study_id": "s", "evidence_id": "EV-1",
                "supporting_lineage": ["x"], "reason": "重验证",
                "revalidation_governance_event": "gov-9"}, context=ctx)
    assert Workspace("s", root=tmp_path).replay().evidence[0]["canonical"] is True


def test_candidate_generic_envelope():
    c = _cand(result_type="atlas_scan",
              result_payload={"viral": [{"feature": "x", "rho": 0.1, "q": 0.01}]},
              artifacts=[{"kind": "tsv", "path": "hits.tsv", "sha256": "sha256:z"}],
              metrics={"viral_tested": 200, "viral_hits": 1})
    assert c.result_type == "atlas_scan" and c.metrics["viral_hits"] == 1
    base = _cand()  # 首用例字段 optional（空 dict 兼容）
    assert base.effect_estimate == {}


def test_compute_capabilities_are_compute_only():
    reg = build_default_registry()
    for cap in ("association.partial_spearman", "atlas.single_exposure_scan"):
        assert reg.resolve(cap).side_effect == "COMPUTE_ONLY"
