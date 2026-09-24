"""B3 Long-horizon Recovery——跨天/跨会话的 Scientific Loop 中断恢复。

H5 B类缺口第3项：断点续跑机制存在（session save/load + append-only 账本），
但跨天/跨会话的中断恢复未测试。本文件以"进程死亡=对象丢弃、恢复=全新对象
从同一账本重建"模拟跨天场景（账本无内存状态，语义等价于跨进程）。

Case A：候选入账后、裁决前崩溃（最危险窗口）→ 新会话从账本恢复原候选，
        不重算不重复入账，直接裁决提交至完成
Case B：中断续跑 vs 一次性跑完 → 两条账本语义投影一致（剥离时间戳后逐事件相等）
Case C：KSDS ResearchSession save/load 跨会话延续（计划/发现/预算不丢不重）
负路径×5：半行损坏 fail-loud / 候选篡改指纹拦截 / 失效裁决拦截 /
        错误 root 无幻影恢复 / 尾部丢失候选引用拦截。
"""
from __future__ import annotations

import json

import pytest

from mra.capability import build_default_registry
from mra.research.scientific_loop import ScientificLoop
from mra.research.session import BudgetExceeded, Finding, ResearchSession
from mra.workspace import (CandidateResult, PlanStep, ResearchPlan,
                           ResearchTask, Workspace)

RULE = "method-zero-variance-guard-001"


def _exec(capability_id, inputs, context=None):
    """确定性注入执行器（COMPUTE_ONLY 形态），零宿主数据依赖。"""
    aid = inputs.get("analysis_id") or "b3-anon"
    return {"candidate": {
        "analysis_id": aid, "capability_id": capability_id,
        "implementation_id": "mra.numpy", "capability_version": "1.0.0",
        "implementation_version": "1.0.0",
        "input_fingerprint": f"sha256:{aid}", "output_summary": "n=10; median=0.5",
        "metrics": {"n_samples": 10, "rho": 0.42},
        "provenance": {"task": "b3", "n": 10}},
        "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}]}


def _task(task_id="T-B3"):
    return ResearchTask(
        task_id=task_id, question="长周期任务恢复：跨会话中断后续跑",
        objective="B3: long-horizon recovery", task_type="exploratory_association",
        entities=["Faecalibacterium prausnitzii"], constraints=[], client="b3-test")


def _plan(task_id):
    return ResearchPlan(
        plan_id="B3-P1", research_task_id=task_id, plan_version=1,
        steps=[
            PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                     inputs={"features": "species", "analysis_id": "B3-A1"},
                     expected_output="candidate_result"),
            PlanStep(step_id="s2", capability_id="diversity.alpha_shannon",
                     inputs={"features": "pathway", "analysis_id": "B3-A2"},
                     depends_on=["s1"], expected_output="sensitivity_comparison"),
        ],
        method_constraints=[RULE],
        stopping_conditions=["insufficient_data", "blocking_governance"],
        fallback_paths=["s2 fail→report"], governance_requirements=["evidence_gate"])


def _recover_candidate(ws: Workspace) -> CandidateResult:
    """恢复会话从账本重建候选对象（账本是唯一事实源）。"""
    ev = next(e for e in ws.events() if e["record_type"] == "CandidateResult")
    return CandidateResult(**ev["record"])


# ---- Case A：候选入账后、裁决前崩溃 ----

def test_case_a_crash_between_candidate_and_decision(tmp_path):
    reg = build_default_registry()
    # Day 1：开任务→采纳计划→执行 s1 出候选→进程死亡（对象直接丢弃）
    loop1 = ScientificLoop("b3-a", registry=reg, workspace_root=tmp_path,
                           executor=_exec)
    task = _task("T-B3A")
    loop1.open_task(task)
    loop1.adopt_plan(task, _plan(task.task_id))
    out = loop1.execute_step(task, _plan(task.task_id), _plan(task.task_id).steps[0])
    assert out["kind"] == "candidate"
    del loop1, out
    # Day 2：全新对象、同一账本恢复
    loop2 = ScientificLoop("b3-a", registry=reg, workspace_root=tmp_path,
                           executor=_exec)
    st = loop2.ws.replay()
    assert st.tasks and st.research_plans == 1 and st.candidate_results == 1
    assert st.evidence == []  # 昨天停在候选，未裁决未提交
    cand = _recover_candidate(loop2.ws)
    res = loop2.evaluate_and_commit(task, cand, rules=[RULE])
    loop2.commit_evidence(task, cand, res["decision"], claim="中断恢复后提交")
    loop2.complete(task.task_id, detail="recovered after crash")
    # 恢复语义：不重算不重复入账、seq 跨会话连续不重置、终态正确
    st2 = loop2.ws.replay()
    assert st2.candidate_results == 1
    assert st2.evidence[0]["evidence_id"] == "EV-T-B3A"
    assert st2.evidence[0]["governance"]["decision"]["decision_id"] == \
        res["decision"].decision_id
    seqs = [e["seq"] for e in loop2.ws.events()]
    assert seqs == list(range(1, len(seqs) + 1))
    terminals = [e["record"] for e in loop2.ws.events()
                 if e["record_type"] == "LoopEvent" and e["record"]["kind"] == "terminal"]
    assert terminals[-1]["verdict"] == "task_completed"


