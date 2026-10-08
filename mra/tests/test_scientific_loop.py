"""G2 Scientific Research Loop：契约/四门/合法停止/重放/Planning-Execution 分离。"""
from __future__ import annotations

import pytest

from mra.capability import build_default_registry
from mra.research.scientific_loop import (LoopStopped, ScientificLoop,
                                          execution_gate, plan_gate)
from mra.workspace import (CandidateResult, PlanStep, ResearchPlan,
                           ResearchTask, Workspace)


def _task(**kw):
    fields = dict(task_id="T-G2", question="UPF 与菌群关联？", client="test")
    fields.update(kw)
    return ResearchTask(**fields)


def _plan(**kw):
    fields = dict(plan_id="P-1", research_task_id="T-G2", plan_version=1,
                  steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon")],
                  method_constraints=["method-multiple-testing-001"],
                  stopping_conditions=["insufficient_data"])
    fields.update(kw)
    return fields


def test_plan_gate_contract():
    reg = build_default_registry()
    ok = plan_gate(ResearchPlan(**_plan()), reg)
    assert ok["allow"]
    bad = plan_gate(ResearchPlan(**_plan(
        steps=[PlanStep(step_id="s1", capability_id="no.such.capability")])), reg)
    assert not bad["allow"] and any("未知能力" in p for p in bad["problems"])
    no_stop = plan_gate(ResearchPlan(**_plan(stopping_conditions=[])), reg)
    assert not no_stop["allow"]


def test_plan_gate_rejects_implementation_binding():
    reg = build_default_registry()
    plan = ResearchPlan(**_plan(steps=[
        PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                 inputs={"implementation_id": "mra.numpy"})]))
    v = plan_gate(plan, reg)
    assert not v["allow"] and any("implementation" in p for p in v["problems"])


def test_execution_gate_respects_task_constraints():
    reg = build_default_registry()
    task = _task(constraints=["forbid:COMPUTE_ONLY"])
    v = execution_gate("diversity.alpha_shannon", reg, task)
    assert not v["allow"] and any("禁止" in p for p in v["problems"])
    v2 = execution_gate("no.such", reg, _task())
    assert not v2["allow"]


def test_legal_stops(tmp_path, monkeypatch):
    reg = build_default_registry()
    loop = ScientificLoop("g2-test", registry=reg, workspace_root=tmp_path)
    task = _task()
    loop.open_task(task)
    monkeypatch.setattr("mra.knowledge.method_rules.search_method_rules", lambda *a, **k: [])
    with pytest.raises(LoopStopped) as ei:
        loop.resolve_method_constraints(task, "不存在的规则查询xyz")
    assert ei.value.state == "unresolved_method_gap"
    st = Workspace("g2-test", root=tmp_path).replay()
    terms = [e for e in [None] and []] or [x for x in range(st.loop_events)]
    assert st.loop_events >= 2  # stage_entered + terminal 均入流


def test_plan_revision_append_only(tmp_path):
    loop = ScientificLoop("g2-rev", workspace_root=tmp_path)
    task = _task()
    loop.open_task(task)
    p1 = ResearchPlan(**_plan())
    loop.ws.append(p1)
    p2 = ResearchPlan(**_plan(plan_id="P-2", plan_version=2, supersedes_plan_id="P-1"))
    loop.ws.append(p2)
    st = Workspace("g2-rev", root=tmp_path).replay()
    assert st.research_plans == 2  # v1 不被覆盖


def test_golden_loop_full_chain(tmp_path):
    """金环精简版：gap→knowledge→method→plan→execute(计算)→evidence门→提交→重放。"""
    reg = build_default_registry()
    loop = ScientificLoop("g2-golden", registry=reg, workspace_root=tmp_path)
    task = _task(task_id="T-G2-G", entities=["Faecalibacterium prausnitzii"],
                 constraints=[])
    loop.open_task(task)
    gaps = loop.assess_gaps(task, entities=["Faecalibacterium prausnitzii"],
                            analysis_types=["零方差 常数列"])
    assert gaps["n_method_gaps"] == 0
    route = loop.acquire_knowledge(task, "Faecalibacterium prausnitzii", "关联疾病？")
    assert route["source_type"] == "LOCAL_KG"
    rules = loop.resolve_method_constraints(task, "零方差 常数列")
    assert rules
    plan = ResearchPlan(**_plan(
        plan_id="P-G", research_task_id="T-G2-G",
        steps=[PlanStep(step_id="d1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "T-G2-G-alpha"})]))
    loop.adopt_plan(task, plan)
    out = loop.execute_step(task, plan, plan.steps[0])
    cand = out["candidate"]
    assert cand.result_type == "diversity_alpha"
    result = loop.evaluate_and_commit(task, cand, rules)
    assert result["decision"].allow_evidence
    commit = loop.commit_evidence(task, cand, result["decision"],
                                  claim="物种层 Shannon 中位数（G2 金环）")
    assert commit["committed"]
    loop.complete(task.task_id)
    st = Workspace("g2-golden", root=tmp_path).replay()
    # 重放重建完整研究过程：任务/计划/候选/裁决/证据/循环事件
    assert st.research_plans == 1 and st.candidate_results == 1
    assert st.governance_decisions == 1 and len(st.evidence) == 1
    assert st.loop_events >= 8
    assert st.evidence[0]["candidate_id"] == "T-G2-G-alpha"
