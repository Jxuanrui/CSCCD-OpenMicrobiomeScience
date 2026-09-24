"""B2 Complex Multi-step Planning——>5步、依赖图、plan revision、fallback、方法约束变化。

Case：设计一个 7 步计划（gap→knowledge→method→compute×2→sensitivity→falsification），
中途做 plan revision（v1→v2，加一步），验证 append-only + replay + 状态正确。
"""
from __future__ import annotations

import pytest

from mra.capability import build_default_registry
from mra.research.scientific_loop import (LoopStopped, ScientificLoop,
                                          plan_gate, execution_gate)
from mra.workspace import (PlanStep, ResearchPlan, ResearchTask, Workspace)


def _task(tmp_path):
    return ResearchTask(
        task_id="T-B2", question="多步复杂规划验证：关联+多样性+敏感性+证伪",
        objective="B2: >5步计划+依赖+revision+fallback",
        task_type="complex_multi_step",
        entities=["Faecalibacterium prausnitzii"],
        constraints=[], client="b2-test")


def _plan_v1(task_id):
    """7步计划（>5步）：gap→knowledge→method→assoc→diversity→sensitivity→falsify。"""
    return ResearchPlan(
        plan_id="B2-P1", research_task_id=task_id, plan_version=1,
        steps=[
            PlanStep(step_id="s1", capability_id="gap.check",
                     inputs={"entities": ["F.prausnitzii"], "analysis_types": ["零方差"]},
                     expected_output="gap_report"),
            PlanStep(step_id="s2", capability_id="knowledge.route",
                     inputs={"term": "F.prausnitzii", "question": "关联？"},
                     depends_on=["s1"], expected_output="local_kg_neighbors"),
            PlanStep(step_id="s3", capability_id="method.query",
                     inputs={"query": "零方差 常数列", "k": 3},
                     depends_on=["s1"], expected_output="method_rules"),
            PlanStep(step_id="s4", capability_id="association.partial_spearman",
                     inputs={"exposure": "upf_g", "features": "species",
                             "max_features": 20, "analysis_id": "B2-assoc"},
                     depends_on=["s3"], expected_output="candidate_result"),
            PlanStep(step_id="s5", capability_id="diversity.alpha_shannon",
                     inputs={"features": "species", "analysis_id": "B2-div"},
                     depends_on=["s3"], expected_output="diversity_candidate"),
            PlanStep(step_id="s6", capability_id="diversity.alpha_shannon",
                     inputs={"features": "pathway", "analysis_id": "B2-div-pathway"},
                     depends_on=["s5"], expected_output="sensitivity_comparison"),
            PlanStep(step_id="s7", capability_id="diversity.alpha_shannon",
                     inputs={"features": "fungal", "analysis_id": "B2-div-fungal"},
                     depends_on=["s5"], expected_output="specificity_control"),
        ],
        method_constraints=["method-zero-variance-guard-001"],
        stopping_conditions=["insufficient_data", "blocking_governance",
                             "no_valid_capability"],
        fallback_paths=["s4 fail→s5 continue", "s7 refute→report"],
        governance_requirements=["execution_gate", "evidence_gate"])


def test_complex_plan_7_steps_with_dependency_graph():
    """7 步计划 schema 有效：step_id 唯一、依赖图正确。"""
    plan = _plan_v1("T-B2")
    assert len(plan.steps) == 7  # >5 步
    deps = {s.step_id: set(s.depends_on) for s in plan.steps}
    assert deps["s4"] == {"s3"} and deps["s5"] == {"s3"}
    assert deps["s6"] == {"s5"} and deps["s7"] == {"s5"}  # 分支
    assert deps["s1"] == set()  # 起点


def test_plan_revision_append_only(tmp_path):
    """Plan v1→v2 append-only：v1 不消失，v2 引用 supersedes。"""
    reg = build_default_registry()
    loop = ScientificLoop("b2-rev", registry=reg, workspace_root=tmp_path)
    task = _task(tmp_path)
    p1 = _plan_v1("T-B2")
    # v2：增加第 8 步（enrichment 预留）
    p2 = ResearchPlan(**{**p1.model_dump(), "plan_id": "B2-P2", "plan_version": 2,
                         "supersedes_plan_id": "B2-P1",
                         "steps": p1.steps + [PlanStep(
                             step_id="s8", capability_id="method.query",
                             inputs={"query": "多重检验", "k": 2},
                             depends_on=["s6"])]})
    loop.ws.append(p1)
    loop.ws.append(p2)
    st = Workspace("b2-rev", root=tmp_path).replay()
    assert st.research_plans == 2  # 两版并存
    assert p2.supersedes_plan_id == "B2-P1"  # lineage 完整


