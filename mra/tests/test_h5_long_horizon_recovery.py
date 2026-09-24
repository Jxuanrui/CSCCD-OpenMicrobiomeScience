"""B3 Long-horizon Recovery——跨天/跨会话的 Scientific Loop 中断恢复。

核心命题不是"程序重启以后还能继续跑"，而是：**科研状态恢复后不会重复
计算、重复裁决、重复写证据或丢失 lineage**（retry ≠ new scientific event，
B4.1 幂等底座之上的恢复语义）。

恢复模型：进程死亡=对象丢弃；恢复=全新对象从同一 append-only 账本重建。
执行状态不另建状态机——由账本派生（候选在场=compute 完成；有效裁决在场
=governance 完成；Evidence 在场=commit 完成），符合"不建第二套 workflow
engine"的架构裁决。

Case A — Planning 后中断：task/plan 恢复、plan version 正确、不重建同版计划、
        supersedes lineage 不丢、current_stage 由账本派生
Case B — Compute 后 Governance 前中断：复用已有候选（deterministic），
        fingerprint/analysis_id 不变，从 Governance 继续
Case C — Governance 后 Evidence 前中断：复用已有有效裁决，不重复创建等价
        decision，用现有 decision 完成 record_evidence
Case D — Evidence commit 后 ACK 前中断（最关键）：caller 不确定是否成功而
        重试 → Evidence 数量不增加
Case E — 多步计划中途恢复：s1-s4 已完成，恢复后仅从 s5 继续（不重跑、不
        跳依赖、零重复事件）
Case F — mutation 后中断：同义 downgrade 重试不产生第二个事件；新 rationale
        允许新事件
等价性 — 中断续跑 vs 一次性跑完：账本语义投影逐事件相等
KSDS — ResearchSession save/load 跨会话延续（进度/发现/预算不丢不重）
负路径×5 — 半行损坏 fail-loud / 候选篡改指纹拦截 / 失效裁决拦截 /
        错误 root 无幻影恢复 / 尾部丢失候选引用拦截

量化指标（每 Case 内断言，全文件口径）：
  Duplicate CandidateResult / GovernanceDecision / Evidence commit rate = 0
  Lost lineage rate = 0；Resume-from-wrong-stage rate = 0
  Illegal re-execution rate = 0；Replay mismatch rate = 0
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


def _plan(task_id, plan_id="B3-P1", version=1, supersedes=None, n_steps=2):
    steps = [PlanStep(step_id=f"s{i}",
                      capability_id="diversity.alpha_shannon",
                      inputs={"features": "species", "analysis_id": f"{plan_id}-s{i}"},
                      depends_on=[f"s{i-1}"] if i > 1 else [],
                      expected_output="candidate_result")
             for i in range(1, n_steps + 1)]
    return ResearchPlan(
        plan_id=plan_id, research_task_id=task_id, plan_version=version,
        supersedes_plan_id=supersedes, steps=steps,
        method_constraints=[RULE],
        stopping_conditions=["insufficient_data", "blocking_governance"],
        fallback_paths=["fail→report"], governance_requirements=["evidence_gate"])


def _loop(study, root, task_id="T-B3"):
    return ScientificLoop(study, registry=build_default_registry(),
                          workspace_root=root, executor=_exec)


def _counts(ws):
    """账本分类计数（量化指标的数据源）。"""
    out: dict[str, int] = {}
    for e in ws.events():
        out[e["record_type"]] = out.get(e["record_type"], 0) + 1
    return out


def _recover_candidate(ws: Workspace, analysis_id: str) -> CandidateResult:
    ev = next(e for e in ws.events()
              if e["record_type"] == "CandidateResult"
              and e["record"]["analysis_id"] == analysis_id)
    return CandidateResult(**ev["record"])


def _drive_step(loop, task, plan, i):
    """单步全链驱动：execute → evaluate → commit（多步任务用步级 evidence_id）。"""
    step = plan.steps[i]
    out = loop.execute_step(task, plan, step)
    res = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    return loop.commit_evidence(task, out["candidate"], res["decision"],
                                claim=f"step {step.step_id} 发现",
                                evidence_id=f"EV-{task.task_id}-{step.step_id}")


# ---- Case A：Planning 后中断 ----

def test_case_a_planning_crash_recovery(tmp_path):
    task_id = "T-B3A"
    task = _task(task_id)
    plan_v1 = _plan(task_id, n_steps=2)
    # Day 1：开任务 + 采纳 v1 + 修订 v2 → crash
    loop1 = _loop("b3-a", tmp_path, task_id)
    loop1.open_task(task)
    loop1.adopt_plan(task, plan_v1)
    plan_v2 = _plan(task_id, plan_id="B3-P2", version=2, supersedes="B3-P1")
    loop1.adopt_plan(task, plan_v2)
    del loop1
    # Day 2：恢复
    loop2 = _loop("b3-a", tmp_path, task_id)
    st = loop2.ws.replay()
    assert st.tasks and st.tasks[0]["task_id"] == task_id          # 任务恢复
    assert st.research_plans == 2                                   # v1+v2 并存
    plans = [e["record"] for e in loop2.ws.events()
             if e["record_type"] == "ResearchPlan"]
    v2 = next(p for p in plans if p["plan_version"] == 2)
    assert v2["supersedes_plan_id"] == "B3-P1"                      # lineage 不丢
    # current_stage 由账本派生：最后一个 stage_entered 是 planning
    stages = [e["record"]["stage"] for e in loop2.ws.events()
              if e["record_type"] == "LoopEvent"
              and e["record"]["kind"] == "stage_entered"]
    assert stages[-1] == "planning"
    # 重驱动同版计划：不重新创造同一个 Plan（reused，零新事件）
    n_before = len(loop2.ws.events())
    verdict = loop2.adopt_plan(task, plan_v2)
    assert verdict.get("reused") and len(loop2.ws.events()) == n_before


# ---- Case B：Compute 后、Governance 前中断 ----

def test_case_b_compute_crash_reuse_candidate(tmp_path):
    task_id = "T-B3B"
    task = _task(task_id)
    plan = _plan(task_id)
    loop1 = _loop("b3-b", tmp_path, task_id)
    loop1.open_task(task)
    loop1.adopt_plan(task, plan)
    out1 = loop1.execute_step(task, plan, plan.steps[0])
    assert out1["kind"] == "candidate" and not out1.get("reused")
    fp, aid = out1["candidate"].input_fingerprint, out1["candidate"].analysis_id
    n_before = _counts(loop1.ws)["CandidateResult"]
    del loop1, out1
    # 恢复后重驱动同一步：复用已有 deterministic 候选，不无条件重复计算
    loop2 = _loop("b3-b", tmp_path, task_id)
    out2 = loop2.execute_step(task, plan, plan.steps[0])
    assert out2.get("reused")                                   # 复用而非重算
    assert out2["candidate"].analysis_id == aid                 # analysis_id 不变
    assert out2["candidate"].input_fingerprint == fp            # fingerprint 不变
    assert _counts(loop2.ws)["CandidateResult"] == n_before     # 零重复候选
    # 从 Governance 阶段继续（而非回到 compute）
    res = loop2.evaluate_and_commit(task, out2["candidate"], rules=[RULE])
    assert not res.get("reused") and res["decision"].allow_evidence


# ---- Case C：Governance 后、Evidence 前中断 ----

def test_case_c_governance_crash_reuse_decision(tmp_path):
    task_id = "T-B3C"
    task = _task(task_id)
    plan = _plan(task_id)
    loop1 = _loop("b3-c", tmp_path, task_id)
    loop1.open_task(task)
    loop1.adopt_plan(task, plan)
    cand = loop1.execute_step(task, plan, plan.steps[0])["candidate"]
    d1 = loop1.evaluate_and_commit(task, cand, rules=[RULE])["decision"]
    n_dec = _counts(loop1.ws)["GovernanceDecision"]
    del loop1
    # 恢复后重驱动 evaluate：找到已有有效裁决，不创建第二个等价 decision
    loop2 = _loop("b3-c", tmp_path, task_id)
    cand_r = _recover_candidate(loop2.ws, cand.analysis_id)
    res = loop2.evaluate_and_commit(task, cand_r, rules=[RULE])
    assert res.get("reused") and res["decision"].decision_id == d1.decision_id
    assert _counts(loop2.ws)["GovernanceDecision"] == n_dec      # 零重复裁决
    # 用现有 decision 完成 record_evidence
    out = loop2.commit_evidence(task, cand_r, res["decision"], claim="恢复后续提交",
                                evidence_id=f"EV-{task_id}-s1")
    assert out["committed"]
    st = loop2.ws.replay()
    assert st.evidence[0]["governance"]["decision"]["decision_id"] == d1.decision_id


# ---- Case D：Evidence commit 后 ACK 前中断（最关键） ----

def test_case_d_ack_loss_retry_no_new_evidence(tmp_path):
    task_id = "T-B3D"
    task = _task(task_id)
    plan = _plan(task_id)
    loop1 = _loop("b3-d", tmp_path, task_id)
    loop1.open_task(task)
    loop1.adopt_plan(task, plan)
    cand = loop1.execute_step(task, plan, plan.steps[0])["candidate"]
    decision = loop1.evaluate_and_commit(task, cand, rules=[RULE])["decision"]
    out1 = loop1.commit_evidence(task, cand, decision, claim="ACK 丢失前的提交",
                                 evidence_id=f"EV-{task_id}-s1")
    assert out1["committed"]                                   # commit 实际成功
    del loop1, out1                                            # …ACK 丢失 + 进程死亡
    # restart：caller 不确定上一次是否成功 → 重试同一命令
    loop2 = _loop("b3-d", tmp_path, task_id)
    cand_r = _recover_candidate(loop2.ws, cand.analysis_id)
    decision_r = loop2.evaluate_and_commit(task, cand_r, rules=[RULE])
    assert decision_r.get("reused")                            # 裁决同样复用
    out2 = loop2.commit_evidence(task, cand_r, decision_r["decision"],
                                 claim="ACK 丢失前的提交",
                                 evidence_id=f"EV-{task_id}-s1")
    assert not out2["committed"] and out2["already_committed"]  # retry ≠ 新事实
    ws = Workspace("b3-d", root=tmp_path)
    assert _counts(ws)["Evidence"] == 1                        # Evidence 数量不增加
    st = ws.replay()
    assert len(st.evidence) == 1 and st.evidence[0]["claim"] == "ACK 丢失前的提交"


# ---- Case E：多步计划中途恢复 ----

def test_case_e_multistep_midway_resume(tmp_path):
    task_id = "T-B3E"
    task = _task(task_id)
    plan = _plan(task_id, n_steps=5)
    loop1 = _loop("b3-e", tmp_path, task_id)
    loop1.open_task(task)
    loop1.adopt_plan(task, plan)
    for i in range(4):                                          # s1-s4 全链完成
        _drive_step(loop1, task, plan, i)
    before = _counts(loop1.ws)
    assert before["CandidateResult"] == 4 and before["Evidence"] == 4
    del loop1                                                   # crash
    # restart + replay → 从下一合法 step 继续（驱动器从头重放整计划）
    loop2 = _loop("b3-e", tmp_path, task_id)
    plan_r = _plan(task_id, n_steps=5)                          # 同一计划对象语义
    hits = []
    for i in range(5):
        step = plan_r.steps[i]
        out = loop2.execute_step(task, plan_r, step)
        res = loop2.evaluate_and_commit(task, out["candidate"], rules=[RULE])
        com = loop2.commit_evidence(task, out["candidate"], res["decision"],
                                    claim=f"step {step.step_id} 发现",
                                    evidence_id=f"EV-{task_id}-{step.step_id}")
        hits.append((out.get("reused"), res.get("reused"), com.get("already_committed")))
    # s1-s4 三层全部幂等命中（不重跑、不重复裁决、不重复证据）
    assert all(r and e and c for r, e, c in hits[:4])
    # s5 全新执行（未跳过依赖：s5 依赖 s4，重放顺序保证先见 s4 在场）
    assert not hits[4][0] and not hits[4][1] and not hits[4][2]
    loop2.complete(task_id, detail="resumed from s5")
    after = _counts(loop2.ws)
    # 量化指标：恢复只新增 s5 的三类事件
    assert after["CandidateResult"] == before["CandidateResult"] + 1
    assert after["GovernanceDecision"] == before["GovernanceDecision"] + 1
    assert after["Evidence"] == before["Evidence"] + 1
    st = loop2.ws.replay()
    assert len(st.evidence) == 5                                 # Replay 一致
    # Resume-from-wrong-stage = 0：s1 的候选/裁决/证据 seq 均早于 s5 的新事件
    seqs = {e["record"].get("analysis_id", ""): e["seq"]
            for e in loop2.ws.events() if e["record_type"] == "CandidateResult"}
    assert seqs["B3-P1-s1"] < seqs["B3-P1-s5"]


# ---- Case F：mutation 后中断 ----

def test_case_f_mutation_retry_dedup(tmp_path):
    task_id = "T-B3F"
    task = _task(task_id)
    plan = _plan(task_id)
    loop1 = _loop("b3-f", tmp_path, task_id)
    loop1.open_task(task)
    loop1.adopt_plan(task, plan)
    cand = loop1.execute_step(task, plan, plan.steps[0])["candidate"]
    decision = loop1.evaluate_and_commit(task, cand, rules=[RULE])["decision"]
    loop1.commit_evidence(task, cand, decision, claim="F 用证据",
                          evidence_id=f"EV-{task_id}-s1")
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    reg.invoke("workspace.mark_downgraded",
               {"study_id": "b3-f", "evidence_id": f"EV-{task_id}-s1",
                "reason": "敏感性分析不稳健", "actor": task_id}, context=ctx)
    n_ev = _counts(loop1.ws)["Evidence"]
    del loop1, reg
    # 恢复后重复同义 downgrade 请求 → 不产生第二个同义事件
    reg2 = build_default_registry()
    out = reg2.invoke("workspace.mark_downgraded",
                      {"study_id": "b3-f", "evidence_id": f"EV-{task_id}-s1",
                       "reason": "敏感性分析不稳健", "actor": task_id}, context=ctx)
    assert out["already_applied"] and not out["committed"]
    assert _counts(Workspace("b3-f", root=tmp_path))["Evidence"] == n_ev
    # 新 GovernanceDecision/rationale 出现 → 允许新 mutation event
    out2 = reg2.invoke("workspace.mark_refuted",
                       {"study_id": "b3-f", "evidence_id": f"EV-{task_id}-s1",
                        "reason": "特异性对照复测推翻", "actor": task_id}, context=ctx)
    assert out2["committed"]
    st = Workspace("b3-f", root=tmp_path).replay()
    assert st.evidence[0]["falsification"] == "refuted"


# ---- 等价性：中断续跑 vs 一次性跑完 ----

def _semantic_projection(events):
    """语义投影：递归剥离时间戳（created_at/at/measured_at/calculated_at）与
    实测时长（wall/compute_duration_ms——物理计时跨运行必然不同，属测量噪声
    而非科研语义）；GovernanceDecision.candidate_hash 归一为占位符——它是对
    含 created_at 的候选全记录的摘要，两次运行语义相同仍必然不同（指纹只用
    于防篡改，不承载跨运行语义；账本内重验见负路径2）。"""
    volatile = ("created_at", "at", "measured_at", "calculated_at",
                "wall_duration_ms", "compute_duration_ms")

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


def test_equivalence_interrupted_equals_uninterrupted(tmp_path):
    task_id = "T-B3EQ"
    claim = "equivalence claim"
    # 一次性跑完
    loop_f = _loop("b3-full", tmp_path, task_id)
    task = _task(task_id)
    plan = _plan(task_id)
    loop_f.open_task(task)
    loop_f.adopt_plan(task, plan)
    out = loop_f.execute_step(task, plan, plan.steps[0])
    res = loop_f.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    loop_f.commit_evidence(task, out["candidate"], res["decision"], claim=claim,
                           evidence_id=f"EV-{task_id}-s1")
    loop_f.complete(task_id)
    # 中断版：同流程但在候选入账后崩溃，恢复会话续完
    loop_i = _loop("b3-int", tmp_path, task_id)
    loop_i.open_task(task)
    loop_i.adopt_plan(task, plan)
    loop_i.execute_step(task, plan, plan.steps[0])
    del loop_i
    loop_r = _loop("b3-int", tmp_path, task_id)
    cand = _recover_candidate(loop_r.ws, "B3-P1-s1")
    res_r = loop_r.evaluate_and_commit(task, cand, rules=[RULE])
    loop_r.commit_evidence(task, cand, res_r["decision"], claim=claim,
                           evidence_id=f"EV-{task_id}-s1")
    loop_r.complete(task_id)
    # 两条账本语义投影逐事件相等（同 evidence/同裁决链/同门序列/同终态）
    assert _semantic_projection(loop_f.ws.events()) == \
        _semantic_projection(loop_r.ws.events())


# ---- KSDS ResearchSession 跨会话延续 ----

def test_ksds_session_save_load_across_days(tmp_path):
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
    """推进到"候选+裁决已入账、证据未提交"，返回 (ws, decision记录, task)。"""
    loop = _loop(study, root, task_id)
    task = _task(task_id)
    plan = _plan(task_id)
    loop.open_task(task)
    loop.adopt_plan(task, plan)
    out = loop.execute_step(task, plan, plan.steps[0])
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
                               "claim": "不应入账", "candidate_id": "B3-P1-s1"}},
                   context={"workspace_root": tmp_path})


def test_neg3_superseded_decision_rejected_on_resume(tmp_path):
    """恢复时引用已被再裁决取代的旧 decision → "已失效"拦截。"""
    ws, decision, task = _seed_candidate_decision(tmp_path, "b3-neg3")
    cand = _recover_candidate(ws, "B3-P1-s1")
    seq = next(e["seq"] for e in ws.events()
               if e["record_type"] == "CandidateResult")
    from mra.governance import evaluate_candidate
    re_decision = evaluate_candidate(
        cand, candidate_event_seq=seq, method_rules_applied=[RULE],
        execution_governance={"verdicts": ["v"]}, decision_id="GD-RESUME-2",
        supersedes_decision_id=decision["decision_id"])
    ws.append(re_decision.model_copy(
        update={"research_task_id": task.task_id}))
    reg = build_default_registry()
    with pytest.raises(ValueError, match="已失效"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b3-neg3", "decision_id": decision["decision_id"],
                    "record": {"evidence_id": "EV-N3", "task_id": task.task_id,
                               "claim": "旧裁决不应复活", "candidate_id": "B3-P1-s1"}},
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
                               "claim": "无中生有", "candidate_id": "B3-P1-s1"}},
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
                               "claim": "悬空引用", "candidate_id": "B3-P1-s1"}},
                   context={"workspace_root": tmp_path})