# ---- Case B：中断续跑 vs 一次性跑完，语义等价 ----

def _semantic_projection(events):
    """语义投影：递归剥离时间戳（created_at/at）；GovernanceDecision.candidate_hash
    归一为占位符——它是对含 created_at 的候选全记录的摘要，两次运行语义相同
    仍必然不同（指纹只用于防篡改，不承载跨运行语义；账本内重验见负路径2）。"""
    volatile = ("created_at", "at")

    def _strip(obj):
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items() if k not in volatile}
        if isinstance(obj, list):
            return [_strip(v) for v in obj]
        return obj

    out = []
    for e in events:
        rec = _strip(e["record"])
        if e["record_type"] == "GovernanceDecision":
            rec = {**rec, "candidate_hash": "<fingerprint>"}
        out.append((e["record_type"], rec))
    return out


def test_case_b_interrupted_equals_uninterrupted(tmp_path):
    task_id = "T-B3EQ"
    claim = "equivalence claim"
    # 一次性跑完
    loop_f = ScientificLoop("b3-full", registry=build_default_registry(),
                            workspace_root=tmp_path, executor=_exec)
    task = _task(task_id)
    plan = _plan(task_id)
    loop_f.open_task(task)
    loop_f.adopt_plan(task, plan)
    out = loop_f.execute_step(task, plan, plan.steps[0])
    res = loop_f.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    loop_f.commit_evidence(task, out["candidate"], res["decision"], claim=claim)
    loop_f.complete(task_id)
    # 中断版：同流程但在候选入账后崩溃，恢复会话续完
    loop_i = ScientificLoop("b3-int", registry=build_default_registry(),
                            workspace_root=tmp_path, executor=_exec)
    loop_i.open_task(task)
    loop_i.adopt_plan(task, plan)
    loop_i.execute_step(task, plan, plan.steps[0])
    del loop_i
    loop_r = ScientificLoop("b3-int", registry=build_default_registry(),
                            workspace_root=tmp_path, executor=_exec)
    cand = _recover_candidate(loop_r.ws)
    res_r = loop_r.evaluate_and_commit(task, cand, rules=[RULE])
    loop_r.commit_evidence(task, cand, res_r["decision"], claim=claim)
    loop_r.complete(task_id)
    # 两条账本语义投影逐事件相等（同 evidence/同裁决链/同门序列/同终态）
    assert _semantic_projection(loop_f.ws.events()) == \
        _semantic_projection(loop_r.ws.events())


# ---- Case C：KSDS ResearchSession 跨会话延续 ----

def test_case_c_session_save_load_across_days(tmp_path):
    s1 = ResearchSession("跨天研究问题", "哈尔滨队列", run_id="b3-day1",
                         root=tmp_path, llm_call_cap=2)
    s1.set_plan([{"step": 1, "capability": "diversity.alpha_shannon"}])
    s1.record_llm_call()
    s1.add_finding(Finding(claim="day1 发现", tool="kg", inputs={}))
    del s1  # Day 1 结束
    s2 = ResearchSession.load(tmp_path / "b3-day1", root=tmp_path)
    assert s2.state["plan"] and s2.state["llm_calls"] == 1
    assert len(s2.state["findings"]) == 1
    assert s2.state["findings"][0]["claim"] == "day1 发现"
    s2.add_finding(Finding(claim="day2 续跑发现", tool="kg", inputs={}))
    assert len(s2.state["findings"]) == 2  # 追加不覆盖
    # 预算跨会话延续：cap=2 已用 1 → 再 1 次成功、下一次触发闸门
    s2.record_llm_call()
    with pytest.raises(BudgetExceeded):
        s2.record_llm_call()
    assert s2.state["status"] == "budget_stopped"


# ---- 负路径×5 ----