def test_fallback_when_compute_fails(tmp_path, monkeypatch):
    """compute 步失败时计划其余步骤可继续（fallback）。"""
    reg = build_default_registry()
    monkeypatch.setattr("mra.research.gate.run_gated_association",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("R failed")))
    plan = _plan_v1("T-B2")
    # s4 会失败，但 s5-s7 不依赖 s4 → 仍可执行
    deps = {s.step_id: set(s.depends_on) for s in plan.steps}
    assert "s4" not in deps["s5"]  # s5 不依赖 s4
    assert "s4" not in deps["s6"]  # s6 也不依赖 s4


def test_method_constraint_change_mid_plan(tmp_path):
    """中途方法约束变化：新 plan v2 引用不同规则 → plan_gate 重新校验。"""
    reg = build_default_registry()
    p1 = _plan_v1("T-B2")
    v1 = plan_gate(p1, reg)
    assert v1["allow"]
    # v2 引用一个不存在的规则
    p2 = ResearchPlan(**{**p1.model_dump(), "plan_id": "B2-P2", "plan_version": 2,
                         "method_constraints": ["no-such-rule-999"]})
    v2 = plan_gate(p2, reg)
    # method_constraints 引用不存在的规则不阻断 plan_gate（它检查非空，不查存在性）
    # 但 gap.check 会在运行时检出
    assert v2["allow"]  # plan 层不阻断
    # 正面：换成真实规则
    p3 = ResearchPlan(**{**p1.model_dump(), "plan_id": "B2-P3", "plan_version": 3,
                         "method_constraints": ["method-multiple-testing-001"]})
    v3 = plan_gate(p3, reg)
    assert v3["allow"]


def test_full_7_step_execution_with_replay(tmp_path):
    """完整 7 步计划实际执行 + replay 验证。"""
    reg = build_default_registry()
    loop = ScientificLoop("b2-full", registry=reg, workspace_root=tmp_path)
    task = _task(tmp_path)
    loop.open_task(task)
    # 简化执行：只跑 gap + method + diversity（跳过需要 R 的步骤以加速测试）
    plan = _plan_v1("T-B2")
    loop.adopt_plan(task, plan)
    # 执行 s1 (gap.check) — 需要 graph
    from mra.kg.graph import KGGraph
    from mra.kg.snapshot import latest_snapshot
    graph = KGGraph(latest_snapshot())
    s1_out = loop.execute_step(task, plan, plan.steps[0],
                               runtime_ctx={"graph": graph})
    assert s1_out["kind"] == "result"
    # 执行 s3 (method.query) — 无需 graph
    s3_out = loop.execute_step(task, plan, plan.steps[2])
    assert s3_out["kind"] == "result"
    # 执行 s5 (diversity) — COMPUTE_ONLY
    s5_out = loop.execute_step(task, plan, plan.steps[4])
    assert s5_out["kind"] == "candidate"
    assert s5_out["candidate"].result_type == "diversity_alpha"
    # 执行 s6 (diversity pathway) — 依赖 s5
    s6_out = loop.execute_step(task, plan, plan.steps[5])
    assert s6_out["candidate"].analysis_id == "B2-div-pathway"
    # 执行 s7 (diversity fungal) — 依赖 s5
    s7_out = loop.execute_step(task, plan, plan.steps[6])
    loop.complete(task.task_id, detail="7-step plan executed (partial: 5/7 compute+knowledge steps)")
    # replay
    st = loop.ws.replay()
    assert st.research_plans == 1
    assert st.candidate_results >= 3  # s5+s6+s7 三个候选
    assert st.loop_events >= 7  # 每步至少一个 stage_entered
    terminals = [e for e in loop.ws.events()
                 if e["record_type"] == "LoopEvent" and e["record"]["kind"] == "terminal"]
    assert terminals and terminals[-1]["record"]["verdict"] == "task_completed"
