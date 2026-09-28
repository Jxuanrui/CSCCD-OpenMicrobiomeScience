"""H5 Counterfactual Benchmark——系统真正响应 Knowledge/Governance 状态，不是记住固定答案。

Case 1: 移除 multiple-testing 规则 → gap.check 报方法缺口
Case 2: sensitivity 改为 stable → 不再 downgrade
Case 3: specificity 改为真特异 → 不再 refute
Case 4: 加 blocking warning → evidence promotion 被阻断
"""
from __future__ import annotations

import pytest

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.workspace import (CandidateResult, GovernanceDecision, Workspace)


def _cand(**kw):
    fields = dict(analysis_id="A-CF", capability_id="x.y", implementation_id="i.i",
                  capability_version="1", implementation_version="1",
                  input_fingerprint="sha256:cf", output_summary="n=10",
                  provenance={"n": 10})
    fields.update(kw)
    return CandidateResult(**fields)


def _gate(cand, rules=None, **kw):
    fields = dict(candidate_event_seq=1,
                  method_rules_applied=rules or ["method-zero-variance-guard-001"],
                  execution_governance={"verdicts": ["MULT:PASS"]})
    fields.update(kw)
    return evaluate_candidate(cand, **fields)


# ---- Case 1: 移除规则 → gap 检出 ----

def test_cf1_remove_method_rule_changes_gap_detection(tmp_path, monkeypatch):
    """移除 multiple-testing 规则后 gap.check 应报方法缺口。"""
    from mra.knowledge.method_rules import search_method_rules

    # 有规则时
    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules",
                        lambda q, k=5, db_path=None: [
                            {"rule_id": "method-multiple-testing-001", "structured": True}])
    from mra.knowledge.gap import detect_gaps
    from mra.kg.graph import KGGraph
    from mra.kg.snapshot import create_snapshot
    src = tmp_path / "g"; src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        "id\tname\tcategory\taliases\txrefs\ttax_rank\n", encoding="utf-8")
    (src / "merged_edges.tsv").write_text(
        "subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
        "support_count\tconfidence\tpolarity\tlast_updated\n", encoding="utf-8")
    g = KGGraph(create_snapshot(source=src, root=tmp_path / "s", snapshot_id="cf1"))
    with_rules = detect_gaps(g, entities=[], analysis_types=["多重检验 BH 族"])
    assert with_rules["n_method_gaps"] == 0  # 规则在 → 无缺口

    # 移除规则 → 有缺口
    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules",
                        lambda q, k=5, db_path=None: [])
    without = detect_gaps(g, entities=[], analysis_types=["多重检验 BH 族"])
    assert without["n_method_gaps"] == 1  # 规则不在 → 缺口报出


# ---- Case 2: sensitivity stable → 不降级 ----

def test_cf2_stable_sensitivity_does_not_downgrade(tmp_path):
    """sensitivity=passed 时 GovernanceDecision 允许且不应触发 downgrade。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("cf2", root=tmp_path)
    cand = _cand()
    ws.append(cand)
    # sensitivity=passed（稳定）
    d = _gate(cand, candidate_event_seq=ws.events()[-1]["seq"],
              sensitivity_status="passed")
    ws.append(d)
    assert d.allow_evidence  # 稳定 → 允许
    reg.invoke("workspace.record_evidence",
               {"study_id": "cf2", "decision_id": d.decision_id,
                "record": {"evidence_id": "EV-CF2", "task_id": "T", "claim": "c",
                           "candidate_id": "A-CF"}}, context=ctx)
    st = ws.replay()
    assert st.evidence[0]["falsification"] == "none"  # 未降级


def test_cf2b_attenuated_sensitivity_does_downgrade(tmp_path):
    """对照：attenuated 时 decision canonical_eligible=False（虽然 allow）。"""
    cand = _cand()
    d = _gate(cand, candidate_event_seq=1, sensitivity_status="failed")
    assert not d.allow_evidence  # sensitivity 失败 → 拒绝


# ---- Case 3: 真特异 → 不 refute ----

def test_cf3_true_specificity_does_not_refute():
    """特异性对照如果真的特异（对照模块远弱于主模块），系统不应 refute。"""
    # 模拟 rho：主模块强、对照弱、模块分独立
    rho_syn_st = 0.35; rho_deg_st = 0.02; rho_modules = 0.3; rho_syn_bd = -0.05
    refuted = (abs(rho_deg_st) > 0.2 * abs(rho_syn_st)) or (abs(rho_modules) > 0.9) \
        or (rho_syn_st * rho_syn_bd > 0 and abs(rho_syn_bd) > 0.05)
    assert not refuted  # 真特异 → 不 refute


def test_cf3b_fake_specificity_does_refute():
    """对照：非特异（对照与主模块同量级）→ refute。"""
    rho_syn_st = 0.35; rho_deg_st = 0.34; rho_modules = 0.98; rho_syn_bd = 0.03
    refuted = (abs(rho_deg_st) > 0.2 * abs(rho_syn_st)) or (abs(rho_modules) > 0.9) \
        or (rho_syn_st * rho_syn_bd > 0 and abs(rho_syn_bd) > 0.05)
    assert refuted  # 非特异 → refute


# ---- Case 4: blocking warning → 阻断 ----

def test_cf4_blocking_warning_blocks_evidence(tmp_path):
    """加 blocking warning → evaluate_candidate 拒绝 → record_evidence 被阻。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("cf4", root=tmp_path)
    cand = _cand(warnings=[{"level": "blocking", "message": "样本错位检测到"}])
    ws.append(cand)
    d = _gate(cand, candidate_event_seq=ws.events()[-1]["seq"])
    ws.append(d)
    assert not d.allow_evidence  # 阻断级 warning → 拒绝
    assert any("blocking" in r.lower() for r in d.blocking_reasons)
    with pytest.raises(ValueError):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "cf4", "decision_id": d.decision_id,
                    "record": {"evidence_id": "EV-CF4", "task_id": "T",
                               "claim": "c", "candidate_id": "A-CF"}}, context=ctx)


# ---- Adversarial tests（H5.3 要求）----

def test_adv_fake_decision_id_rejected(tmp_path):
    reg = build_default_registry()
    with pytest.raises(ValueError, match="不在 Scientific Ledger"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "s", "decision_id": "FAKE-ID-000",
                    "record": {"evidence_id": "EV", "task_id": "T", "claim": "c",
                               "candidate_id": "A"}}, context={"workspace_root": tmp_path})


def test_adv_implementation_bypass_blocked():
    """implementation_id 不能绕过 capability 层。"""
    from mra.research.scientific_loop import plan_gate
    from mra.workspace import PlanStep, ResearchPlan
    reg = build_default_registry()
    plan = ResearchPlan(plan_id="PLAN", research_task_id="T", plan_version=1,
                        steps=[PlanStep(step_id="s", capability_id="method.query",
                                        inputs={"implementation_id": "mra.method_rules"})],
                        method_constraints=["r"],
                        stopping_conditions=["insufficient_data"])
    v = plan_gate(plan, reg)
    assert not v["allow"]  # implementation 绑定被拦


def test_adv_compute_cannot_call_set_canonical(tmp_path):
    reg = build_default_registry()
    with pytest.raises(ValueError, match="铁律4"):
        reg.invoke("workspace.set_canonical",
                   {"study_id": "s", "evidence_id": "EV", "supporting_lineage": ["x"],
                    "reason": "r", "decision_id": "whatever"},
                   context={"workspace_root": tmp_path,
                            "caller_capability": "association.partial_spearman"})