def _rewrite_events(ws: Workspace, transform) -> None:
    """模拟盘上账本被篡改/截断（绕过 append 接口直接改文件）。"""
    lines = ws.events_path.read_text(encoding="utf-8").splitlines()
    lines = transform(lines)
    ws.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_candidate_decision(root, study, task_id="T-B3N"):
    """推进到"候选+裁决已入账、证据未提交"，返回 (ws, decision记录)。"""
    loop = ScientificLoop(study, registry=build_default_registry(),
                          workspace_root=root, executor=_exec)
    task = _task(task_id)
    loop.open_task(task)
    loop.adopt_plan(task, _plan(task_id))
    out = loop.execute_step(task, _plan(task_id), _plan(task_id).steps[0])
    res = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    return loop.ws, res["decision"].model_dump(), task


def test_neg1_half_written_line_fails_loud(tmp_path):
    """进程死时最后一行只写了一半 → 恢复读账本 fail-loud，不静默丢数据。"""
    ws, _, _ = _seed_candidate_decision(tmp_path, "b3-neg1")
    with ws.events_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "record_type": "Candi')  # 无换行的半行
    with pytest.raises(json.JSONDecodeError):
        Workspace("b3-neg1", root=tmp_path).events()


def test_neg2_tampered_candidate_fingerprint_mismatch(tmp_path):
    """恢复前候选记录被篡改 → 六验"候选指纹与裁决时不一致"拦截。"""
    ws, decision, task = _seed_candidate_decision(tmp_path, "b3-neg2")

    def _tamper(lines):
        parsed = [json.loads(l) for l in lines if l.strip()]
        for p in parsed:
            if p["record_type"] == "CandidateResult":
                p["record"]["output_summary"] = "n=999; tampered"
        return [json.dumps(p, ensure_ascii=False) for p in parsed]

    _rewrite_events(ws, _tamper)
    reg = build_default_registry()
    with pytest.raises(ValueError, match="候选指纹"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b3-neg2", "decision_id": decision["decision_id"],
                    "record": {"evidence_id": "EV-N2", "task_id": task.task_id,
                               "claim": "不应入账", "candidate_id": "B3-A1"}},
                   context={"workspace_root": tmp_path})


def test_neg3_superseded_decision_rejected_on_resume(tmp_path):
    """恢复时引用已被再裁决取代的旧 decision → "已失效"拦截。"""
    ws, decision, task = _seed_candidate_decision(tmp_path, "b3-neg3")
    cand = _recover_candidate(ws)
    seq = next(e["seq"] for e in ws.events()
               if e["record_type"] == "CandidateResult")
    from mra.governance import evaluate_candidate
    re_decision = evaluate_candidate(
        cand, candidate_event_seq=seq, method_rules_applied=[RULE],
        execution_governance={"verdicts": ["v"]}, decision_id="GD-RESUME-2",
        supersedes_decision_id=decision["decision_id"])
    ws.append(re_decision)
    reg = build_default_registry()
    with pytest.raises(ValueError, match="已失效"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b3-neg3", "decision_id": decision["decision_id"],
                    "record": {"evidence_id": "EV-N3", "task_id": task.task_id,
                               "claim": "旧裁决不应复活", "candidate_id": "B3-A1"}},
                   context={"workspace_root": tmp_path})


def test_neg4_wrong_root_no_phantom_recovery(tmp_path):
    """恢复到错误 root（空账本）→ 无幻影状态，旧 decision_id 找不到。"""
    _, decision, _ = _seed_candidate_decision(tmp_path, "b3-neg4")
    reg = build_default_registry()
    empty_root = tmp_path / "elsewhere"
    with pytest.raises(ValueError, match="不在 Scientific Ledger"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b3-neg4", "decision_id": decision["decision_id"],
                    "record": {"evidence_id": "EV-N4", "task_id": "T-B3N",
                               "claim": "无中生有", "candidate_id": "B3-A1"}},
                   context={"workspace_root": empty_root})
    # 空 root 的 replay 为零事件：不存在任何"被恢复"的状态
    assert Workspace("b3-neg4", root=empty_root).replay().n_events == 0


def test_neg5_tail_loss_losing_candidate_reference(tmp_path):
    """崩溃尾部丢失候选事件但裁决残留 → 裁决引用悬空，提交被拦。"""
    ws, decision, _ = _seed_candidate_decision(tmp_path, "b3-neg5")

    def _drop_candidate(lines):
        parsed = [json.loads(l) for l in lines if l.strip()]
        return [json.dumps(p, ensure_ascii=False) for p in parsed
                if p["record_type"] != "CandidateResult"]

    _rewrite_events(ws, _drop_candidate)
    reg = build_default_registry()
    with pytest.raises(ValueError, match="不在账本"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b3-neg5", "decision_id": decision["decision_id"],
                    "record": {"evidence_id": "EV-N5", "task_id": "T-B3N",
                               "claim": "悬空引用", "candidate_id": "B3-A1"}},
                   context={"workspace_root": tmp_path})
